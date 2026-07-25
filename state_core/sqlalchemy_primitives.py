"""SQLAlchemy repositories for shared durable runtime primitives."""

from __future__ import annotations

import dataclasses
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterator, Mapping, TypeVar

from sqlalchemy import (
    JSON,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    select,
    text,
    update,
)
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from models import Base

from .runtime_primitives import (
    APPROVED_EXECUTION_TERMINAL_STATUSES,
    EXECUTION_TASK_TERMINAL_STATUSES,
    HOOK_INVOCATION_TERMINAL_STATUSES,
    MAX_EXECUTION_OUTPUT_BYTES,
    MAX_EXECUTION_READ_BYTES,
    PERMISSION_REQUEST_TERMINAL_STATUSES,
    TEAM_MEMBER_TERMINAL_STATUSES,
    ApprovedToolExecutionRecord,
    ApprovedToolExecutionStatus,
    ExecutionOutputChunk,
    ExecutionTaskRecord,
    ExecutionTaskStatus,
    HookAsyncMode,
    HookDefinitionRecord,
    HookInvocationRecord,
    HookInvocationStatus,
    InvalidRuntimePrimitiveTransition,
    PermissionRequestRecord,
    PermissionRequestStatus,
    PermissionRuleKind,
    PermissionRuleRecord,
    PermissionRuleScope,
    SkillActivationRecord,
    SkillActivationStatus,
    TeamMemberRecord,
    TeamMemberStatus,
    TeamMessageRecord,
    TeamRecord,
    TeamStatus,
)
from .runtime_records import RuntimeRecordRevisionConflict

SessionFactory = Callable[[], Session]
RecordT = TypeVar("RecordT")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return {field.name: _encode(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


_ENUM_FIELDS: dict[type[Any], dict[str, type[Enum]]] = {
    PermissionRequestRecord: {"status": PermissionRequestStatus},
    ApprovedToolExecutionRecord: {"status": ApprovedToolExecutionStatus},
    PermissionRuleRecord: {"kind": PermissionRuleKind, "scope": PermissionRuleScope},
    HookDefinitionRecord: {"async_mode": HookAsyncMode},
    HookInvocationRecord: {"status": HookInvocationStatus},
    ExecutionTaskRecord: {"status": ExecutionTaskStatus},
    TeamRecord: {"status": TeamStatus},
    TeamMemberRecord: {"status": TeamMemberStatus},
    SkillActivationRecord: {"status": SkillActivationStatus},
}
_DATETIME_FIELDS = frozenset(
    {
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "deadline_at",
        "resolved_at",
        "revoked_at",
        "lease_expires_at",
        "closed_at",
        "stopped_at",
    }
)
_TUPLE_FIELDS = frozenset(
    {"suggestions", "permission_updates", "assigned_task_ids", "registered_hook_ids", "allowed_tools"}
)


def _decode(record_type: type[RecordT], data: Mapping[str, Any]) -> RecordT:
    values = dict(data)
    for name, enum_type in _ENUM_FIELDS.get(record_type, {}).items():
        if values.get(name) is not None:
            values[name] = enum_type(values[name])
    for name in _DATETIME_FIELDS.intersection(values):
        if values[name] is not None and not isinstance(values[name], datetime):
            values[name] = datetime.fromisoformat(values[name])
    for name in _TUPLE_FIELDS.intersection(values):
        values[name] = tuple(values[name])
    return record_type(**values)


def _ensure_allowed_changes(
    record_label: str,
    changes: Mapping[str, Any],
    allowed: set[str],
) -> None:
    unsupported = set(changes).difference(allowed)
    if unsupported:
        raise TypeError(f"unsupported {record_label} fields: {sorted(unsupported)!r}")


def _same_operation(
    existing: Any,
    retried: Any,
    *,
    fields: frozenset[str],
) -> bool:
    return all(getattr(existing, name) == getattr(retried, name) for name in fields)


_PERMISSION_OPERATION_FIELDS = frozenset(
    {
        "root_session_id", "agent_id", "tool_call_id", "tool_name", "original_input",
        "effective_input", "input_digest", "reason", "permission_mode", "policy_revision",
        "suggestions", "deadline_at", "idempotency_key",
    }
)
_HOOK_OPERATION_FIELDS = frozenset(
    {
        "root_session_id", "definition_id", "definition_revision", "event",
        "event_envelope", "correlation_id", "idempotency_key", "agent_id", "attempt",
        "deadline_at", "retry_of_invocation_id",
    }
)
_TEAM_MESSAGE_FIELDS = frozenset(
    {
        "message_id", "team_id", "root_session_id", "sender_member_id",
        "recipient_member_id", "message_type", "body", "request_correlation_id",
    }
)


class RuntimePermissionRequest(Base):
    __tablename__ = "runtime_permission_requests"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    __table_args__ = (
        Index("ix_runtime_permission_requests_root_status", "root_session_id", "status"),
    )


class RuntimeApprovedToolExecution(Base):
    __tablename__ = "runtime_approved_tool_executions"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    request_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    root_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    __table_args__ = (
        Index("ix_runtime_approved_executions_root_status", "root_session_id", "status"),
    )


class RuntimePermissionRule(Base):
    __tablename__ = "runtime_permission_rules"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class RuntimeHookDefinition(Base):
    __tablename__ = "runtime_hook_definitions"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    definition_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    config_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("definition_id", "config_revision"),)


class RuntimeHookInvocation(Base):
    __tablename__ = "runtime_hook_invocations"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    __table_args__ = (
        Index("ix_runtime_hook_invocations_root_status", "root_session_id", "status"),
    )


class RuntimeExecutionTask(Base):
    __tablename__ = "runtime_execution_tasks"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, default=b"")
    __table_args__ = (
        Index("ix_runtime_execution_tasks_root_status", "root_session_id", "status"),
    )


class RuntimeTeam(Base):
    __tablename__ = "runtime_teams"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    __table_args__ = (
        UniqueConstraint("root_session_id", "record_id"),
        Index("ix_runtime_teams_root_status", "root_session_id", "status"),
    )


class RuntimeTeamMember(Base):
    __tablename__ = "runtime_team_members"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    team_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    __table_args__ = (
        UniqueConstraint("team_id", "record_id"),
        Index("ix_runtime_team_members_root_status", "root_session_id", "status"),
    )


class RuntimeTeamMessage(Base):
    __tablename__ = "runtime_team_messages"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    team_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    recipient_member_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("team_id", "sequence"),)


class RuntimeSkillActivation(Base):
    __tablename__ = "runtime_skill_activations"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    skill_name: Mapped[str] = mapped_column(String, nullable=False)
    skill_digest: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("root_session_id", "agent_id", "skill_name"),)


@contextmanager
def _transaction(session_factory: SessionFactory, *, immediate: bool = False) -> Iterator[Session]:
    db = session_factory()
    try:
        if immediate and db.bind is not None and db.bind.dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        else:
            db.begin()
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


class _CASRepository:
    record_type: type[Any]
    model_type: type[Any]
    id_field: str
    record_label: str
    initial_status: Enum | None = None

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._before_compare_and_swap: Callable[[], None] | None = None

    def _record(self, row: Any) -> Any:
        return _decode(self.record_type, row.data)

    def _id(self, record: Any) -> str:
        return getattr(record, self.id_field)

    def _row_kwargs(self, record: Any) -> dict[str, Any]:
        kwargs = {
            "record_id": self._id(record),
            "root_session_id": record.root_session_id,
            "revision": getattr(record, "revision", 0),
            "data": _encode(record),
        }
        if hasattr(record, "status"):
            kwargs["status"] = record.status.value
        return kwargs

    def create(self, record: Any) -> Any:
        detached = self.record_type(**record.__dict__)
        if getattr(detached, "revision", 0) != 0:
            raise ValueError(f"new {self.record_label} records must start at revision 0")
        if self.initial_status is not None and detached.status is not self.initial_status:
            raise ValueError(
                f"new {self.record_label} records have invalid initial status {detached.status.value!r}"
            )
        try:
            with _transaction(self._session_factory) as db:
                db.add(self.model_type(**self._row_kwargs(detached)))
        except IntegrityError as exc:
            current = self.get(self._id(detached))
            raise RuntimeRecordRevisionConflict(
                self.record_label,
                self._id(detached),
                None,
                current.revision if current is not None and hasattr(current, "revision") else None,
            ) from exc
        return detached

    def get(self, record_id: str) -> Any | None:
        with self._session_factory() as db:
            row = db.get(self.model_type, record_id)
            return self._record(row) if row is not None else None

    def list(self, root_session_id: str, *, status: Enum | None = None) -> list[Any]:
        with self._session_factory() as db:
            statement = select(self.model_type).where(self.model_type.root_session_id == root_session_id)
            if status is not None:
                statement = statement.where(self.model_type.status == status.value)
            rows = db.scalars(statement.order_by(self.model_type.record_id)).all()
            return [self._record(row) for row in rows]

    def _mutate(self, record_id: str, expected_revision: int, changes: Mapping[str, Any]) -> Any:
        lost_update = False
        with _transaction(self._session_factory, immediate=True) as db:
            row = db.get(self.model_type, record_id)
            actual = row.revision if row is not None else None
            if row is None or actual != expected_revision:
                raise RuntimeRecordRevisionConflict(self.record_label, record_id, expected_revision, actual)
            current = self._record(row)
            next_record = dataclasses.replace(
                current, revision=actual + 1, updated_at=_utc_now(), **changes
            )
            if self._before_compare_and_swap is not None:
                self._before_compare_and_swap()
            values = {"revision": actual + 1, "data": _encode(next_record)}
            if hasattr(next_record, "status"):
                values["status"] = next_record.status.value
            result = db.execute(
                update(self.model_type)
                .where(self.model_type.record_id == record_id, self.model_type.revision == expected_revision)
                .values(**values)
            )
            if result.rowcount != 1:
                lost_update = True
            else:
                return next_record
        assert lost_update
        current = self.get(record_id)
        raise RuntimeRecordRevisionConflict(
            self.record_label,
            record_id,
            expected_revision,
            current.revision if current is not None else None,
        )


class SQLAlchemyPermissionRequestRepository(_CASRepository):
    record_type = PermissionRequestRecord
    model_type = RuntimePermissionRequest
    id_field = "request_id"
    record_label = "permission request"
    initial_status = PermissionRequestStatus.PENDING

    def _row_kwargs(self, record: PermissionRequestRecord) -> dict[str, Any]:
        return {**super()._row_kwargs(record), "idempotency_key": record.idempotency_key}

    def create(self, record: PermissionRequestRecord) -> PermissionRequestRecord:
        existing = self.get_by_idempotency_key(record.idempotency_key)
        if existing is not None:
            if _same_operation(existing, record, fields=_PERMISSION_OPERATION_FIELDS):
                return existing
            raise RuntimeRecordRevisionConflict(self.record_label, record.request_id, None, existing.revision)
        try:
            return super().create(record)
        except RuntimeRecordRevisionConflict:
            winner = self.get_by_idempotency_key(record.idempotency_key)
            if winner is not None and _same_operation(winner, record, fields=_PERMISSION_OPERATION_FIELDS):
                return winner
            raise

    def get_by_idempotency_key(self, idempotency_key: str) -> PermissionRequestRecord | None:
        with self._session_factory() as db:
            row = db.scalar(select(RuntimePermissionRequest).where(RuntimePermissionRequest.idempotency_key == idempotency_key))
            return self._record(row) if row is not None else None

    def transition(self, request_id: str, status: PermissionRequestStatus, expected_revision: int, **changes: Any) -> PermissionRequestRecord:
        _ensure_allowed_changes(
            self.record_label,
            changes,
            {"actor", "decision_reason", "updated_input", "permission_updates", "interruption_reason"},
        )
        current = self.get(request_id)
        if current is None:
            raise RuntimeRecordRevisionConflict(self.record_label, request_id, expected_revision, None)
        terminal_changes = {**changes, "status": status}
        if status in PERMISSION_REQUEST_TERMINAL_STATUSES:
            terminal_changes.setdefault("resolved_at", _utc_now())
        if current.status is status and current.status in PERMISSION_REQUEST_TERMINAL_STATUSES:
            terminal_effect = {
                "actor": changes.get("actor"),
                "decision_reason": changes.get("decision_reason"),
                "updated_input": changes.get("updated_input"),
                "permission_updates": changes.get("permission_updates", ()),
                "interruption_reason": changes.get("interruption_reason"),
            }
            comparable = dataclasses.replace(current, **terminal_effect)
            if all(
                getattr(comparable, name) == getattr(current, name)
                for name in terminal_effect
            ):
                return current
            raise RuntimeRecordRevisionConflict(
                self.record_label, request_id, expected_revision, current.revision
            )
        if current.revision != expected_revision:
            raise RuntimeRecordRevisionConflict(self.record_label, request_id, expected_revision, current.revision)
        if current.status is not PermissionRequestStatus.PENDING or status not in PERMISSION_REQUEST_TERMINAL_STATUSES:
            raise InvalidRuntimePrimitiveTransition(self.record_label, current.status, status)
        return self._mutate(request_id, expected_revision, terminal_changes)

    def expire_due(self, root_session_id: str, now: datetime | None = None) -> list[PermissionRequestRecord]:
        cutoff = now or _utc_now()
        expired: list[PermissionRequestRecord] = []
        for record in self.list(root_session_id, status=PermissionRequestStatus.PENDING):
            if record.deadline_at is None or record.deadline_at > cutoff:
                continue
            try:
                expired.append(
                    self.transition(
                        record.request_id,
                        PermissionRequestStatus.TIMED_OUT,
                        record.revision,
                        decision_reason="approval deadline expired",
                    )
                )
            except RuntimeRecordRevisionConflict:
                continue
        return expired


class SQLAlchemyApprovedToolExecutionRepository(_CASRepository):
    record_type = ApprovedToolExecutionRecord
    model_type = RuntimeApprovedToolExecution
    id_field = "execution_id"
    record_label = "approved tool execution"
    initial_status = ApprovedToolExecutionStatus.PENDING

    def _row_kwargs(self, record: ApprovedToolExecutionRecord) -> dict[str, Any]:
        return {**super()._row_kwargs(record), "request_id": record.request_id}

    def get_by_request(self, request_id: str) -> ApprovedToolExecutionRecord | None:
        with self._session_factory() as db:
            row = db.scalar(select(RuntimeApprovedToolExecution).where(RuntimeApprovedToolExecution.request_id == request_id))
            return self._record(row) if row is not None else None

    def transition(self, execution_id: str, status: ApprovedToolExecutionStatus, expected_revision: int, **changes: Any) -> ApprovedToolExecutionRecord:
        _ensure_allowed_changes(self.record_label, changes, {"result_reference", "error"})
        current = self.get(execution_id)
        if current is None or current.revision != expected_revision:
            raise RuntimeRecordRevisionConflict(self.record_label, execution_id, expected_revision, current.revision if current else None)
        allowed = (
            {ApprovedToolExecutionStatus.RUNNING, ApprovedToolExecutionStatus.CANCELLED, ApprovedToolExecutionStatus.INTERRUPTED}
            if current.status is ApprovedToolExecutionStatus.PENDING
            else APPROVED_EXECUTION_TERMINAL_STATUSES
            if current.status is ApprovedToolExecutionStatus.RUNNING
            else frozenset()
        )
        if status not in allowed:
            raise InvalidRuntimePrimitiveTransition(self.record_label, current.status, status)
        values = {**changes, "status": status}
        if status is ApprovedToolExecutionStatus.RUNNING:
            values["started_at"] = current.started_at or _utc_now()
        if status in APPROVED_EXECUTION_TERMINAL_STATUSES:
            values["finished_at"] = _utc_now()
        return self._mutate(execution_id, expected_revision, values)

    def interrupt_open(
        self,
        root_session_id: str,
        *,
        live_owner_tokens: set[str],
        now: datetime,
        observer: bool = False,
    ) -> list[ApprovedToolExecutionRecord]:
        del now
        if observer:
            raise PermissionError("observer runtimes cannot interrupt approved executions")
        interrupted: list[ApprovedToolExecutionRecord] = []
        for status in (ApprovedToolExecutionStatus.PENDING, ApprovedToolExecutionStatus.RUNNING):
            for record in self.list(root_session_id, status=status):
                if record.claim_owner in live_owner_tokens:
                    continue
                try:
                    interrupted.append(
                        self.transition(
                            record.execution_id,
                            ApprovedToolExecutionStatus.INTERRUPTED,
                            record.revision,
                            error={"reason": "runtime owner unavailable"},
                        )
                    )
                except RuntimeRecordRevisionConflict:
                    continue
        return interrupted


class SQLAlchemyPermissionRuleRepository(_CASRepository):
    record_type = PermissionRuleRecord
    model_type = RuntimePermissionRule
    id_field = "rule_id"
    record_label = "permission rule"

    def create(self, record: PermissionRuleRecord) -> PermissionRuleRecord:
        if record.scope not in {
            PermissionRuleScope.USER_SETTINGS,
            PermissionRuleScope.PROJECT_SETTINGS,
            PermissionRuleScope.LOCAL_SETTINGS,
        }:
            raise ValueError(f"permission rule scope {record.scope.value!r} is not repository-backed")
        return super().create(record)

    def revoke(self, rule_id: str, expected_revision: int) -> PermissionRuleRecord:
        return self._mutate(rule_id, expected_revision, {"revoked_at": _utc_now()})


class SQLAlchemyHookDefinitionRepository:
    _ALLOWED_VERSION_FIELDS = {
        "event", "matcher", "runner_kind", "runner_config", "source", "order",
        "timeout_ms", "once", "async_mode", "enabled", "idempotent",
    }

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _record(row: RuntimeHookDefinition) -> HookDefinitionRecord:
        return _decode(HookDefinitionRecord, row.data)

    @staticmethod
    def _storage_id(definition_id: str, config_revision: int) -> str:
        return f"{definition_id}:{config_revision}"

    def create(self, record: HookDefinitionRecord) -> HookDefinitionRecord:
        detached = HookDefinitionRecord(**record.__dict__)
        if detached.revision != 0 or detached.config_revision != 0:
            raise ValueError("new hook definition records must start at revision 0 and config revision 0")
        with _transaction(self._session_factory) as db:
            db.add(RuntimeHookDefinition(
                record_id=self._storage_id(detached.definition_id, 0),
                root_session_id=detached.root_session_id,
                definition_id=detached.definition_id,
                config_revision=0,
                status=None,
                revision=0,
                data=_encode(detached),
            ))
        return detached

    def get(self, definition_id: str) -> HookDefinitionRecord | None:
        with self._session_factory() as db:
            row = db.scalar(
                select(RuntimeHookDefinition)
                .where(RuntimeHookDefinition.definition_id == definition_id)
                .order_by(RuntimeHookDefinition.config_revision.desc())
                .limit(1)
            )
            return self._record(row) if row is not None else None

    def get_version(self, definition_id: str, config_revision: int) -> HookDefinitionRecord | None:
        with self._session_factory() as db:
            row = db.get(RuntimeHookDefinition, self._storage_id(definition_id, config_revision))
            return self._record(row) if row is not None else None

    def create_version(self, definition_id: str, expected_revision: int, **changes: Any) -> HookDefinitionRecord:
        _ensure_allowed_changes("hook definition", changes, self._ALLOWED_VERSION_FIELDS)
        with _transaction(self._session_factory, immediate=True) as db:
            row = db.scalar(
                select(RuntimeHookDefinition)
                .where(RuntimeHookDefinition.definition_id == definition_id)
                .order_by(RuntimeHookDefinition.config_revision.desc())
                .limit(1)
            )
            if row is None or row.config_revision != expected_revision:
                raise RuntimeRecordRevisionConflict(
                    "hook definition", definition_id, expected_revision,
                    row.config_revision if row is not None else None,
                )
            current = self._record(row)
            next_revision = current.config_revision + 1
            next_record = dataclasses.replace(
                current,
                config_revision=next_revision,
                revision=0,
                updated_at=_utc_now(),
                **changes,
            )
            db.add(RuntimeHookDefinition(
                record_id=self._storage_id(definition_id, next_revision),
                root_session_id=next_record.root_session_id,
                definition_id=definition_id,
                config_revision=next_revision,
                status=None,
                revision=0,
                data=_encode(next_record),
            ))
            return next_record

    def list(self, root_session_id: str) -> list[HookDefinitionRecord]:
        with self._session_factory() as db:
            rows = db.scalars(
                select(RuntimeHookDefinition)
                .where(RuntimeHookDefinition.root_session_id == root_session_id)
                .order_by(RuntimeHookDefinition.definition_id, RuntimeHookDefinition.config_revision.desc())
            ).all()
            latest: dict[str, HookDefinitionRecord] = {}
            for row in rows:
                latest.setdefault(row.definition_id, self._record(row))
            return list(latest.values())


class SQLAlchemyHookInvocationRepository(_CASRepository):
    record_type = HookInvocationRecord
    model_type = RuntimeHookInvocation
    id_field = "invocation_id"
    record_label = "hook invocation"
    initial_status = HookInvocationStatus.QUEUED

    def _row_kwargs(self, record: HookInvocationRecord) -> dict[str, Any]:
        return {**super()._row_kwargs(record), "idempotency_key": record.idempotency_key}

    def create(self, record: HookInvocationRecord) -> HookInvocationRecord:
        existing = self.get_by_idempotency_key(record.idempotency_key)
        if existing is not None:
            if _same_operation(existing, record, fields=_HOOK_OPERATION_FIELDS):
                return existing
            raise RuntimeRecordRevisionConflict(self.record_label, record.invocation_id, None, existing.revision)
        try:
            return super().create(record)
        except RuntimeRecordRevisionConflict:
            winner = self.get_by_idempotency_key(record.idempotency_key)
            if winner is not None and _same_operation(winner, record, fields=_HOOK_OPERATION_FIELDS):
                return winner
            raise

    def get_by_idempotency_key(self, idempotency_key: str) -> HookInvocationRecord | None:
        with self._session_factory() as db:
            row = db.scalar(select(RuntimeHookInvocation).where(RuntimeHookInvocation.idempotency_key == idempotency_key))
            return self._record(row) if row is not None else None

    def transition(self, invocation_id: str, status: HookInvocationStatus, expected_revision: int, **changes: Any) -> HookInvocationRecord:
        _ensure_allowed_changes(
            self.record_label, changes, {"lease_owner", "lease_expires_at", "outcome", "error"}
        )
        current = self.get(invocation_id)
        if current is None or current.revision != expected_revision:
            raise RuntimeRecordRevisionConflict(self.record_label, invocation_id, expected_revision, current.revision if current else None)
        allowed = (
            {HookInvocationStatus.RUNNING, HookInvocationStatus.CANCELLED, HookInvocationStatus.TIMED_OUT, HookInvocationStatus.INTERRUPTED}
            if current.status is HookInvocationStatus.QUEUED
            else HOOK_INVOCATION_TERMINAL_STATUSES
            if current.status is HookInvocationStatus.RUNNING
            else frozenset()
        )
        if status not in allowed:
            raise InvalidRuntimePrimitiveTransition(self.record_label, current.status, status)
        values = {**changes, "status": status}
        if status is HookInvocationStatus.RUNNING:
            values["started_at"] = current.started_at or _utc_now()
        if status in HOOK_INVOCATION_TERMINAL_STATUSES:
            finished = _utc_now()
            values["finished_at"] = finished
            if current.started_at is not None:
                values["duration_ms"] = int((finished - current.started_at).total_seconds() * 1000)
        return self._mutate(invocation_id, expected_revision, values)

    def interrupt_open(
        self,
        root_session_id: str,
        *,
        live_owner_tokens: set[str],
        now: datetime,
        observer: bool = False,
    ) -> list[HookInvocationRecord]:
        if observer:
            raise PermissionError("observer runtimes cannot interrupt hook invocations")
        interrupted: list[HookInvocationRecord] = []
        for status in (HookInvocationStatus.QUEUED, HookInvocationStatus.RUNNING):
            for record in self.list(root_session_id, status=status):
                if status is HookInvocationStatus.QUEUED:
                    should_interrupt = True
                else:
                    should_interrupt = not (
                        record.lease_owner in live_owner_tokens
                        and (record.lease_expires_at is None or record.lease_expires_at > now)
                    )
                if not should_interrupt:
                    continue
                try:
                    interrupted.append(
                        self.transition(
                            record.invocation_id,
                            HookInvocationStatus.INTERRUPTED,
                            record.revision,
                            error={"reason": "runtime owner unavailable or lease expired"},
                        )
                    )
                except RuntimeRecordRevisionConflict:
                    continue
        return interrupted


class SQLAlchemyExecutionTaskRepository(_CASRepository):
    record_type = ExecutionTaskRecord
    model_type = RuntimeExecutionTask
    id_field = "task_id"
    record_label = "execution task"
    initial_status = ExecutionTaskStatus.PENDING

    def transition(self, task_id: str, status: ExecutionTaskStatus, expected_revision: int, **changes: Any) -> ExecutionTaskRecord:
        _ensure_allowed_changes(
            self.record_label, changes, {"exit_code", "termination_reason", "process_owner_token"}
        )
        current = self.get(task_id)
        if current is None or current.revision != expected_revision:
            raise RuntimeRecordRevisionConflict(self.record_label, task_id, expected_revision, current.revision if current else None)
        allowed = (
            {ExecutionTaskStatus.RUNNING, ExecutionTaskStatus.FAILED, ExecutionTaskStatus.KILLED, ExecutionTaskStatus.TIMED_OUT, ExecutionTaskStatus.INTERRUPTED}
            if current.status is ExecutionTaskStatus.PENDING
            else EXECUTION_TASK_TERMINAL_STATUSES
            if current.status is ExecutionTaskStatus.RUNNING
            else frozenset()
        )
        if status not in allowed:
            raise InvalidRuntimePrimitiveTransition(self.record_label, current.status, status)
        values = {**changes, "status": status}
        if status is ExecutionTaskStatus.RUNNING:
            values["started_at"] = current.started_at or _utc_now()
        if status in EXECUTION_TASK_TERMINAL_STATUSES:
            values["finished_at"] = _utc_now()
        return self._mutate(task_id, expected_revision, values)

    def interrupt_open(
        self,
        root_session_id: str,
        *,
        live_owner_tokens: set[str],
        now: datetime,
        observer: bool = False,
    ) -> list[ExecutionTaskRecord]:
        del now
        if observer:
            raise PermissionError("observer runtimes cannot interrupt execution tasks")
        interrupted: list[ExecutionTaskRecord] = []
        for status in (ExecutionTaskStatus.PENDING, ExecutionTaskStatus.RUNNING):
            for record in self.list(root_session_id, status=status):
                if record.process_owner_token in live_owner_tokens:
                    continue
                try:
                    interrupted.append(
                        self.transition(
                            record.task_id,
                            ExecutionTaskStatus.INTERRUPTED,
                            record.revision,
                            termination_reason="runtime owner unavailable",
                        )
                    )
                except RuntimeRecordRevisionConflict:
                    continue
        return interrupted

    def append_output(self, task_id: str, data: bytes, expected_revision: int) -> ExecutionTaskRecord:
        if not isinstance(data, bytes):
            raise TypeError("execution output must be bytes")
        with _transaction(self._session_factory, immediate=True) as db:
            row = db.get(RuntimeExecutionTask, task_id)
            actual = row.revision if row is not None else None
            if row is None or actual != expected_revision:
                raise RuntimeRecordRevisionConflict(self.record_label, task_id, expected_revision, actual)
            current = self._record(row)
            if current.status is not ExecutionTaskStatus.RUNNING:
                raise InvalidRuntimePrimitiveTransition(self.record_label, current.status, current.status)
            output = bytes(row.output or b"") + data
            if len(output) > MAX_EXECUTION_OUTPUT_BYTES:
                raise ValueError(
                    f"execution output exceeds per-task limit of {MAX_EXECUTION_OUTPUT_BYTES} bytes"
                )
            next_record = dataclasses.replace(
                current,
                revision=actual + 1,
                output_byte_count=len(output),
                last_readable_cursor=len(output),
                updated_at=_utc_now(),
            )
            result = db.execute(
                update(RuntimeExecutionTask)
                .where(RuntimeExecutionTask.record_id == task_id, RuntimeExecutionTask.revision == expected_revision)
                .values(revision=actual + 1, data=_encode(next_record), output=output)
            )
            if result.rowcount == 1:
                return next_record
        current = self.get(task_id)
        raise RuntimeRecordRevisionConflict(self.record_label, task_id, expected_revision, current.revision if current else None)

    def read_output(self, task_id: str, *, cursor: int, max_bytes: int) -> ExecutionOutputChunk:
        if cursor < 0 or max_bytes < 0:
            raise ValueError("cursor and max_bytes must be non-negative")
        if max_bytes > MAX_EXECUTION_READ_BYTES:
            raise ValueError(
                f"execution output request exceeds per-read limit of {MAX_EXECUTION_READ_BYTES} bytes"
            )
        with self._session_factory() as db:
            row = db.get(RuntimeExecutionTask, task_id)
            if row is None:
                raise KeyError(task_id)
            output = bytes(row.output or b"")
            start = min(cursor, len(output))
            data = output[start : start + max_bytes]
            return ExecutionOutputChunk(data=data, next_cursor=start + len(data), total_bytes=len(output))


class SQLAlchemyTeamRepository(_CASRepository):
    record_type = TeamRecord
    model_type = RuntimeTeam
    id_field = "team_id"
    record_label = "team"
    initial_status = TeamStatus.ACTIVE

    def transition(self, team_id: str, status: TeamStatus, expected_revision: int) -> TeamRecord:
        current = self.get(team_id)
        if current is None or current.revision != expected_revision:
            raise RuntimeRecordRevisionConflict(self.record_label, team_id, expected_revision, current.revision if current else None)
        allowed = {TeamStatus.CLOSING} if current.status is TeamStatus.ACTIVE else {TeamStatus.CLOSED} if current.status is TeamStatus.CLOSING else set()
        if status not in allowed:
            raise InvalidRuntimePrimitiveTransition(self.record_label, current.status, status)
        changes: dict[str, Any] = {"status": status}
        if status is TeamStatus.CLOSED:
            changes["closed_at"] = _utc_now()
        return self._mutate(team_id, expected_revision, changes)


class SQLAlchemyTeamMemberRepository(_CASRepository):
    record_type = TeamMemberRecord
    model_type = RuntimeTeamMember
    id_field = "member_id"
    record_label = "team member"
    initial_status = TeamMemberStatus.STARTING

    def _row_kwargs(self, record: TeamMemberRecord) -> dict[str, Any]:
        return {**super()._row_kwargs(record), "team_id": record.team_id}

    def transition(self, member_id: str, status: TeamMemberStatus, expected_revision: int, **changes: Any) -> TeamMemberRecord:
        _ensure_allowed_changes(
            self.record_label,
            changes,
            {"assigned_task_ids", "shutdown_request_id", "owner_token", "lease_expires_at"},
        )
        current = self.get(member_id)
        if current is None or current.revision != expected_revision:
            raise RuntimeRecordRevisionConflict(self.record_label, member_id, expected_revision, current.revision if current else None)
        transitions = {
            TeamMemberStatus.STARTING: {TeamMemberStatus.RUNNING, TeamMemberStatus.SHUTDOWN_REQUESTED, TeamMemberStatus.FAILED, TeamMemberStatus.INTERRUPTED},
            TeamMemberStatus.RUNNING: {TeamMemberStatus.IDLE, TeamMemberStatus.SHUTDOWN_REQUESTED, TeamMemberStatus.FAILED, TeamMemberStatus.INTERRUPTED},
            TeamMemberStatus.IDLE: {TeamMemberStatus.RUNNING, TeamMemberStatus.SHUTDOWN_REQUESTED, TeamMemberStatus.FAILED, TeamMemberStatus.INTERRUPTED},
            TeamMemberStatus.SHUTDOWN_REQUESTED: {TeamMemberStatus.STOPPED, TeamMemberStatus.FAILED, TeamMemberStatus.INTERRUPTED},
            TeamMemberStatus.INTERRUPTED: {TeamMemberStatus.STARTING},
        }
        if status not in transitions.get(current.status, set()):
            raise InvalidRuntimePrimitiveTransition(self.record_label, current.status, status)
        values = {**changes, "status": status}
        if current.status is TeamMemberStatus.INTERRUPTED and status is TeamMemberStatus.STARTING:
            values["stopped_at"] = None
        if status in TEAM_MEMBER_TERMINAL_STATUSES:
            values["stopped_at"] = _utc_now()
        return self._mutate(member_id, expected_revision, values)

    def update_mailbox_cursor(self, member_id: str, expected_revision: int, cursor: int) -> TeamMemberRecord:
        current = self.get(member_id)
        if current is not None and cursor < current.mailbox_cursor:
            raise ValueError("mailbox cursor cannot move backwards")
        return self._mutate(member_id, expected_revision, {"mailbox_cursor": cursor})

    def reconcile(
        self,
        root_session_id: str,
        *,
        live_owner_tokens: set[str],
        now: datetime,
        observer: bool,
    ) -> list[TeamMemberRecord]:
        if observer:
            raise PermissionError("observer runtimes cannot reconcile team members")
        changed: list[TeamMemberRecord] = []
        for status in (
            TeamMemberStatus.STARTING,
            TeamMemberStatus.RUNNING,
            TeamMemberStatus.IDLE,
            TeamMemberStatus.SHUTDOWN_REQUESTED,
        ):
            for record in self.list(root_session_id, status=status):
                if (
                    record.owner_token in live_owner_tokens
                    and (record.lease_expires_at is None or record.lease_expires_at > now)
                ):
                    continue
                try:
                    changed.append(
                        self.transition(
                            record.member_id,
                            TeamMemberStatus.INTERRUPTED,
                            record.revision,
                        )
                    )
                except RuntimeRecordRevisionConflict:
                    continue
        return changed


class SQLAlchemyTeamMessageRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _record(row: RuntimeTeamMessage) -> TeamMessageRecord:
        return _decode(TeamMessageRecord, row.data)

    def append(self, record: TeamMessageRecord) -> TeamMessageRecord:
        detached = TeamMessageRecord(**record.__dict__)
        with _transaction(self._session_factory, immediate=True) as db:
            existing = db.get(RuntimeTeamMessage, detached.message_id)
            if existing is not None:
                current = self._record(existing)
                if _same_operation(current, detached, fields=_TEAM_MESSAGE_FIELDS):
                    return current
                raise RuntimeRecordRevisionConflict("team message", detached.message_id, None, 0)
            sequence = detached.sequence
            if sequence == 0:
                sequence = int(db.scalar(select(func.coalesce(func.max(RuntimeTeamMessage.sequence), 0)).where(RuntimeTeamMessage.team_id == detached.team_id)) or 0) + 1
            persisted = dataclasses.replace(detached, sequence=sequence)
            db.add(RuntimeTeamMessage(
                record_id=persisted.message_id,
                root_session_id=persisted.root_session_id,
                team_id=persisted.team_id,
                recipient_member_id=persisted.recipient_member_id,
                sequence=persisted.sequence,
                data=_encode(persisted),
            ))
            return persisted

    def list_for_member(self, team_id: str, member_id: str, *, after_sequence: int) -> list[TeamMessageRecord]:
        with self._session_factory() as db:
            rows = db.scalars(
                select(RuntimeTeamMessage)
                .where(
                    RuntimeTeamMessage.team_id == team_id,
                    RuntimeTeamMessage.sequence > after_sequence,
                    (RuntimeTeamMessage.recipient_member_id == member_id) | (RuntimeTeamMessage.recipient_member_id.is_(None)),
                )
                .order_by(RuntimeTeamMessage.sequence)
            ).all()
            return [self._record(row) for row in rows]


class SQLAlchemySkillActivationRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._migrate_sqlite_schema()

    def _migrate_sqlite_schema(self) -> None:
        with self._session_factory() as db:
            bind = db.get_bind()
            if bind.dialect.name != "sqlite":
                return
            if isinstance(bind, Connection):
                if bind.in_transaction():
                    with bind.begin_nested():
                        self._apply_sqlite_migration(bind)
                else:
                    bind.exec_driver_sql("BEGIN IMMEDIATE")
                    try:
                        self._apply_sqlite_migration(bind)
                        bind.commit()
                    except BaseException:
                        bind.rollback()
                        raise
                return
            with bind.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    self._apply_sqlite_migration(connection)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise

    @staticmethod
    def _apply_sqlite_migration(connection: Connection) -> None:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS runtime_schema_migrations "
                "(name TEXT PRIMARY KEY, version INTEGER NOT NULL)"
            )
        )
        columns = connection.execute(
            text("PRAGMA table_info(runtime_skill_activations)")
        ).all()
        if not columns:
            return
        names = {column.name for column in columns}
        version = connection.execute(
            text(
                "SELECT version FROM runtime_schema_migrations "
                "WHERE name = 'runtime_skill_activations'"
            )
        ).scalar_one_or_none()
        if {"status", "revision"} <= names:
            if version is None:
                connection.execute(
                    text(
                        "INSERT INTO runtime_schema_migrations (name, version) "
                        "VALUES ('runtime_skill_activations', 1)"
                    )
                )
            return

        rows = connection.execute(
            text(
                "SELECT rowid, record_id, root_session_id, agent_id, skill_name, "
                "skill_digest, data FROM runtime_skill_activations"
            )
        ).all()
        winners: dict[tuple[str, str, str], tuple[tuple[str, int, str], Any]] = {}
        for row in rows:
            data = json.loads(row.data) if isinstance(row.data, str) else dict(row.data)
            if not isinstance(data, dict):
                raise ValueError("legacy skill activation data must be a JSON object")
            order = (
                str(data.get("created_at") or ""),
                int(row.rowid),
                str(row.record_id),
            )
            key = (str(row.root_session_id), str(row.agent_id), str(row.skill_name))
            current = winners.get(key)
            if current is None or order < current[0]:
                winners[key] = (order, (row, data))

        connection.execute(
            text("DROP TABLE IF EXISTS runtime_skill_activations_v1_migration")
        )
        connection.execute(
            text(
                "CREATE TABLE runtime_skill_activations_v1_migration ("
                "record_id VARCHAR NOT NULL PRIMARY KEY, "
                "root_session_id VARCHAR NOT NULL, agent_id VARCHAR NOT NULL, "
                "skill_name VARCHAR NOT NULL, skill_digest VARCHAR NOT NULL, "
                "status VARCHAR NOT NULL, revision INTEGER NOT NULL, data JSON NOT NULL, "
                "UNIQUE (root_session_id, agent_id, skill_name))"
            )
        )
        migrated_at = _utc_now().isoformat()
        for _, (row, data) in sorted(winners.values(), key=lambda item: item[0]):
            created_at = str(data.get("created_at") or migrated_at)
            data.update(
                {
                    "activation_id": str(row.record_id),
                    "root_session_id": str(row.root_session_id),
                    "agent_id": str(row.agent_id),
                    "skill_name": str(row.skill_name),
                    "skill_digest": str(row.skill_digest),
                    "status": SkillActivationStatus.ACTIVE.value,
                    "revision": 1,
                    "created_at": created_at,
                    "updated_at": str(data.get("updated_at") or created_at),
                }
            )
            connection.execute(
                text(
                    "INSERT INTO runtime_skill_activations_v1_migration "
                    "(record_id, root_session_id, agent_id, skill_name, skill_digest, "
                    "status, revision, data) VALUES "
                    "(:record_id, :root_session_id, :agent_id, :skill_name, :skill_digest, "
                    "'active', 1, :data)"
                ),
                {
                    "record_id": row.record_id,
                    "root_session_id": row.root_session_id,
                    "agent_id": row.agent_id,
                    "skill_name": row.skill_name,
                    "skill_digest": row.skill_digest,
                    "data": json.dumps(data),
                },
            )
        connection.execute(text("DROP TABLE runtime_skill_activations"))
        connection.execute(
            text(
                "ALTER TABLE runtime_skill_activations_v1_migration "
                "RENAME TO runtime_skill_activations"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX ix_runtime_skill_activations_root_session_id "
                "ON runtime_skill_activations (root_session_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX ix_runtime_skill_activations_agent_id "
                "ON runtime_skill_activations (agent_id)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO runtime_schema_migrations (name, version) "
                "VALUES ('runtime_skill_activations', 1) "
                "ON CONFLICT(name) DO UPDATE SET version = excluded.version"
            )
        )

    @staticmethod
    def _record(row: RuntimeSkillActivation) -> SkillActivationRecord:
        return _decode(SkillActivationRecord, row.data)

    def create(self, record: SkillActivationRecord) -> SkillActivationRecord:
        claimed, created = self.claim_by_name(record)
        if created:
            claimed, _ = self.finalize_active(claimed.activation_id, claimed.revision)
        return claimed

    def claim(self, record: SkillActivationRecord) -> tuple[SkillActivationRecord, bool]:
        return self.claim_by_name(record)

    def claim_by_name(self, record: SkillActivationRecord) -> tuple[SkillActivationRecord, bool]:
        detached = SkillActivationRecord(**record.__dict__)
        existing = self.get_by_name(
            detached.root_session_id,
            detached.agent_id,
            detached.skill_name,
        )
        if existing is not None:
            return existing, False
        if detached.revision != 0 or detached.status is not SkillActivationStatus.PREPARING:
            raise ValueError("new skill activations must start preparing at revision 0")
        try:
            with _transaction(self._session_factory) as db:
                db.add(RuntimeSkillActivation(
                    record_id=detached.activation_id,
                    root_session_id=detached.root_session_id,
                    agent_id=detached.agent_id,
                    skill_name=detached.skill_name,
                    skill_digest=detached.skill_digest,
                    status=detached.status.value,
                    revision=detached.revision,
                    data=_encode(detached),
                ))
        except IntegrityError as exc:
            winner = self.get_by_name(
                detached.root_session_id,
                detached.agent_id,
                detached.skill_name,
            )
            if winner is not None:
                return winner, False
            raise RuntimeRecordRevisionConflict("skill activation", detached.activation_id, None, 0) from exc
        return detached, True

    def get_by_name(
        self,
        root_session_id: str,
        agent_id: str,
        skill_name: str,
    ) -> SkillActivationRecord | None:
        with self._session_factory() as db:
            row = db.scalar(select(RuntimeSkillActivation).where(
                RuntimeSkillActivation.root_session_id == root_session_id,
                RuntimeSkillActivation.agent_id == agent_id,
                RuntimeSkillActivation.skill_name == skill_name,
            ))
            return self._record(row) if row is not None else None

    def get_for_skill(self, root_session_id: str, agent_id: str, skill_name: str, skill_digest: str) -> SkillActivationRecord | None:
        record = self.get_by_name(root_session_id, agent_id, skill_name)
        return record if record is not None and record.skill_digest == skill_digest else None

    def finalize_active(
        self,
        activation_id: str,
        expected_revision: int,
    ) -> tuple[SkillActivationRecord, bool]:
        lost_update = False
        with _transaction(self._session_factory, immediate=True) as db:
            row = db.get(RuntimeSkillActivation, activation_id)
            actual = row.revision if row is not None else None
            if row is None:
                raise RuntimeRecordRevisionConflict(
                    "skill activation", activation_id, expected_revision, None
                )
            current = self._record(row)
            if current.status is SkillActivationStatus.ACTIVE:
                return current, False
            if actual != expected_revision:
                raise RuntimeRecordRevisionConflict(
                    "skill activation", activation_id, expected_revision, actual
                )
            active = dataclasses.replace(
                current,
                status=SkillActivationStatus.ACTIVE,
                revision=actual + 1,
                updated_at=_utc_now(),
            )
            result = db.execute(
                update(RuntimeSkillActivation)
                .where(
                    RuntimeSkillActivation.record_id == activation_id,
                    RuntimeSkillActivation.revision == expected_revision,
                    RuntimeSkillActivation.status == SkillActivationStatus.PREPARING.value,
                )
                .values(
                    status=active.status.value,
                    revision=active.revision,
                    data=_encode(active),
                )
            )
            if result.rowcount == 1:
                return active, True
            lost_update = True
        assert lost_update
        current = self.get_by_id(activation_id)
        if current is not None and current.status is SkillActivationStatus.ACTIVE:
            return current, False
        raise RuntimeRecordRevisionConflict(
            "skill activation",
            activation_id,
            expected_revision,
            current.revision if current is not None else None,
        )

    def get_by_id(self, activation_id: str) -> SkillActivationRecord | None:
        with self._session_factory() as db:
            row = db.get(RuntimeSkillActivation, activation_id)
            return self._record(row) if row is not None else None

    def list(self, root_session_id: str, *, agent_id: str | None = None) -> list[SkillActivationRecord]:
        with self._session_factory() as db:
            statement = select(RuntimeSkillActivation).where(RuntimeSkillActivation.root_session_id == root_session_id)
            if agent_id is not None:
                statement = statement.where(RuntimeSkillActivation.agent_id == agent_id)
            return [self._record(row) for row in db.scalars(statement.order_by(RuntimeSkillActivation.record_id)).all()]
