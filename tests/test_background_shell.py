from __future__ import annotations

import asyncio
import os
import shlex
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from harness import CancellationToken, PermissionMode, SessionHarnessFactory
from harness.execution_tasks import (
    ExecutionTaskLaunchError,
    ExecutionTaskManager,
    ExecutionTaskNotRunning,
)
from models import Base
from state_core import (
    ExecutionTaskRecord,
    ExecutionTaskStatus,
    SessionRuntimeFactory,
    SQLAlchemyStateStore,
)
from state_core.runtime_primitives import MAX_EXECUTION_OUTPUT_BYTES
from tools.bash_tool import BashTool


@pytest.fixture
def store(tmp_path: Path) -> SQLAlchemyStateStore:
    engine = create_engine(f"sqlite:///{tmp_path / 'background-shell.db'}")
    Base.metadata.create_all(engine)
    return SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timeout",
    [True, False, None, "1", 0, -1, 601, float("nan"), float("inf"), float("-inf")],
)
async def test_bash_rejects_invalid_timeout_values(timeout, tmp_path: Path) -> None:
    result = await BashTool().run(
        {"command": "printf invalid", "timeout": timeout},
        {"effective_cwd": str(tmp_path)},
    )

    assert not result.success
    assert "timeout must be" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("run_in_background", [0, 1, None, "false", []])
async def test_bash_rejects_non_boolean_background_values(
    run_in_background, tmp_path: Path
) -> None:
    result = await BashTool().run(
        {"command": "printf invalid", "run_in_background": run_in_background},
        {"effective_cwd": str(tmp_path)},
    )

    assert not result.success
    assert "run_in_background must be a boolean" in result.message


def test_bash_timeout_schema_matches_runtime_bounds() -> None:
    timeout = BashTool().get_schema()["parameters"]["properties"]["timeout"]

    assert timeout["exclusiveMinimum"] == 0
    assert timeout["maximum"] == 600


@pytest.mark.asyncio
async def test_launch_persists_pending_before_starting_a_process_group(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    seen: dict[str, object] = {}

    async def spawn(command: str, **kwargs):
        pending = store.execution_tasks.list("root-1")
        assert len(pending) == 1
        assert pending[0].status is ExecutionTaskStatus.PENDING
        seen.update(kwargs)
        return await asyncio.create_subprocess_shell(command, **kwargs)

    manager = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
        process_factory=spawn,
    )

    task = await manager.launch("printf ready", description="print output", timeout=5)

    assert task.status is ExecutionTaskStatus.RUNNING
    assert seen["start_new_session"] is True
    completed = await manager.wait(task.task_id, timeout=2)
    assert completed.status is ExecutionTaskStatus.COMPLETED
    assert manager._owned == {}


@pytest.mark.asyncio
async def test_output_is_readable_while_running_with_byte_cursors(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    manager = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
    )
    program = (
        "import sys,time;"
        "sys.stdout.write('alpha');sys.stdout.flush();"
        "time.sleep(.3);"
        "sys.stderr.write('β');sys.stderr.flush()"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"

    task = await manager.launch(command, description="stream output", timeout=5)
    first = await manager.read(task.task_id, cursor=0, max_bytes=5, block=True, timeout=1)

    assert first.data == b"alpha"
    assert first.next_cursor == 5
    assert first.record.status is ExecutionTaskStatus.RUNNING

    second = await manager.read(
        task.task_id, cursor=first.next_cursor, max_bytes=8, block=True, timeout=1
    )
    assert second.data == "β".encode()
    assert second.next_cursor == len("alphaβ".encode())
    assert (await manager.wait(task.task_id, timeout=2)).status is ExecutionTaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_timeout_and_stop_publish_one_terminal_state(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    manager = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
    )
    timed = await manager.launch("sleep 10", description="timeout", timeout=0.05)
    timed_result = await manager.wait(timed.task_id, timeout=2)
    assert timed_result.status is ExecutionTaskStatus.TIMED_OUT
    assert timed_result.termination_reason == "timed_out"

    stopped = await manager.launch("sleep 10", description="stop", timeout=5)
    stopped_result = await manager.stop(stopped.task_id)
    assert stopped_result.status is ExecutionTaskStatus.KILLED
    assert stopped_result.termination_reason == "stopped"
    await asyncio.sleep(0.05)
    assert store.execution_tasks.get(stopped.task_id) == stopped_result


@pytest.mark.asyncio
async def test_timeout_kills_child_after_shell_exits_with_stdout_open(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    if os.name != "posix":
        pytest.skip("process group assertion is POSIX-specific")
    manager = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
    )
    pid_file = tmp_path / "orphan-child.pid"
    command = f"sleep 2 & echo $! > {shlex.quote(str(pid_file))}"
    task = await manager.launch(command, description="stdout holder", timeout=0.05)
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    child_pid = int(pid_file.read_text().strip())

    try:
        result = await manager.wait(task.task_id, timeout=1)

        assert result.status is ExecutionTaskStatus.TIMED_OUT
        assert result.termination_reason == "timed_out"
        for _ in range(100):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("stdout-holding child survived task timeout")
        assert manager._live == {}
        assert manager._owned == {}
    finally:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.asyncio
async def test_timeout_closes_output_when_setsid_child_escapes_process_group(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    if os.name != "posix":
        pytest.skip("setsid assertion is POSIX-specific")
    manager = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
    )
    pid_file = tmp_path / "setsid-child.pid"
    program = (
        "import os,time;"
        "os.setsid();"
        f"open({str(pid_file)!r},'w').write(str(os.getpid()));"
        "time.sleep(2)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(program)} &"
    task = await manager.launch(command, description="escaped stdout holder", timeout=0.05)
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    child_pid = int(pid_file.read_text().strip())

    try:
        result = await manager.wait(task.task_id, timeout=0.5)

        assert result.status is ExecutionTaskStatus.TIMED_OUT
        assert manager._live == {}
        assert manager._owned == {}
    finally:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.asyncio
async def test_stop_terminates_the_complete_process_group(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    if os.name != "posix":
        pytest.skip("process group assertion is POSIX-specific")
    manager = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
    )
    pid_file = tmp_path / "child.pid"
    command = f"sleep 30 & echo $! > {shlex.quote(str(pid_file))}; wait"
    task = await manager.launch(command, description="process tree", timeout=10)
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    child_pid = int(pid_file.read_text().strip())

    result = await manager.stop(task.task_id)

    assert result.status is ExecutionTaskStatus.KILLED
    for _ in range(100):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("background child process survived TaskStop")


@pytest.mark.asyncio
async def test_spawn_failure_is_durable_and_read_supports_timeout_and_tail(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    async def fail_spawn(command: str, **kwargs):
        raise OSError("cannot spawn")

    failing = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
        process_factory=fail_spawn,
    )
    with pytest.raises(ExecutionTaskLaunchError, match="cannot spawn"):
        await failing.launch("anything", description="failure", timeout=1)
    failed = store.execution_tasks.list("root-1")[0]
    assert failed.status is ExecutionTaskStatus.FAILED
    assert failed.termination_reason == "spawn_failed"
    assert failing._owned == {}

    manager = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-2",
        agent_id="main",
        cwd=tmp_path,
    )
    quiet = await manager.launch("sleep .15", description="quiet", timeout=2)
    assert (await manager.read(quiet.task_id, block=False)).retrieval_status == "not_ready"
    assert (await manager.read(quiet.task_id, block=True, timeout=0)).retrieval_status == "timeout"
    await manager.wait(quiet.task_id, timeout=2)

    noisy = await manager.launch("printf 0123456789", description="tail", timeout=2)
    await manager.wait(noisy.task_id, timeout=2)
    tail = await manager.read(noisy.task_id, max_bytes=4, tail=True)
    assert tail.data == b"6789"
    assert tail.next_cursor == 10


@pytest.mark.asyncio
async def test_launch_persists_only_supported_environment_snapshot_keys(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    seen_environment: dict[str, str] = {}

    async def spawn(command: str, **kwargs):
        seen_environment.update(kwargs["env"])
        return await asyncio.create_subprocess_shell(command, **kwargs)

    manager = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
        process_factory=spawn,
    )
    environment = {"LANG": "C", "LOGNAME": "operator", "SECRET": "not-durable"}

    task = await manager.launch(
        "printf ready",
        description="environment snapshot",
        timeout=5,
        environment=environment,
    )

    assert task.safe_environment == {"LANG": "C"}
    assert seen_environment == environment
    assert (await manager.wait(task.task_id, timeout=2)).status is ExecutionTaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_output_limit_terminates_process_group_and_publishes_failed(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    processes: list[asyncio.subprocess.Process] = []

    async def spawn(command: str, **kwargs):
        process = await asyncio.create_subprocess_shell(command, **kwargs)
        processes.append(process)
        return process

    manager = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
        process_factory=spawn,
    )
    program = (
        f"import sys,time;sys.stdout.buffer.write(b'x'*{MAX_EXECUTION_OUTPUT_BYTES + 1});"
        "sys.stdout.flush();time.sleep(30)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"

    task = await manager.launch(command, description="bounded output", timeout=10)
    result = await manager.wait(task.task_id, timeout=2)

    assert result.status is ExecutionTaskStatus.FAILED
    assert result.termination_reason == "output_limit"
    assert result.output_byte_count <= MAX_EXECUTION_OUTPUT_BYTES
    assert processes[0].returncode is not None


@pytest.mark.asyncio
async def test_detached_watcher_recovers_from_terminal_repository_failure(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    class FailFirstTerminalTransition:
        def __init__(self, repository):
            self.repository = repository
            self.failed = False

        def __getattr__(self, name: str):
            return getattr(self.repository, name)

        def transition(self, task_id, status, expected_revision, **changes):
            if (
                status
                in {
                    ExecutionTaskStatus.COMPLETED,
                    ExecutionTaskStatus.FAILED,
                    ExecutionTaskStatus.KILLED,
                    ExecutionTaskStatus.TIMED_OUT,
                    ExecutionTaskStatus.INTERRUPTED,
                }
                and not self.failed
            ):
                self.failed = True
                raise RuntimeError("temporary terminal write failure")
            return self.repository.transition(task_id, status, expected_revision, **changes)

    repository = FailFirstTerminalTransition(store.execution_tasks)
    manager = ExecutionTaskManager(
        repository,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
    )
    task = await manager.launch("printf done", description="detached", timeout=2)

    for _ in range(100):
        record = store.execution_tasks.get(task.task_id)
        if record is not None and record.status in {
            ExecutionTaskStatus.FAILED,
            ExecutionTaskStatus.COMPLETED,
        }:
            break
        await asyncio.sleep(0.01)

    try:
        assert record is not None
        assert record.status is ExecutionTaskStatus.FAILED
        assert record.termination_reason == "watcher_failed"
        assert manager._live == {}
        assert manager._owned == {}
    finally:
        live = manager._live.pop(task.task_id, None)
        manager._owned.pop(task.task_id, None)
        if live is not None and live.completion is not None:
            await asyncio.gather(live.completion, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancellation_before_running_result_kills_the_spawned_process(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    token = CancellationToken()
    spawned = asyncio.Event()
    release = asyncio.Event()
    process_holder: list[asyncio.subprocess.Process] = []

    async def delayed_spawn(command: str, **kwargs):
        process = await asyncio.create_subprocess_shell(command, **kwargs)
        process_holder.append(process)
        spawned.set()
        await release.wait()
        return process

    manager = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
        process_factory=delayed_spawn,
    )
    launching = asyncio.create_task(
        manager.launch(
            "sleep 30",
            description="cancel before publish",
            timeout=10,
            cancellation=token,
        )
    )
    await asyncio.wait_for(spawned.wait(), timeout=1)
    token.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(launching, timeout=2)
    assert process_holder[0].returncode is not None
    record = store.execution_tasks.list("root-1")[0]
    assert record.status is ExecutionTaskStatus.KILLED
    assert record.termination_reason == "cancelled_before_start"
    assert manager._owned == {}


@pytest.mark.asyncio
async def test_natural_exit_and_stop_race_has_one_consumed_terminal_result(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    manager = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
    )
    unhandled: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        for _ in range(20):
            task = await manager.launch("true", description="race", timeout=2)
            stop_result = await asyncio.gather(manager.stop(task.task_id), return_exceptions=True)
            final = await manager.wait(task.task_id, timeout=2)

            assert final.status in {
                ExecutionTaskStatus.COMPLETED,
                ExecutionTaskStatus.KILLED,
            }
            assert isinstance(stop_result[0], (ExecutionTaskRecord, ExecutionTaskNotRunning))
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert unhandled == []


def test_reconcile_interrupts_durable_running_tasks_without_process_resurrection(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    pending = store.execution_tasks.create(
        ExecutionTaskRecord(
            task_id="shell-orphan",
            root_session_id="root-1",
            agent_id="main",
            kind="shell",
            command="mutating-command",
            description="orphan",
            canonical_cwd=str(tmp_path),
            output_artifact_id="output-orphan",
            timeout_ms=1000,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    store.execution_tasks.transition(
        pending.task_id,
        ExecutionTaskStatus.RUNNING,
        pending.revision,
        process_owner_token="dead-runtime",
    )
    restarted = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
    )

    reconciled = restarted.reconcile()

    assert [item.task_id for item in reconciled] == [pending.task_id]
    assert reconciled[0].status is ExecutionTaskStatus.INTERRUPTED
    assert reconciled[0].termination_reason == "runtime owner unavailable"


@pytest.mark.asyncio
async def test_reconcile_interrupts_pending_orphan_and_preserves_live_owner(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    manager = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
    )
    live = await manager.launch("sleep 10", description="live", timeout=20)
    pending = store.execution_tasks.create(
        ExecutionTaskRecord(
            task_id="shell-pending-orphan",
            root_session_id="root-1",
            agent_id="main",
            kind="shell",
            command="never-started",
            description="pending orphan",
            canonical_cwd=str(tmp_path),
            output_artifact_id="output-pending-orphan",
            timeout_ms=1000,
        )
    )

    try:
        reconciled = manager.reconcile()

        assert [item.task_id for item in reconciled] == [pending.task_id]
        assert reconciled[0].status is ExecutionTaskStatus.INTERRUPTED
        assert store.execution_tasks.get(live.task_id).status is ExecutionTaskStatus.RUNNING
    finally:
        await manager.stop(live.task_id)


@pytest.mark.asyncio
async def test_reconcile_preserves_owned_pending_task_during_spawn(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    spawned = asyncio.Event()
    release = asyncio.Event()

    async def delayed_spawn(command: str, **kwargs):
        process = await asyncio.create_subprocess_shell(command, **kwargs)
        spawned.set()
        await release.wait()
        return process

    manager = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
        process_factory=delayed_spawn,
    )
    launching = asyncio.create_task(
        manager.launch("sleep 10", description="pending live", timeout=20)
    )
    await asyncio.wait_for(spawned.wait(), timeout=1)

    try:
        pending = store.execution_tasks.list("root-1")[0]
        assert pending.status is ExecutionTaskStatus.PENDING
        assert pending.process_owner_token is not None
        assert manager.reconcile() == []
        assert store.execution_tasks.get(pending.task_id).status is ExecutionTaskStatus.PENDING
    finally:
        release.set()

    running = await launching
    await manager.stop(running.task_id)


@pytest.mark.asyncio
async def test_resume_through_second_sqlite_adapter_preserves_live_process(
    tmp_path: Path,
) -> None:
    database = tmp_path / "shared-live-shell.db"
    first_engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(first_engine)
    first_store = SQLAlchemyStateStore(sessionmaker(bind=first_engine, expire_on_commit=False))
    first_factory = SessionHarnessFactory(
        SessionRuntimeFactory(first_store),
        workspace_root=tmp_path,
    )
    owner = ExecutionTaskManager.for_harness(first_factory.create("shared-root"))
    task = await owner.launch("sleep 30", description="live owner", timeout=60)

    second_engine = create_engine(f"sqlite:///{database}")
    second_store = SQLAlchemyStateStore(sessionmaker(bind=second_engine, expire_on_commit=False))
    second_factory = SessionHarnessFactory(
        SessionRuntimeFactory(second_store),
        workspace_root=tmp_path,
    )
    live_process = owner._live[task.task_id].process
    try:
        second_factory.resume("shared-root")

        assert second_store.execution_tasks.get(task.task_id).status is ExecutionTaskStatus.RUNNING
        assert live_process.returncode is None
    finally:
        if live_process.returncode is None:
            await owner._terminate_process_group(live_process)
        live = owner._live.get(task.task_id)
        if live is not None and live.completion is not None:
            await asyncio.gather(live.completion, return_exceptions=True)
        first_engine.dispose()
        second_engine.dispose()


def test_resume_through_second_sqlite_adapter_interrupts_cold_task(
    tmp_path: Path,
) -> None:
    database = tmp_path / "shared-cold-shell.db"
    first_engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(first_engine)
    first_store = SQLAlchemyStateStore(sessionmaker(bind=first_engine, expire_on_commit=False))
    first_factory = SessionHarnessFactory(
        SessionRuntimeFactory(first_store),
        workspace_root=tmp_path,
    )
    created = first_factory.create("cold-root")
    task = first_store.execution_tasks.create(
        ExecutionTaskRecord(
            task_id="shell-cold-adapter",
            root_session_id=created.root_session_id,
            agent_id="main",
            kind="shell",
            command="stale",
            description="cold owner",
            canonical_cwd=str(tmp_path),
            output_artifact_id="output-cold-adapter",
            timeout_ms=30_000,
            process_owner_token="unregistered-owner",
        )
    )
    task = first_store.execution_tasks.transition(
        task.task_id,
        ExecutionTaskStatus.RUNNING,
        task.revision,
        process_owner_token=task.process_owner_token,
    )

    second_engine = create_engine(f"sqlite:///{database}")
    second_store = SQLAlchemyStateStore(sessionmaker(bind=second_engine, expire_on_commit=False))
    second_factory = SessionHarnessFactory(
        SessionRuntimeFactory(second_store),
        workspace_root=tmp_path,
    )
    try:
        second_factory.resume("cold-root")

        reconciled = second_store.execution_tasks.get(task.task_id)
        assert reconciled is not None
        assert reconciled.status is ExecutionTaskStatus.INTERRUPTED
        assert reconciled.termination_reason == "runtime owner unavailable"
    finally:
        first_engine.dispose()
        second_engine.dispose()


@pytest.mark.asyncio
async def test_running_transition_loser_terminates_spawned_process_and_drops_owner(
    store: SQLAlchemyStateStore, tmp_path: Path
) -> None:
    processes: list[asyncio.subprocess.Process] = []

    async def spawn_then_interrupt(command: str, **kwargs):
        process = await asyncio.create_subprocess_shell(command, **kwargs)
        processes.append(process)
        store.execution_tasks.interrupt_open(
            "root-1", live_owner_tokens=set(), now=datetime.now(timezone.utc)
        )
        return process

    manager = ExecutionTaskManager(
        store.execution_tasks,
        root_session_id="root-1",
        agent_id="main",
        cwd=tmp_path,
        process_factory=spawn_then_interrupt,
    )

    with pytest.raises(ExecutionTaskLaunchError, match="lost ownership"):
        await manager.launch("sleep 30", description="transition loser", timeout=20)

    assert processes[0].returncode is not None
    record = store.execution_tasks.list("root-1")[0]
    assert record.status is ExecutionTaskStatus.INTERRUPTED
    assert manager._owned == {}


@pytest.mark.asyncio
async def test_bash_background_contract_and_foreground_compatibility(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'bash-tool.db'}")
    Base.metadata.create_all(engine)
    store = SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))
    harness = SessionHarnessFactory(
        SessionRuntimeFactory(store),
        workspace_root=tmp_path,
        permission_mode=PermissionMode.BYPASS,
    ).create("root-1")
    tool = BashTool()

    schema = tool.get_schema()["parameters"]["properties"]
    assert schema["run_in_background"]["type"] == "boolean"
    execution = await harness.tool_runtime.execute(
        "Bash",
        {
            "command": "printf background",
            "description": "background output",
            "run_in_background": True,
        },
        harness.runtime_context,
    )

    assert execution.result.success
    assert execution.result.data["status"] == "running"
    task_id = execution.result.data["task_id"]
    assert execution.result.data["background_task_id"] == task_id
    manager = ExecutionTaskManager.for_harness(harness)
    assert (await manager.wait(task_id, timeout=2)).status is ExecutionTaskStatus.COMPLETED
    assert (await manager.read(task_id)).data == b"background"

    foreground = await tool.run(
        {"command": "printf foreground"},
        {
            "session_harness": harness,
            "effective_cwd": str(tmp_path),
        },
    )
    assert foreground.success
    assert foreground.data["stdout"] == "foreground"
    assert foreground.data["return_code"] == 0


@pytest.mark.asyncio
async def test_bash_background_forwards_active_cancellation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = CancellationToken()
    received: dict[str, object] = {}

    class Manager:
        async def launch(self, command: str, **kwargs):
            received.update(kwargs)
            return type(
                "TaskRecord",
                (),
                {"task_id": "shell-1", "status": ExecutionTaskStatus.RUNNING},
            )()

    manager = Manager()
    monkeypatch.setattr(
        ExecutionTaskManager,
        "for_harness",
        classmethod(lambda cls, harness: manager),
    )

    result = await BashTool().run(
        {"command": "printf background", "run_in_background": True},
        {
            "session_harness": object(),
            "effective_cwd": str(tmp_path),
            "cancellation": token,
        },
    )

    assert result.success
    assert received["cancellation"] is token


@pytest.mark.asyncio
async def test_foreground_timeout_terminates_complete_process_group(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("process group assertion is POSIX-specific")
    pid_file = tmp_path / "foreground-timeout-child.pid"
    command = f"sleep 30 & echo $! > {shlex.quote(str(pid_file))}; wait"

    result = await BashTool().run(
        {"command": command, "timeout": 0.05},
        {"effective_cwd": str(tmp_path)},
    )
    child_pid = int(pid_file.read_text().strip())

    try:
        assert not result.success
        for _ in range(100):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("foreground child survived Bash timeout")
    finally:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.asyncio
async def test_foreground_cancellation_terminates_complete_process_group(
    tmp_path: Path,
) -> None:
    if os.name != "posix":
        pytest.skip("process group assertion is POSIX-specific")
    pid_file = tmp_path / "foreground-cancel-child.pid"
    command = f"sleep 30 & echo $! > {shlex.quote(str(pid_file))}; wait"
    execution = asyncio.create_task(
        BashTool().run(
            {"command": command, "timeout": 10},
            {"effective_cwd": str(tmp_path)},
        )
    )
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    child_pid = int(pid_file.read_text().strip())

    try:
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
        for _ in range(100):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("foreground child survived Bash cancellation")
    finally:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
