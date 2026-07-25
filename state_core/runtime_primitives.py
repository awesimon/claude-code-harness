"""Durable records shared by the remaining Claude-compatible runtime services."""

from __future__ import annotations

import builtins
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

from .runtime_records import _as_utc, _json_object, _utc_now, sanitize_runtime_error
from .types import StateCoreError


class PermissionRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    SUPERSEDED = "superseded"


class ApprovedToolExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class HookInvocationStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ExecutionTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"


class TeamStatus(str, Enum):
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED = "closed"


class TeamMemberStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    IDLE = "idle"
    SHUTDOWN_REQUESTED = "shutdown_requested"
    STOPPED = "stopped"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class PermissionRuleScope(str, Enum):
    USER_SETTINGS = "userSettings"
    PROJECT_SETTINGS = "projectSettings"
    LOCAL_SETTINGS = "localSettings"
    SESSION = "session"
    CLI_ARG = "cliArg"


class PermissionRuleKind(str, Enum):
    RULE = "rule"
    DIRECTORY = "directory"
    MODE = "mode"


class HookAsyncMode(str, Enum):
    SYNC = "sync"
    ASYNC = "async"
    ASYNC_REWAKE = "asyncRewake"


class SkillActivationStatus(str, Enum):
    PREPARING = "preparing"
    ACTIVE = "active"


DURABLE_PERMISSION_RULE_SCOPES = frozenset(
    {
        PermissionRuleScope.USER_SETTINGS,
        PermissionRuleScope.PROJECT_SETTINGS,
        PermissionRuleScope.LOCAL_SETTINGS,
    }
)
SAFE_EXECUTION_ENVIRONMENT_KEYS = frozenset(
    {"HOME", "LANG", "LC_ALL", "LC_CTYPE", "PATH", "SHELL", "TERM", "TMPDIR", "USER"}
)
MAX_EXECUTION_OUTPUT_BYTES = 1_048_576
MAX_EXECUTION_READ_BYTES = 65_536
MAX_PERMISSION_INPUT_BYTES = 262_144
MAX_PERMISSION_UPDATE_COUNT = 128
MAX_PERMISSION_UPDATES_BYTES = 262_144
MAX_PERMISSION_SCOPE_SNAPSHOT_BYTES = 262_144
MAX_HOOK_RUNNER_CONFIG_BYTES = 65_536
MAX_HOOK_EVENT_ENVELOPE_BYTES = 262_144
MAX_HOOK_OUTCOME_BYTES = 131_072
MAX_TEAM_MESSAGE_BODY_BYTES = 262_144
MAX_SKILL_SNAPSHOT_BYTES = 524_288

_SENSITIVE_JSON_KEYS = frozenset(
    {
        "accesstoken", "apikey", "authorization", "authtoken", "bearertoken",
        "cookie", "cookies", "credential", "credentials", "idtoken", "password",
        "privatekey", "refreshtoken", "secret", "sessiontoken", "signingkey",
        "stderr", "stdin", "stdout", "token", "xapikey",
    }
)
_SAFE_DURABLE_HEADER_NAMES = frozenset(
    {"accept", "content-type", "user-agent", "x-request-id"}
)
_SECRET_TEXT = re.compile(
    r"(?i)(?:\b(?:bearer|basic)\s+\S+|"
    r"\b(?:api[_-]?key|password|secret|token)\s*[:=]\s*\S+)"
)


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _is_sensitive_json_key(normalized: str) -> bool:
    return (
        normalized in _SENSITIVE_JSON_KEYS
        or normalized.endswith(("password", "secret", "privatekey", "signingkey"))
        or normalized.startswith(("stdin", "stdout", "stderr"))
    )


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _restricted_json_object(
    value: Mapping[str, Any],
    path: str,
    *,
    max_bytes: int,
) -> Mapping[str, Any]:
    copied = _json_object(value, path)
    encoded = json.dumps(copied, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(f"{path} exceeds size limit of {max_bytes} bytes")

    def validate(item: Any, item_path: str) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                normalized = _normalized_key(key)
                if _is_sensitive_json_key(normalized):
                    raise ValueError(f"{item_path}.{key} contains a sensitive durable field")
                if normalized == "headers":
                    if not isinstance(nested, Mapping):
                        raise ValueError(f"{item_path}.{key} headers must be an object")
                    unsupported = {
                        header
                        for header in nested
                        if header.casefold() not in _SAFE_DURABLE_HEADER_NAMES
                    }
                    if unsupported:
                        raise ValueError(
                            f"{item_path}.{key} contains sensitive or unsupported headers"
                        )
                validate(nested, f"{item_path}.{key}")
        elif isinstance(item, list):
            for index, nested in enumerate(item):
                validate(nested, f"{item_path}[{index}]")
        elif isinstance(item, str) and _SECRET_TEXT.search(item):
            raise ValueError(f"{item_path} contains sensitive authorization text")

    validate(copied, path)
    return _deep_freeze(copied)


PERMISSION_REQUEST_TERMINAL_STATUSES = frozenset(
    set(PermissionRequestStatus) - {PermissionRequestStatus.PENDING}
)
APPROVED_EXECUTION_TERMINAL_STATUSES = frozenset(
    set(ApprovedToolExecutionStatus)
    - {ApprovedToolExecutionStatus.PENDING, ApprovedToolExecutionStatus.RUNNING}
)
HOOK_INVOCATION_TERMINAL_STATUSES = frozenset(
    set(HookInvocationStatus) - {HookInvocationStatus.QUEUED, HookInvocationStatus.RUNNING}
)
EXECUTION_TASK_TERMINAL_STATUSES = frozenset(
    set(ExecutionTaskStatus) - {ExecutionTaskStatus.PENDING, ExecutionTaskStatus.RUNNING}
)
TEAM_MEMBER_TERMINAL_STATUSES = frozenset(
    {TeamMemberStatus.STOPPED, TeamMemberStatus.FAILED, TeamMemberStatus.INTERRUPTED}
)


class InvalidRuntimePrimitiveTransition(StateCoreError):
    def __init__(self, record_type: str, current: Enum, target: Enum) -> None:
        self.record_type = record_type
        self.current = current
        self.target = target
        super().__init__(
            f"invalid {record_type} transition from {current.value!r} to {target.value!r}"
        )


def _validate_revision(revision: int) -> None:
    if revision < 0:
        raise ValueError("revision must be non-negative")


def _timestamps(record: Any, *names: str) -> None:
    for name in names:
        value = getattr(record, name)
        if value is not None:
            object.__setattr__(record, name, _as_utc(value))


@dataclass(frozen=True)
class PermissionRequestRecord:
    request_id: str
    root_session_id: str
    agent_id: str
    tool_call_id: str
    tool_name: str
    original_input: Mapping[str, Any]
    effective_input: Mapping[str, Any]
    input_digest: str
    reason: str
    permission_mode: str
    policy_revision: int
    idempotency_key: str
    suggestions: tuple[str, ...] = ()
    status: PermissionRequestStatus = PermissionRequestStatus.PENDING
    revision: int = 0
    deadline_at: datetime | None = None
    resolved_at: datetime | None = None
    actor: str | None = None
    decision_reason: str | None = None
    updated_input: Mapping[str, Any] | None = None
    permission_updates: tuple[Mapping[str, Any], ...] = ()
    interruption_reason: str | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        _validate_revision(self.revision)
        object.__setattr__(self, "original_input", _restricted_json_object(self.original_input, "original_input", max_bytes=MAX_PERMISSION_INPUT_BYTES))
        object.__setattr__(self, "effective_input", _restricted_json_object(self.effective_input, "effective_input", max_bytes=MAX_PERMISSION_INPUT_BYTES))
        if self.updated_input is not None:
            object.__setattr__(self, "updated_input", _restricted_json_object(self.updated_input, "updated_input", max_bytes=MAX_PERMISSION_INPUT_BYTES))
        object.__setattr__(self, "suggestions", tuple(self.suggestions))
        raw_updates = tuple(self.permission_updates)
        if len(raw_updates) > MAX_PERMISSION_UPDATE_COUNT:
            raise ValueError(
                f"permission_updates exceeds count limit of {MAX_PERMISSION_UPDATE_COUNT}"
            )
        detached_updates = [
            _json_object(item, f"permission_updates[{index}]")
            for index, item in enumerate(raw_updates)
        ]
        updates_size = len(
            json.dumps(
                detached_updates,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if updates_size > MAX_PERMISSION_UPDATES_BYTES:
            raise ValueError(
                f"permission_updates exceeds total size limit of {MAX_PERMISSION_UPDATES_BYTES} bytes"
            )
        object.__setattr__(self, "permission_updates", tuple(_restricted_json_object(item, "permission_update", max_bytes=MAX_PERMISSION_INPUT_BYTES) for item in detached_updates))
        _timestamps(self, "deadline_at", "resolved_at", "created_at", "updated_at")
        if self.status is PermissionRequestStatus.PENDING and self.resolved_at is not None:
            raise ValueError("pending permission requests cannot be resolved")
        if self.status in PERMISSION_REQUEST_TERMINAL_STATUSES and self.resolved_at is None:
            raise ValueError("terminal permission requests require resolved_at")


@dataclass(frozen=True)
class ApprovedToolExecutionRecord:
    execution_id: str
    request_id: str
    root_session_id: str
    request_revision: int
    policy_revision: int
    claim_owner: str
    tool_call_id: str
    idempotency_key: str
    status: ApprovedToolExecutionStatus = ApprovedToolExecutionStatus.PENDING
    revision: int = 0
    result_reference: str | None = None
    error: Mapping[str, Any] | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_revision(self.revision)
        if self.error is not None:
            object.__setattr__(self, "error", _deep_freeze(sanitize_runtime_error(self.error)))
        _timestamps(self, "created_at", "updated_at", "started_at", "finished_at")


@dataclass(frozen=True)
class PermissionRuleRecord:
    rule_id: str
    root_session_id: str
    kind: PermissionRuleKind | str
    scope: PermissionRuleScope
    source: str
    behavior: str | None = None
    rule: str | None = None
    directory: str | None = None
    mode: str | None = None
    revision: int = 0
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_revision(self.revision)
        try:
            kind = PermissionRuleKind(self.kind)
        except ValueError as exc:
            raise ValueError(f"unsupported permission rule kind {self.kind!r}") from exc
        object.__setattr__(self, "kind", kind)
        if kind is PermissionRuleKind.RULE:
            if not self.rule or self.behavior not in {"allow", "deny", "ask"}:
                raise ValueError("rule permission records require rule and allow/deny/ask behavior")
            if self.directory is not None or self.mode is not None:
                raise ValueError("rule permission records cannot carry directory or mode")
        elif kind is PermissionRuleKind.DIRECTORY:
            if not self.directory or any(value is not None for value in (self.rule, self.behavior, self.mode)):
                raise ValueError("directory permission records require only directory")
        elif not self.mode or any(value is not None for value in (self.rule, self.behavior, self.directory)):
            raise ValueError("mode permission records require only mode")
        _timestamps(self, "created_at", "updated_at", "revoked_at")


@dataclass(frozen=True)
class HookDefinitionRecord:
    definition_id: str
    root_session_id: str
    event: str
    matcher: str | None
    runner_kind: str
    runner_config: Mapping[str, Any]
    source: str
    order: int = 0
    timeout_ms: int = 60_000
    once: bool = False
    async_mode: HookAsyncMode | str = HookAsyncMode.SYNC
    enabled: bool = True
    idempotent: bool = False
    config_revision: int = 0
    revision: int = 0
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        _validate_revision(self.revision)
        _validate_revision(self.config_revision)
        if self.timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        try:
            object.__setattr__(self, "async_mode", HookAsyncMode(self.async_mode))
        except ValueError as exc:
            raise ValueError(f"unsupported hook async mode {self.async_mode!r}") from exc
        object.__setattr__(self, "runner_config", _restricted_json_object(self.runner_config, "runner_config", max_bytes=MAX_HOOK_RUNNER_CONFIG_BYTES))
        _timestamps(self, "created_at", "updated_at")


@dataclass(frozen=True)
class HookInvocationRecord:
    invocation_id: str
    root_session_id: str
    definition_id: str
    definition_revision: int
    event: str
    event_envelope: Mapping[str, Any]
    correlation_id: str
    idempotency_key: str
    agent_id: str | None = None
    retry_of_invocation_id: str | None = None
    status: HookInvocationStatus = HookInvocationStatus.QUEUED
    revision: int = 0
    attempt: int = 1
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    deadline_at: datetime | None = None
    outcome: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None
    duration_ms: int | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_revision(self.revision)
        object.__setattr__(self, "event_envelope", _restricted_json_object(self.event_envelope, "event_envelope", max_bytes=MAX_HOOK_EVENT_ENVELOPE_BYTES))
        if self.outcome is not None:
            object.__setattr__(self, "outcome", _restricted_json_object(self.outcome, "outcome", max_bytes=MAX_HOOK_OUTCOME_BYTES))
        if self.error is not None:
            object.__setattr__(self, "error", _deep_freeze(sanitize_runtime_error(self.error)))
        _timestamps(self, "lease_expires_at", "deadline_at", "created_at", "updated_at", "started_at", "finished_at")


@dataclass(frozen=True)
class ExecutionTaskRecord:
    task_id: str
    root_session_id: str
    agent_id: str
    kind: str
    command: str
    description: str
    canonical_cwd: str
    output_artifact_id: str
    timeout_ms: int
    safe_environment: Mapping[str, Any] = field(default_factory=dict)
    status: ExecutionTaskStatus = ExecutionTaskStatus.PENDING
    revision: int = 0
    exit_code: int | None = None
    termination_reason: str | None = None
    output_byte_count: int = 0
    last_readable_cursor: int = 0
    process_owner_token: str | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_revision(self.revision)
        if self.kind != "shell":
            raise ValueError("only shell execution tasks are supported")
        if self.timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if self.output_byte_count < 0 or self.last_readable_cursor < 0:
            raise ValueError("output cursors must be non-negative")
        environment = _json_object(self.safe_environment, "safe_environment")
        unsupported = set(environment).difference(SAFE_EXECUTION_ENVIRONMENT_KEYS)
        if unsupported or any(type(value) is not str for value in environment.values()):
            raise ValueError(
                f"safe environment contains unsupported keys or values: {sorted(unsupported)!r}"
            )
        object.__setattr__(self, "safe_environment", _deep_freeze(environment))
        _timestamps(self, "created_at", "updated_at", "started_at", "finished_at")


@dataclass(frozen=True)
class ExecutionOutputChunk:
    data: bytes
    next_cursor: int
    total_bytes: int


@dataclass(frozen=True)
class TeamRecord:
    team_id: str
    root_session_id: str
    name: str
    lead_agent_id: str
    task_list_id: str
    status: TeamStatus = TeamStatus.ACTIVE
    revision: int = 0
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    closed_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_revision(self.revision)
        _timestamps(self, "created_at", "updated_at", "closed_at")


@dataclass(frozen=True)
class TeamMemberRecord:
    member_id: str
    team_id: str
    root_session_id: str
    agent_id: str
    name: str
    agent_type: str
    role: str
    status: TeamMemberStatus = TeamMemberStatus.STARTING
    assigned_task_ids: tuple[str, ...] = ()
    mailbox_cursor: int = 0
    shutdown_request_id: str | None = None
    owner_token: str | None = None
    lease_expires_at: datetime | None = None
    revision: int = 0
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    stopped_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_revision(self.revision)
        object.__setattr__(self, "assigned_task_ids", tuple(self.assigned_task_ids))
        if self.mailbox_cursor < 0:
            raise ValueError("mailbox_cursor must be non-negative")
        _timestamps(self, "lease_expires_at", "created_at", "updated_at", "stopped_at")
        if self.status in TEAM_MEMBER_TERMINAL_STATUSES and self.stopped_at is None:
            raise ValueError("terminal team members require stopped_at")
        if self.status not in TEAM_MEMBER_TERMINAL_STATUSES and self.stopped_at is not None:
            raise ValueError("active team members cannot carry stopped_at")


@dataclass(frozen=True)
class TeamMessageRecord:
    message_id: str
    team_id: str
    root_session_id: str
    sender_member_id: str | None
    recipient_member_id: str | None
    message_type: str
    body: Mapping[str, Any]
    request_correlation_id: str | None = None
    sequence: int = 0
    delivery_state: str = "pending"
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        object.__setattr__(self, "body", _restricted_json_object(self.body, "team message body", max_bytes=MAX_TEAM_MESSAGE_BODY_BYTES))
        _timestamps(self, "created_at")


@dataclass(frozen=True)
class SkillActivationRecord:
    activation_id: str
    root_session_id: str
    agent_id: str
    skill_name: str
    skill_digest: str
    snapshot: Mapping[str, Any]
    registered_hook_ids: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    status: SkillActivationStatus = SkillActivationStatus.PREPARING
    revision: int = 0
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        _validate_revision(self.revision)
        object.__setattr__(self, "snapshot", _restricted_json_object(self.snapshot, "skill snapshot", max_bytes=MAX_SKILL_SNAPSHOT_BYTES))
        object.__setattr__(self, "registered_hook_ids", tuple(self.registered_hook_ids))
        object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools))
        _timestamps(self, "created_at", "updated_at")


@runtime_checkable
class DurableRecordRepository(Protocol):
    def get(self, record_id: str) -> Any | None: ...
    def list(self, root_session_id: str, **filters: Any) -> builtins.list[Any]: ...
