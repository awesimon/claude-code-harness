from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import harness.agents as scheduler_module
from agents.built_in import EXPLORE_AGENT
from agents.engine import AgentExecutor
from agents.types import (
    AgentDefinitionError,
    AgentExecutionResult,
    AgentHooks,
    AgentRequest,
    CustomAgentDefinition,
)
from harness import SessionHarnessFactory
from harness.agents import (
    AgentNotFound,
    AgentOwnershipError,
    AgentScheduler,
    AgentSchedulerAlreadyActive,
    AgentSchedulerDegraded,
    AgentWaitTimeout,
)
from models import Base
from services.llm_service import ChatCompletionResponse
from state_core import (
    AgentRecord,
    AgentStatus,
    AgentTerminationReason,
    SessionRuntimeFactory,
    SQLAlchemyStateStore,
)


class ControlledRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[tuple[AgentRecord, Any]] = []
        self.active = 0
        self.peak = 0

    async def run(self, record: AgentRecord, child_harness: Any) -> AgentExecutionResult:
        self.calls.append((record, child_harness))
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.set()
        try:
            await self.release.wait()
            return AgentExecutionResult(
                content=[{"type": "text", "text": record.prompt}],
                usage={"total_tokens": 7},
                tool_count=2,
                termination_reason="completed",
                output={"answer": record.prompt},
            )
        finally:
            self.active -= 1


def make_harness(tmp_path: Path, session_id: str = "root"):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    engine = create_engine(f"sqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    store = SQLAlchemyStateStore(session_factory)
    factory = SessionHarnessFactory(
        SessionRuntimeFactory(store), workspace_root=workspace
    )
    return factory.create(session_id), session_factory


async def wait_until(predicate, timeout: float = 1.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(poll(), timeout)


def create_running_parent(harness, agent_id: str, *, root: str | None = None):
    record = harness.store.agents.create(
        AgentRecord(
            agent_id,
            root or harness.root_session_id,
            "Explore",
            "parent",
            "parent",
            True,
            str(harness.effective_cwd),
            {},
        )
    )
    return harness.store.agents.transition(
        record.agent_id, AgentStatus.RUNNING, record.revision
    )


@pytest.mark.asyncio
async def test_foreground_lifecycle_persists_output_and_usage(tmp_path: Path) -> None:
    harness, _ = make_harness(tmp_path)
    runner = ControlledRunner()
    scheduler = AgentScheduler(harness, harness.store.agents, runner=runner)
    spawn = asyncio.create_task(
        scheduler.spawn(AgentRequest("inspect", "Explore", "find files"))
    )

    await runner.started.wait()
    running = scheduler.list(status=AgentStatus.RUNNING)
    assert len(running) == 1
    assert running[0].started_at is not None
    runner.release.set()

    completed = await spawn
    assert completed.status is AgentStatus.COMPLETED
    assert completed.termination_reason is AgentTerminationReason.COMPLETED
    assert completed.output == {
        "content": [{"type": "text", "text": "inspect"}],
        "tool_count": 2,
        "output": {"answer": "inspect"},
    }
    assert completed.usage == {"total_tokens": 7}


@pytest.mark.asyncio
async def test_background_returns_pending_then_status_and_wait(tmp_path: Path) -> None:
    harness, _ = make_harness(tmp_path)
    runner = ControlledRunner()
    scheduler = AgentScheduler(harness, harness.store.agents, runner=runner)

    created = await scheduler.spawn(
        AgentRequest("inspect", "Explore", "find files", background=True)
    )
    assert created.status is AgentStatus.PENDING
    await runner.started.wait()
    assert scheduler.status(created.agent_id).status is AgentStatus.RUNNING
    runner.release.set()
    assert (await scheduler.wait(created.agent_id)).status is AgentStatus.COMPLETED


@pytest.mark.asyncio
async def test_fresh_scheduler_reads_completed_history(tmp_path: Path) -> None:
    harness, session_factory = make_harness(tmp_path)
    runner = ControlledRunner()
    runner.release.set()
    first = AgentScheduler(harness, harness.store.agents, runner=runner)
    completed = await first.spawn(AgentRequest("inspect", "Explore", "find files"))
    await first.shutdown()

    fresh_store = SQLAlchemyStateStore(session_factory)
    resumed = AgentScheduler(harness, fresh_store.agents, runner=ControlledRunner())
    loaded = await resumed.wait(completed.agent_id, timeout=0)

    assert loaded == completed


@pytest.mark.asyncio
async def test_fresh_scheduler_waits_for_other_live_scheduler(tmp_path: Path) -> None:
    harness, session_factory = make_harness(tmp_path)
    runner = ControlledRunner()
    owner = AgentScheduler(harness, harness.store.agents, runner=runner)
    record = await owner.spawn(AgentRequest("live", "Explore", "d", background=True))
    await runner.started.wait()
    fresh_repository = SQLAlchemyStateStore(session_factory).agents
    observer = AgentScheduler.for_harness(
        harness, fresh_repository, runner=ControlledRunner()
    )
    assert observer is owner

    waiter = asyncio.create_task(observer.wait(record.agent_id, timeout=1))
    await asyncio.sleep(0)
    assert not waiter.done()
    runner.release.set()

    assert (await waiter).status is AgentStatus.COMPLETED


@pytest.mark.asyncio
async def test_single_scheduler_owner_rejects_direct_duplicate_and_routes_factory(
    tmp_path: Path,
) -> None:
    harness, session_factory = make_harness(tmp_path)
    owner_runner = ControlledRunner()
    owner = AgentScheduler(
        harness,
        harness.store.agents,
        runner=owner_runner,
        root_concurrency=1,
    )
    duplicate_runner = ControlledRunner()
    repository = SQLAlchemyStateStore(session_factory).agents

    with pytest.raises(AgentSchedulerAlreadyActive):
        AgentScheduler(harness, repository, runner=duplicate_runner)
    routed = AgentScheduler.for_harness(
        harness, repository, runner=duplicate_runner, root_concurrency=99
    )
    assert routed is owner

    first = await routed.spawn(
        AgentRequest("first", "Explore", "d", background=True)
    )
    await owner_runner.started.wait()
    second = await routed.spawn(
        AgentRequest("second", "Explore", "d", background=True)
    )
    await asyncio.sleep(0)
    assert [record.prompt for record, _ in owner_runner.calls] == ["first"]
    assert duplicate_runner.calls == []

    await routed.stop(first.agent_id)
    owner_runner.release.set()
    await routed.wait(second.agent_id)
    await owner.shutdown()


def test_concurrent_direct_construction_registers_exactly_one_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, _ = make_harness(tmp_path)
    ready = Barrier(2)
    original_owner_key = scheduler_module._owner_key
    original_semaphore = asyncio.BoundedSemaphore

    def synchronized_owner_key(harness, repository):
        key = original_owner_key(harness, repository)
        ready.wait(timeout=1)
        return key

    def slow_semaphore(value):
        time.sleep(0.05)
        return original_semaphore(value)

    monkeypatch.setattr(scheduler_module, "_owner_key", synchronized_owner_key)
    monkeypatch.setattr(asyncio, "BoundedSemaphore", slow_semaphore)

    def construct():
        try:
            return AgentScheduler(
                harness, harness.store.agents, runner=ControlledRunner()
            )
        except BaseException as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result() for future in [executor.submit(construct) for _ in range(2)]]

    owners = [result for result in results if isinstance(result, AgentScheduler)]
    errors = [result for result in results if isinstance(result, BaseException)]
    assert len(owners) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], AgentSchedulerAlreadyActive)
    asyncio.run(owners[0].shutdown())


def test_repository_adapters_over_one_harness_store_fail_closed(
    tmp_path: Path,
) -> None:
    harness, _ = make_harness(tmp_path)

    class RepositoryAdapter:
        def __init__(self, repository) -> None:
            self.repository = repository

        def create(self, record):
            return self.repository.create(record)

        def get(self, agent_id):
            return self.repository.get(agent_id)

        def list(self, root_session_id, **filters):
            return self.repository.list(root_session_id, **filters)

        def transition(self, agent_id, status, expected_revision, **fields):
            return self.repository.transition(
                agent_id, status, expected_revision, **fields
            )

        def reconcile(self, root_session_id, live_agent_ids):
            return self.repository.reconcile(root_session_id, live_agent_ids)

    owner = AgentScheduler(
        harness, RepositoryAdapter(harness.store.agents), runner=ControlledRunner()
    )
    duplicate = None
    try:
        with pytest.raises(AgentSchedulerAlreadyActive):
            duplicate = AgentScheduler(
                harness,
                RepositoryAdapter(harness.store.agents),
                runner=ControlledRunner(),
            )
    finally:
        if duplicate is not None:
            asyncio.run(duplicate.shutdown())
        asyncio.run(owner.shutdown())


@pytest.mark.asyncio
async def test_reconcile_scopes_root_and_preserves_live_and_terminal(tmp_path: Path) -> None:
    harness, _ = make_harness(tmp_path)
    repository = harness.store.agents
    stale_pending = repository.create(
        AgentRecord("pending", "root", "Explore", "p", "d", False, str(tmp_path), {})
    )
    stale_running = repository.create(
        AgentRecord("running", "root", "Explore", "p", "d", False, str(tmp_path), {})
    )
    stale_running = repository.transition(
        stale_running.agent_id, AgentStatus.RUNNING, stale_running.revision
    )
    terminal = repository.create(
        AgentRecord("done", "root", "Explore", "p", "d", False, str(tmp_path), {})
    )
    terminal = repository.transition(terminal.agent_id, AgentStatus.RUNNING, terminal.revision)
    repository.transition(terminal.agent_id, AgentStatus.COMPLETED, terminal.revision)
    repository.create(
        AgentRecord("foreign", "other", "Explore", "p", "d", False, str(tmp_path), {})
    )

    live_runner = ControlledRunner()
    other_scheduler = AgentScheduler(harness, repository, runner=live_runner)
    live = await other_scheduler.spawn(
        AgentRequest("live", "Explore", "d", background=True)
    )
    await live_runner.started.wait()

    changed = other_scheduler.reconcile()
    assert {item.agent_id for item in changed} == {
        stale_pending.agent_id,
        stale_running.agent_id,
    }
    assert repository.get(live.agent_id).status is AgentStatus.RUNNING  # type: ignore[union-attr]
    assert repository.get("done").status is AgentStatus.COMPLETED  # type: ignore[union-attr]
    assert repository.get("foreign").status is AgentStatus.PENDING  # type: ignore[union-attr]
    live_runner.release.set()
    await other_scheduler.wait(live.agent_id)


@pytest.mark.asyncio
async def test_nested_parent_root_and_child_todo_scope(tmp_path: Path) -> None:
    root, _ = make_harness(tmp_path)
    root.session_runtime.enable_todo_v1()
    parent = root.child("parent")
    runner = ControlledRunner()
    runner.release.set()
    scheduler = AgentScheduler(parent, root.store.agents, runner=runner)

    result = await scheduler.spawn(AgentRequest("nested", "Explore", "d"))
    record, child = runner.calls[0]

    assert record.agent_id == result.agent_id
    assert record.root_session_id == root.root_session_id
    assert record.parent_agent_id == "parent"
    assert child.agent_id == record.agent_id
    assert child.parent_agent_id == "parent"
    await child.tool_runtime.execute(
        "TodoWrite",
        {
            "todos": [
                {"content": "child", "status": "pending", "activeForm": "working"}
            ]
        },
        child.runtime_context,
    )
    assert root.session_runtime.state.todos[record.agent_id][0]["content"] == "child"


@pytest.mark.asyncio
async def test_root_and_per_parent_concurrency_limits(tmp_path: Path) -> None:
    root, _ = make_harness(tmp_path)
    for parent in ("a", "b", "c"):
        create_running_parent(root, parent)
    runner = ControlledRunner()
    scheduler = AgentScheduler(
        root,
        root.store.agents,
        runner=runner,
        root_concurrency=3,
        per_parent_concurrency=1,
    )
    requests = [
        AgentRequest(str(index), "Explore", "d", background=True, parent_agent_id=parent)
        for index, parent in enumerate(("a", "a", "b", "b", "c", "c"))
    ]
    records = [await scheduler.spawn(request) for request in requests]
    await wait_until(lambda: len(runner.calls) == 3)
    assert runner.peak == 3
    assert len({record.parent_agent_id for record, _ in runner.calls}) == 3

    runner.release.set()
    await asyncio.gather(*(scheduler.wait(record.agent_id) for record in records))
    assert len(runner.calls) == 6
    assert scheduler.root_available_capacity == 3
    assert scheduler.managed_concurrency_count == 0
    assert scheduler.parent_limiter_count == 0
    assert scheduler.parent_limiter_refcounts == {}


@pytest.mark.asyncio
async def test_queued_stop_never_enters_runner(tmp_path: Path) -> None:
    root, _ = make_harness(tmp_path)
    runner = ControlledRunner()
    scheduler = AgentScheduler(
        root, root.store.agents, runner=runner, root_concurrency=1, stop_grace=0.01
    )
    first = await scheduler.spawn(AgentRequest("first", "Explore", "d", background=True))
    await runner.started.wait()
    queued = await scheduler.spawn(AgentRequest("queued", "Explore", "d", background=True))

    stopped = await scheduler.stop(queued.agent_id)
    assert stopped.status is AgentStatus.CANCELLED
    assert [record.prompt for record, _ in runner.calls] == ["first"]
    runner.release.set()
    await scheduler.wait(first.agent_id)


@pytest.mark.asyncio
async def test_running_stop_cooperative_force_and_idempotent(tmp_path: Path) -> None:
    root, _ = make_harness(tmp_path)

    class CooperativeRunner:
        async def run(self, record, child):
            await child.runtime_context.cancellation.wait()
            raise asyncio.CancelledError

    cooperative = AgentScheduler(root, root.store.agents, runner=CooperativeRunner())
    first = await cooperative.spawn(AgentRequest("one", "Explore", "d", background=True))
    await wait_until(lambda: cooperative.status(first.agent_id).status is AgentStatus.RUNNING)
    cancelled = await cooperative.stop(first.agent_id)
    assert cancelled.status is AgentStatus.CANCELLED
    assert await cooperative.stop(first.agent_id) == cancelled
    await cooperative.shutdown()

    blocker = ControlledRunner()
    forced = AgentScheduler(root, root.store.agents, runner=blocker, stop_grace=0.01)
    second = await forced.spawn(AgentRequest("two", "Explore", "d", background=True))
    await blocker.started.wait()
    assert (await forced.stop(second.agent_id)).status is AgentStatus.CANCELLED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runner", "timeout", "status"),
    [
        ("exception", None, AgentStatus.FAILED),
        ("cancel", None, AgentStatus.CANCELLED),
        ("slow", 0.01, AgentStatus.TIMED_OUT),
    ],
)
async def test_runner_terminal_mappings(
    tmp_path: Path, runner: str, timeout: float | None, status: AgentStatus
) -> None:
    root, _ = make_harness(tmp_path)

    class TerminalRunner:
        async def run(self, record, child):
            if runner == "exception":
                raise RuntimeError("api_key=secret boom")
            if runner == "cancel":
                raise asyncio.CancelledError
            await asyncio.sleep(10)
            raise AssertionError("unreachable")

    scheduler = AgentScheduler(root, root.store.agents, runner=TerminalRunner())
    result = await scheduler.spawn(
        AgentRequest("terminal", "Explore", "d", timeout=timeout)
    )
    assert result.status is status
    assert result.termination_reason.value == status.value  # type: ignore[union-attr]
    if status is AgentStatus.FAILED:
        assert result.error["type"] == "RuntimeError"  # type: ignore[index]
        assert "secret" not in result.error["message"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_unknown_runner_termination_reason_fails_closed(tmp_path: Path) -> None:
    root, _ = make_harness(tmp_path)

    class UnknownReasonRunner:
        async def run(self, record, child):
            return AgentExecutionResult(
                content="unexpected", termination_reason="made_up_reason"
            )

    scheduler = AgentScheduler(
        root, root.store.agents, runner=UnknownReasonRunner()
    )

    result = await scheduler.spawn(AgentRequest("unknown", "Explore", "d"))

    assert result.status is AgentStatus.FAILED
    assert result.termination_reason is AgentTerminationReason.FAILED


@pytest.mark.asyncio
async def test_scheduler_error_credentials_are_absent_from_record_and_raw_sql(
    tmp_path: Path,
) -> None:
    root, session_factory = make_harness(tmp_path)

    class CredentialErrorRunner:
        async def run(self, record, child):
            raise RuntimeError("Authorization: Bearer topsecret")

    scheduler = AgentScheduler(
        root, root.store.agents, runner=CredentialErrorRunner()
    )

    result = await scheduler.spawn(AgentRequest("fail", "Explore", "d"))

    assert "topsecret" not in str(result.error)
    with session_factory() as db:
        physical = db.execute(
            text(
                "SELECT error_json FROM runtime_agents WHERE agent_id = :agent_id"
            ),
            {"agent_id": result.agent_id},
        ).scalar_one()
    assert "topsecret" not in str(physical)


@pytest.mark.asyncio
async def test_definition_metadata_can_configure_executor_timeout(tmp_path: Path) -> None:
    root, _ = make_harness(tmp_path)
    scheduler = AgentScheduler(root, root.store.agents, runner=ControlledRunner())

    result = await scheduler.spawn(
        AgentRequest(
            "timeout",
            "Explore",
            "d",
            definition_metadata={"timeout": 0.01},
        )
    )

    assert result.status is AgentStatus.TIMED_OUT


@pytest.mark.asyncio
async def test_waiter_timeout_and_cancellation_do_not_cancel_agent(tmp_path: Path) -> None:
    root, _ = make_harness(tmp_path)
    runner = ControlledRunner()
    scheduler = AgentScheduler(root, root.store.agents, runner=runner)
    record = await scheduler.spawn(AgentRequest("wait", "Explore", "d", background=True))
    await runner.started.wait()

    with pytest.raises(AgentWaitTimeout):
        await scheduler.wait(record.agent_id, timeout=0.01)
    waiter = asyncio.create_task(scheduler.wait(record.agent_id))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert scheduler.status(record.agent_id).status is AgentStatus.RUNNING

    runner.release.set()
    assert (await scheduler.wait(record.agent_id)).status is AgentStatus.COMPLETED


@pytest.mark.asyncio
async def test_cross_session_and_unknown_access_are_rejected(tmp_path: Path) -> None:
    root, session_factory = make_harness(tmp_path, "one")
    runner = ControlledRunner()
    runner.release.set()
    scheduler = AgentScheduler(root, root.store.agents, runner=runner)
    owned = await scheduler.spawn(AgentRequest("owned", "Explore", "d"))

    other_factory = SessionHarnessFactory(
        SessionRuntimeFactory(SQLAlchemyStateStore(session_factory)),
        workspace_root=root.effective_cwd,
    )
    other = AgentScheduler(
        other_factory.create("two"), SQLAlchemyStateStore(session_factory).agents, runner=runner
    )
    with pytest.raises(AgentOwnershipError):
        other.status(owned.agent_id)
    with pytest.raises(AgentOwnershipError):
        await other.wait(owned.agent_id)
    with pytest.raises(AgentNotFound):
        scheduler.status("missing")


@pytest.mark.asyncio
async def test_definition_snapshot_detached_and_shutdown_cleans_maps(tmp_path: Path) -> None:
    root, _ = make_harness(tmp_path)
    runner = ControlledRunner()
    scheduler = AgentScheduler(root, root.store.agents, runner=runner, stop_grace=0.01)
    metadata = {"nested": {"version": 1}}
    record = await scheduler.spawn(
        AgentRequest(
            "snapshot",
            "Explore",
            "d",
            background=True,
            definition_metadata=metadata,
        )
    )
    metadata["nested"]["version"] = 2
    assert scheduler.status(record.agent_id).definition_snapshot["metadata"] == {
        "nested": {"version": 1}
    }

    await scheduler.shutdown()
    assert scheduler.live_agent_ids == frozenset()
    assert scheduler.status(record.agent_id).status is AgentStatus.CANCELLED


@pytest.mark.asyncio
async def test_invalid_type_and_cwd_fail_before_durable_mutation(tmp_path: Path) -> None:
    root, _ = make_harness(tmp_path)
    scheduler = AgentScheduler(root, root.store.agents, runner=ControlledRunner())
    with pytest.raises(ValueError, match="Unknown agent type"):
        await scheduler.spawn(AgentRequest("bad", "missing", "d"))
    with pytest.raises(ValueError, match="outside"):
        await scheduler.spawn(
            AgentRequest("bad", "Explore", "d", cwd=tmp_path.parent / "outside")
        )
    assert scheduler.list() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "foreign", "terminal"])
async def test_invalid_parent_rejected_before_durable_mutation(
    tmp_path: Path, kind: str
) -> None:
    root, _ = make_harness(tmp_path)
    parent_id = f"{kind}-parent"
    if kind == "foreign":
        create_running_parent(root, parent_id, root="other-root")
    elif kind == "terminal":
        parent = create_running_parent(root, parent_id)
        root.store.agents.transition(
            parent.agent_id, AgentStatus.COMPLETED, parent.revision
        )
    scheduler = AgentScheduler(root, root.store.agents, runner=ControlledRunner())
    before = scheduler.list()

    with pytest.raises(AgentOwnershipError):
        await scheduler.spawn(
            AgentRequest(
                "invalid parent",
                "Explore",
                "d",
                background=True,
                parent_agent_id=parent_id,
            )
        )

    assert scheduler.list() == before


@pytest.mark.asyncio
async def test_current_harness_agent_is_allowed_as_parent(tmp_path: Path) -> None:
    root, _ = make_harness(tmp_path)
    parent = root.child("current-parent")
    runner = ControlledRunner()
    runner.release.set()
    scheduler = AgentScheduler(parent, root.store.agents, runner=runner)

    result = await scheduler.spawn(
        AgentRequest(
            "valid parent",
            "Explore",
            "d",
            parent_agent_id=parent.agent_id,
        )
    )

    assert result.parent_agent_id == parent.agent_id


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stop", "shutdown"])
async def test_force_stop_is_bounded_and_quarantines_cancel_suppressing_runner(
    tmp_path: Path, operation: str
) -> None:
    root, _ = make_harness(tmp_path)

    class StubbornRunner:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancel_seen = asyncio.Event()
            self.release = asyncio.Event()

        async def run(self, record, child):
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancel_seen.set()
                await self.release.wait()
            return AgentExecutionResult(content="late", termination_reason="completed")

    runner = StubbornRunner()
    scheduler = AgentScheduler(
        root,
        root.store.agents,
        runner=runner,
        stop_grace=0.01,
        force_grace=0.01,
    )
    record = await scheduler.spawn(
        AgentRequest("stubborn", "Explore", "d", background=True)
    )
    await runner.started.wait()

    async def perform_stop():
        if operation == "stop":
            return await scheduler.stop(record.agent_id)
        report = await scheduler.shutdown()
        assert report.unresolved_quarantines == 1
        return scheduler.status(record.agent_id)

    cancelled = await asyncio.wait_for(perform_stop(), 0.2)

    assert runner.cancel_seen.is_set()
    assert cancelled.status is AgentStatus.CANCELLED
    assert await scheduler.stop(record.agent_id) == cancelled
    assert record.agent_id not in scheduler.live_agent_ids
    assert scheduler.quarantined_task_count == 1
    assert scheduler.is_degraded
    assert scheduler.root_available_capacity == 3
    assert scheduler.managed_concurrency_count == 1
    assert scheduler.parent_limiter_count == 1
    with pytest.raises(AgentSchedulerDegraded):
        await scheduler.spawn(AgentRequest("blocked", "Explore", "d"))
    cancelled_revision = cancelled.revision

    if operation == "shutdown":
        with pytest.raises(AgentSchedulerAlreadyActive):
            AgentScheduler(root, root.store.agents, runner=ControlledRunner())

    runner.release.set()
    await wait_until(lambda: scheduler.quarantined_task_count == 0)
    reaped = scheduler.status(record.agent_id)
    assert reaped.status is AgentStatus.CANCELLED
    assert reaped.revision == cancelled_revision
    assert not scheduler.is_degraded
    assert scheduler.managed_concurrency_count == 0
    if operation == "stop":
        assert (
            await scheduler.spawn(AgentRequest("recovered", "Explore", "d"))
        ).status is AgentStatus.COMPLETED
    else:
        replacement = AgentScheduler(root, root.store.agents, runner=ControlledRunner())
        await replacement.shutdown()


@pytest.mark.asyncio
async def test_custom_definition_is_validated_snapshotted_and_run(tmp_path: Path) -> None:
    root, _ = make_harness(tmp_path)
    runner = ControlledRunner()
    runner.release.set()
    scheduler = AgentScheduler(root, root.store.agents, runner=runner)
    definition = CustomAgentDefinition(
        agent_type="custom-review",
        when_to_use="Review custom input",
        tools=["read_file"],
        disallowed_tools=["bash"],
        model="custom-model",
        skills=["review"],
        get_system_prompt=lambda: "custom system prompt",
    )

    result = await scheduler.spawn(
        AgentRequest(
            "review",
            "custom-review",
            "custom",
            definition=definition,
        )
    )

    snapshot = result.definition_snapshot
    assert result.status is AgentStatus.COMPLETED
    assert snapshot["agent_type"] == "custom-review"
    assert snapshot["system_prompt"] == "custom system prompt"
    assert snapshot["tools"] == ["read_file"]
    assert snapshot["disallowed_tools"] == ["bash"]
    reconstructed = AgentExecutor.from_record(result).agent_definition
    assert isinstance(reconstructed, CustomAgentDefinition)
    assert reconstructed.get_system_prompt() == "custom system prompt"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_queued_definition_uses_detached_snapshot_not_mutated_globals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = make_harness(tmp_path)
    scheduler = AgentScheduler(
        root, root.store.agents, stop_grace=0.01, force_grace=0.01
    )
    original_tools = EXPLORE_AGENT.tools
    assert original_tools is not None
    original_copy = list(original_tools)
    original_when = EXPLORE_AGENT.when_to_use
    original_prompt = EXPLORE_AGENT.get_system_prompt
    record = await scheduler.spawn(
        AgentRequest("queued snapshot", "Explore", "d", background=True)
    )

    class SnapshotLLM:
        def __init__(self) -> None:
            self.requests = []

        async def chat_completion(self, request):
            self.requests.append(request)
            return ChatCompletionResponse(
                id="snapshot", model="test", content="snapshot used", finish_reason="stop"
            )

    llm = SnapshotLLM()
    monkeypatch.setattr("agents.engine.LLMService", lambda: llm)
    try:
        original_tools.append("write_file")
        EXPLORE_AGENT.when_to_use = "mutated after spawn"
        EXPLORE_AGENT.get_system_prompt = lambda: "mutated prompt"

        completed = await scheduler.wait(record.agent_id)

        assert completed.status is AgentStatus.COMPLETED
        request = llm.requests[0]
        schema_names = {item["function"]["name"] for item in request.tools}
        assert "write_file" not in schema_names
        assert request.messages[0].content == record.definition_snapshot["system_prompt"]
    finally:
        original_tools[:] = original_copy
        EXPLORE_AGENT.when_to_use = original_when
        EXPLORE_AGENT.get_system_prompt = original_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        CustomAgentDefinition(agent_type="invalid", when_to_use=""),
        CustomAgentDefinition(
            agent_type="invalid", when_to_use="use", background=1  # type: ignore[arg-type]
        ),
        CustomAgentDefinition(
            agent_type="invalid",
            when_to_use="use",
            max_turns=True,  # type: ignore[arg-type]
        ),
        CustomAgentDefinition(
            agent_type="invalid",
            when_to_use="use",
            hooks=AgentHooks(pre_start="bad"),  # type: ignore[arg-type]
        ),
        CustomAgentDefinition(
            agent_type="invalid",
            when_to_use="use",
            source="userSettings",  # type: ignore[arg-type]
        ),
    ],
)
async def test_invalid_custom_definition_rejected_before_persistence(
    tmp_path: Path, invalid: CustomAgentDefinition
) -> None:
    root, _ = make_harness(tmp_path)
    scheduler = AgentScheduler(root, root.store.agents, runner=ControlledRunner())

    with pytest.raises(AgentDefinitionError):
        await scheduler.spawn(
            AgentRequest(
                "invalid", "invalid", "d", background=True, definition=invalid
            )
        )

    assert scheduler.list() == []


@pytest.mark.asyncio
async def test_parent_limiters_are_refcounted_and_removed(tmp_path: Path) -> None:
    root, _ = make_harness(tmp_path)
    runner = ControlledRunner()
    runner.release.set()
    scheduler = AgentScheduler(
        root,
        root.store.agents,
        runner=runner,
        root_concurrency=4,
        per_parent_concurrency=1,
    )

    for index in range(30):
        create_running_parent(root, f"parent-{index}")
        await scheduler.spawn(
            AgentRequest(
                str(index), "Explore", "d", parent_agent_id=f"parent-{index}"
            )
        )
    assert scheduler.parent_limiter_count == 0

    create_running_parent(root, "shared")
    runner.release.clear()
    siblings = [
        await scheduler.spawn(
            AgentRequest(
                f"sibling-{index}",
                "Explore",
                "d",
                background=True,
                parent_agent_id="shared",
            )
        )
        for index in range(3)
    ]
    await wait_until(lambda: len(runner.calls) == 31)
    assert scheduler.parent_limiter_count == 1
    assert scheduler.parent_limiter_refcounts == {"shared": 3}

    runner.release.set()
    await asyncio.gather(*(scheduler.wait(item.agent_id) for item in siblings))
    assert scheduler.parent_limiter_count == 0
    assert scheduler.parent_limiter_refcounts == {}


class QuarantineCapacityRunner:
    def __init__(self, *, block_second: bool = False) -> None:
        self.first_started = asyncio.Event()
        self.first_cancelled = asyncio.Event()
        self.release_first = asyncio.Event()
        self.second_started = asyncio.Event()
        self.release_second = asyncio.Event()
        self.block_second = block_second

    async def run(self, record, child):
        if record.prompt == "first":
            self.first_started.set()
            try:
                await self.release_first.wait()
            except asyncio.CancelledError:
                self.first_cancelled.set()
                await self.release_first.wait()
        else:
            self.second_started.set()
            if self.block_second:
                await self.release_second.wait()
        return AgentExecutionResult(content=record.prompt)


@pytest.mark.asyncio
async def test_force_quarantine_retains_root_capacity_until_runner_exits(
    tmp_path: Path,
) -> None:
    root, _ = make_harness(tmp_path)
    runner = QuarantineCapacityRunner()
    scheduler = AgentScheduler(
        root,
        root.store.agents,
        runner=runner,
        root_concurrency=1,
        per_parent_concurrency=1,
        stop_grace=0.01,
        force_grace=0.01,
    )
    create_running_parent(root, "one")
    first = await scheduler.spawn(
        AgentRequest("first", "Explore", "d", background=True, parent_agent_id="one")
    )
    await runner.first_started.wait()
    assert (await scheduler.stop(first.agent_id)).status is AgentStatus.CANCELLED
    assert scheduler.root_available_capacity == 0
    assert scheduler.managed_concurrency_count == 1
    with pytest.raises(AgentSchedulerDegraded):
        await scheduler.spawn(AgentRequest("second", "Explore", "d"))

    runner.release_first.set()
    await wait_until(lambda: scheduler.quarantined_task_count == 0)
    second = await scheduler.spawn(
        AgentRequest("second", "Explore", "d", background=True)
    )
    await asyncio.wait_for(runner.second_started.wait(), 0.5)
    completed = await scheduler.wait(second.agent_id, timeout=0.5)
    assert completed.status is AgentStatus.COMPLETED

    assert scheduler.root_available_capacity == 1
    assert scheduler.managed_concurrency_count == 0
    assert scheduler.parent_limiter_count == 0
    assert scheduler.parent_limiter_refcounts == {}


@pytest.mark.asyncio
async def test_force_quarantine_retains_same_parent_capacity_idempotently(
    tmp_path: Path,
) -> None:
    root, _ = make_harness(tmp_path)
    runner = QuarantineCapacityRunner(block_second=True)
    scheduler = AgentScheduler(
        root,
        root.store.agents,
        runner=runner,
        root_concurrency=2,
        per_parent_concurrency=1,
        stop_grace=0.01,
        force_grace=0.01,
    )
    create_running_parent(root, "shared")
    first = await scheduler.spawn(
        AgentRequest(
            "first", "Explore", "d", background=True, parent_agent_id="shared"
        )
    )
    await runner.first_started.wait()
    await scheduler.stop(first.agent_id)
    assert scheduler.parent_limiter_refcounts == {"shared": 1}
    assert scheduler.parent_limiter_count == 1
    assert scheduler.root_available_capacity == 1
    assert scheduler.managed_concurrency_count == 1
    with pytest.raises(AgentSchedulerDegraded):
        await scheduler.spawn(
            AgentRequest(
                "second", "Explore", "d", background=True, parent_agent_id="shared"
            )
        )

    runner.release_first.set()
    await wait_until(lambda: scheduler.quarantined_task_count == 0)

    assert scheduler.root_available_capacity == 2
    assert scheduler.managed_concurrency_count == 0
    assert scheduler.parent_limiter_count == 0
