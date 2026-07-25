"""Durable, storage-neutral records for the agent harness runtime."""
# ruff: noqa: E501

from __future__ import annotations

import builtins
import math
import re
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


def _trace_duration_ms(started_at: datetime, finished_at: datetime) -> int:
    """Return the shared, truncating duration representation for trace spans."""

    return int((finished_at - started_at).total_seconds() * 1000)


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


_MAX_RUNTIME_ERROR_DEPTH = 16
_MAX_RUNTIME_ERROR_ITEMS = 64
_MAX_RUNTIME_ERROR_NODES = 256
_MAX_RUNTIME_ERROR_SCAN = 4096
_MAX_RUNTIME_ERROR_STRING = 2000
_MAX_RUNTIME_ERROR_TEXT = 16000
_TRUNCATED_RUNTIME_ERROR = "[TRUNCATED]"
_NON_FINITE_RUNTIME_ERROR = "[NON_FINITE]"
_UNSUPPORTED_RUNTIME_ERROR = "[UNSUPPORTED]"
_UNSANITIZABLE_RUNTIME_ERROR = "[UNSANITIZABLE]"
_EXACT_SENSITIVE_RUNTIME_ERROR_KEYS = frozenset(
    {
        "authorization",
        "proxyauthorization",
        "cookie",
        "cookies",
        "setcookie",
        "apikey",
        "xapikey",
        "accesstoken",
        "refreshtoken",
        "password",
        "secret",
        "token",
        "credentials",
        "clientsecret",
        "secretaccesskey",
        "privatekey",
        "signingkey",
        "bearertoken",
        "idtoken",
        "sessiontoken",
    }
)
_RUNTIME_HEADER_CONTAINER_KEYS = frozenset(
    {"headers", "requestheaders", "responseheaders"}
)
_AUTHORIZATION_VALUE = re.compile(
    r'''(?i)\b(authorization|proxy[-_ ]authorization)(?:\\*["'])?\s*[:=]\s*'''
    r'''(?:(?:\\*")(?:\\[\s\S]?|[^"\\])*(?:(?:\\*")|$)|'''
    r'''(?:\\*')(?:\\[\s\S]?|[^'\\])*(?:(?:\\*')|$)|'''
    r"(?:(?:bearer|basic)\s+)?[^\s,;&]+)"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|x[-_]?api[-_]?key|password|passwd|secret|"
    r"client[_-]?secret|access[_-]?token|refresh[_-]?token|session[_-]?token|"
    r'''id[_-]?token|auth[_-]?token|token|credentials?)(?:\\*["'])?\s*[:=]\s*'''
    r'''(?:(?:\\*")(?:\\[\s\S]?|[^"\\])*(?:(?:\\*")|$)|'''
    r'''(?:\\*')(?:\\[\s\S]?|[^'\\])*(?:(?:\\*')|$)|[^\s,;&]+)'''
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[^\s,;&]+")


def _normalize_runtime_error_key(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def _is_sensitive_runtime_error_key(key: str) -> bool:
    tokenized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key).casefold()
    tokens = tuple(token for token in re.split(r"[^a-z0-9]+", tokenized) if token)
    normalized = "".join(tokens)
    if normalized in (
        _EXACT_SENSITIVE_RUNTIME_ERROR_KEYS | _RUNTIME_HEADER_CONTAINER_KEYS
    ):
        return True
    if {"password", "passwd", "secret", "credential", "authorization", "cookie"}.intersection(tokens):
        return True
    if ("private" in tokens or "signing" in tokens) and "key" in tokens:
        return True
    return "token" in tokens and bool(
        {"auth", "access", "refresh", "bearer", "id", "session", "oauth"}.intersection(tokens)
    )


def _sanitize_runtime_error_string(value: str) -> str:
    sanitized = _AUTHORIZATION_VALUE.sub(
        r"\1=[REDACTED]", value[:_MAX_RUNTIME_ERROR_SCAN]
    )
    sanitized = _CREDENTIAL_ASSIGNMENT.sub(r"\1=[REDACTED]", sanitized)
    sanitized = _BEARER_VALUE.sub("Bearer [REDACTED]", sanitized)
    return sanitized[:_MAX_RUNTIME_ERROR_STRING]


def _json_encoded_character_size(character: str) -> int:
    codepoint = ord(character)
    if character in {'"', "\\"}:
        return 2
    if codepoint < 0x20:
        return 6
    if codepoint <= 0x7F:
        return 1
    if codepoint <= 0xFFFF:
        return 6
    return 12


def sanitize_runtime_error(value: Any) -> dict[str, Any] | None:
    """Detach, redact, and bound a durable agent or trace error tree."""

    if value is None:
        return None
    root: Mapping[Any, Any] = value if isinstance(value, Mapping) else {"value": value}

    nodes_remaining = _MAX_RUNTIME_ERROR_NODES
    text_remaining = _MAX_RUNTIME_ERROR_TEXT

    def bounded_text(text: str) -> tuple[str, bool]:
        nonlocal text_remaining
        if not text:
            return "", False
        if text_remaining <= 0:
            return _TRUNCATED_RUNTIME_ERROR, True
        bounded_characters: list[str] = []
        for character in text[:_MAX_RUNTIME_ERROR_STRING]:
            encoded_size = _json_encoded_character_size(character)
            if encoded_size > text_remaining:
                break
            bounded_characters.append(character)
            text_remaining -= encoded_size
        if not bounded_characters:
            return _TRUNCATED_RUNTIME_ERROR, True
        bounded = "".join(bounded_characters)
        return bounded, len(bounded) < len(text)

    def consume_literal(text: str) -> bool:
        nonlocal text_remaining
        if len(text) > text_remaining:
            return False
        text_remaining -= len(text)
        return True

    def add_mapping_truncation(target: dict[str, Any]) -> None:
        if _TRUNCATED_RUNTIME_ERROR in target.values():
            return
        key = "__truncated__"
        while key in target:
            key = f"_{key}"
        target[key] = _TRUNCATED_RUNTIME_ERROR

    def sanitize(item: Any, path: str, depth: int) -> Any:
        nonlocal nodes_remaining
        if nodes_remaining <= 0:
            return _TRUNCATED_RUNTIME_ERROR
        nodes_remaining -= 1

        if item is None:
            return item if consume_literal("null") else _TRUNCATED_RUNTIME_ERROR
        if type(item) is bool:
            literal = "true" if item else "false"
            return item if consume_literal(literal) else _TRUNCATED_RUNTIME_ERROR
        if type(item) is int:
            if item.bit_length() > 4096:
                return _TRUNCATED_RUNTIME_ERROR
            literal = str(item)
            return item if consume_literal(literal) else _TRUNCATED_RUNTIME_ERROR
        if type(item) is float:
            if not math.isfinite(item):
                return _NON_FINITE_RUNTIME_ERROR
            return item if consume_literal(repr(item)) else _TRUNCATED_RUNTIME_ERROR
        if type(item) is str:
            sanitized, _ = bounded_text(_sanitize_runtime_error_string(item))
            return sanitized
        if isinstance(item, Mapping):
            if depth >= _MAX_RUNTIME_ERROR_DEPTH:
                return _TRUNCATED_RUNTIME_ERROR
            sanitized: dict[str, Any] = {}
            for index, (key, nested) in enumerate(item.items()):
                if (
                    index >= _MAX_RUNTIME_ERROR_ITEMS
                    or nodes_remaining <= 0
                    or text_remaining <= 0
                ):
                    add_mapping_truncation(sanitized)
                    break
                if type(key) is not str:
                    add_mapping_truncation(sanitized)
                    sanitized["__unsupported_key__"] = _UNSUPPORTED_RUNTIME_ERROR
                    continue
                bounded_key, key_was_truncated = bounded_text(key)
                nested_path = f"{path}.{key[:80]}"
                key_for_scan = key[:_MAX_RUNTIME_ERROR_SCAN]
                if len(key) > _MAX_RUNTIME_ERROR_SCAN or _is_sensitive_runtime_error_key(
                    key_for_scan
                ):
                    redacted, _ = bounded_text("[REDACTED]")
                    sanitized[bounded_key] = redacted
                else:
                    sanitized[bounded_key] = sanitize(
                        nested, nested_path, depth + 1
                    )
                if key_was_truncated and text_remaining <= 0:
                    add_mapping_truncation(sanitized)
                    break
            return sanitized
        if isinstance(item, (list, tuple)):
            if depth >= _MAX_RUNTIME_ERROR_DEPTH:
                return _TRUNCATED_RUNTIME_ERROR
            sanitized_list: list[Any] = []
            for index, nested in enumerate(item):
                if (
                    index >= _MAX_RUNTIME_ERROR_ITEMS
                    or nodes_remaining <= 0
                    or text_remaining <= 0
                ):
                    sanitized_list.append(_TRUNCATED_RUNTIME_ERROR)
                    break
                sanitized_list.append(
                    sanitize(nested, f"{path}[{index}]", depth + 1)
                )
            return sanitized_list
        return _UNSUPPORTED_RUNTIME_ERROR

    try:
        sanitized = sanitize(root, "error", 0)
    except Exception:
        return {"error": _UNSANITIZABLE_RUNTIME_ERROR}
    return (
        sanitized
        if isinstance(sanitized, dict)
        else {"error": _UNSANITIZABLE_RUNTIME_ERROR}
    )


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    ORPHANED = "orphaned"
    BUDGET_EXHAUSTED = "budget_exhausted"


class AgentTerminationReason(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    ORPHANED = "orphaned"
    BUDGET_EXHAUSTED = "budget_exhausted"


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
        AgentStatus.BUDGET_EXHAUSTED,
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


class InvalidAgentParent(StateCoreError):
    """Raised when a child does not reference a running same-root parent."""

    def __init__(self, parent_agent_id: str) -> None:
        self.parent_agent_id = parent_agent_id
        super().__init__(
            f"agent parent {parent_agent_id!r} must be running in the same durable root"
        )


class InvalidWorktreeTransition(StateCoreError):
    """Raised when a worktree lifecycle transition is not allowed."""

    def __init__(self, current: WorktreeStatus, target: WorktreeStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"invalid worktree transition from {current.value!r} to {target.value!r}")


class InvalidTraceSpanTransition(StateCoreError):
    """Raised when a trace span lifecycle transition is not allowed."""

    def __init__(self, current: TraceSpanStatus, target: TraceSpanStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"invalid trace span transition from {current.value!r} to {target.value!r}")


def ensure_agent_transition(current: AgentStatus, target: AgentStatus) -> None:
    if target not in _AGENT_TRANSITIONS[current]:
        raise InvalidAgentTransition(current, target)


def ensure_worktree_transition(current: WorktreeStatus, target: WorktreeStatus) -> None:
    if target is not current and target not in _WORKTREE_TRANSITIONS[current]:
        raise InvalidWorktreeTransition(current, target)


def ensure_trace_span_transition(current: TraceSpanStatus, target: TraceSpanStatus) -> None:
    if current is not TraceSpanStatus.RUNNING or target not in TRACE_TERMINAL_STATUSES:
        raise InvalidTraceSpanTransition(current, target)


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
    error: Any = None
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
        object.__setattr__(self, "error", sanitize_runtime_error(self.error))
        object.__setattr__(self, "output", _json_copy(self.output, "output"))
        object.__setattr__(self, "created_at", _as_utc(self.created_at))
        object.__setattr__(self, "updated_at", _as_utc(self.updated_at))
        if self.started_at is not None:
            object.__setattr__(self, "started_at", _as_utc(self.started_at))
        if self.finished_at is not None:
            object.__setattr__(self, "finished_at", _as_utc(self.finished_at))
        expected_reason = (
            AgentTerminationReason(self.status.value) if self.status in AGENT_TERMINAL_STATUSES else None
        )
        if expected_reason is None:
            if self.termination_reason is not None or self.finished_at is not None or self.output is not None:
                raise ValueError("nonterminal agents cannot carry terminal fields")
        elif self.termination_reason is not expected_reason:
            raise ValueError("termination_reason must match terminal agent status")
        elif self.finished_at is None:
            raise ValueError("terminal agents require finished_at")


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
    duration_ms: int | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    error: Any = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        object.__setattr__(self, "usage", _json_object(self.usage, "usage"))
        object.__setattr__(self, "error", sanitize_runtime_error(self.error))
        object.__setattr__(self, "started_at", _as_utc(self.started_at))
        object.__setattr__(self, "created_at", _as_utc(self.created_at))
        object.__setattr__(self, "updated_at", _as_utc(self.updated_at))
        if self.status is TraceSpanStatus.RUNNING and self.finished_at is not None:
            raise ValueError("running trace spans cannot have finished_at")
        if self.status in TRACE_TERMINAL_STATUSES and self.finished_at is None:
            raise ValueError("terminal trace spans require finished_at")
        if self.finished_at is not None:
            object.__setattr__(self, "finished_at", _as_utc(self.finished_at))
            if self.finished_at < self.started_at:
                raise ValueError("finished_at must not precede started_at")
            expected_duration = _trace_duration_ms(self.started_at, self.finished_at)
            if self.duration_ms is not None and self.duration_ms != expected_duration:
                raise ValueError("duration_ms must match started_at and finished_at")
            object.__setattr__(self, "duration_ms", expected_duration)
        elif self.duration_ms is not None:
            raise ValueError("duration_ms is only available after a trace span finishes")


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
        if self.status is not WorktreeStatus.REMOVED and self.removed_at is not None:
            raise ValueError("removed_at is only valid for removed worktrees")
        if self.status is WorktreeStatus.REMOVED and self.removed_at is None:
            raise ValueError("removed worktrees require removed_at")


@runtime_checkable
class AgentRepository(Protocol):
    def create(self, record: AgentRecord) -> AgentRecord: ...
    def create_with_parent_guard(self, record: AgentRecord) -> AgentRecord: ...
    def get(self, agent_id: str) -> AgentRecord | None: ...
    def list_all(self) -> builtins.list[AgentRecord]: ...
    def list(
        self,
        root_session_id: str,
        *,
        parent_agent_id: str | None = None,
        status: AgentStatus | None = None,
        is_background: bool | None = None,
    ) -> builtins.list[AgentRecord]: ...
    def transition(
        self,
        agent_id: str,
        status: AgentStatus,
        expected_revision: int,
        *,
        termination_reason: AgentTerminationReason | None = None,
        output: Any = None,
        usage: Mapping[str, Any] | None = None,
        error: Any = None,
    ) -> AgentRecord: ...
    def reconcile(self, root_session_id: str, live_agent_ids: set[str]) -> builtins.list[AgentRecord]: ...


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
    ) -> builtins.list[TraceSpanRecord]: ...
    def interrupt_open(self, root_session_id: str) -> builtins.list[TraceSpanRecord]: ...


@runtime_checkable
class WorktreeRepository(Protocol):
    def create(self, record: WorktreeRecord) -> WorktreeRecord: ...
    def get(self, worktree_id: str) -> WorktreeRecord | None: ...
    def list(
        self, root_session_id: str, *, agent_id: str | None = None, status: WorktreeStatus | None = None
    ) -> builtins.list[WorktreeRecord]: ...
    def update(
        self, worktree_id: str, expected_revision: int, **changes: Any
    ) -> WorktreeRecord: ...
