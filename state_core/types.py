"""Authoritative, persistence-safe types for durable session state.

Python attributes use snake_case. ``to_dict`` and ``from_dict`` define the
stable Node-facing wire contract and use camelCase for compound field names.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


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
        super().__init__(
            f"invalid {domain} transition from {current.value!r} to {target.value!r}"
        )


class InvalidTaskDependency(StateCoreError):
    """Raised when a task dependency would violate task-list invariants."""


def _copy_wire_value(value: Any) -> Any:
    """Return a detached JSON-compatible representation of a payload value."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _copy_wire_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_wire_value(item) for item in value]
    return deepcopy(value)


def _datetime_from_wire(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


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
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "activeForm": self.active_form,
            "owner": self.owner,
            "status": self.status.value,
            "blocks": list(self.blocks),
            "blockedBy": list(self.blocked_by),
            "metadata": _copy_wire_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TaskItem:
        return cls(
            id=str(value["id"]),
            subject=str(value["subject"]),
            description=str(value["description"]),
            active_form=value.get("activeForm"),
            owner=value.get("owner"),
            status=TaskStatus(value.get("status", TaskStatus.PENDING.value)),
            blocks=[str(item) for item in value.get("blocks", [])],
            blocked_by=[str(item) for item in value.get("blockedBy", [])],
            metadata=dict(_copy_wire_value(value.get("metadata", {}))),
        )


@dataclass
class NewTask:
    subject: str
    description: str
    active_form: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "description": self.description,
            "activeForm": self.active_form,
            "metadata": _copy_wire_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> NewTask:
        return cls(
            subject=str(value["subject"]),
            description=str(value["description"]),
            active_form=value.get("activeForm"),
            metadata=dict(_copy_wire_value(value.get("metadata", {}))),
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
            "subject": self.subject,
            "description": self.description,
            "activeForm": self.active_form,
            "status": self.status.value if self.status is not None else None,
            "owner": self.owner,
            "addBlocks": list(self.add_blocks),
            "addBlockedBy": list(self.add_blocked_by),
            "removeBlocks": list(self.remove_blocks),
            "removeBlockedBy": list(self.remove_blocked_by),
            "metadata": _copy_wire_value(self.metadata) if self.metadata is not None else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TaskMutation:
        raw_status = value.get("status")
        raw_metadata = value.get("metadata")
        return cls(
            subject=value.get("subject"),
            description=value.get("description"),
            active_form=value.get("activeForm"),
            status=TaskStatus(raw_status) if raw_status is not None else None,
            owner=value.get("owner"),
            add_blocks=[str(item) for item in value.get("addBlocks", [])],
            add_blocked_by=[str(item) for item in value.get("addBlockedBy", [])],
            remove_blocks=[str(item) for item in value.get("removeBlocks", [])],
            remove_blocked_by=[str(item) for item in value.get("removeBlockedBy", [])],
            metadata=(dict(_copy_wire_value(raw_metadata)) if raw_metadata is not None else None),
        )


@dataclass
class ClaimResult:
    success: bool
    task: TaskItem | None = None
    reason: str | None = None
    current_owner: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "task": self.task.to_dict() if self.task is not None else None,
            "reason": self.reason,
            "currentOwner": self.current_owner,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ClaimResult:
        raw_task = value.get("task")
        return cls(
            success=bool(value["success"]),
            task=TaskItem.from_dict(raw_task) if raw_task is not None else None,
            reason=value.get("reason"),
            current_owner=value.get("currentOwner"),
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

    _TRANSITIONS = {
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
            "slug": self.slug,
            "filePath": self.file_path,
            "allowedPrompts": _copy_wire_value(self.allowed_prompts),
            "approvedBy": self.approved_by,
            "approvalMetadata": _copy_wire_value(self.approval_metadata),
            "submittedAt": self.submitted_at.isoformat() if self.submitted_at else None,
            "approvedAt": self.approved_at.isoformat() if self.approved_at else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Plan:
        return cls(
            state=PlanState(value.get("state", PlanState.IDLE.value)),
            slug=value.get("slug"),
            file_path=value.get("filePath"),
            allowed_prompts=[dict(item) for item in value.get("allowedPrompts", [])],
            approved_by=value.get("approvedBy"),
            approval_metadata=dict(_copy_wire_value(value.get("approvalMetadata", {}))),
            submitted_at=_datetime_from_wire(value.get("submittedAt")),
            approved_at=_datetime_from_wire(value.get("approvedAt")),
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
    parent_session_id: str | None = None
    child_agent_ids: list[str] = field(default_factory=list)
    agent_statuses: dict[str, str] = field(default_factory=dict)
    health: SessionHealth = SessionHealth.READY
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    interrupted_at: datetime | None = None

    @classmethod
    def new(cls, session_id: str, *, now: datetime | None = None) -> SessionState:
        timestamp = now or datetime.now(timezone.utc)
        return cls(session_id=session_id, created_at=timestamp, updated_at=timestamp)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "revision": self.revision,
            "permissionMode": self.permission_mode,
            "prePlanPermissionMode": self.pre_plan_permission_mode,
            "plan": self.plan.to_dict(),
            "taskListId": self.task_list_id,
            "taskMode": self.task_mode.value,
            "todos": _copy_wire_value(self.todos),
            "transcriptCursor": self.transcript_cursor,
            "lastEventId": self.last_event_id,
            "parentSessionId": self.parent_session_id,
            "childAgentIds": list(self.child_agent_ids),
            "agentStatuses": dict(self.agent_statuses),
            "health": self.health.value,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
            "interruptedAt": self.interrupted_at.isoformat() if self.interrupted_at else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SessionState:
        raw_plan = value.get("plan", {})
        created_at = _datetime_from_wire(value.get("createdAt"))
        updated_at = _datetime_from_wire(value.get("updatedAt"))
        return cls(
            session_id=str(value["sessionId"]),
            revision=int(value.get("revision", 0)),
            permission_mode=str(value.get("permissionMode", "default")),
            pre_plan_permission_mode=value.get("prePlanPermissionMode"),
            plan=Plan.from_dict(raw_plan),
            task_list_id=value.get("taskListId"),
            task_mode=TaskMode(value.get("taskMode", TaskMode.TASK_V2.value)),
            todos={
                str(scope): [dict(_copy_wire_value(todo)) for todo in todos]
                for scope, todos in value.get("todos", {}).items()
            },
            transcript_cursor=int(value.get("transcriptCursor", 0)),
            last_event_id=int(value.get("lastEventId", 0)),
            parent_session_id=value.get("parentSessionId"),
            child_agent_ids=[str(item) for item in value.get("childAgentIds", [])],
            agent_statuses={
                str(agent_id): str(status)
                for agent_id, status in value.get("agentStatuses", {}).items()
            },
            health=SessionHealth(value.get("health", SessionHealth.READY.value)),
            created_at=created_at or datetime.now(timezone.utc),
            updated_at=updated_at or created_at or datetime.now(timezone.utc),
            interrupted_at=_datetime_from_wire(value.get("interruptedAt")),
        )


@dataclass
class SessionEvent:
    id: int
    session_id: str
    event_type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    parent_event_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sessionId": self.session_id,
            "type": self.event_type.value,
            "payload": _copy_wire_value(self.payload),
            "createdAt": self.created_at.isoformat(),
            "parentEventId": self.parent_event_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SessionEvent:
        return cls(
            id=int(value["id"]),
            session_id=str(value["sessionId"]),
            event_type=EventType(value["type"]),
            payload=dict(_copy_wire_value(value.get("payload", {}))),
            created_at=_datetime_from_wire(value.get("createdAt")) or datetime.now(timezone.utc),
            parent_event_id=(
                int(value["parentEventId"]) if value.get("parentEventId") is not None else None
            ),
        )


@dataclass
class SessionSnapshot:
    session_id: str
    last_event_id: int
    state: SessionState
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "lastEventId": self.last_event_id,
            "state": self.state.to_dict(),
            "createdAt": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SessionSnapshot:
        return cls(
            session_id=str(value["sessionId"]),
            last_event_id=int(value["lastEventId"]),
            state=SessionState.from_dict(value["state"]),
            created_at=_datetime_from_wire(value.get("createdAt")) or datetime.now(timezone.utc),
        )
