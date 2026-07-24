"""Authoritative, persistence-safe types for durable session state.

Python attributes use snake_case. ``to_dict`` and ``from_dict`` define the
stable Node-facing wire contract and use camelCase for compound field names.
Persisted decoders require the complete emitted shape; compatibility defaults
belong in explicit constructors or migration code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Mapping, cast


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class PlanState(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"


class SessionHealth(str, Enum):
    READY = "ready"
    RECOVERY_REQUIRED = "recovery_required"


class TaskMode(str, Enum):
    TASK_V2 = "task_v2"
    TODO_V1 = "todo_v1"


class EventType(str, Enum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    PLAN_TRANSITION = "plan_transition"
    TASK_MUTATION = "task_mutation"
    TODO_REPLACED = "todo_replaced"
    AGENT_LIFECYCLE = "agent_lifecycle"
    CHECKPOINT = "checkpoint"
    EXECUTION_INTERRUPTED = "execution_interrupted"


class StateCoreError(Exception):
    """Base class for state-core domain failures."""


class RevisionConflict(StateCoreError):
    """Raised when a commit is based on a stale session revision."""

    def __init__(
        self,
        session_id: str,
        expected_revision: int | None = None,
        actual_revision: int | None = None,
    ) -> None:
        self.session_id = session_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        detail = ""
        if expected_revision is not None or actual_revision is not None:
            detail = f" (expected {expected_revision}, found {actual_revision})"
        super().__init__(f"revision conflict for session {session_id!r}{detail}")


class InvalidTransition(StateCoreError):
    """Raised when a domain state machine transition is not allowed."""

    def __init__(self, current: Enum, target: Enum, domain: str = "plan") -> None:
        self.current = current
        self.target = target
        self.domain = domain
        super().__init__(f"invalid {domain} transition from {current.value!r} to {target.value!r}")


class InvalidTaskDependency(StateCoreError):
    """Raised when a task dependency would violate task-list invariants."""


def _copy_json(value: Any, path: str = "$") -> Any:
    """Validate and detach a JSON tree without coercing values or mapping keys."""

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
            copied[key] = _copy_json(item, f"{path}.{key}")
        return copied
    if isinstance(value, (list, tuple)):
        return [_copy_json(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(f"{path} contains unsupported JSON value {type(value).__name__}")


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be a mapping")
    for key in value:
        if type(key) is not str:
            raise TypeError(f"{path} mapping keys must be strings")
    return cast(Mapping[str, Any], value)


def _require_json_object(value: Any, path: str) -> dict[str, Any]:
    copied = _copy_json(value, path)
    if not isinstance(copied, dict):
        raise TypeError(f"{path} must be a JSON object")
    return copied


def _require_str(value: Any, path: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{path} must be a string")
    return value


def _require_optional_str(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, path)


def _require_bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{path} must be a boolean")
    return value


def _require_int(value: Any, path: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise TypeError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{path} must be at least {minimum}")
    return value


def _require_optional_int(
    value: Any,
    path: str,
    *,
    minimum: int | None = None,
) -> int | None:
    if value is None:
        return None
    return _require_int(value, path, minimum=minimum)


def _require_string_sequence(value: Any, path: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{path} must be a sequence")
    return [_require_str(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _require_optional_status(value: Any, path: str) -> TaskStatus | None:
    if value is None:
        return None
    return TaskStatus(_require_str(value, path))


def _normalize_timestamp(value: datetime, path: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{path} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{path} must be timezone-aware")
    normalized = value.astimezone(timezone.utc)
    milliseconds = (normalized.microsecond // 1000) * 1000
    return normalized.replace(microsecond=milliseconds)


def _timestamp_to_wire(value: datetime, path: str) -> str:
    normalized = _normalize_timestamp(value, path)
    return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _timestamp_from_wire(value: Any, path: str) -> datetime:
    raw = _require_str(value, path)
    normalized_raw = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized_raw)
    except ValueError as exc:
        raise ValueError(f"{path} must be an ISO 8601 timestamp") from exc
    return _normalize_timestamp(parsed, path)


def _optional_timestamp_to_wire(value: datetime | None, path: str) -> str | None:
    return _timestamp_to_wire(value, path) if value is not None else None


def _optional_timestamp_from_wire(value: Any, path: str) -> datetime | None:
    return _timestamp_from_wire(value, path) if value is not None else None


def _utc_now() -> datetime:
    return _normalize_timestamp(datetime.now(timezone.utc), "timestamp")


def _allowed_prompts(value: Any, path: str = "allowedPrompts") -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{path} must be a sequence")
    prompts: list[dict[str, str]] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        prompt = _require_mapping(item, item_path)
        prompts.append(
            {
                _require_str(key, f"{item_path} key"): _require_str(
                    prompt_value, f"{item_path}.{key}"
                )
                for key, prompt_value in prompt.items()
            }
        )
    return prompts


def _todos(value: Any) -> dict[str, list[dict[str, Any]]]:
    copied = _require_json_object(value, "todos")
    result: dict[str, list[dict[str, Any]]] = {}
    for scope, raw_todos in copied.items():
        if not isinstance(raw_todos, list):
            raise TypeError(f"todos.{scope} must be a list")
        todos: list[dict[str, Any]] = []
        for index, todo in enumerate(raw_todos):
            if not isinstance(todo, dict):
                raise TypeError(f"todos.{scope}[{index}] must be a JSON object")
            todos.append(todo)
        result[scope] = todos
    return result


@dataclass
class TaskItem:
    id: str
    subject: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    active_form: str | None = None
    owner: str | None = None
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": _require_str(self.id, "id"),
            "subject": _require_str(self.subject, "subject"),
            "description": _require_str(self.description, "description"),
            "activeForm": _require_optional_str(self.active_form, "activeForm"),
            "owner": _require_optional_str(self.owner, "owner"),
            "status": self.status.value,
            "blocks": _require_string_sequence(self.blocks, "blocks"),
            "blockedBy": _require_string_sequence(self.blocked_by, "blockedBy"),
            "metadata": _require_json_object(self.metadata, "metadata"),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TaskItem:
        wire = _require_mapping(value, "task")
        return cls(
            id=_require_str(wire["id"], "id"),
            subject=_require_str(wire["subject"], "subject"),
            description=_require_str(wire["description"], "description"),
            active_form=_require_optional_str(wire["activeForm"], "activeForm"),
            owner=_require_optional_str(wire["owner"], "owner"),
            status=TaskStatus(_require_str(wire["status"], "status")),
            blocks=_require_string_sequence(wire["blocks"], "blocks"),
            blocked_by=_require_string_sequence(wire["blockedBy"], "blockedBy"),
            metadata=_require_json_object(wire["metadata"], "metadata"),
        )


@dataclass
class NewTask:
    subject: str
    description: str
    active_form: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": _require_str(self.subject, "subject"),
            "description": _require_str(self.description, "description"),
            "activeForm": _require_optional_str(self.active_form, "activeForm"),
            "metadata": _require_json_object(self.metadata, "metadata"),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> NewTask:
        wire = _require_mapping(value, "newTask")
        return cls(
            subject=_require_str(wire["subject"], "subject"),
            description=_require_str(wire["description"], "description"),
            active_form=_require_optional_str(wire["activeForm"], "activeForm"),
            metadata=_require_json_object(wire["metadata"], "metadata"),
        )


@dataclass
class TaskMutation:
    subject: str | None = None
    description: str | None = None
    active_form: str | None = None
    status: TaskStatus | None = None
    owner: str | None = None
    add_blocks: list[str] = field(default_factory=list)
    add_blocked_by: list[str] = field(default_factory=list)
    remove_blocks: list[str] = field(default_factory=list)
    remove_blocked_by: list[str] = field(default_factory=list)
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": _require_optional_str(self.subject, "subject"),
            "description": _require_optional_str(self.description, "description"),
            "activeForm": _require_optional_str(self.active_form, "activeForm"),
            "status": self.status.value if self.status is not None else None,
            "owner": _require_optional_str(self.owner, "owner"),
            "addBlocks": _require_string_sequence(self.add_blocks, "addBlocks"),
            "addBlockedBy": _require_string_sequence(self.add_blocked_by, "addBlockedBy"),
            "removeBlocks": _require_string_sequence(self.remove_blocks, "removeBlocks"),
            "removeBlockedBy": _require_string_sequence(self.remove_blocked_by, "removeBlockedBy"),
            "metadata": (
                _require_json_object(self.metadata, "metadata")
                if self.metadata is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TaskMutation:
        wire = _require_mapping(value, "taskMutation")
        raw_metadata = wire["metadata"]
        return cls(
            subject=_require_optional_str(wire["subject"], "subject"),
            description=_require_optional_str(wire["description"], "description"),
            active_form=_require_optional_str(wire["activeForm"], "activeForm"),
            status=_require_optional_status(wire["status"], "status"),
            owner=_require_optional_str(wire["owner"], "owner"),
            add_blocks=_require_string_sequence(wire["addBlocks"], "addBlocks"),
            add_blocked_by=_require_string_sequence(wire["addBlockedBy"], "addBlockedBy"),
            remove_blocks=_require_string_sequence(wire["removeBlocks"], "removeBlocks"),
            remove_blocked_by=_require_string_sequence(wire["removeBlockedBy"], "removeBlockedBy"),
            metadata=(
                _require_json_object(raw_metadata, "metadata") if raw_metadata is not None else None
            ),
        )


@dataclass
class ClaimResult:
    success: bool
    task: TaskItem | None = None
    reason: str | None = None
    current_owner: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": _require_bool(self.success, "success"),
            "task": self.task.to_dict() if self.task is not None else None,
            "reason": _require_optional_str(self.reason, "reason"),
            "currentOwner": _require_optional_str(self.current_owner, "currentOwner"),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ClaimResult:
        wire = _require_mapping(value, "claimResult")
        raw_task = wire["task"]
        return cls(
            success=_require_bool(wire["success"], "success"),
            task=(
                TaskItem.from_dict(_require_mapping(raw_task, "task"))
                if raw_task is not None
                else None
            ),
            reason=_require_optional_str(wire["reason"], "reason"),
            current_owner=_require_optional_str(wire["currentOwner"], "currentOwner"),
        )


@dataclass
class Plan:
    state: PlanState = PlanState.IDLE
    slug: str | None = None
    file_path: str | None = None
    allowed_prompts: list[dict[str, str]] = field(default_factory=list)
    approved_by: str | None = None
    approval_metadata: dict[str, Any] = field(default_factory=dict)
    submitted_at: datetime | None = None
    approved_at: datetime | None = None

    _TRANSITIONS: ClassVar[dict[PlanState, frozenset[PlanState]]] = {
        PlanState.IDLE: frozenset({PlanState.PLANNING}),
        PlanState.PLANNING: frozenset({PlanState.PENDING_APPROVAL}),
        PlanState.PENDING_APPROVAL: frozenset({PlanState.PLANNING, PlanState.APPROVED}),
        PlanState.APPROVED: frozenset({PlanState.IDLE}),
    }

    def transition_to(self, target: PlanState) -> None:
        if target not in self._TRANSITIONS[self.state]:
            raise InvalidTransition(self.state, target)
        self.state = target

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "slug": _require_optional_str(self.slug, "slug"),
            "filePath": _require_optional_str(self.file_path, "filePath"),
            "allowedPrompts": _allowed_prompts(self.allowed_prompts),
            "approvedBy": _require_optional_str(self.approved_by, "approvedBy"),
            "approvalMetadata": _require_json_object(self.approval_metadata, "approvalMetadata"),
            "submittedAt": _optional_timestamp_to_wire(self.submitted_at, "submittedAt"),
            "approvedAt": _optional_timestamp_to_wire(self.approved_at, "approvedAt"),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Plan:
        wire = _require_mapping(value, "plan")
        return cls(
            state=PlanState(_require_str(wire["state"], "state")),
            slug=_require_optional_str(wire["slug"], "slug"),
            file_path=_require_optional_str(wire["filePath"], "filePath"),
            allowed_prompts=_allowed_prompts(wire["allowedPrompts"]),
            approved_by=_require_optional_str(wire["approvedBy"], "approvedBy"),
            approval_metadata=_require_json_object(wire["approvalMetadata"], "approvalMetadata"),
            submitted_at=_optional_timestamp_from_wire(wire["submittedAt"], "submittedAt"),
            approved_at=_optional_timestamp_from_wire(wire["approvedAt"], "approvedAt"),
        )


@dataclass
class SessionState:
    session_id: str
    revision: int = 0
    permission_mode: str = "default"
    pre_plan_permission_mode: str | None = None
    plan: Plan = field(default_factory=Plan)
    task_list_id: str | None = None
    task_mode: TaskMode = TaskMode.TASK_V2
    todos: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    transcript_cursor: int = 0
    last_event_id: int = 0
    agents: dict[str, Any] = field(default_factory=dict)
    health: SessionHealth = SessionHealth.READY
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    interrupted_at: datetime | None = None

    @classmethod
    def new(cls, session_id: str, *, now: datetime | None = None) -> SessionState:
        timestamp = _normalize_timestamp(now or _utc_now(), "now")
        return cls(session_id=session_id, created_at=timestamp, updated_at=timestamp)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessionId": _require_str(self.session_id, "sessionId"),
            "revision": _require_int(self.revision, "revision", minimum=0),
            "permissionMode": _require_str(self.permission_mode, "permissionMode"),
            "prePlanPermissionMode": _require_optional_str(
                self.pre_plan_permission_mode, "prePlanPermissionMode"
            ),
            "plan": self.plan.to_dict(),
            "taskListId": _require_optional_str(self.task_list_id, "taskListId"),
            "taskMode": self.task_mode.value,
            "todos": _todos(self.todos),
            "transcriptCursor": _require_int(self.transcript_cursor, "transcriptCursor", minimum=0),
            "lastEventId": _require_int(self.last_event_id, "lastEventId", minimum=0),
            "agents": _require_json_object(self.agents, "agents"),
            "health": self.health.value,
            "createdAt": _timestamp_to_wire(self.created_at, "createdAt"),
            "updatedAt": _timestamp_to_wire(self.updated_at, "updatedAt"),
            "interruptedAt": _optional_timestamp_to_wire(self.interrupted_at, "interruptedAt"),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SessionState:
        wire = _require_mapping(value, "sessionState")
        return cls(
            session_id=_require_str(wire["sessionId"], "sessionId"),
            revision=_require_int(wire["revision"], "revision", minimum=0),
            permission_mode=_require_str(wire["permissionMode"], "permissionMode"),
            pre_plan_permission_mode=_require_optional_str(
                wire["prePlanPermissionMode"], "prePlanPermissionMode"
            ),
            plan=Plan.from_dict(_require_mapping(wire["plan"], "plan")),
            task_list_id=_require_optional_str(wire["taskListId"], "taskListId"),
            task_mode=TaskMode(_require_str(wire["taskMode"], "taskMode")),
            todos=_todos(wire["todos"]),
            transcript_cursor=_require_int(wire["transcriptCursor"], "transcriptCursor", minimum=0),
            last_event_id=_require_int(wire["lastEventId"], "lastEventId", minimum=0),
            agents=_require_json_object(wire["agents"], "agents"),
            health=SessionHealth(_require_str(wire["health"], "health")),
            created_at=_timestamp_from_wire(wire["createdAt"], "createdAt"),
            updated_at=_timestamp_from_wire(wire["updatedAt"], "updatedAt"),
            interrupted_at=_optional_timestamp_from_wire(wire["interruptedAt"], "interruptedAt"),
        )


@dataclass
class SessionEvent:
    id: int
    session_id: str
    event_type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)
    parent_event_id: int | None = None

    def _validate_cursor(self) -> tuple[int, int | None]:
        event_id = _require_int(self.id, "id", minimum=0)
        parent_id = _require_optional_int(self.parent_event_id, "parentEventId", minimum=0)
        if parent_id is not None and parent_id >= event_id:
            raise ValueError("parentEventId must be less than id")
        return event_id, parent_id

    def to_dict(self) -> dict[str, Any]:
        event_id, parent_id = self._validate_cursor()
        return {
            "id": event_id,
            "sessionId": _require_str(self.session_id, "sessionId"),
            "type": self.event_type.value,
            "payload": _require_json_object(self.payload, "payload"),
            "createdAt": _timestamp_to_wire(self.created_at, "createdAt"),
            "parentEventId": parent_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SessionEvent:
        wire = _require_mapping(value, "sessionEvent")
        event_id = _require_int(wire["id"], "id", minimum=0)
        parent_id = _require_optional_int(wire["parentEventId"], "parentEventId", minimum=0)
        if parent_id is not None and parent_id >= event_id:
            raise ValueError("parentEventId must be less than id")
        return cls(
            id=event_id,
            session_id=_require_str(wire["sessionId"], "sessionId"),
            event_type=EventType(_require_str(wire["type"], "type")),
            payload=_require_json_object(wire["payload"], "payload"),
            created_at=_timestamp_from_wire(wire["createdAt"], "createdAt"),
            parent_event_id=parent_id,
        )


@dataclass
class SessionSnapshot:
    session_id: str
    last_event_id: int
    state: SessionState
    created_at: datetime = field(default_factory=_utc_now)

    def _validate_identity_and_cursor(self) -> tuple[str, int]:
        session_id = _require_str(self.session_id, "sessionId")
        last_event_id = _require_int(self.last_event_id, "lastEventId", minimum=0)
        if session_id != self.state.session_id:
            raise ValueError("snapshot sessionId must match state sessionId")
        if last_event_id != self.state.last_event_id:
            raise ValueError("snapshot lastEventId must match state lastEventId")
        return session_id, last_event_id

    def to_dict(self) -> dict[str, Any]:
        session_id, last_event_id = self._validate_identity_and_cursor()
        return {
            "sessionId": session_id,
            "lastEventId": last_event_id,
            "state": self.state.to_dict(),
            "createdAt": _timestamp_to_wire(self.created_at, "createdAt"),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SessionSnapshot:
        wire = _require_mapping(value, "sessionSnapshot")
        session_id = _require_str(wire["sessionId"], "sessionId")
        last_event_id = _require_int(wire["lastEventId"], "lastEventId", minimum=0)
        state = SessionState.from_dict(_require_mapping(wire["state"], "state"))
        if session_id != state.session_id:
            raise ValueError("snapshot sessionId must match state sessionId")
        if last_event_id != state.last_event_id:
            raise ValueError("snapshot lastEventId must match state lastEventId")
        return cls(
            session_id=session_id,
            last_event_id=last_event_id,
            state=state,
            created_at=_timestamp_from_wire(wire["createdAt"], "createdAt"),
        )
