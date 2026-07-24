"""Durable, session-scoped scheduling for child agents.

Scheduler ownership is enforced within this process. Cross-process concurrent
scheduling over the same durable root is unsupported; deployments must ensure
one scheduling process and fail closed by treating an unverifiable owner as
unavailable.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import enum
import math
import threading
import uuid
import weakref
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agents.built_in import get_agent_by_type
from agents.types import (
    AgentDefinition,
    AgentDefinitionError,
    AgentExecutionResult,
    AgentRequest,
    AgentRunner,
    CustomAgentDefinition,
    PluginAgentDefinition,
    parse_agent_definition,
)
from state_core import (
    AgentRecord,
    AgentRepository,
    AgentStatus,
    AgentTerminationReason,
    RuntimeRecordRevisionConflict,
    sanitize_runtime_error,
)
from state_core.runtime_records import AGENT_TERMINAL_STATUSES

from .context import PermissionMode
from .session import SessionHarness


class AgentSchedulerError(RuntimeError):
    """Base error for scheduler operations."""


class AgentSchedulerAlreadyActive(AgentSchedulerError):
    """Raised when another scheduler already owns the durable root."""


class AgentSchedulerDegraded(AgentSchedulerError):
    """Raised while unresolved runner work retains scheduler capacity."""


class AgentNotFound(AgentSchedulerError):
    """Raised when no durable agent record exists."""


class AgentOwnershipError(AgentSchedulerError):
    """Raised when an agent belongs to another root session."""


class AgentWaitTimeout(TimeoutError, AgentSchedulerError):
    """Raised when a waiter reaches its deadline without stopping the agent."""


@dataclasses.dataclass(frozen=True)
class AgentShutdownReport:
    """Bounded shutdown outcome for work Python could not terminate."""

    unresolved_quarantines: int


_SCHEDULER_OWNERS: dict[
    tuple[str, str], weakref.ReferenceType[AgentScheduler]
] = {}
_SCHEDULER_OWNER_LOCK = threading.RLock()
def _repository_identity(
    repository: AgentRepository, harness_store: Any
) -> str:
    session_factory = getattr(repository, "_session_factory", None)
    bind = getattr(session_factory, "kw", {}).get("bind")
    url = getattr(bind, "url", None)
    if url is not None:
        return f"sqlalchemy:{url}"
    if session_factory is not None:
        return f"session-factory:{id(session_factory)}"
    # Generic repository protocols expose no durable identity. Keying their
    # adapters to the harness store may reject independent adapters, which is
    # the required fail-closed behavior when ownership cannot be verified.
    return f"harness-store:{id(harness_store)}"


def _owner_key(
    harness: SessionHarness, repository: AgentRepository
) -> tuple[str, str]:
    return (
        _repository_identity(repository, harness.store),
        harness.root_session_id,
    )


def _registered_owner(key: tuple[str, str]) -> AgentScheduler | None:
    reference = _SCHEDULER_OWNERS.get(key)
    owner = reference() if reference is not None else None
    if reference is not None and owner is None:
        _SCHEDULER_OWNERS.pop(key, None)
    return owner


class _ConcurrencyLease:
    """One idempotent ownership handle for root and parent scheduler capacity."""

    def __init__(
        self,
        agent_id: str,
        key: str,
        root_semaphore: asyncio.Semaphore,
        parent_semaphore: asyncio.Semaphore,
    ) -> None:
        self.agent_id = agent_id
        self.key = key
        self.root_semaphore = root_semaphore
        self.parent_semaphore = parent_semaphore
        self.root_acquired = False
        self.parent_acquired = False
        self.released = False


def _json_value(value: Any) -> Any:
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, enum.Enum):
        return _json_value(value.value)
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if field.name != "get_system_prompt"
        }
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported definition value: {type(value).__name__}")


def _definition_snapshot(
    definition: AgentDefinition, request: AgentRequest
) -> dict[str, Any]:
    snapshot = _json_value(definition)
    assert isinstance(snapshot, dict)
    if definition.get_system_prompt is None:
        system_prompt = (
            "You are an agent for Claude Code.\n"
            f"Agent Type: {definition.agent_type}\n\n"
            f"{definition.when_to_use}\n\n"
            "Complete the task and return a concise factual report."
        )
    else:
        system_prompt = definition.get_system_prompt()
    if not isinstance(system_prompt, str) or not system_prompt:
        raise AgentDefinitionError("Agent system prompt must be a non-empty string")
    snapshot["system_prompt"] = system_prompt
    snapshot["metadata"] = _json_value(copy.deepcopy(request.definition_metadata))
    if request.model is not None:
        snapshot["model"] = request.model
    snapshot["execution_timeout"] = _execution_timeout(request)
    return snapshot


def _execution_timeout(request: AgentRequest) -> float | None:
    value = request.timeout
    if value is None:
        value = request.definition_metadata.get("timeout")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Agent timeout must be a positive finite number")
    timeout = float(value)
    if timeout <= 0 or not math.isfinite(timeout):
        raise ValueError("Agent timeout must be a positive finite number")
    return timeout


def _permission_mode(definition: AgentDefinition) -> PermissionMode | None:
    configured = definition.permission_mode
    if configured is None:
        return None
    return PermissionMode(configured.value)


def _normalized_content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if content is None:
        return []
    if isinstance(content, (list, tuple)):
        return [
            {"type": "text", "text": item}
            if isinstance(item, str)
            else _json_value(item)
            for item in content
        ]
    return [{"type": "text", "text": str(content)}]


def _error_payload(error: BaseException | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if error is None:
        return None
    if isinstance(error, BaseException):
        name = type(error).__name__
        message = str(error)
    else:
        copied = _json_value(error)
        assert isinstance(copied, dict)
        name = str(copied.get("type", "AgentExecutionError"))
        message = str(copied.get("message", "Agent execution failed"))
    sanitized = sanitize_runtime_error({"type": name, "message": message})
    assert sanitized is not None
    return sanitized


class AgentScheduler:
    """Own live asyncio handles while keeping lifecycle state in the repository."""

    def __init__(
        self,
        harness: SessionHarness,
        repository: AgentRepository | None = None,
        *,
        runner: AgentRunner | Callable[[AgentRecord, SessionHarness], Any] | None = None,
        runner_factory: Callable[[AgentRecord], AgentRunner] | None = None,
        root_concurrency: int = 4,
        per_parent_concurrency: int = 2,
        stop_grace: float = 0.25,
        force_grace: float = 0.25,
    ) -> None:
        if root_concurrency < 1 or per_parent_concurrency < 1:
            raise ValueError("agent concurrency limits must be positive")
        if (
            stop_grace < 0
            or force_grace < 0
            or not math.isfinite(stop_grace)
            or not math.isfinite(force_grace)
        ):
            raise ValueError("stop grace periods must be non-negative finite numbers")
        if runner is not None and runner_factory is not None:
            raise ValueError("provide runner or runner_factory, not both")
        resolved_repository = repository or harness.store.agents
        owner_key = _owner_key(harness, resolved_repository)
        with _SCHEDULER_OWNER_LOCK:
            owner = _registered_owner(owner_key)
            if owner is not None:
                raise AgentSchedulerAlreadyActive(
                    "An AgentScheduler already owns this durable root"
                )
            self.harness = harness
            self.repository = resolved_repository
            self._owner_key = owner_key
            self._runner = runner
            self._runner_factory = runner_factory
            self._root_semaphore = asyncio.BoundedSemaphore(root_concurrency)
            self._per_parent_concurrency = per_parent_concurrency
            self._parent_semaphores: dict[str, asyncio.Semaphore] = {}
            self._parent_refcounts: dict[str, int] = {}
            self._concurrency_leases: dict[str, _ConcurrencyLease] = {}
            self._concurrency_lock = asyncio.Lock()
            self._tasks: dict[str, asyncio.Task[AgentRecord]] = {}
            self._children: dict[str, SessionHarness] = {}
            self._quarantined_tasks: set[asyncio.Task[AgentRecord]] = set()
            self._stop_grace = stop_grace
            self._force_grace = force_grace
            self._closed = False
            _SCHEDULER_OWNERS[owner_key] = weakref.ref(self)

    @classmethod
    def for_harness(
        cls,
        harness: SessionHarness,
        repository: AgentRepository | None = None,
        **options: Any,
    ) -> AgentScheduler:
        """Return the process owner for a durable root, creating it if absent."""

        resolved_repository = repository or harness.store.agents
        key = _owner_key(harness, resolved_repository)
        with _SCHEDULER_OWNER_LOCK:
            owner = _registered_owner(key)
            if owner is not None:
                return owner
            return cls(harness, resolved_repository, **options)

    @property
    def live_agent_ids(self) -> frozenset[str]:
        return frozenset(self._tasks)

    @property
    def quarantined_task_count(self) -> int:
        return len(self._quarantined_tasks)

    @property
    def unresolved_quarantine_count(self) -> int:
        return len(self._quarantined_tasks)

    @property
    def is_degraded(self) -> bool:
        return bool(self._quarantined_tasks)

    @property
    def parent_limiter_count(self) -> int:
        return len(self._parent_semaphores)

    @property
    def parent_limiter_refcounts(self) -> dict[str, int]:
        return dict(self._parent_refcounts)

    @property
    def root_available_capacity(self) -> int:
        return self._root_semaphore._value

    @property
    def managed_concurrency_count(self) -> int:
        return len(self._concurrency_leases)

    def _owned(self, agent_id: str) -> AgentRecord:
        record = self.repository.get(agent_id)
        if record is None:
            raise AgentNotFound(f"Agent {agent_id} not found")
        if record.root_session_id != self.harness.root_session_id:
            raise AgentOwnershipError(
                f"Agent {agent_id} belongs to root session {record.root_session_id!r}"
            )
        return record

    def _validate_parent(self, parent_agent_id: str | None) -> None:
        if parent_agent_id is None or parent_agent_id == self.harness.agent_id:
            return
        parent = self.repository.get(parent_agent_id)
        if (
            parent is None
            or parent.root_session_id != self.harness.root_session_id
            or parent.status is not AgentStatus.RUNNING
        ):
            raise AgentOwnershipError(
                "Agent parent must be the current harness or a running agent "
                "in the same durable root"
            )

    @staticmethod
    def _validate_request(
        request: AgentRequest,
    ) -> tuple[AgentDefinition, dict[str, Any]]:
        if not isinstance(request.prompt, str) or not request.prompt:
            raise ValueError("Agent prompt must be non-empty")
        if not isinstance(request.description, str) or not request.description:
            raise ValueError("Agent description must be non-empty")
        built_in_definition = get_agent_by_type(request.agent_type)
        if request.definition is None:
            if built_in_definition is None:
                raise ValueError(f"Unknown agent type: {request.agent_type}")
            definition: AgentDefinition = built_in_definition
        else:
            definition = parse_agent_definition(
                request.definition, expected_agent_type=request.agent_type
            )
        definition = parse_agent_definition(
            definition, expected_agent_type=request.agent_type
        )
        if built_in_definition is None and not isinstance(
            definition, (CustomAgentDefinition, PluginAgentDefinition)
        ):
            raise AgentDefinitionError(f"Unknown agent type: {request.agent_type}")
        if request.model is not None and not isinstance(request.model, str):
            raise AgentDefinitionError("Agent model must be a string")
        _execution_timeout(request)
        return definition, _definition_snapshot(definition, request)

    async def spawn(self, request: AgentRequest) -> AgentRecord:
        if self.is_degraded:
            raise AgentSchedulerDegraded(
                "Agent scheduler has unresolved quarantined runner work"
            )
        if self._closed:
            raise RuntimeError("Agent scheduler is shut down")
        parent_agent_id = (
            request.parent_agent_id
            if request.parent_agent_id is not None
            else self.harness.agent_id
        )
        self._validate_parent(parent_agent_id)
        definition, definition_snapshot = self._validate_request(request)
        agent_id = f"agent-{request.agent_type.lower()}-{uuid.uuid4().hex[:12]}"
        child_options: dict[str, Any] = {
            "parent_agent_id": parent_agent_id,
            "cwd": request.cwd,
            "metadata": {"agent_type": request.agent_type},
        }
        permission_mode = _permission_mode(definition)
        if permission_mode is not None:
            child_options["permission_mode"] = permission_mode
        child = self.harness.child(agent_id, **child_options)
        record = self.repository.create(
            AgentRecord(
                agent_id=agent_id,
                root_session_id=self.harness.root_session_id,
                parent_agent_id=parent_agent_id,
                agent_type=request.agent_type,
                prompt=request.prompt,
                description=request.description,
                is_background=request.background,
                effective_cwd=str(child.effective_cwd),
                definition_snapshot=definition_snapshot,
                worktree_id=request.worktree_id,
            )
        )
        self._children[agent_id] = child
        task = asyncio.create_task(self._execute(record, child), name=f"agent:{agent_id}")
        self._tasks[agent_id] = task
        task.add_done_callback(lambda completed, key=agent_id: self._forget(key, completed))
        if request.background:
            return record
        return await self.wait(agent_id)

    def _forget(self, agent_id: str, completed: asyncio.Task[AgentRecord]) -> None:
        if self._tasks.get(agent_id) is completed:
            self._tasks.pop(agent_id, None)
            child = self._children.pop(agent_id, None)
            if child is not None:
                child.runtime_context.cancellation.dispose()

    async def _acquire_concurrency_lease(
        self, record: AgentRecord
    ) -> "_ConcurrencyLease":
        key = record.parent_agent_id or "<root>"
        async with self._concurrency_lock:
            parent_semaphore = self._parent_semaphores.setdefault(
                key, asyncio.BoundedSemaphore(self._per_parent_concurrency)
            )
            self._parent_refcounts[key] = self._parent_refcounts.get(key, 0) + 1
            lease = _ConcurrencyLease(
                record.agent_id,
                key,
                self._root_semaphore,
                parent_semaphore,
            )
            self._concurrency_leases[record.agent_id] = lease
        try:
            await parent_semaphore.acquire()
            lease.parent_acquired = True
            await self._root_semaphore.acquire()
            lease.root_acquired = True
            return lease
        except BaseException:
            await self._release_concurrency_lease(record.agent_id, lease)
            raise

    async def _release_concurrency_lease(
        self, agent_id: str, lease: "_ConcurrencyLease" | None = None
    ) -> None:
        lease = lease or self._concurrency_leases.get(agent_id)
        if lease is None:
            return
        async with self._concurrency_lock:
            if lease.released:
                return
            lease.released = True
            if lease.root_acquired:
                lease.root_semaphore.release()
            if lease.parent_acquired:
                lease.parent_semaphore.release()
            current = self._parent_refcounts.get(lease.key, 0)
            if current <= 1:
                self._parent_refcounts.pop(lease.key, None)
                if self._parent_semaphores.get(lease.key) is lease.parent_semaphore:
                    self._parent_semaphores.pop(lease.key, None)
            else:
                self._parent_refcounts[lease.key] = current - 1
            if self._concurrency_leases.get(agent_id) is lease:
                self._concurrency_leases.pop(agent_id, None)

    def _runner_for(self, record: AgentRecord):
        if self._runner_factory is not None:
            return self._runner_factory(record)
        if self._runner is not None:
            return self._runner
        from agents.engine import AgentExecutor

        return AgentExecutor.from_record(record)

    async def _execute(self, record: AgentRecord, child: SessionHarness) -> AgentRecord:
        lease: _ConcurrencyLease | None = None
        try:
            lease = await self._acquire_concurrency_lease(record)
            if child.runtime_context.cancellation.cancelled:
                raise asyncio.CancelledError
            current = self._owned(record.agent_id)
            current = self.repository.transition(
                current.agent_id, AgentStatus.RUNNING, current.revision
            )
            runner = self._runner_for(current)
            invocation = (
                runner.run(current, child)
                if hasattr(runner, "run")
                else runner(current, child)
            )
            timeout = current.definition_snapshot.get("execution_timeout")
            if timeout is None:
                result = await invocation
            else:
                result = await asyncio.wait_for(invocation, float(timeout))
            return self._persist_result(current.agent_id, result)
        except asyncio.TimeoutError:
            return self._finish(record.agent_id, AgentStatus.TIMED_OUT)
        except asyncio.CancelledError:
            return self._finish(record.agent_id, AgentStatus.CANCELLED)
        except BaseException as exc:
            return self._finish(
                record.agent_id, AgentStatus.FAILED, error=_error_payload(exc)
            )
        finally:
            if lease is not None:
                await self._release_concurrency_lease(record.agent_id, lease)

    def _persist_result(
        self, agent_id: str, result: AgentExecutionResult
    ) -> AgentRecord:
        reason = (
            result.termination_reason.value
            if isinstance(result.termination_reason, enum.Enum)
            else str(result.termination_reason)
        )
        aliases = {"timeout": "timed_out", "killed": "cancelled"}
        reason = aliases.get(reason, reason)
        try:
            status = AgentStatus(reason)
        except ValueError:
            status = AgentStatus.FAILED
        if status not in AGENT_TERMINAL_STATUSES:
            status = AgentStatus.FAILED
        output = {
            "content": _normalized_content(result.content),
            "tool_count": int(result.tool_count),
            "output": _json_value(result.output),
        }
        return self._finish(
            agent_id,
            status,
            output=output,
            usage=_json_value(result.usage),
            error=_error_payload(result.error),
        )

    def _finish(
        self,
        agent_id: str,
        status: AgentStatus,
        *,
        output: Any = None,
        usage: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> AgentRecord:
        current = self._owned(agent_id)
        if current.status in AGENT_TERMINAL_STATUSES:
            return current
        try:
            return self.repository.transition(
                agent_id,
                status,
                current.revision,
                termination_reason=AgentTerminationReason(status.value),
                output=output,
                usage=usage,
                error=error,
            )
        except RuntimeRecordRevisionConflict:
            current = self._owned(agent_id)
            if current.status in AGENT_TERMINAL_STATUSES:
                return current
            return self.repository.transition(
                agent_id,
                status,
                current.revision,
                termination_reason=AgentTerminationReason(status.value),
                output=output,
                usage=usage,
                error=error,
            )

    async def wait(self, agent_id: str, timeout: float | None = None) -> AgentRecord:
        record = self._owned(agent_id)
        if record.status in AGENT_TERMINAL_STATUSES:
            return record
        task = self._tasks.get(agent_id)
        if task is None:
            async def poll_durable() -> AgentRecord:
                while True:
                    current = self._owned(agent_id)
                    if current.status in AGENT_TERMINAL_STATUSES:
                        return current
                    await asyncio.sleep(0.01)

            try:
                if timeout is None:
                    return await poll_durable()
                return await asyncio.wait_for(poll_durable(), timeout)
            except asyncio.TimeoutError as exc:
                raise AgentWaitTimeout(f"Timed out waiting for agent {agent_id}") from exc
        try:
            if timeout is None:
                await asyncio.shield(task)
            else:
                await asyncio.wait_for(asyncio.shield(task), timeout)
        except asyncio.TimeoutError as exc:
            raise AgentWaitTimeout(f"Timed out waiting for agent {agent_id}") from exc
        return self._owned(agent_id)

    def status(self, agent_id: str) -> AgentRecord:
        return self._owned(agent_id)

    def list(
        self,
        *,
        parent_agent_id: str | None = None,
        status: AgentStatus | None = None,
        background: bool | None = None,
    ) -> list[AgentRecord]:
        return self.repository.list(
            self.harness.root_session_id,
            parent_agent_id=parent_agent_id,
            status=status,
            is_background=background,
        )

    async def stop(self, agent_id: str) -> AgentRecord:
        record = self._owned(agent_id)
        if record.status in AGENT_TERMINAL_STATUSES:
            return record
        child = self._children.get(agent_id)
        if child is not None:
            child.runtime_context.cancellation.cancel()
        task = self._tasks.get(agent_id)
        if task is None:
            return self._finish(agent_id, AgentStatus.CANCELLED)
        try:
            await asyncio.wait_for(asyncio.shield(task), self._stop_grace)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), self._force_grace)
            except asyncio.TimeoutError:
                cancelled = self._finish(agent_id, AgentStatus.CANCELLED)
                await self._quarantine(agent_id, task)
                return cancelled
        return self._owned(agent_id)

    async def _quarantine(
        self, agent_id: str, task: asyncio.Task[AgentRecord]
    ) -> None:
        if self._tasks.get(agent_id) is task:
            self._tasks.pop(agent_id, None)
        child = self._children.pop(agent_id, None)
        if child is not None:
            child.runtime_context.cancellation.dispose()
        # Python cannot kill a coroutine that suppresses cancellation. Keep its
        # quota lease until _execute actually leaves its finally block.
        self._quarantined_tasks.add(task)
        task.add_done_callback(self._reap_quarantined)

    def _reap_quarantined(self, task: asyncio.Task[AgentRecord]) -> None:
        self._quarantined_tasks.discard(task)
        try:
            task.result()
        except BaseException:
            pass
        if self._closed and not self._quarantined_tasks:
            self._unregister_owner()

    def reconcile(self) -> list[AgentRecord]:
        return self.repository.reconcile(
            self.harness.root_session_id, set(self.live_agent_ids)
        )

    def _unregister_owner(self) -> None:
        with _SCHEDULER_OWNER_LOCK:
            if _registered_owner(self._owner_key) is self:
                _SCHEDULER_OWNERS.pop(self._owner_key, None)

    async def shutdown(self) -> AgentShutdownReport:
        self._closed = True
        agent_ids = list(self._tasks)
        if agent_ids:
            await asyncio.gather(
                *(self.stop(agent_id) for agent_id in agent_ids),
                return_exceptions=True,
            )
        await asyncio.sleep(0)
        if not self._quarantined_tasks:
            self._unregister_owner()
        return AgentShutdownReport(len(self._quarantined_tasks))


__all__ = [
    "AgentNotFound",
    "AgentOwnershipError",
    "AgentScheduler",
    "AgentSchedulerAlreadyActive",
    "AgentSchedulerDegraded",
    "AgentSchedulerError",
    "AgentShutdownReport",
    "AgentWaitTimeout",
]
