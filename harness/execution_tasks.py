"""Durable execution of background shell tasks."""

from __future__ import annotations

import asyncio
import os
import signal
import uuid
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from state_core import (
    ExecutionTaskRecord,
    ExecutionTaskStatus,
    RuntimeRecordRevisionConflict,
)
from state_core.runtime_primitives import SAFE_EXECUTION_ENVIRONMENT_KEYS

_TERMINAL_STATUSES = frozenset(
    {
        ExecutionTaskStatus.COMPLETED,
        ExecutionTaskStatus.FAILED,
        ExecutionTaskStatus.KILLED,
        ExecutionTaskStatus.TIMED_OUT,
        ExecutionTaskStatus.INTERRUPTED,
    }
)


class ExecutionTaskLaunchError(RuntimeError):
    """Raised after a failed process start has been recorded durably."""


class ExecutionTaskWaitTimeout(TimeoutError):
    """Raised when a task does not reach a terminal state before the deadline."""


class ExecutionTaskNotRunning(RuntimeError):
    """Raised when a task cannot be controlled by this live runtime."""


class _ExecutionTaskOutputLimit(RuntimeError):
    pass


@dataclass
class _LiveTask:
    process: asyncio.subprocess.Process
    completion: asyncio.Task[ExecutionTaskRecord] | None = None
    requested_status: ExecutionTaskStatus | None = None
    requested_reason: str | None = None


@dataclass(frozen=True)
class ExecutionTaskReadResult:
    record: ExecutionTaskRecord
    data: bytes
    next_cursor: int
    total_bytes: int
    retrieval_status: str


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
    except (ProcessLookupError, PermissionError):
        pass


def _process_group_exists(process: asyncio.subprocess.Process) -> bool:
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _close_process_streams(process: asyncio.subprocess.Process) -> None:
    for stream in (process.stdout, process.stderr):
        transport = getattr(stream, "_transport", None)
        if transport is not None:
            transport.close()


def _repository_identity(repository: Any, fallback_owner: Any) -> str:
    """Match AgentScheduler's durable bind identity without importing it."""

    session_factory = getattr(repository, "_session_factory", None)
    bind = getattr(session_factory, "kw", {}).get("bind")
    url = getattr(bind, "url", None)
    if url is not None:
        drivername = getattr(url, "drivername", "")
        if drivername.startswith("sqlite"):
            database = getattr(url, "database", None)
            if database and database != ":memory:":
                canonical_database = Path(database).expanduser().resolve()
                return f"sqlalchemy:sqlite:{canonical_database}"
            return f"sqlalchemy:sqlite-memory:{id(bind)}"
        rendered = url.render_as_string(hide_password=False)
        return f"sqlalchemy:{rendered}"
    if session_factory is not None:
        return f"session-factory:{id(session_factory)}"
    return f"fallback-owner:{id(fallback_owner)}"


class ExecutionTaskManager:
    _instances: weakref.WeakSet["ExecutionTaskManager"] = weakref.WeakSet()

    def __init__(
        self,
        repository: Any,
        *,
        root_session_id: str,
        agent_id: str,
        cwd: Path,
        process_factory: Callable[..., Any] | None = None,
        identity_source: Any | None = None,
    ) -> None:
        self.repository = repository
        self.root_session_id = root_session_id
        self.agent_id = agent_id
        self.cwd = Path(cwd).expanduser().resolve()
        self.process_factory = process_factory or asyncio.create_subprocess_shell
        self._owner_key = (
            _repository_identity(
                repository,
                repository if identity_source is None else identity_source,
            ),
            root_session_id,
        )
        self._live: dict[str, _LiveTask] = {}
        self._owned: dict[str, str] = {}
        self._instances.add(self)

    @classmethod
    def for_harness(cls, harness: Any) -> "ExecutionTaskManager":
        services = getattr(harness, "_services", None)
        if not isinstance(services, dict):
            raise TypeError("session harness does not expose a service registry")
        existing = services.get("execution_tasks")
        if isinstance(existing, cls):
            return existing
        manager = cls(
            harness.store.execution_tasks,
            root_session_id=harness.root_session_id,
            agent_id=harness.agent_id or "main",
            cwd=harness.effective_cwd,
            identity_source=harness.store,
        )
        services["execution_tasks"] = manager
        return manager

    async def launch(
        self,
        command: str,
        *,
        description: str,
        timeout: float,
        cwd: Path | None = None,
        environment: dict[str, str] | None = None,
        cancellation: Any = None,
    ) -> ExecutionTaskRecord:
        canonical_cwd = (cwd or self.cwd).expanduser().resolve()
        source_environment = os.environ if environment is None else environment
        safe_environment = {
            key: value
            for key, value in source_environment.items()
            if key in SAFE_EXECUTION_ENVIRONMENT_KEYS
        }
        task_id = f"shell_{uuid.uuid4().hex}"
        owner_token = uuid.uuid4().hex
        self._owned[task_id] = owner_token
        try:
            pending = self.repository.create(
                ExecutionTaskRecord(
                    task_id=task_id,
                    root_session_id=self.root_session_id,
                    agent_id=self.agent_id,
                    kind="shell",
                    command=command,
                    description=description,
                    canonical_cwd=str(canonical_cwd),
                    output_artifact_id=f"output_{task_id}",
                    timeout_ms=max(1, int(timeout * 1000)),
                    safe_environment=safe_environment,
                    process_owner_token=owner_token,
                )
            )
        except BaseException:
            self._owned.pop(task_id, None)
            raise
        spawn = asyncio.ensure_future(
            self.process_factory(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(canonical_cwd),
                env=environment,
                start_new_session=True,
            )
        )
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            try:
                process = await spawn
            except BaseException:
                await self._finish_launch(
                    pending.task_id,
                    None,
                    ExecutionTaskStatus.KILLED,
                    "cancelled_before_start",
                )
                raise
            await self._finish_launch(
                pending.task_id,
                process,
                ExecutionTaskStatus.KILLED,
                "cancelled_before_start",
            )
            raise
        except Exception as exc:
            await self._finish_launch(
                pending.task_id,
                None,
                ExecutionTaskStatus.FAILED,
                "spawn_failed",
            )
            raise ExecutionTaskLaunchError(str(exc)) from exc

        if cancellation is not None and cancellation.cancelled:
            await self._finish_launch(
                pending.task_id,
                process,
                ExecutionTaskStatus.KILLED,
                "cancelled_before_start",
            )
            raise asyncio.CancelledError()

        try:
            running = self.repository.transition(
                pending.task_id,
                ExecutionTaskStatus.RUNNING,
                pending.revision,
                process_owner_token=owner_token,
            )
        except Exception as exc:
            await self._finish_launch(
                pending.task_id,
                process,
                ExecutionTaskStatus.FAILED,
                "running_transition_failed",
            )
            raise ExecutionTaskLaunchError(
                f"background task {pending.task_id} lost ownership before running"
            ) from exc
        live = _LiveTask(process)
        self._live[running.task_id] = live
        live.completion = asyncio.create_task(self._watch(running.task_id, live, timeout))
        live.completion.add_done_callback(self._consume_completion_exception)
        return running

    async def _finish_launch(
        self,
        task_id: str,
        process: asyncio.subprocess.Process | None,
        status: ExecutionTaskStatus,
        reason: str,
    ) -> ExecutionTaskRecord:
        try:
            if process is not None:
                await self._terminate_process_group(process)
            return await self._publish_terminal(
                task_id,
                status,
                exit_code=None if process is None else process.returncode,
                termination_reason=reason,
            )
        finally:
            self._owned.pop(task_id, None)

    async def _watch(
        self,
        task_id: str,
        live: _LiveTask,
        timeout: float,
    ) -> ExecutionTaskRecord:
        try:
            return await self._watch_process(task_id, live, timeout)
        except asyncio.CancelledError:
            try:
                await self._terminate_process_group(live.process)
            except BaseException:
                pass
            return await self._publish_terminal(
                task_id,
                ExecutionTaskStatus.INTERRUPTED,
                exit_code=live.process.returncode,
                termination_reason="runtime_shutdown",
            )
        except BaseException:
            try:
                await self._terminate_process_group(live.process)
            except BaseException:
                pass
            return await self._publish_terminal(
                task_id,
                ExecutionTaskStatus.FAILED,
                exit_code=live.process.returncode,
                termination_reason="watcher_failed",
            )
        finally:
            if self._live.get(task_id) is live:
                self._live.pop(task_id, None)
            self._owned.pop(task_id, None)

    async def _watch_process(
        self,
        task_id: str,
        live: _LiveTask,
        timeout: float,
    ) -> ExecutionTaskRecord:
        process = live.process
        status = ExecutionTaskStatus.COMPLETED
        reason = "completed"
        assert process.stdout is not None
        output = asyncio.create_task(self._pump_output(task_id, process.stdout))
        process_wait = asyncio.create_task(process.wait())
        monitor = asyncio.create_task(self._wait_for_exit_and_output(process_wait, output))
        terminate = False
        try:
            done, _ = await asyncio.wait({monitor}, timeout=timeout)
            if monitor not in done:
                if live.requested_status is None:
                    live.requested_status = ExecutionTaskStatus.TIMED_OUT
                    live.requested_reason = "timed_out"
                terminate = True
        except asyncio.CancelledError:
            if live.requested_status is None:
                live.requested_status = ExecutionTaskStatus.INTERRUPTED
                live.requested_reason = "runtime_shutdown"
            terminate = True
        if terminate:
            await self._terminate_process_group(process)
        monitor_result = await self._settle_monitor(
            process,
            monitor,
            process_wait,
            output,
            bounded=terminate,
        )
        if isinstance(monitor_result, _ExecutionTaskOutputLimit):
            if live.requested_status is None:
                status = ExecutionTaskStatus.FAILED
                reason = "output_limit"
            await self._terminate_process_group(process)
        elif isinstance(monitor_result, asyncio.CancelledError):
            if live.requested_status is None:
                status = ExecutionTaskStatus.FAILED
                reason = "output_cancelled"
            await self._terminate_process_group(process)
        elif isinstance(monitor_result, BaseException):
            if live.requested_status is None:
                status = ExecutionTaskStatus.FAILED
                reason = "output_failed"
            await self._terminate_process_group(process)
        current = self.repository.get(task_id)
        assert current is not None
        if live.requested_status is not None:
            status = live.requested_status
            reason = live.requested_reason or live.requested_status.value
        elif status is ExecutionTaskStatus.COMPLETED and process.returncode != 0:
            status = ExecutionTaskStatus.FAILED
            reason = "nonzero_exit"
        result = await self._publish_terminal(
            task_id,
            status,
            exit_code=process.returncode,
            termination_reason=reason,
        )
        return result

    @staticmethod
    async def _settle_monitor(
        process: asyncio.subprocess.Process,
        monitor: asyncio.Task[None],
        process_wait: asyncio.Task[int],
        output: asyncio.Task[None],
        *,
        bounded: bool,
    ) -> BaseException | None:
        if monitor.done():
            monitor_result = (await asyncio.gather(monitor, return_exceptions=True))[0]
            if isinstance(monitor_result, BaseException):
                await ExecutionTaskManager._terminate_process_group(process)
                bounded = True
        if bounded:
            pending = {task for task in (monitor, process_wait, output) if not task.done()}
            if pending:
                _, pending = await asyncio.wait(pending, timeout=0.2)
            if pending:
                _close_process_streams(process)
                for task in pending:
                    task.cancel()
        results = await asyncio.gather(
            monitor,
            process_wait,
            output,
            return_exceptions=True,
        )
        monitor_result = results[0]
        return monitor_result if isinstance(monitor_result, BaseException) else None

    @staticmethod
    def _consume_completion_exception(task: asyncio.Task[ExecutionTaskRecord]) -> None:
        if not task.cancelled():
            task.exception()

    @staticmethod
    async def _wait_for_exit_and_output(
        process_wait: asyncio.Task[int], output: asyncio.Task[None]
    ) -> None:
        output_finished = False
        while not process_wait.done():
            waiters: set[asyncio.Task[Any]] = {process_wait}
            if not output_finished:
                waiters.add(output)
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if output in done:
                await output
                output_finished = True
        await process_wait
        if not output_finished:
            await output

    @staticmethod
    async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                return
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 0.2
            if process.returncode is None:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(process.wait()),
                        max(0.0, deadline - loop.time()),
                    )
                except asyncio.TimeoutError:
                    pass
            while _process_group_exists(process) and loop.time() < deadline:
                await asyncio.sleep(min(0.01, deadline - loop.time()))
            if _process_group_exists(process):
                _kill_process_group(process)
            if process.returncode is None:
                await process.wait()
            return
        if process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), 0.2)
            return
        except asyncio.TimeoutError:
            _kill_process_group(process)
        await process.wait()

    async def _publish_terminal(
        self,
        task_id: str,
        status: ExecutionTaskStatus,
        **changes: Any,
    ) -> ExecutionTaskRecord:
        while True:
            current = self.repository.get(task_id)
            if current is None:
                raise KeyError(task_id)
            if current.status in _TERMINAL_STATUSES:
                return current
            try:
                return self.repository.transition(task_id, status, current.revision, **changes)
            except RuntimeRecordRevisionConflict:
                await asyncio.sleep(0)

    async def _pump_output(self, task_id: str, stream: asyncio.StreamReader) -> None:
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                return
            while True:
                current = self.repository.get(task_id)
                if current is None or current.status in _TERMINAL_STATUSES:
                    return
                try:
                    self.repository.append_output(task_id, chunk, current.revision)
                    break
                except RuntimeRecordRevisionConflict:
                    await asyncio.sleep(0)
                except ValueError as exc:
                    raise _ExecutionTaskOutputLimit from exc

    async def read(
        self,
        task_id: str,
        *,
        cursor: int = 0,
        max_bytes: int = 64 * 1024,
        block: bool = False,
        timeout: float = 30.0,
        tail: bool = False,
    ) -> ExecutionTaskReadResult:
        if cursor < 0:
            raise ValueError("cursor must be non-negative")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            record = self.repository.get(task_id)
            if record is None:
                raise KeyError(task_id)
            start = max(0, record.output_byte_count - max_bytes) if tail else cursor
            chunk = self.repository.read_output(task_id, cursor=start, max_bytes=max_bytes)
            if chunk.data or record.status in _TERMINAL_STATUSES:
                return ExecutionTaskReadResult(
                    record,
                    chunk.data,
                    chunk.next_cursor,
                    chunk.total_bytes,
                    "success",
                )
            if not block:
                return ExecutionTaskReadResult(
                    record,
                    b"",
                    chunk.next_cursor,
                    chunk.total_bytes,
                    "not_ready",
                )
            if loop.time() >= deadline:
                return ExecutionTaskReadResult(
                    record,
                    b"",
                    chunk.next_cursor,
                    chunk.total_bytes,
                    "timeout",
                )
            await asyncio.sleep(min(0.02, max(0.0, deadline - loop.time())))

    async def wait(self, task_id: str, timeout: float | None = None) -> ExecutionTaskRecord:
        live = self._live.get(task_id)
        if live is None:
            record = self.repository.get(task_id)
            if record is None:
                raise KeyError(task_id)
            return record
        try:
            if timeout is None:
                assert live.completion is not None
                return await asyncio.shield(live.completion)
            assert live.completion is not None
            return await asyncio.wait_for(asyncio.shield(live.completion), timeout)
        except asyncio.TimeoutError as exc:
            raise ExecutionTaskWaitTimeout(task_id) from exc

    async def stop(self, task_id: str) -> ExecutionTaskRecord:
        record = self.repository.get(task_id)
        if record is None:
            raise KeyError(task_id)
        if record.status in _TERMINAL_STATUSES:
            raise ExecutionTaskNotRunning(
                f"task {task_id} is not running (status: {record.status.value})"
            )
        live = self._live.get(task_id)
        if live is None:
            raise ExecutionTaskNotRunning(f"task {task_id} has no process owned by this runtime")
        live.requested_status = ExecutionTaskStatus.KILLED
        live.requested_reason = "stopped"
        await self._terminate_process_group(live.process)
        assert live.completion is not None
        return await asyncio.shield(live.completion)

    def reconcile(self) -> list[ExecutionTaskRecord]:
        live_owner_tokens = {
            owner_token
            for manager in tuple(self._instances)
            if manager._owner_key == self._owner_key
            for owner_token in manager._owned.values()
        }
        return self.repository.interrupt_open(
            self.root_session_id,
            live_owner_tokens=live_owner_tokens,
            now=datetime.now(timezone.utc),
        )
