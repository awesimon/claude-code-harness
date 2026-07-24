"""Durable, storage-neutral records for the agent harness runtime."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

from .types import StateCoreError


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _json_copy(value: Any, path: str = "$") -> Any:
    """Validate and detach a JSON-compatible tree."""

    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite JSON numbers")
        return value
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{path} mapping keys must be strings")
            copied[key] = _json_copy(item, f"{path}.{key}")
        return copied
    if isinstance(value, (list, tuple)):
        return [_json_copy(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(f"{path} contains unsupported JSON value {type(value).__name__}")


def _json_object(value: Mapping[str, Any], path: str) -> dict[str, Any]:
    copied = _json_copy(value, path)
    if not isinstance(copied, dict):
        raise TypeError(f"{path} must be a JSON object")
    return copied


def _optional_json_object(value: Mapping[str, Any] | None, path: str) -> dict[str, Any] | None:
    return _json_object(value, path) if value is not None else None


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    ORPHANED = "orphaned"


class AgentTerminationReason(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    ORPHANED = "orphaned"


class TraceSpanStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"


class WorktreeStatus(str, Enum):
    PENDING = "pending"
    CREATING = "creating"
    READY = "ready"
    FAILED = "failed"
    REMOVING = "removing"
    REMOVED = "removed"
    ORPHANED = "orphaned"


AGENT_TERMINAL_STATUSES = frozenset(
    {
        AgentStatus.COMPLETED,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
        AgentStatus.TIMED_OUT,
        AgentStatus.INTERRUPTED,
        AgentStatus.ORPHANED,
    }
)
TRACE_TERMINAL_STATUSES = frozenset(set(TraceSpanStatus) - {TraceSpanStatus.RUNNING})

_AGENT_TRANSITIONS: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.PENDING: frozenset(
        {
            AgentStatus.RUNNING,
            AgentStatus.FAILED,
            AgentStatus.CANCELLED,
            AgentStatus.INTERRUPTED,
            AgentStatus.ORPHANED,
        }
    ),
    AgentStatus.RUNNING: AGENT_TERMINAL_STATUSES,
    **{status: frozenset() for status in AGENT_TERMINAL_STATUSES},
}

_WORKTREE_TRANSITIONS: dict[WorktreeStatus, frozenset[WorktreeStatus]] = {
    WorktreeStatus.PENDING: frozenset(
        {WorktreeStatus.CREATING, WorktreeStatus.FAILED, WorktreeStatus.ORPHANED}
    ),
    WorktreeStatus.CREATING: frozenset(
        {WorktreeStatus.READY, WorktreeStatus.FAILED, WorktreeStatus.ORPHANED}
    ),
    WorktreeStatus.READY: frozenset(
        {WorktreeStatus.REMOVING, WorktreeStatus.ORPHANED, WorktreeStatus.FAILED}
    ),
    WorktreeStatus.FAILED: frozenset({WorktreeStatus.REMOVING, WorktreeStatus.ORPHANED}),
    WorktreeStatus.ORPHANED: frozenset({WorktreeStatus.REMOVING}),
    WorktreeStatus.REMOVING: frozenset({WorktreeStatus.REMOVED, WorktreeStatus.FAILED}),
    WorktreeStatus.REMOVED: frozenset(),
}


class RuntimeRecordRevisionConflict(StateCoreError):
    """Raised when a runtime-record update uses a stale revision."""

    def __init__(self, record_type: str, record_id: str, expected: int | None, actual: int | None):
        self.record_type = record_type
        self.record_id = record_id
        self.expected_revision = expected
        self.actual_revision = actual
        super().__init__(
            f"revision conflict for {record_type} {record_id!r} "
            f"(expected {expected}, found {actual})"
        )


class InvalidAgentTransition(StateCoreError):
    """Raised when an agent lifecycle transition is not allowed."""

    def __init__(self, current: AgentStatus, target: AgentStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"invalid agent transition from {current.value!r} to {target.value!r}")


class InvalidWorktreeTransition(StateCoreError):
    """Raised when a worktree lifecycle transition is not allowed."""

    def __init__(self, current: WorktreeStatus, target: WorktreeStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"invalid worktree transition from {current.value!r} to {target.value!r}")


def ensure_agent_transition(current: AgentStatus, target: AgentStatus) -> None:
    if target not in _AGENT_TRANSITIONS[current]:
        raise InvalidAgentTransition(current, target)


def ensure_worktree_transition(current: WorktreeStatus, target: WorktreeStatus) -> None:
    if target is not current and target not in _WORKTREE_TRANSITIONS[current]:
        raise InvalidWorktreeTransition(current, target)


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    root_session_id: str
    agent_type: str
    prompt: str
    description: str
    is_background: bool
    effective_cwd: str
    definition_snapshot: Mapping[str, Any]
    parent_agent_id: str | None = None
    status: AgentStatus = AgentStatus.PENDING
    revision: int = 0
    usage: Mapping[str, Any] = field(default_factory=dict)
    termination_reason: AgentTerminationReason | None = None
    error: Mapping[str, Any] | None = None
    output: Any = None
    worktree_id: str | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        object.__setattr__(self, "definition_snapshot", _json_object(self.definition_snapshot, "definition"))
        object.__setattr__(self, "usage", _json_object(self.usage, "usage"))
        object.__setattr__(self, "error", _optional_json_object(self.error, "error"))
        object.__setattr__(self, "output", _json_copy(self.output, "output"))
        object.__setattr__(self, "created_at", _as_utc(self.created_at))
        object.__setattr__(self, "updated_at", _as_utc(self.updated_at))
        if self.started_at is not None:
            object.__setattr__(self, "started_at", _as_utc(self.started_at))
        if self.finished_at is not None:
            object.__setattr__(self, "finished_at", _as_utc(self.finished_at))


@dataclass(frozen=True)
class RuntimeMetadataRecord:
    root_session_id: str
    namespace: str
    snapshot: Mapping[str, Any]
    revision: int = 0
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        object.__setattr__(self, "snapshot", _json_object(self.snapshot, "snapshot"))
        object.__setattr__(self, "created_at", _as_utc(self.created_at))
        object.__setattr__(self, "updated_at", _as_utc(self.updated_at))


@dataclass(frozen=True)
class TraceSpanRecord:
    span_id: str
    root_session_id: str
    kind: str
    name: str
    agent_id: str | None = None
    parent_span_id: str | None = None
    status: TraceSpanStatus = TraceSpanStatus.RUNNING
    revision: int = 0
    started_at: datetime = field(default_factory=_utc_now)
    finished_at: datetime | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        object.__setattr__(self, "usage", _json_object(self.usage, "usage"))
        object.__setattr__(self, "error", _optional_json_object(self.error, "error"))
        object.__setattr__(self, "started_at", _as_utc(self.started_at))
        object.__setattr__(self, "created_at", _as_utc(self.created_at))
        object.__setattr__(self, "updated_at", _as_utc(self.updated_at))
        if self.finished_at is not None:
            object.__setattr__(self, "finished_at", _as_utc(self.finished_at))


@dataclass(frozen=True)
class WorktreeRecord:
    worktree_id: str
    root_session_id: str
    repository_root: str
    canonical_path: str
    branch: str
    base_commit: str
    agent_id: str | None = None
    status: WorktreeStatus = WorktreeStatus.PENDING
    revision: int = 0
    details: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    removed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        object.__setattr__(self, "details", _json_object(self.details, "details"))
        object.__setattr__(self, "created_at", _as_utc(self.created_at))
        object.__setattr__(self, "updated_at", _as_utc(self.updated_at))
        if self.removed_at is not None:
            object.__setattr__(self, "removed_at", _as_utc(self.removed_at))


@runtime_checkable
class AgentRepository(Protocol):
    def create(self, record: AgentRecord) -> AgentRecord: ...
    def get(self, agent_id: str) -> AgentRecord | None: ...
    def list(
        self,
        root_session_id: str,
        *,
        parent_agent_id: str | None = None,
        status: AgentStatus | None = None,
        is_background: bool | None = None,
    ) -> list[AgentRecord]: ...
    def transition(
        self,
        agent_id: str,
        status: AgentStatus,
        expected_revision: int,
        *,
        termination_reason: AgentTerminationReason | None = None,
        output: Any = None,
        usage: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> AgentRecord: ...
    def reconcile(self, root_session_id: str, live_agent_ids: set[str]) -> list[AgentRecord]: ...


@runtime_checkable
class RuntimeMetadataRepository(Protocol):
    def get(self, root_session_id: str, namespace: str) -> RuntimeMetadataRecord | None: ...
    def put(
        self,
        root_session_id: str,
        namespace: str,
        snapshot: Mapping[str, Any],
        expected_revision: int | None = None,
    ) -> RuntimeMetadataRecord: ...


@runtime_checkable
class TraceSpanRepository(Protocol):
    def start(self, record: TraceSpanRecord) -> TraceSpanRecord: ...
    def get(self, span_id: str) -> TraceSpanRecord | None: ...
    def finish(
        self,
        span_id: str,
        status: TraceSpanStatus,
        expected_revision: int,
        *,
        usage: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> TraceSpanRecord: ...
    def list(
        self, root_session_id: str, *, agent_id: str | None = None, status: TraceSpanStatus | None = None
    ) -> list[TraceSpanRecord]: ...
    def interrupt_open(self, root_session_id: str) -> list[TraceSpanRecord]: ...


@runtime_checkable
class WorktreeRepository(Protocol):
    def create(self, record: WorktreeRecord) -> WorktreeRecord: ...
    def get(self, worktree_id: str) -> WorktreeRecord | None: ...
    def list(
        self, root_session_id: str, *, agent_id: str | None = None, status: WorktreeStatus | None = None
    ) -> list[WorktreeRecord]: ...
    def update(
        self, worktree_id: str, expected_revision: int, **changes: Any
    ) -> WorktreeRecord: ...
