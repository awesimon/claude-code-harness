"""Transactional SQLAlchemy persistence for the durable state-core domain."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    cast,
    delete,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from models import Base

from .types import (
    ClaimResult,
    CommitResult,
    EventType,
    InvalidTaskDependency,
    NewTask,
    PendingEventBatch,
    RevisionConflict,
    SessionEvent,
    SessionSnapshot,
    SessionState,
    TaskItem,
    TaskMutation,
    TaskStatus,
)

SessionFactory = Callable[[], Session]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


class RuntimeSession(Base):
    __tablename__ = "runtime_sessions"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    migrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeEvent(Base):
    __tablename__ = "runtime_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("runtime_sessions.session_id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    parent_event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("runtime_events.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_runtime_events_session_id", "session_id"),)


class RuntimeSnapshot(Base):
    __tablename__ = "runtime_snapshots"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    last_event_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String, nullable=True)
    valid: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeTask(Base):
    __tablename__ = "runtime_tasks"

    task_list_id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    active_form: Mapped[str | None] = mapped_column(String, nullable=True)
    owner: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    blocks: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    blocked_by: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RuntimeTaskCounter(Base):
    __tablename__ = "runtime_task_counters"

    task_list_id: Mapped[str] = mapped_column(String, primary_key=True)
    high_water_mark: Mapped[int] = mapped_column(Integer, nullable=False)


class SQLAlchemyStateRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def create_session(self, state: SessionState) -> SessionState:
        wire = state.to_dict()
        with self._session_factory() as db, db.begin():
            db.add(
                RuntimeSession(
                    session_id=state.session_id,
                    revision=state.revision,
                    state=wire,
                    migrated_at=None,
                    created_at=state.created_at,
                    updated_at=state.updated_at,
                )
            )
        return SessionState.from_dict(wire)

    def load_session(self, session_id: str) -> SessionState | None:
        with self._session_factory() as db:
            row = db.get(RuntimeSession, session_id)
            return SessionState.from_dict(row.state) if row is not None else None

    def delete_session(self, session_id: str) -> bool:
        with self._session_factory() as db, db.begin():
            db.execute(delete(RuntimeSnapshot).where(RuntimeSnapshot.session_id == session_id))
            db.execute(delete(RuntimeEvent).where(RuntimeEvent.session_id == session_id))
            result = db.execute(
                delete(RuntimeSession).where(RuntimeSession.session_id == session_id)
            )
            return result.rowcount == 1

    def commit(
        self,
        state: SessionState,
        batch: PendingEventBatch,
        expected_revision: int,
    ) -> CommitResult:
        batch.validate_state(state)
        proposed_state = SessionState.from_dict(state.to_dict())
        assigned_events: list[SessionEvent] = []

        with self._session_factory() as db, db.begin():
            session_row = db.get(RuntimeSession, state.session_id)
            actual_revision = session_row.revision if session_row is not None else None
            if session_row is None or actual_revision != expected_revision:
                raise RevisionConflict(state.session_id, expected_revision, actual_revision)

            parent_ids = {
                event.parent_event_id for event in batch.events if event.parent_event_id is not None
            }
            parent_sessions = dict(
                db.execute(
                    select(RuntimeEvent.id, RuntimeEvent.session_id).where(
                        RuntimeEvent.id.in_(parent_ids)
                    )
                ).all()
            )
            batch.validate_existing_parents(parent_sessions)

            assigned_ids: dict[int, int] = {}
            for pending in batch.events:
                parent_event_id = pending.parent_event_id
                if pending.parent_sequence is not None:
                    parent_event_id = assigned_ids[pending.parent_sequence]
                row = RuntimeEvent(
                    session_id=pending.session_id,
                    event_type=pending.event_type.value,
                    payload=pending.to_dict()["payload"],
                    parent_event_id=parent_event_id,
                    created_at=pending.created_at,
                )
                db.add(row)
                db.flush()
                assigned_ids[pending.sequence] = row.id
                assigned_events.append(
                    SessionEvent(
                        id=row.id,
                        session_id=row.session_id,
                        event_type=EventType(row.event_type),
                        payload=row.payload,
                        parent_event_id=row.parent_event_id,
                        created_at=_as_utc(row.created_at),
                    )
                )

            persisted_state = SessionState.from_dict(session_row.state)
            proposed_state.revision = expected_revision + 1
            proposed_state.last_event_id = (
                assigned_events[-1].id if assigned_events else persisted_state.last_event_id
            )
            proposed_state.updated_at = _utc_now()
            state_wire = proposed_state.to_dict()
            result = db.execute(
                update(RuntimeSession)
                .where(
                    RuntimeSession.session_id == state.session_id,
                    RuntimeSession.revision == expected_revision,
                )
                .values(
                    revision=proposed_state.revision,
                    state=state_wire,
                    updated_at=proposed_state.updated_at,
                )
            )
            if result.rowcount != 1:
                raise RevisionConflict(state.session_id, expected_revision, actual_revision)

        return CommitResult(state=proposed_state, events=assigned_events)

    def list_events(self, session_id: str, after_id: int = 0) -> list[SessionEvent]:
        with self._session_factory() as db:
            rows = db.scalars(
                select(RuntimeEvent)
                .where(
                    RuntimeEvent.session_id == session_id,
                    RuntimeEvent.id > after_id,
                )
                .order_by(RuntimeEvent.id)
            ).all()
            return [
                SessionEvent(
                    id=row.id,
                    session_id=row.session_id,
                    event_type=EventType(row.event_type),
                    payload=row.payload,
                    parent_event_id=row.parent_event_id,
                    created_at=_as_utc(row.created_at),
                )
                for row in rows
            ]

    def save_snapshot(self, snapshot: SessionSnapshot) -> None:
        wire = snapshot.to_dict()
        state_json = json.dumps(wire["state"], sort_keys=True, separators=(",", ":"))
        with self._session_factory() as db, db.begin():
            db.merge(
                RuntimeSnapshot(
                    session_id=snapshot.session_id,
                    last_event_id=snapshot.last_event_id,
                    revision=snapshot.state.revision,
                    state=wire["state"],
                    checksum=hashlib.sha256(state_json.encode()).hexdigest(),
                    valid=True,
                    created_at=snapshot.created_at,
                )
            )

    def latest_snapshot(self, session_id: str) -> SessionSnapshot | None:
        with self._session_factory() as db:
            row = db.scalar(
                select(RuntimeSnapshot)
                .where(
                    RuntimeSnapshot.session_id == session_id,
                    RuntimeSnapshot.valid.is_(True),
                )
                .order_by(RuntimeSnapshot.last_event_id.desc(), RuntimeSnapshot.revision.desc())
                .limit(1)
            )
            if row is None:
                return None
            state_json = json.dumps(row.state, sort_keys=True, separators=(",", ":"))
            checksum = hashlib.sha256(state_json.encode()).hexdigest()
            if row.checksum is not None and row.checksum != checksum:
                return None
            return SessionSnapshot(
                session_id=row.session_id,
                last_event_id=row.last_event_id,
                state=SessionState.from_dict(row.state),
                created_at=_as_utc(row.created_at),
            )


class SQLAlchemyTaskRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[Session]:
        db = self._session_factory()
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

    def _allocate_task_id(self, task_list_id: str) -> str:
        with self._transaction(immediate=True) as db:
            if db.bind is not None and db.bind.dialect.name == "sqlite":
                statement = sqlite_insert(RuntimeTaskCounter).values(
                    task_list_id=task_list_id,
                    high_water_mark=1,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[RuntimeTaskCounter.task_list_id],
                    set_={
                        "high_water_mark": RuntimeTaskCounter.high_water_mark + 1,
                    },
                )
                db.execute(statement)
            else:
                counter = db.get(RuntimeTaskCounter, task_list_id)
                if counter is None:
                    db.add(RuntimeTaskCounter(task_list_id=task_list_id, high_water_mark=1))
                else:
                    counter.high_water_mark += 1
            counter = db.get(RuntimeTaskCounter, task_list_id)
            assert counter is not None
            return str(counter.high_water_mark)

    @staticmethod
    def _to_task_item(row: RuntimeTask) -> TaskItem:
        return TaskItem(
            id=row.task_id,
            subject=row.subject,
            description=row.description,
            active_form=row.active_form,
            owner=row.owner,
            status=TaskStatus(row.status),
            blocks=_json_copy(row.blocks),
            blocked_by=_json_copy(row.blocked_by),
            metadata=_json_copy(row.metadata_json),
        )

    @staticmethod
    def _add_unique(items: list[str], value: str) -> list[str]:
        return items if value in items else [*items, value]

    @staticmethod
    def _without(items: list[str], value: str) -> list[str]:
        return [item for item in items if item != value]

    def create(self, task_list_id: str, task: NewTask) -> TaskItem:
        task_wire = task.to_dict()
        if type(task_list_id) is not str:
            raise TypeError("task_list_id must be a string")
        task_id = self._allocate_task_id(task_list_id)
        with self._transaction() as db:
            row = RuntimeTask(
                task_list_id=task_list_id,
                task_id=task_id,
                subject=task_wire["subject"],
                description=task_wire["description"],
                active_form=task_wire["activeForm"],
                owner=None,
                status=TaskStatus.PENDING.value,
                blocks=[],
                blocked_by=[],
                metadata_json=task_wire["metadata"],
                version=1,
            )
            db.add(row)
            db.flush()
            return self._to_task_item(row)

    def get(self, task_list_id: str, task_id: str) -> TaskItem | None:
        with self._session_factory() as db:
            row = db.get(RuntimeTask, (task_list_id, task_id))
            return self._to_task_item(row) if row is not None else None

    def list(self, task_list_id: str) -> list[TaskItem]:
        with self._session_factory() as db:
            rows = db.scalars(
                select(RuntimeTask)
                .where(RuntimeTask.task_list_id == task_list_id)
                .order_by(cast(RuntimeTask.task_id, Integer), RuntimeTask.task_id)
            ).all()
            return [self._to_task_item(row) for row in rows]

    def _dependency_rows(
        self,
        db: Session,
        task_list_id: str,
        task_id: str,
        mutation: TaskMutation,
    ) -> dict[str, RuntimeTask]:
        dependency_ids = set(
            mutation.add_blocks
            + mutation.add_blocked_by
            + mutation.remove_blocks
            + mutation.remove_blocked_by
        )
        if task_id in dependency_ids:
            raise InvalidTaskDependency("a task cannot depend on itself")
        if not dependency_ids:
            return {}
        rows = db.scalars(
            select(RuntimeTask).where(
                RuntimeTask.task_list_id == task_list_id,
                RuntimeTask.task_id.in_(dependency_ids),
            )
        ).all()
        by_id = {row.task_id: row for row in rows}
        missing = dependency_ids.difference(by_id)
        if missing:
            raise InvalidTaskDependency(f"missing task dependencies: {sorted(missing)!r}")
        return by_id

    def update(
        self,
        task_list_id: str,
        task_id: str,
        mutation: TaskMutation,
    ) -> TaskItem | None:
        mutation.to_dict()
        with self._transaction(immediate=True) as db:
            row = db.get(RuntimeTask, (task_list_id, task_id))
            if row is None:
                return None
            dependencies = self._dependency_rows(db, task_list_id, task_id, mutation)

            if mutation.subject is not None:
                row.subject = mutation.subject
            if mutation.description is not None:
                row.description = mutation.description
            if mutation.active_form is not None:
                row.active_form = mutation.active_form
            if mutation.owner is not None:
                row.owner = mutation.owner
            if mutation.status is not None:
                row.status = mutation.status.value

            for dependency_id in mutation.remove_blocks:
                row.blocks = self._without(row.blocks, dependency_id)
                dependency = dependencies[dependency_id]
                dependency.blocked_by = self._without(dependency.blocked_by, task_id)
            for dependency_id in mutation.remove_blocked_by:
                row.blocked_by = self._without(row.blocked_by, dependency_id)
                dependency = dependencies[dependency_id]
                dependency.blocks = self._without(dependency.blocks, task_id)
            for dependency_id in mutation.add_blocks:
                row.blocks = self._add_unique(row.blocks, dependency_id)
                dependency = dependencies[dependency_id]
                dependency.blocked_by = self._add_unique(dependency.blocked_by, task_id)
            for dependency_id in mutation.add_blocked_by:
                row.blocked_by = self._add_unique(row.blocked_by, dependency_id)
                dependency = dependencies[dependency_id]
                dependency.blocks = self._add_unique(dependency.blocks, task_id)

            if mutation.metadata is not None:
                metadata = _json_copy(row.metadata_json)
                for key, value in mutation.metadata.items():
                    if value is None:
                        metadata.pop(key, None)
                    else:
                        metadata[key] = _json_copy(value)
                row.metadata_json = metadata
            row.version += 1
            for dependency in dependencies.values():
                dependency.version += 1
            db.flush()
            return self._to_task_item(row)

    def claim(self, task_list_id: str, task_id: str, owner: str) -> ClaimResult:
        if type(owner) is not str:
            raise TypeError("owner must be a string")
        with self._transaction(immediate=True) as db:
            row = db.get(RuntimeTask, (task_list_id, task_id))
            if row is None:
                return ClaimResult(success=False, reason="not_found")
            current = self._to_task_item(row)
            if current.blocked_by:
                return ClaimResult(
                    success=False,
                    task=current,
                    reason="blocked",
                    current_owner=current.owner,
                )
            if current.owner is not None:
                return ClaimResult(
                    success=False,
                    task=current,
                    reason="already_claimed",
                    current_owner=current.owner,
                )
            if current.status is not TaskStatus.PENDING:
                return ClaimResult(
                    success=False,
                    task=current,
                    reason="not_pending",
                    current_owner=current.owner,
                )

            result = db.execute(
                update(RuntimeTask)
                .where(
                    RuntimeTask.task_list_id == task_list_id,
                    RuntimeTask.task_id == task_id,
                    RuntimeTask.owner.is_(None),
                    RuntimeTask.status == TaskStatus.PENDING.value,
                    RuntimeTask.version == row.version,
                )
                .values(
                    owner=owner,
                    status=TaskStatus.IN_PROGRESS.value,
                    version=row.version + 1,
                )
            )
            db.flush()
            if result.rowcount == 1:
                db.refresh(row)
                return ClaimResult(success=True, task=self._to_task_item(row))

            db.expire_all()
            current_row = db.get(RuntimeTask, (task_list_id, task_id))
            if current_row is None:
                return ClaimResult(success=False, reason="not_found")
            current = self._to_task_item(current_row)
            return ClaimResult(
                success=False,
                task=current,
                reason="already_claimed" if current.owner is not None else "conflict",
                current_owner=current.owner,
            )

    def delete(self, task_list_id: str, task_id: str) -> bool:
        with self._transaction(immediate=True) as db:
            row = db.get(RuntimeTask, (task_list_id, task_id))
            if row is None:
                return False
            all_rows = db.scalars(
                select(RuntimeTask).where(RuntimeTask.task_list_id == task_list_id)
            ).all()
            for other in all_rows:
                if other.task_id == task_id:
                    continue
                new_blocks = self._without(other.blocks, task_id)
                new_blocked_by = self._without(other.blocked_by, task_id)
                if new_blocks != other.blocks or new_blocked_by != other.blocked_by:
                    other.blocks = new_blocks
                    other.blocked_by = new_blocked_by
                    other.version += 1
            db.delete(row)
        return True

    def unassign(self, task_list_id: str, task_id: str) -> TaskItem | None:
        with self._transaction(immediate=True) as db:
            row = db.get(RuntimeTask, (task_list_id, task_id))
            if row is None:
                return None
            row.owner = None
            if row.status == TaskStatus.IN_PROGRESS.value:
                row.status = TaskStatus.PENDING.value
            row.version += 1
            db.flush()
            return self._to_task_item(row)

    def delete_list(self, task_list_id: str) -> int:
        with self._transaction(immediate=True) as db:
            tasks = db.execute(delete(RuntimeTask).where(RuntimeTask.task_list_id == task_list_id))
            db.execute(
                delete(RuntimeTaskCounter).where(RuntimeTaskCounter.task_list_id == task_list_id)
            )
            return int(tasks.rowcount or 0)


class SQLAlchemyStateStore:
    """Facade exposing repositories backed by one injected session factory."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self.states = SQLAlchemyStateRepository(session_factory)
        self.tasks = SQLAlchemyTaskRepository(session_factory)
