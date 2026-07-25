"""Idempotent migration from legacy conversation tables into state-core."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep
from typing import Any, Iterator
from uuid import uuid4

from sqlalchemy import select

from models import Conversation as LegacyConversation
from models import Message
from models import Plan as LegacyPlan
from models import Task as LegacyTask

from .plan_files import PlanFileStore
from .runtime import SessionRuntime, plan_slug
from .runtime_records import RuntimeRecordRevisionConflict
from .sqlalchemy_store import RuntimeSession, SQLAlchemyStateStore
from .tool_events import normalize_tool_call, normalize_tool_result
from .types import EventType, NewTask, PlanState, TaskMutation, TaskStatus

_CONVERSATION_NAMESPACE = "api.conversation"
_PLAN_NAMESPACE = "api.plan"
_MIGRATION_NAMESPACE = "state_core.migration"
_MIGRATION_LEASE_TTL = timedelta(minutes=5)
_MIGRATION_POLL_SECONDS = 0.01


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _lease_expired(snapshot: dict[str, Any], now: datetime) -> bool:
    raw = snapshot.get("leaseExpiresAt")
    if not isinstance(raw, str):
        return True
    try:
        expires_at = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= now


@dataclass
class _MigrationLease:
    store: SQLAlchemyStateStore
    session_id: str
    owner_token: str

    def _transition(self, status: str) -> bool:
        record = self.store.metadata.get(self.session_id, _MIGRATION_NAMESPACE)
        if (
            record is None
            or record.snapshot.get("ownerToken") != self.owner_token
            or record.snapshot.get("status") != "running"
        ):
            return False
        now = _utc_now()
        snapshot = {
            **record.snapshot,
            "status": status,
            "updatedAt": now.isoformat(),
            "leaseExpiresAt": (
                (now + _MIGRATION_LEASE_TTL).isoformat()
                if status == "running"
                else now.isoformat()
            ),
        }
        if status != "running":
            snapshot["finishedAt"] = now.isoformat()
        try:
            self.store.metadata.put(
                self.session_id,
                _MIGRATION_NAMESPACE,
                snapshot,
                expected_revision=record.revision,
            )
        except RuntimeRecordRevisionConflict:
            return False
        return True

    def heartbeat(self) -> None:
        if not self._transition("running"):
            raise RuntimeError(f"migration lease lost for session {self.session_id}")

    def complete(self) -> None:
        if not self._transition("completed"):
            raise RuntimeError(f"migration lease lost for session {self.session_id}")

    def fail(self) -> None:
        self._transition("failed")


@contextmanager
def _migration_lease(
    store: SQLAlchemyStateStore,
    session_id: str,
) -> Iterator[_MigrationLease | None]:
    owner_token = str(uuid4())
    lease: _MigrationLease | None = None
    while lease is None:
        record = store.metadata.get(session_id, _MIGRATION_NAMESPACE)
        if record is not None and record.snapshot.get("status") == "completed":
            yield None
            return
        now = _utc_now()
        if (
            record is not None
            and record.snapshot.get("status") == "running"
            and not _lease_expired(dict(record.snapshot), now)
        ):
            sleep(_MIGRATION_POLL_SECONDS)
            continue
        snapshot = {
            "status": "running",
            "ownerToken": owner_token,
            "startedAt": now.isoformat(),
            "updatedAt": now.isoformat(),
            "leaseExpiresAt": (now + _MIGRATION_LEASE_TTL).isoformat(),
        }
        try:
            store.metadata.put(
                session_id,
                _MIGRATION_NAMESPACE,
                snapshot,
                expected_revision=record.revision if record is not None else None,
            )
        except RuntimeRecordRevisionConflict:
            continue
        lease = _MigrationLease(store, session_id, owner_token)

    try:
        yield lease
    except BaseException:
        lease.fail()
        raise
    else:
        lease.complete()


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _backfill_api_metadata(
    store: SQLAlchemyStateStore,
    session_id: str,
    conversation: LegacyConversation | None,
    legacy_plan: LegacyPlan | None,
) -> None:
    if (
        conversation is not None
        and store.metadata.get(session_id, _CONVERSATION_NAMESPACE) is None
    ):
        store.metadata.put(
            session_id,
            _CONVERSATION_NAMESPACE,
            {
                "title": conversation.title,
                "created_at": _timestamp(conversation.created_at),
                "updated_at": _timestamp(conversation.updated_at),
            },
        )
    if legacy_plan is not None and store.metadata.get(session_id, _PLAN_NAMESPACE) is None:
        store.metadata.put(
            session_id,
            _PLAN_NAMESPACE,
            {
                "id": legacy_plan.id,
                "version": legacy_plan.version,
                "created_at": _timestamp(legacy_plan.created_at),
                "updated_at": _timestamp(legacy_plan.updated_at),
                "deleted": False,
            },
        )


def _migrate_legacy_tasks(
    runtime: SessionRuntime,
    tasks: list[LegacyTask],
) -> None:
    by_legacy_id = {
        str(task.metadata["legacyId"]): task.id
        for task in runtime.list_tasks()
        if task.metadata.get("legacyId") is not None
    }
    for legacy in tasks:
        internal_id = by_legacy_id.get(legacy.id)
        if internal_id is None:
            created = runtime.create_task(
                NewTask(
                    subject=legacy.subject,
                    description=legacy.description,
                    active_form=legacy.active_form,
                    metadata={**(legacy.meta or {}), "legacyId": legacy.id},
                )
            )
            internal_id = created.id
            by_legacy_id[legacy.id] = internal_id
        current = runtime.get_task(internal_id)
        assert current is not None
        status = getattr(legacy.status, "value", legacy.status)
        desired_status = TaskStatus(status)
        metadata = {
            key: value
            for key, value in {**(legacy.meta or {}), "legacyId": legacy.id}.items()
            if current.metadata.get(key) != value
        }
        mutation = TaskMutation(
            subject=legacy.subject if current.subject != legacy.subject else None,
            description=(
                legacy.description if current.description != legacy.description else None
            ),
            active_form=(
                legacy.active_form if current.active_form != legacy.active_form else None
            ),
            status=desired_status if current.status is not desired_status else None,
            owner=(
                legacy.owner
                if legacy.owner is not None and current.owner != legacy.owner
                else None
            ),
            metadata=metadata or None,
        )
        if any(
            value is not None
            for value in (
                mutation.subject,
                mutation.description,
                mutation.active_form,
                mutation.status,
                mutation.owner,
                mutation.metadata,
            )
        ):
            runtime.update_task(internal_id, mutation)

    for legacy in tasks:
        internal_id = by_legacy_id[legacy.id]
        current = runtime.get_task(internal_id)
        assert current is not None
        blocks = [by_legacy_id[item] for item in legacy.blocks or [] if item in by_legacy_id]
        blocked_by = [
            by_legacy_id[item]
            for item in legacy.blocked_by or []
            if item in by_legacy_id
        ]
        add_blocks = [item for item in blocks if item not in current.blocks]
        add_blocked_by = [item for item in blocked_by if item not in current.blocked_by]
        if add_blocks or add_blocked_by:
            runtime.update_task(
                internal_id,
                TaskMutation(
                    add_blocks=add_blocks,
                    add_blocked_by=add_blocked_by,
                ),
            )


def _migrate_legacy_transcript(
    runtime: SessionRuntime,
    messages: list[Message],
) -> None:
    existing_counts: dict[tuple[EventType, str, str | None], int] = {}
    existing_events: dict[tuple[EventType, str, str | None, int], Any] = {}
    for event in runtime.events():
        legacy_id = event.payload.get("legacyMessageId")
        if legacy_id is None or event.event_type not in {
            EventType.USER_MESSAGE,
            EventType.ASSISTANT_MESSAGE,
            EventType.TOOL_CALL,
            EventType.TOOL_RESULT,
        }:
            continue
        base = (
            event.event_type,
            str(legacy_id),
            event.payload.get("toolCallId"),
        )
        occurrence = existing_counts.get(base, 0)
        existing_counts[base] = occurrence + 1
        existing_events[(*base, occurrence)] = event

    expected_counts: dict[tuple[EventType, str, str | None], int] = {}

    def append_missing(
        event_type: EventType,
        payload: dict[str, Any],
        *,
        parent_event_id: int | None = None,
    ) -> Any:
        base = (
            event_type,
            str(payload["legacyMessageId"]),
            payload.get("toolCallId"),
        )
        occurrence = expected_counts.get(base, 0)
        expected_counts[base] = occurrence + 1
        key = (*base, occurrence)
        event = existing_events.get(key)
        if event is None:
            runtime.append_event(
                event_type,
                payload,
                parent_event_id=parent_event_id,
            )
            event = runtime.events()[-1]
            existing_events[key] = event
        return event

    for message in messages:
        event_type = (
            EventType.USER_MESSAGE
            if message.role == "user"
            else EventType.ASSISTANT_MESSAGE
        )
        append_missing(
            event_type,
            {
                "legacyMessageId": message.id,
                "role": message.role,
                "content": message.content,
                "thinking": message.thinking,
            },
        )
        call_events: dict[str, Any] = {}
        for raw_call in message.tool_calls or []:
            call = normalize_tool_call(raw_call)
            call_event = append_missing(
                EventType.TOOL_CALL,
                {
                    "legacyMessageId": message.id,
                    **call,
                },
            )
            call_events[call["toolCallId"]] = call_event
        for raw_result in message.tool_results or []:
            result = normalize_tool_result(raw_result)
            call_event = call_events.get(result["toolCallId"])
            if call_event is not None and not result["name"]:
                result = {
                    **result,
                    "name": str(call_event.payload.get("name") or ""),
                }
            append_missing(
                EventType.TOOL_RESULT,
                {
                    "legacyMessageId": message.id,
                    **result,
                },
                parent_event_id=call_event.id if call_event is not None else None,
            )


def _migrate_legacy_global_tasks_owned(
    session_factory: Any,
    lease: _MigrationLease,
) -> SessionRuntime:
    store = SQLAlchemyStateStore(session_factory)
    runtime = SessionRuntime("global", store)
    with session_factory() as db:
        marker = db.get(RuntimeSession, "global")
        if marker is not None and marker.migrated_at is not None:
            return SessionRuntime.recover("global", store)
        tasks = db.scalars(
            select(LegacyTask)
            .where(LegacyTask.conversation_id.is_(None))
            .order_by(LegacyTask.created_at, LegacyTask.id)
        ).all()
    _migrate_legacy_tasks(runtime, list(tasks))
    lease.heartbeat()
    with session_factory() as db, db.begin():
        marker = db.get(RuntimeSession, "global")
        if marker is not None:
            marker.migrated_at = datetime.now(timezone.utc)
    return runtime


def migrate_legacy_global_tasks(session_factory: Any) -> SessionRuntime:
    """Import conversation-less legacy tasks under a durable session lease."""

    store = SQLAlchemyStateStore(session_factory)
    with _migration_lease(store, "global") as lease:
        if lease is None:
            return SessionRuntime.recover("global", store)
        return _migrate_legacy_global_tasks_owned(session_factory, lease)


def _migrate_legacy_session_owned(
    session_id: str,
    session_factory: Any,
    lease: _MigrationLease,
    *,
    plan_root: Path | None = None,
) -> SessionRuntime:
    store = SQLAlchemyStateStore(session_factory)
    runtime = SessionRuntime(session_id, store)
    with session_factory() as db:
        marker = db.get(RuntimeSession, session_id)
        legacy_conversation = db.get(LegacyConversation, session_id)
        legacy_plan = db.scalar(
            select(LegacyPlan).where(LegacyPlan.conversation_id == session_id).limit(1)
        )

    _backfill_api_metadata(store, session_id, legacy_conversation, legacy_plan)
    if marker is not None and marker.migrated_at is not None:
        return SessionRuntime.recover(session_id, store)

    with session_factory() as db:
        messages = db.scalars(
            select(Message)
            .where(Message.conversation_id == session_id)
            .order_by(Message.timestamp, Message.id)
        ).all()
        tasks = db.scalars(
            select(LegacyTask)
            .where(LegacyTask.conversation_id == session_id)
            .order_by(LegacyTask.created_at, LegacyTask.id)
        ).all()

    _migrate_legacy_transcript(runtime, list(messages))
    lease.heartbeat()

    _migrate_legacy_tasks(runtime, list(tasks))
    lease.heartbeat()

    migrated_plan_ids = {
        event.payload.get("legacyPlanId")
        for event in runtime.events()
        if event.event_type is EventType.PLAN_TRANSITION
        and event.payload.get("action") == "migrate"
    }
    if legacy_plan is not None and legacy_plan.id not in migrated_plan_ids:
        if runtime.state.plan.state is PlanState.IDLE:
            runtime.enter_plan(runtime.state.permission_mode)
        if runtime.state.plan.state is PlanState.PLANNING:
            root = (plan_root or Path.cwd()).resolve()
            slug = plan_slug(session_id)
            path = PlanFileStore(root).save(slug, legacy_plan.content)
            runtime.state.plan.slug = slug
            runtime.state.plan.file_path = path
            runtime._persist(
                EventType.PLAN_TRANSITION,
                {"action": "migrate", "legacyPlanId": legacy_plan.id},
            )
    lease.heartbeat()

    with session_factory() as db, db.begin():
        marker = db.get(RuntimeSession, session_id)
        if marker is not None:
            marker.migrated_at = datetime.now(timezone.utc)

    return runtime


def migrate_legacy_session(
    session_id: str,
    session_factory: Any,
    *,
    plan_root: Path | None = None,
) -> SessionRuntime:
    """Import a legacy conversation exactly once under a durable session lease."""

    store = SQLAlchemyStateStore(session_factory)
    with _migration_lease(store, session_id) as lease:
        if lease is None:
            return SessionRuntime.recover(session_id, store)
        return _migrate_legacy_session_owned(
            session_id,
            session_factory,
            lease,
            plan_root=plan_root,
        )
