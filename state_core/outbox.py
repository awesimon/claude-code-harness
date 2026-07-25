"""Atomic outbox coordinators for approval and hook side effects."""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping

from sqlalchemy import JSON, String, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from models import Base

from .runtime_primitives import (
    HOOK_INVOCATION_TERMINAL_STATUSES,
    HookInvocationRecord,
    HookInvocationStatus,
    PermissionRequestRecord,
    PermissionRequestStatus,
    PermissionRuleKind,
    PermissionRuleRecord,
    PermissionRuleScope,
)
from .runtime_records import RuntimeRecordRevisionConflict
from .sqlalchemy_primitives import (
    RuntimeHookInvocation,
    RuntimePermissionRequest,
    RuntimePermissionRule,
    SessionFactory,
    _decode,
    _encode,
    _transaction,
    _utc_now,
)
from .types import SessionState


@dataclass(frozen=True)
class OutboxEventRecord:
    event_id: str
    root_session_id: str
    kind: str
    aggregate_id: str
    payload: Mapping[str, Any]
    revision: int = 0
    delivered_at: datetime | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)


class RuntimeOutboxEvent(Base):
    __tablename__ = "runtime_outbox_events"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    aggregate_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


def _decode_outbox(row: RuntimeOutboxEvent) -> OutboxEventRecord:
    values = dict(row.data)
    for name in ("delivered_at", "created_at", "updated_at"):
        if values.get(name) is not None and not isinstance(values[name], datetime):
            values[name] = datetime.fromisoformat(values[name])
    return OutboxEventRecord(**values)


def _insert_outbox(
    db: Session,
    *,
    root_session_id: str,
    kind: str,
    aggregate_id: str,
    payload: Mapping[str, Any],
    event_id: str | None = None,
) -> OutboxEventRecord:
    record = OutboxEventRecord(
        event_id=event_id or f"outbox_{uuid.uuid4().hex}",
        root_session_id=root_session_id,
        kind=kind,
        aggregate_id=aggregate_id,
        payload=dict(payload),
    )
    db.add(
        RuntimeOutboxEvent(
            record_id=record.event_id,
            root_session_id=root_session_id,
            kind=kind,
            aggregate_id=aggregate_id,
            status="pending",
            revision=0,
            data=_encode(record),
        )
    )
    return record


class SQLAlchemyOutboxRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def get(self, event_id: str) -> OutboxEventRecord | None:
        with self._session_factory() as db:
            row = db.get(RuntimeOutboxEvent, event_id)
            return _decode_outbox(row) if row is not None else None

    def list(
        self,
        root_session_id: str,
        *,
        kind: str | None = None,
        pending_only: bool = False,
    ) -> list[OutboxEventRecord]:
        with self._session_factory() as db:
            statement = select(RuntimeOutboxEvent).where(
                RuntimeOutboxEvent.root_session_id == root_session_id
            )
            if kind is not None:
                statement = statement.where(RuntimeOutboxEvent.kind == kind)
            if pending_only:
                statement = statement.where(RuntimeOutboxEvent.status == "pending")
            rows = db.scalars(statement.order_by(RuntimeOutboxEvent.record_id)).all()
            return [_decode_outbox(row) for row in rows]

    def mark_delivered(self, event_id: str, expected_revision: int) -> OutboxEventRecord:
        with _transaction(self._session_factory, immediate=True) as db:
            row = db.get(RuntimeOutboxEvent, event_id)
            actual = row.revision if row is not None else None
            if row is None or actual != expected_revision:
                raise RuntimeRecordRevisionConflict(
                    "outbox event", event_id, expected_revision, actual
                )
            current = _decode_outbox(row)
            if current.delivered_at is not None:
                return current
            updated = dataclasses.replace(
                current,
                revision=current.revision + 1,
                delivered_at=_utc_now(),
                updated_at=_utc_now(),
            )
            row.revision = updated.revision
            row.status = "delivered"
            row.data = _encode(updated)
            return updated


class SQLAlchemyApprovalTransactionService:
    """Resolve a request, mutate durable rules, and enqueue one event atomically."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._before_commit: Callable[[], None] | None = None

    def resolve(
        self,
        request_id: str,
        status: PermissionRequestStatus,
        expected_revision: int,
        *,
        actor: str,
        decision_reason: str | None,
        updated_input: Mapping[str, Any] | None,
        permission_updates: tuple[Mapping[str, Any], ...],
    ) -> PermissionRequestRecord:
        with _transaction(self._session_factory, immediate=True) as db:
            row = db.get(RuntimePermissionRequest, request_id)
            actual = row.revision if row is not None else None
            if row is None or actual != expected_revision:
                raise RuntimeRecordRevisionConflict(
                    "permission request", request_id, expected_revision, actual
                )
            current = _decode(PermissionRequestRecord, row.data)
            if current.status is not PermissionRequestStatus.PENDING:
                raise RuntimeRecordRevisionConflict(
                    "permission request", request_id, expected_revision, current.revision
                )
            self._apply_rules(db, current.root_session_id, permission_updates)
            self._apply_permission_snapshots(db, current.root_session_id, permission_updates)
            resolved = dataclasses.replace(
                current,
                status=status,
                revision=current.revision + 1,
                actor=actor,
                decision_reason=decision_reason,
                updated_input=dict(updated_input) if updated_input is not None else None,
                permission_updates=permission_updates,
                resolved_at=_utc_now(),
                updated_at=_utc_now(),
            )
            result = db.execute(
                update(RuntimePermissionRequest)
                .where(
                    RuntimePermissionRequest.record_id == request_id,
                    RuntimePermissionRequest.revision == expected_revision,
                    RuntimePermissionRequest.status == PermissionRequestStatus.PENDING.value,
                )
                .values(
                    status=status.value,
                    revision=resolved.revision,
                    data=_encode(resolved),
                )
            )
            if result.rowcount != 1:
                raise RuntimeRecordRevisionConflict(
                    "permission request", request_id, expected_revision, actual
                )
            _insert_outbox(
                db,
                root_session_id=current.root_session_id,
                kind="permission_resolved",
                aggregate_id=request_id,
                event_id=f"permission_resolved:{request_id}",
                payload={
                    "request_id": request_id,
                    "status": status.value,
                    "revision": resolved.revision,
                    "permission_updates": list(permission_updates),
                },
            )
            if self._before_commit is not None:
                self._before_commit()
            return resolved

    @staticmethod
    def _apply_rules(
        db: Session,
        root_session_id: str,
        updates: tuple[Mapping[str, Any], ...],
    ) -> None:
        durable_scopes = {
            PermissionRuleScope.USER_SETTINGS,
            PermissionRuleScope.PROJECT_SETTINGS,
            PermissionRuleScope.LOCAL_SETTINGS,
        }
        rows = db.scalars(
            select(RuntimePermissionRule).where(
                RuntimePermissionRule.root_session_id == root_session_id
            )
        ).all()
        active: list[tuple[RuntimePermissionRule, PermissionRuleRecord]] = [
            (row, _decode(PermissionRuleRecord, row.data)) for row in rows
        ]
        for item in updates:
            scope = PermissionRuleScope(item["destination"])
            if scope not in durable_scopes:
                continue
            operation = item["type"]
            if operation in {"replaceRules", "removeRules"}:
                targets = set(item["rules"])
                for index, (row, record) in enumerate(active):
                    if (
                        record.revoked_at is None
                        and record.scope is scope
                        and record.kind is PermissionRuleKind.RULE
                        and record.behavior == item["behavior"]
                        and (operation == "replaceRules" or record.rule in targets)
                    ):
                        active[index] = (
                            row,
                            SQLAlchemyApprovalTransactionService._revoke(row, record),
                        )
            elif operation in {"setMode", "removeDirectories"}:
                kind = (
                    PermissionRuleKind.MODE
                    if operation == "setMode"
                    else PermissionRuleKind.DIRECTORY
                )
                targets = set(item.get("directories", ()))
                for index, (row, record) in enumerate(active):
                    if (
                        record.revoked_at is None
                        and record.scope is scope
                        and record.kind is kind
                        and (operation == "setMode" or record.directory in targets)
                    ):
                        active[index] = (
                            row,
                            SQLAlchemyApprovalTransactionService._revoke(row, record),
                        )
            if operation in {"addRules", "replaceRules"}:
                for rule in item["rules"]:
                    active.append(
                        SQLAlchemyApprovalTransactionService._insert_rule(
                            db,
                            root_session_id,
                            scope,
                            PermissionRuleKind.RULE,
                            behavior=item["behavior"],
                            rule=rule,
                        )
                    )
            elif operation == "setMode":
                active.append(
                    SQLAlchemyApprovalTransactionService._insert_rule(
                        db,
                        root_session_id,
                        scope,
                        PermissionRuleKind.MODE,
                        mode=item["mode"],
                    )
                )
            elif operation == "addDirectories":
                for directory in item["directories"]:
                    active.append(
                        SQLAlchemyApprovalTransactionService._insert_rule(
                            db,
                            root_session_id,
                            scope,
                            PermissionRuleKind.DIRECTORY,
                            directory=directory,
                        )
                    )

    @staticmethod
    def _apply_permission_snapshots(
        db: Session,
        root_session_id: str,
        updates: tuple[Mapping[str, Any], ...],
    ) -> None:
        snapshot_updates = tuple(
            item for item in updates if item["destination"] in {"session", "cliArg"}
        )
        if not snapshot_updates:
            return
        session_table = Base.metadata.tables["runtime_sessions"]
        row = (
            db.execute(
                select(session_table.c.revision, session_table.c.state).where(
                    session_table.c.session_id == root_session_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise RuntimeRecordRevisionConflict("session", root_session_id, None, None)
        expected_revision = int(row["revision"])
        state = SessionState.from_dict(row["state"])
        snapshots = {
            scope: [dict(item) for item in state.permission_scope_snapshots[scope]]
            for scope in ("session", "cliArg")
        }
        for update_item in snapshot_updates:
            SQLAlchemyApprovalTransactionService._apply_snapshot_update(
                snapshots[update_item["destination"]], update_item
            )
        state.permission_scope_snapshots = snapshots
        state.revision = expected_revision + 1
        state.updated_at = _utc_now()
        result = db.execute(
            update(session_table)
            .where(
                session_table.c.session_id == root_session_id,
                session_table.c.revision == expected_revision,
            )
            .values(
                revision=state.revision,
                state=state.to_dict(),
                updated_at=state.updated_at,
            )
        )
        if result.rowcount != 1:
            raise RuntimeRecordRevisionConflict(
                "session", root_session_id, expected_revision, expected_revision
            )

    @staticmethod
    def _apply_snapshot_update(items: list[dict[str, Any]], update_item: Mapping[str, Any]) -> None:
        operation = update_item["type"]
        if operation in {"replaceRules", "removeRules"}:
            targets = set(update_item["rules"])
            items[:] = [
                item
                for item in items
                if not (
                    item.get("kind") == "rule"
                    and item.get("behavior") == update_item["behavior"]
                    and (operation == "replaceRules" or item.get("rule") in targets)
                )
            ]
        elif operation == "setMode":
            items[:] = [item for item in items if item.get("kind") != "mode"]
        elif operation == "removeDirectories":
            targets = set(update_item["directories"])
            items[:] = [
                item
                for item in items
                if not (item.get("kind") == "directory" and item.get("directory") in targets)
            ]
        if operation in {"addRules", "replaceRules"}:
            items.extend(
                {
                    "kind": "rule",
                    "behavior": update_item["behavior"],
                    "rule": rule,
                }
                for rule in update_item["rules"]
            )
        elif operation == "setMode":
            items.append({"kind": "mode", "mode": update_item["mode"]})
        elif operation == "addDirectories":
            for directory in update_item["directories"]:
                if not any(
                    item.get("kind") == "directory" and item.get("directory") == directory
                    for item in items
                ):
                    items.append({"kind": "directory", "directory": directory})

    @staticmethod
    def _revoke(row: RuntimePermissionRule, record: PermissionRuleRecord) -> PermissionRuleRecord:
        updated = dataclasses.replace(
            record,
            revision=record.revision + 1,
            revoked_at=_utc_now(),
            updated_at=_utc_now(),
        )
        row.revision = updated.revision
        row.data = _encode(updated)
        return updated

    @staticmethod
    def _insert_rule(
        db: Session,
        root_session_id: str,
        scope: PermissionRuleScope,
        kind: PermissionRuleKind,
        **values: Any,
    ) -> tuple[RuntimePermissionRule, PermissionRuleRecord]:
        record = PermissionRuleRecord(
            rule_id=f"permission_rule_{uuid.uuid4().hex}",
            root_session_id=root_session_id,
            kind=kind,
            scope=scope,
            source="approval",
            **values,
        )
        row = RuntimePermissionRule(
            record_id=record.rule_id,
            root_session_id=root_session_id,
            status=None,
            revision=0,
            data=_encode(record),
        )
        db.add(row)
        return row, record


class SQLAlchemyHookTransactionService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def complete_with_rewake(
        self,
        invocation_id: str,
        status: HookInvocationStatus,
        expected_revision: int,
        *,
        outcome: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> HookInvocationRecord:
        with _transaction(self._session_factory, immediate=True) as db:
            row = db.get(RuntimeHookInvocation, invocation_id)
            actual = row.revision if row is not None else None
            if row is None or actual != expected_revision:
                raise RuntimeRecordRevisionConflict(
                    "hook invocation", invocation_id, expected_revision, actual
                )
            current = _decode(HookInvocationRecord, row.data)
            if current.status is not HookInvocationStatus.RUNNING:
                raise RuntimeRecordRevisionConflict(
                    "hook invocation", invocation_id, expected_revision, actual
                )
            if status not in HOOK_INVOCATION_TERMINAL_STATUSES:
                raise ValueError("hook completion must be terminal")
            finished = _utc_now()
            completed = dataclasses.replace(
                current,
                status=status,
                revision=current.revision + 1,
                lease_owner=None,
                lease_expires_at=None,
                outcome=outcome,
                error=error,
                finished_at=finished,
                duration_ms=(
                    int((finished - current.started_at).total_seconds() * 1000)
                    if current.started_at is not None
                    else None
                ),
                updated_at=finished,
            )
            row.status = status.value
            row.revision = completed.revision
            row.data = _encode(completed)
            _insert_outbox(
                db,
                root_session_id=current.root_session_id,
                kind="hook_async_rewake",
                aggregate_id=invocation_id,
                event_id=f"hook_async_rewake:{invocation_id}",
                payload={
                    "invocation_id": invocation_id,
                    "event": current.event,
                    "status": status.value,
                    "correlation_id": current.correlation_id,
                },
            )
            return completed
