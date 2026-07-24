"""Durable, session-scoped scheduling for child agents."""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import enum
import math
import re
import uuid
import weakref
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agents.built_in import get_agent_by_type
from agents.types import (
    AgentDefinition,
    AgentExecutionResult,
    AgentRequest,
    AgentRunner,
)
from state_core import (
    AgentRecord,
    AgentRepository,
    AgentStatus,
    AgentTerminationReason,
    RuntimeRecordRevisionConflict,
)
from state_core.runtime_records import AGENT_TERMINAL_STATUSES

from .context import PermissionMode
from .session import SessionHarness


class AgentSchedulerError(RuntimeError):
    """Base error for scheduler operations."""


class AgentNotFound(AgentSchedulerError):
    """Raised when no durable agent record exists."""


class AgentOwnershipError(AgentSchedulerError):
    """Raised when an agent belongs to another root session."""


class AgentWaitTimeout(TimeoutError, AgentSchedulerError):
    """Raised when a waiter reaches its deadline without stopping the agent."""


_LIVE_SCHEDULERS: weakref.WeakSet[AgentScheduler] = weakref.WeakSet()
_REDACT = re.compile(
    r"(?i)\b(api[_-]?key|authorization|password|secret|token)\s*[:=]\s*\S+"
)


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
    return {"type": name, "message": _REDACT.sub(r"\1=[REDACTED]", message)[:1000]}


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
    ) -> None:
        if root_concurrency < 1 or per_parent_concurrency < 1:
            raise ValueError("agent concurrency limits must be positive")
        if stop_grace < 0 or not math.isfinite(stop_grace):
            raise ValueError("stop_grace must be a non-negative finite number")
        if runner is not None and runner_factory is not None:
            raise ValueError("provide runner or runner_factory, not both")
        self.harness = harness
        self.repository = repository or harness.store.agents
        self._runner = runner
        self._runner_factory = runner_factory
        self._root_semaphore = asyncio.Semaphore(root_concurrency)
        self._per_parent_concurrency = per_parent_concurrency
        self._parent_semaphores: dict[str, asyncio.Semaphore] = {}
        self._tasks: dict[str, asyncio.Task[AgentRecord]] = {}
        self._children: dict[str, SessionHarness] = {}
        self._stop_grace = stop_grace
        self._closed = False
        _LIVE_SCHEDULERS.add(self)

    @property
    def live_agent_ids(self) -> frozenset[str]:
        return frozenset(self._tasks)

    def _owned(self, agent_id: str) -> AgentRecord:
        record = self.repository.get(agent_id)
        if record is None:
            raise AgentNotFound(f"Agent {agent_id} not found")
        if record.root_session_id != self.harness.root_session_id:
            raise AgentOwnershipError(
                f"Agent {agent_id} belongs to root session {record.root_session_id!r}"
            )
        return record

    @staticmethod
    def _validate_request(request: AgentRequest) -> AgentDefinition:
        if not isinstance(request.prompt, str) or not request.prompt:
            raise ValueError("Agent prompt must be non-empty")
        if not isinstance(request.description, str) or not request.description:
            raise ValueError("Agent description must be non-empty")
        built_in_definition = get_agent_by_type(request.agent_type)
        if built_in_definition is None:
            raise ValueError(f"Unknown agent type: {request.agent_type}")
        definition = request.definition or built_in_definition
        if definition.agent_type != request.agent_type:
            raise ValueError("Agent definition type does not match request")
        _execution_timeout(request)
        return definition

    async def spawn(self, request: AgentRequest) -> AgentRecord:
        if self._closed:
            raise RuntimeError("Agent scheduler is shut down")
        definition = self._validate_request(request)
        agent_id = f"agent-{request.agent_type.lower()}-{uuid.uuid4().hex[:12]}"
        parent_agent_id = request.parent_agent_id or self.harness.agent_id
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
                definition_snapshot=_definition_snapshot(definition, request),
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

    def _parent_semaphore(self, record: AgentRecord) -> asyncio.Semaphore:
        key = record.parent_agent_id or "<root>"
        return self._parent_semaphores.setdefault(
            key, asyncio.Semaphore(self._per_parent_concurrency)
        )

    def _runner_for(self, record: AgentRecord):
        if self._runner_factory is not None:
            return self._runner_factory(record)
        if self._runner is not None:
            return self._runner
        from agents.engine import AgentExecutor

        definition = get_agent_by_type(record.agent_type)
        if definition is None:
            raise ValueError(f"Unknown agent type: {record.agent_type}")
        return AgentExecutor(definition)

    async def _execute(self, record: AgentRecord, child: SessionHarness) -> AgentRecord:
        try:
            async with self._parent_semaphore(record):
                async with self._root_semaphore:
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
        except TimeoutError:
            return self._finish(record.agent_id, AgentStatus.TIMED_OUT)
        except asyncio.CancelledError:
            return self._finish(record.agent_id, AgentStatus.CANCELLED)
        except BaseException as exc:
            return self._finish(
                record.agent_id, AgentStatus.FAILED, error=_error_payload(exc)
            )

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
            status = AgentStatus.COMPLETED
        if status not in AGENT_TERMINAL_STATUSES:
            status = AgentStatus.COMPLETED
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
            await asyncio.shield(task)
        return self._owned(agent_id)

    def reconcile(self) -> list[AgentRecord]:
        live = {
            agent_id
            for scheduler in tuple(_LIVE_SCHEDULERS)
            if scheduler.harness.root_session_id == self.harness.root_session_id
            for agent_id in scheduler.live_agent_ids
        }
        return self.repository.reconcile(self.harness.root_session_id, live)

    async def shutdown(self) -> None:
        self._closed = True
        agent_ids = list(self._tasks)
        if agent_ids:
            await asyncio.gather(
                *(self.stop(agent_id) for agent_id in agent_ids),
                return_exceptions=True,
            )
        await asyncio.sleep(0)


__all__ = [
    "AgentNotFound",
    "AgentOwnershipError",
    "AgentScheduler",
    "AgentSchedulerError",
    "AgentWaitTimeout",
]
