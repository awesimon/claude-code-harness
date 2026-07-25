from __future__ import annotations

import asyncio
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import harness
from harness import PermissionMode, RuntimeContext, SessionHarness, ToolRuntime
from harness.agents import AgentScheduler
from harness.execution_tasks import ExecutionTaskManager
from models import Base
from state_core import (
    AgentRecord,
    AgentStatus,
    EventType,
    ExecutionTaskRecord,
    ExecutionTaskStatus,
    SessionRuntimeFactory,
    SQLAlchemyStateStore,
    TraceSpanRecord,
    TraceSpanStatus,
    WorktreeRecord,
    WorktreeStatus,
)
from tools.base import Tool, ToolResult


class SlowHarnessTool(Tool[dict, dict]):
    name = "slow_harness"
    description = "slow harness tool"
    input_type = dict

    async def execute(self, input_data: dict) -> ToolResult:
        await asyncio.sleep(0.03)
        return ToolResult.ok({"completed": True})

    def is_read_only(self) -> bool:
        return True


class LocalRegistry:
    def __init__(self, *tools: Tool) -> None:
        self.tools = {tool.name: tool for tool in tools}

    def get(self, name: str):
        return self.tools.get(name)

    def resolve_name(self, name: str):
        return name if name in self.tools else None


class NonCopyableMetadata:
    def __deepcopy__(self, memo):
        raise TypeError("not copyable")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def factory(tmp_path: Path, workspace: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'harness.db'}")
    Base.metadata.create_all(engine)
    runtime_factory = SessionRuntimeFactory(SQLAlchemyStateStore(sessionmaker(bind=engine)))
    return harness.SessionHarnessFactory(runtime_factory, workspace_root=workspace)


def test_root_composes_authoritative_runtime_context_and_metadata(factory, workspace: Path) -> None:
    harness = factory.create("root", metadata={"request_id": "request-1"})

    assert harness.session_id == harness.root_session_id == "root"
    assert harness.store is harness.session_runtime.store
    assert harness.runtime_context.session_id == "root"
    assert harness.effective_cwd == workspace.resolve()
    assert harness.runtime_context.metadata["session_runtime"] is harness.session_runtime
    assert harness.runtime_context.metadata["agent_id"] is None
    assert harness.runtime_context.metadata["session_harness"] is harness
    assert harness.runtime_context.metadata["request_id"] == "request-1"


def test_child_scopes_share_runtime_pipeline_and_store_but_not_context(
    factory,
) -> None:
    root = factory.create("children")
    child = root.child("worker")

    assert child.session_runtime is root.session_runtime
    assert child.tool_runtime is root.tool_runtime
    assert child.store is root.store
    assert child.runtime_context is not root.runtime_context
    assert child.runtime_context.cancellation is not root.runtime_context.cancellation
    assert child.runtime_context.cancellation.parent is root.runtime_context.cancellation
    assert child.agent_id == "worker"
    assert child.parent_agent_id is None


def test_cancellation_flows_downward_without_sibling_or_parent_leaks(
    factory,
) -> None:
    root = factory.create("cancellation")
    left = root.child("left")
    grandchild = left.child("grandchild")
    right = root.child("right")

    left.runtime_context.cancellation.cancel()
    assert left.runtime_context.cancellation.cancelled
    assert grandchild.runtime_context.cancellation.cancelled
    assert not root.runtime_context.cancellation.cancelled
    assert not right.runtime_context.cancellation.cancelled

    root.runtime_context.cancellation.cancel()
    assert right.runtime_context.cancellation.cancelled


def test_child_metadata_merges_and_harness_values_cannot_be_overridden(
    factory,
) -> None:
    root = factory.create("metadata", metadata={"trace": "parent", "shared": {"key": "value"}})
    child = root.child(
        "worker",
        parent_agent_id="supervisor",
        metadata={"trace": "child", "extra": True},
    )

    assert child.parent_agent_id == "supervisor"
    assert child.runtime_context.metadata["shared"] == {"key": "value"}
    assert child.runtime_context.metadata["trace"] == "child"
    assert child.runtime_context.metadata["extra"] is True
    assert child.runtime_context.metadata["agent_id"] == "worker"
    assert child.runtime_context.metadata["session_harness"] is child


def test_child_cwd_enforces_workspace_policy_without_changing_process_cwd(
    factory, workspace: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    original_cwd = Path.cwd()
    root = factory.create("cwd", metadata={"allowed_workspaces": [outside]})

    inside = root.child("inside", cwd=workspace / "nested" / "..")
    allowed = root.child("allowed", cwd=outside)

    assert inside.effective_cwd == workspace.resolve()
    assert allowed.effective_cwd == outside.resolve()
    assert Path.cwd() == original_cwd
    with pytest.raises(harness.HarnessScopeError, match="outside"):
        root.child("rejected", cwd=tmp_path / "escape")
    assert os.getcwd() == str(original_cwd)


def test_effective_cwd_change_rebuilds_skill_resolver_and_keeps_durable_state(
    factory,
    workspace: Path,
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    for root, name, body in (
        (workspace, "base", "Base instructions."),
        (checkout, "checkout", "Checkout instructions."),
    ):
        skill_dir = root / ".claude" / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} skill\n---\n\n{body}",
            encoding="utf-8",
        )
    scoped_factory = harness.SessionHarnessFactory(
        factory.session_runtime_factory,
        workspace_root=workspace,
        allowed_workspaces=[checkout],
    )
    root = scoped_factory.create("skill-cwd")
    original = root.skills
    original.activate("base")
    original.announcement_delta(context_window_tokens=200_000)
    durable_history = original.announcement_history()

    root._set_effective_cwd(checkout)
    rebuilt = root.skills

    assert rebuilt is not original
    assert rebuilt.catalog.cwd == checkout.resolve()
    assert [entry.name for entry in rebuilt.index()] == ["checkout"]
    assert rebuilt.announcement_history() == durable_history
    assert [snapshot.name for snapshot in rebuilt.active_snapshots()] == ["base"]


def test_root_metadata_cannot_expand_child_cwd_policy(factory, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = factory.create("protected", metadata={"_harness_root_workspace": outside})

    with pytest.raises(harness.HarnessScopeError, match="outside"):
        root.child("rejected", cwd=outside)


def test_child_metadata_cannot_escalate_grandchild_workspace(factory, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    child = factory.create("capability").child("child")

    with pytest.raises(harness.HarnessScopeError, match="reserved"):
        child.child("grandchild", metadata={"allowed_workspaces": [outside]})


def test_child_cwd_rejects_symlink_escape(factory, workspace: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    escape = workspace / "escape"
    escape.symlink_to(outside, target_is_directory=True)

    with pytest.raises(harness.HarnessScopeError, match="outside"):
        factory.create("symlink").child("child", cwd=escape)


def test_reserved_metadata_is_rejected_and_user_metadata_isolated(factory) -> None:
    with pytest.raises(harness.HarnessScopeError, match="reserved"):
        factory.create("reserved", metadata={"session_id": "spoof"})
    with pytest.raises(harness.HarnessScopeError, match="reserved"):
        factory.create("reserved-child").child("child", metadata={"agent_id": "spoof"})

    root = factory.create("isolated", metadata={"nested": {"items": []}})
    left = root.child("left")
    right = root.child("right")
    left.runtime_context.metadata["nested"]["items"].append("left")

    assert root.runtime_context.metadata["nested"] == {"items": []}
    assert right.runtime_context.metadata["nested"] == {"items": []}
    with pytest.raises(harness.HarnessScopeError, match="copy"):
        factory.create("non-copyable", metadata={"value": NonCopyableMetadata()})


def test_invalid_factory_calls_do_not_mutate_durable_runtime(factory, workspace: Path) -> None:
    with pytest.raises(harness.HarnessScopeError, match="permission_mode"):
        factory.create("ghost", permission_mode="invalid")
    assert factory.session_runtime_factory.store.states.load_session("ghost") is None

    running = factory.create("running")
    running.session_runtime.update_agent_lifecycle("worker", "running")
    event_count = len(list(running.session_runtime.events()))
    with pytest.raises(harness.HarnessScopeError, match="reserved"):
        factory.resume("running", metadata={"session_id": "spoof"})

    persisted = factory.session_runtime_factory.store.states.load_session("running")
    assert persisted is not None
    assert persisted.agents["worker"]["status"] == "running"
    assert len(list(running.session_runtime.events())) == event_count


@pytest.mark.asyncio
async def test_explicit_child_timeout_none_disables_shared_default(
    tmp_path: Path, workspace: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'timeout.db'}")
    Base.metadata.create_all(engine)
    factory = harness.SessionHarnessFactory(
        SessionRuntimeFactory(SQLAlchemyStateStore(sessionmaker(bind=engine))),
        tool_registry=LocalRegistry(SlowHarnessTool()),
        workspace_root=workspace,
        tool_timeout=0.01,
    )
    root = factory.create("timeout")
    disabled = root.child("disabled", tool_timeout=None)

    timed_out = await root.tool_runtime.execute("slow_harness", {}, root.runtime_context)
    completed = await disabled.tool_runtime.execute("slow_harness", {}, disabled.runtime_context)

    assert timed_out.termination_reason.value == "timed_out"
    assert completed.result.success


def test_direct_harness_rejects_untrusted_identity_and_workspace_capabilities(
    factory, workspace: Path, tmp_path: Path
) -> None:
    root = factory.create("identity")
    context = RuntimeContext(session_id="other", workspace_root=workspace)

    with pytest.raises(harness.HarnessScopeError, match="session_id"):
        SessionHarness(root.session_runtime, ToolRuntime(root.tool_runtime.registry), context)

    outside = tmp_path / "outside"
    outside.mkdir()
    context = RuntimeContext(session_id="identity", workspace_root=workspace)
    with pytest.raises(harness.HarnessScopeError, match="factory"):
        SessionHarness(
            root.session_runtime,
            ToolRuntime(root.tool_runtime.registry),
            context,
            allowed_workspaces=(outside,),
        )


def test_harness_and_context_capabilities_are_immutable_after_construction(
    factory, workspace: Path, tmp_path: Path
) -> None:
    root = factory.create("immutable", metadata={"nested": {"items": []}})
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(FrozenInstanceError):
        root.allowed_workspaces = (outside,)
    with pytest.raises(FrozenInstanceError):
        root.agent_id = "spoof"
    with pytest.raises(FrozenInstanceError):
        root.runtime_context.session_id = "spoof"
    with pytest.raises(FrozenInstanceError):
        root.runtime_context.workspace_root = outside
    with pytest.raises(TypeError):
        root.runtime_context.metadata["agent_id"] = "spoof"
    with pytest.raises(TypeError):
        del root.runtime_context.metadata["session_runtime"]
    with pytest.raises(harness.HarnessScopeError):
        root.child("child", cwd=outside)

    child = root.child("child")
    child.runtime_context.metadata["nested"]["items"].append("child")
    assert root.runtime_context.metadata["nested"] == {"items": []}


@pytest.mark.asyncio
async def test_immutable_metadata_cannot_redirect_todo_to_another_session(factory) -> None:
    first = factory.create("todo-first")
    second = factory.create("todo-second")
    first.session_runtime.enable_todo_v1()
    second.session_runtime.enable_todo_v1()

    with pytest.raises(TypeError):
        first.runtime_context.metadata["session_runtime"] = second.session_runtime

    result = await first.tool_runtime.execute(
        "TodoWrite",
        {"todos": [{"content": "first", "status": "pending", "activeForm": "First"}]},
        first.runtime_context,
    )

    assert result.result.success
    assert first.session_runtime.state.todos["todo-first"][0]["content"] == "first"
    assert second.session_runtime.state.todos == {}


@pytest.mark.asyncio
async def test_root_and_child_todo_scopes_remain_isolated(factory) -> None:
    root = factory.create("todos")
    root.session_runtime.enable_todo_v1()
    left = root.child("left")
    right = root.child("right")

    def todo(text: str) -> list[dict[str, str]]:
        return [{"content": text, "status": "pending", "activeForm": text}]

    root_result = await root.tool_runtime.execute(
        "TodoWrite", {"todos": todo("root")}, root.runtime_context
    )
    left_result = await left.tool_runtime.execute(
        "TodoWrite", {"todos": todo("left")}, left.runtime_context
    )
    right_result = await right.tool_runtime.execute(
        "TodoWrite", {"todos": todo("right")}, right.runtime_context
    )

    assert root_result.result.success and left_result.result.success and right_result.result.success
    assert root.session_runtime.state.todos == {
        "todos": todo("root"),
        "left": todo("left"),
        "right": todo("right"),
    }


def test_resume_is_fresh_and_observes_durable_checkpoint_without_replaying_work(
    factory,
) -> None:
    first = factory.create("durable")
    first.session_runtime.append_event(EventType.USER_MESSAGE, {"content": "persisted"})
    first.session_runtime.checkpoint()

    resumed = factory.resume("durable")
    resumed_again = factory.resume("durable")

    assert resumed is not first
    assert resumed_again is not resumed
    assert resumed.store is first.store
    assert resumed.session_runtime.state.transcript_cursor == 1
    assert [event.event_type for event in resumed.session_runtime.events()] == [
        EventType.USER_MESSAGE
    ]


def test_resume_reconciles_running_agents_through_runtime_recovery(
    factory,
) -> None:
    first = factory.create("interrupted")
    first.session_runtime.update_agent_lifecycle("worker", "running")

    resumed = factory.resume("interrupted")

    assert resumed.session_runtime.state.agents["worker"]["status"] == "interrupted"
    assert [event.event_type for event in resumed.session_runtime.events()] == [
        EventType.AGENT_LIFECYCLE,
        EventType.EXECUTION_INTERRUPTED,
    ]


@pytest.mark.asyncio
async def test_resume_reuses_scheduler_and_reconciles_durable_runtime_agents(
    factory, workspace: Path
) -> None:
    created = factory.create("durable-agents")
    repository = created.store.agents
    pending = repository.create(
        AgentRecord(
            "pending-agent",
            created.root_session_id,
            "Explore",
            "pending",
            "pending",
            True,
            str(workspace),
            {},
        )
    )
    running = repository.create(
        AgentRecord(
            "running-agent",
            created.root_session_id,
            "Explore",
            "running",
            "running",
            True,
            str(workspace),
            {},
        )
    )
    repository.transition(running.agent_id, AgentStatus.RUNNING, running.revision)

    resumed = factory.resume("durable-agents")
    owner = AgentScheduler.for_harness(resumed)
    resumed_again = factory.resume("durable-agents")

    assert repository.get(pending.agent_id).status is AgentStatus.INTERRUPTED  # type: ignore[union-attr]
    assert repository.get(running.agent_id).status is AgentStatus.INTERRUPTED  # type: ignore[union-attr]
    assert AgentScheduler.for_harness(resumed_again) is owner
    await owner.shutdown()


@pytest.mark.asyncio
async def test_factory_cold_resume_interrupts_shell_tasks_and_preserves_output(
    factory, workspace: Path
) -> None:
    created = factory.create("cold-shells")
    repository = created.store.execution_tasks
    pending = repository.create(
        ExecutionTaskRecord(
            task_id="shell-cold-pending",
            root_session_id=created.root_session_id,
            agent_id="main",
            kind="shell",
            command="never-started",
            description="pending",
            canonical_cwd=str(workspace),
            output_artifact_id="output-cold-pending",
            timeout_ms=1000,
            process_owner_token="dead-pending-owner",
        )
    )
    running = repository.create(
        ExecutionTaskRecord(
            task_id="shell-cold-running",
            root_session_id=created.root_session_id,
            agent_id="main",
            kind="shell",
            command="old-process",
            description="running",
            canonical_cwd=str(workspace),
            output_artifact_id="output-cold-running",
            timeout_ms=1000,
            process_owner_token="dead-running-owner",
        )
    )
    running = repository.transition(
        running.task_id,
        ExecutionTaskStatus.RUNNING,
        running.revision,
        process_owner_token=running.process_owner_token,
    )
    repository.append_output(running.task_id, b"partial-output", running.revision)

    resumed = factory.resume("cold-shells")
    manager = ExecutionTaskManager.for_harness(resumed)
    output = await manager.read(running.task_id)

    assert repository.get(pending.task_id).status is ExecutionTaskStatus.INTERRUPTED
    assert repository.get(running.task_id).status is ExecutionTaskStatus.INTERRUPTED
    assert output.data == b"partial-output"


@pytest.mark.asyncio
async def test_factory_observer_is_read_only_and_resume_preserves_live_shell_owner(
    factory, workspace: Path
) -> None:
    observed = factory.create("observed-shell")
    pending = observed.store.execution_tasks.create(
        ExecutionTaskRecord(
            task_id="shell-observed-pending",
            root_session_id=observed.root_session_id,
            agent_id="main",
            kind="shell",
            command="never-started",
            description="pending",
            canonical_cwd=str(workspace),
            output_artifact_id="output-observed-pending",
            timeout_ms=1000,
            process_owner_token="other-owner",
        )
    )

    factory.resume_observer("observed-shell")

    assert observed.store.execution_tasks.get(pending.task_id).status is ExecutionTaskStatus.PENDING

    live_harness = factory.create("live-shell")
    owner = ExecutionTaskManager.for_harness(live_harness)
    live = await owner.launch("sleep 30", description="live", timeout=60)
    try:
        factory.resume("live-shell")
        assert (
            live_harness.store.execution_tasks.get(live.task_id).status
            is ExecutionTaskStatus.RUNNING
        )
    finally:
        await owner.stop(live.task_id)


def test_factory_observer_does_not_mutate_any_durable_runtime_state(
    factory, workspace: Path
) -> None:
    created = factory.create("strict-observer")
    created.session_runtime.update_agent_lifecycle("state-worker", "running")
    runtime_agent = created.store.agents.create(
        AgentRecord(
            "runtime-worker",
            created.root_session_id,
            "Explore",
            "observe",
            "running",
            True,
            str(workspace),
            {},
        )
    )
    runtime_agent = created.store.agents.transition(
        runtime_agent.agent_id,
        AgentStatus.RUNNING,
        runtime_agent.revision,
    )
    shell = created.store.execution_tasks.create(
        ExecutionTaskRecord(
            task_id="shell-observer",
            root_session_id=created.root_session_id,
            agent_id=runtime_agent.agent_id,
            kind="shell",
            command="sleep 30",
            description="observe",
            canonical_cwd=str(workspace),
            output_artifact_id="output-observer",
            timeout_ms=30_000,
            process_owner_token="live-observer-owner",
        )
    )
    shell = created.store.execution_tasks.transition(
        shell.task_id,
        ExecutionTaskStatus.RUNNING,
        shell.revision,
        process_owner_token=shell.process_owner_token,
    )
    trace = created.store.traces.start(
        TraceSpanRecord(
            span_id="trace-observer",
            root_session_id=created.root_session_id,
            agent_id=runtime_agent.agent_id,
            kind="agent",
            name="observe",
        )
    )
    worktree = created.store.worktrees.create(
        WorktreeRecord(
            worktree_id="worktree-observer",
            root_session_id=created.root_session_id,
            agent_id=runtime_agent.agent_id,
            repository_root=str(workspace),
            canonical_path=str(workspace / "observer-worktree"),
            branch="observer",
            base_commit="deadbeef",
            status=WorktreeStatus.CREATING,
        )
    )
    state_before = created.store.states.load_session(created.session_id)
    events_before = created.store.states.list_events(created.session_id)

    observed = factory.resume_observer(created.session_id)

    assert observed.session_runtime.state == state_before
    assert created.store.states.load_session(created.session_id) == state_before
    assert created.store.states.list_events(created.session_id) == events_before
    assert created.store.agents.get(runtime_agent.agent_id) == runtime_agent
    assert created.store.execution_tasks.get(shell.task_id) == shell
    assert created.store.traces.get(trace.span_id) == trace
    assert created.store.worktrees.get(worktree.worktree_id) == worktree
    assert observed.session_runtime.state.agents["state-worker"]["status"] == "running"
    assert runtime_agent.status is AgentStatus.RUNNING
    assert shell.status is ExecutionTaskStatus.RUNNING
    assert trace.status is TraceSpanStatus.RUNNING
    assert worktree.status is WorktreeStatus.CREATING


def test_factory_inherits_and_allows_scope_overrides(tmp_path: Path, workspace: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'overrides.db'}")
    Base.metadata.create_all(engine)

    def callback(request) -> bool:
        return True

    factory = harness.SessionHarnessFactory(
        SessionRuntimeFactory(SQLAlchemyStateStore(sessionmaker(bind=engine))),
        workspace_root=workspace,
        permission_mode=PermissionMode.AUTO,
        approval_callback=callback,
        tool_timeout=12.0,
    )

    root = factory.create("overrides")
    child = root.child(
        "worker",
        permission_mode=PermissionMode.BYPASS,
        approval_callback=None,
        tool_timeout=3.0,
    )

    assert root.runtime_context.permission_mode is PermissionMode.AUTO
    assert root.runtime_context.approval_callback is callback
    assert root.runtime_context.tool_timeout == 12.0
    assert child.runtime_context.permission_mode is PermissionMode.BYPASS
    assert child.runtime_context.approval_callback is None
    assert child.runtime_context.tool_timeout == 3.0


def test_factory_without_default_workspace_uses_per_call_workspace(
    tmp_path: Path, workspace: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'per-call-workspace.db'}")
    Base.metadata.create_all(engine)
    factory = harness.SessionHarnessFactory(
        SessionRuntimeFactory(SQLAlchemyStateStore(sessionmaker(bind=engine)))
    )

    created = factory.create("per-call", workspace_root=workspace)
    resumed = factory.resume("per-call", workspace_root=workspace)

    assert created.effective_cwd == workspace.resolve()
    assert resumed.effective_cwd == workspace.resolve()
    assert resumed.store is created.store


@pytest.mark.parametrize("method_name", ["create", "resume"])
def test_factory_without_any_workspace_raises_harness_scope_error(
    tmp_path: Path, method_name: str
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'missing-workspace.db'}")
    Base.metadata.create_all(engine)
    factory = harness.SessionHarnessFactory(
        SessionRuntimeFactory(SQLAlchemyStateStore(sessionmaker(bind=engine)))
    )

    with pytest.raises(harness.HarnessScopeError, match="workspace_root is required"):
        getattr(factory, method_name)("missing-workspace")
