"""SQLAlchemy-backed repositories for durable harness runtime records."""
# ruff: noqa: E501

from __future__ import annotations

import builtins
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping

from sqlalchemy import (
    JSON,
    Connection,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from models import Base

from .runtime_records import (
    AGENT_TERMINAL_STATUSES,
    TRACE_TERMINAL_STATUSES,
    AgentRecord,
    AgentStatus,
    AgentTerminationReason,
    InvalidAgentParent,
    RuntimeMetadataRecord,
    RuntimeRecordRevisionConflict,
    TraceSpanRecord,
    TraceSpanStatus,
    WorktreeRecord,
    WorktreeStatus,
    _json_copy,
    _trace_duration_ms,
    ensure_agent_transition,
    ensure_trace_span_transition,
    ensure_worktree_transition,
    sanitize_runtime_error,
)

SessionFactory = Callable[[], Session]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class RuntimeAgent(Base):
    __tablename__ = "runtime_agents"

    agent_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_session_id: Mapped[str] = mapped_column(String, nullable=False)
    parent_agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    agent_type: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_background: Mapped[bool] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    termination_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    effective_cwd: Mapped[str] = mapped_column(Text, nullable=False)
    worktree_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_runtime_agents_root_session", "root_session_id"),
        Index("ix_runtime_agents_root_status_created", "root_session_id", "status", "created_at"),
        Index("ix_runtime_agents_parent", "parent_agent_id"),
    )


class RuntimeMetadata(Base):
    __tablename__ = "runtime_metadata"

    root_session_id: Mapped[str] = mapped_column(String, primary_key=True)
    namespace: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeTraceSpan(Base):
    __tablename__ = "runtime_trace_spans"

    span_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_session_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    parent_span_id: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_runtime_trace_spans_root_session", "root_session_id"),
        Index("ix_runtime_trace_spans_agent", "agent_id"),
    )


class RuntimeWorktree(Base):
    __tablename__ = "runtime_worktrees"

    worktree_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_session_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    repository_root: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_path: Mapped[str] = mapped_column(Text, nullable=False)
    branch: Mapped[str] = mapped_column(String, nullable=False)
    base_commit: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_runtime_worktrees_root_session", "root_session_id"),
        Index("ix_runtime_worktrees_agent", "agent_id"),
    )


class SQLAlchemyAgentRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._before_compare_and_swap: Callable[[], None] | None = None

    @staticmethod
    def _record(row: RuntimeAgent) -> AgentRecord:
        return AgentRecord(
            agent_id=row.agent_id,
            root_session_id=row.root_session_id,
            parent_agent_id=row.parent_agent_id,
            agent_type=row.agent_type,
            prompt=row.prompt,
            description=row.description,
            is_background=row.is_background,
            effective_cwd=row.effective_cwd,
            definition_snapshot=row.definition_snapshot,
            status=AgentStatus(row.status),
            revision=row.revision,
            usage=row.usage,
            termination_reason=(
                AgentTerminationReason(row.termination_reason)
                if row.termination_reason is not None
                else None
            ),
            error=row.error_json,
            output=row.output_json,
            worktree_id=row.worktree_id,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
            started_at=_as_utc(row.started_at) if row.started_at is not None else None,
            finished_at=_as_utc(row.finished_at) if row.finished_at is not None else None,
        )

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

    @staticmethod
    def _add(db: Session, detached: AgentRecord) -> None:
        db.add(
            RuntimeAgent(
                agent_id=detached.agent_id,
                root_session_id=detached.root_session_id,
                parent_agent_id=detached.parent_agent_id,
                agent_type=detached.agent_type,
                prompt=detached.prompt,
                description=detached.description,
                is_background=detached.is_background,
                status=detached.status.value,
                revision=detached.revision,
                definition_snapshot=_json_copy(detached.definition_snapshot),
                usage=_json_copy(detached.usage),
                termination_reason=(
                    detached.termination_reason.value
                    if detached.termination_reason is not None
                    else None
                ),
                error_json=_json_copy(detached.error),
                output_json=_json_copy(detached.output),
                effective_cwd=detached.effective_cwd,
                worktree_id=detached.worktree_id,
                created_at=detached.created_at,
                updated_at=detached.updated_at,
                started_at=detached.started_at,
                finished_at=detached.finished_at,
            )
        )

    def create(self, record: AgentRecord) -> AgentRecord:
        detached = AgentRecord(**record.__dict__)
        with self._transaction() as db:
            self._add(db, detached)
        return detached

    def create_with_parent_guard(self, record: AgentRecord) -> AgentRecord:
        detached = AgentRecord(**record.__dict__)
        parent_agent_id = detached.parent_agent_id
        if parent_agent_id is None:
            raise InvalidAgentParent("<missing>")
        with self._transaction(immediate=True) as db:
            statement = (
                select(RuntimeAgent)
                .where(RuntimeAgent.agent_id == parent_agent_id)
                .with_for_update()
            )
            parent = db.execute(statement).scalar_one_or_none()
            if (
                parent is None
                or parent.root_session_id != detached.root_session_id
                or parent.status != AgentStatus.RUNNING.value
            ):
                raise InvalidAgentParent(parent_agent_id)
            self._add(db, detached)
        return detached

    def get(self, agent_id: str) -> AgentRecord | None:
        with self._session_factory() as db:
            row = db.get(RuntimeAgent, agent_id)
            return self._record(row) if row is not None else None

    def list_all(self) -> builtins.list[AgentRecord]:
        with self._session_factory() as db:
            rows = db.scalars(
                select(RuntimeAgent).order_by(
                    RuntimeAgent.root_session_id,
                    RuntimeAgent.created_at,
                    RuntimeAgent.agent_id,
                )
            ).all()
            return [self._record(row) for row in rows]

    def list(
        self,
        root_session_id: str,
        *,
        parent_agent_id: str | None = None,
        status: AgentStatus | None = None,
        is_background: bool | None = None,
    ) -> builtins.list[AgentRecord]:
        with self._session_factory() as db:
            statement = select(RuntimeAgent).where(RuntimeAgent.root_session_id == root_session_id)
            if parent_agent_id is not None:
                statement = statement.where(RuntimeAgent.parent_agent_id == parent_agent_id)
            if status is not None:
                statement = statement.where(RuntimeAgent.status == status.value)
            if is_background is not None:
                statement = statement.where(RuntimeAgent.is_background.is_(is_background))
            rows = db.scalars(statement.order_by(RuntimeAgent.created_at, RuntimeAgent.agent_id)).all()
            return [self._record(row) for row in rows]

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
    ) -> AgentRecord:
        lost_update = False
        with self._transaction(immediate=True) as db:
            row = db.get(RuntimeAgent, agent_id)
            actual = row.revision if row is not None else None
            if row is None or actual != expected_revision:
                raise RuntimeRecordRevisionConflict("agent", agent_id, expected_revision, actual)
            current = AgentStatus(row.status)
            ensure_agent_transition(current, status)
            now = _utc_now()
            values: dict[str, Any] = {"status": status.value, "revision": actual + 1, "updated_at": now}
            if status is AgentStatus.RUNNING:
                values["started_at"] = row.started_at or now
            if status in AGENT_TERMINAL_STATUSES:
                expected_reason = AgentTerminationReason(status.value)
                if termination_reason is not None and termination_reason is not expected_reason:
                    raise ValueError("termination_reason must match terminal agent status")
                values["finished_at"] = now
                values["termination_reason"] = expected_reason.value
            if usage is not None:
                values["usage"] = _json_copy(usage)
            if error is not None:
                values["error_json"] = sanitize_runtime_error(error)
            if output is not None:
                values["output_json"] = _json_copy(output)
            if self._before_compare_and_swap is not None:
                self._before_compare_and_swap()
            result = db.execute(
                update(RuntimeAgent)
                .where(RuntimeAgent.agent_id == agent_id, RuntimeAgent.revision == expected_revision)
                .values(**values)
            )
            if result.rowcount != 1:
                lost_update = True
            else:
                db.expire_all()
                persisted = db.get(RuntimeAgent, agent_id)
                assert persisted is not None
                return self._record(persisted)
        assert lost_update
        current_record = self.get(agent_id)
        raise RuntimeRecordRevisionConflict(
            "agent",
            agent_id,
            expected_revision,
            current_record.revision if current_record is not None else None,
        )

    def reconcile(self, root_session_id: str, live_agent_ids: set[str]) -> builtins.list[AgentRecord]:
        with self._session_factory() as db, db.begin():
            rows = db.scalars(
                select(RuntimeAgent)
                .where(
                    RuntimeAgent.root_session_id == root_session_id,
                    RuntimeAgent.status.in_([AgentStatus.PENDING.value, AgentStatus.RUNNING.value]),
                )
                .order_by(RuntimeAgent.created_at, RuntimeAgent.agent_id)
            ).all()
            now = _utc_now()
            changed: list[AgentRecord] = []
            for row in rows:
                if row.agent_id in live_agent_ids:
                    continue
                result = db.execute(
                    update(RuntimeAgent)
                    .where(RuntimeAgent.agent_id == row.agent_id, RuntimeAgent.revision == row.revision)
                    .values(
                        status=AgentStatus.INTERRUPTED.value,
                        termination_reason=AgentTerminationReason.INTERRUPTED.value,
                        finished_at=now,
                        updated_at=now,
                        revision=row.revision + 1,
                    )
                )
                if result.rowcount == 1:
                    db.expire(row)
                    changed.append(self._record(row))
            return changed


class SQLAlchemyRuntimeMetadataRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _record(row: RuntimeMetadata) -> RuntimeMetadataRecord:
        return RuntimeMetadataRecord(
            root_session_id=row.root_session_id,
            namespace=row.namespace,
            snapshot=row.snapshot,
            revision=row.revision,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
        )

    def get(self, root_session_id: str, namespace: str) -> RuntimeMetadataRecord | None:
        with self._session_factory() as db:
            row = db.get(RuntimeMetadata, (root_session_id, namespace))
            return self._record(row) if row is not None else None

    def put(
        self,
        root_session_id: str,
        namespace: str,
        snapshot: Mapping[str, Any],
        expected_revision: int | None = None,
    ) -> RuntimeMetadataRecord:
        detached_snapshot = _json_copy(snapshot)
        if not isinstance(detached_snapshot, dict):
            raise TypeError("snapshot must be a JSON object")
        try:
            return self._put(root_session_id, namespace, detached_snapshot, expected_revision)
        except IntegrityError as exc:
            if expected_revision is not None:
                raise
            current = self.get(root_session_id, namespace)
            raise RuntimeRecordRevisionConflict(
                "metadata",
                f"{root_session_id}:{namespace}",
                None,
                current.revision if current is not None else None,
            ) from exc

    def _put(
        self,
        root_session_id: str,
        namespace: str,
        detached_snapshot: dict[str, Any],
        expected_revision: int | None,
    ) -> RuntimeMetadataRecord:
        with self._session_factory() as db, db.begin():
            row = db.get(RuntimeMetadata, (root_session_id, namespace))
            actual = row.revision if row is not None else None
            if row is None:
                if expected_revision is not None:
                    raise RuntimeRecordRevisionConflict(
                        "metadata", f"{root_session_id}:{namespace}", expected_revision, None
                    )
                now = _utc_now()
                row = RuntimeMetadata(
                    root_session_id=root_session_id,
                    namespace=namespace,
                    snapshot=detached_snapshot,
                    revision=0,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                db.flush()
                return self._record(row)
            if expected_revision != actual:
                raise RuntimeRecordRevisionConflict(
                    "metadata", f"{root_session_id}:{namespace}", expected_revision, actual
                )
            now = _utc_now()
            result = db.execute(
                update(RuntimeMetadata)
                .where(
                    RuntimeMetadata.root_session_id == root_session_id,
                    RuntimeMetadata.namespace == namespace,
                    RuntimeMetadata.revision == expected_revision,
                )
                .values(snapshot=detached_snapshot, revision=actual + 1, updated_at=now)
            )
            if result.rowcount != 1:
                db.expire_all()
                current = db.get(RuntimeMetadata, (root_session_id, namespace))
                raise RuntimeRecordRevisionConflict(
                    "metadata",
                    f"{root_session_id}:{namespace}",
                    expected_revision,
                    current.revision if current is not None else None,
                )
            db.expire_all()
            persisted = db.get(RuntimeMetadata, (root_session_id, namespace))
            assert persisted is not None
            return self._record(persisted)


class SQLAlchemyTraceSpanRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._before_compare_and_swap: Callable[[], None] | None = None
        self._migrate_sqlite_runtime_schema()

    def _migrate_sqlite_runtime_schema(self) -> None:
        """Run the one-time SQLite trace upgrade under an exclusive writer lock."""

        with self._session_factory() as db:
            bind = db.get_bind()
            if bind.dialect.name != "sqlite":
                return
            if isinstance(bind, Connection):
                if bind.in_transaction():
                    with bind.begin_nested():
                        self._apply_sqlite_runtime_migration(bind)
                else:
                    bind.exec_driver_sql("BEGIN IMMEDIATE")
                    try:
                        self._apply_sqlite_runtime_migration(bind)
                        bind.commit()
                    except BaseException:
                        bind.rollback()
                        raise
                return
            with bind.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    self._apply_sqlite_runtime_migration(connection)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise

    @staticmethod
    def _apply_sqlite_runtime_migration(connection: Connection) -> None:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS runtime_schema_migrations "
                "(name TEXT PRIMARY KEY, version INTEGER NOT NULL)"
            )
        )
        version = connection.execute(
            text("SELECT version FROM runtime_schema_migrations WHERE name = 'runtime_trace_spans'")
        ).scalar_one_or_none()
        columns = connection.execute(text("PRAGMA table_info(runtime_trace_spans)")).all()
        if not columns or version is not None and version >= 2:
            return
        names = {column.name for column in columns}
        if version is None and "duration_ms" not in names:
            connection.execute(text("ALTER TABLE runtime_trace_spans ADD COLUMN duration_ms INTEGER"))
        if version is None:
            rows = connection.execute(
                text(
                    "SELECT span_id, started_at, finished_at FROM runtime_trace_spans "
                    "WHERE status != 'running' AND started_at IS NOT NULL "
                    "AND finished_at IS NOT NULL AND duration_ms IS NULL"
                )
            ).all()
            for row in rows:
                started_at = datetime.fromisoformat(str(row.started_at)).replace(tzinfo=timezone.utc)
                finished_at = datetime.fromisoformat(str(row.finished_at)).replace(tzinfo=timezone.utc)
                if finished_at < started_at:
                    raise ValueError("finished_at must not precede started_at")
                connection.execute(
                    text("UPDATE runtime_trace_spans SET duration_ms = :duration_ms WHERE span_id = :span_id"),
                    {"span_id": row.span_id, "duration_ms": _trace_duration_ms(started_at, finished_at)},
                )
            connection.execute(
                text("INSERT INTO runtime_schema_migrations (name, version) VALUES ('runtime_trace_spans', 1)")
            )
        if connection.execute(text("PRAGMA table_info(runtime_agents)")).first() is not None:
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_runtime_agents_root_status_created "
                    "ON runtime_agents (root_session_id, status, created_at)"
                )
            )
        rows = connection.execute(
            text(
                "SELECT span_id, started_at, finished_at FROM runtime_trace_spans "
                "WHERE status != 'running' AND started_at IS NOT NULL "
                "AND finished_at IS NOT NULL"
            )
        ).all()
        for row in rows:
            started_at = datetime.fromisoformat(str(row.started_at)).replace(tzinfo=timezone.utc)
            finished_at = datetime.fromisoformat(str(row.finished_at)).replace(tzinfo=timezone.utc)
            if finished_at < started_at:
                raise ValueError("finished_at must not precede started_at")
            duration_ms = _trace_duration_ms(started_at, finished_at)
            connection.execute(
                text("UPDATE runtime_trace_spans SET duration_ms = :duration_ms WHERE span_id = :span_id"),
                {"span_id": row.span_id, "duration_ms": duration_ms},
            )
        rows = connection.execute(
            text("SELECT span_id, error_json FROM runtime_trace_spans WHERE error_json IS NOT NULL")
        ).all()
        for row in rows:
            error = json.loads(row.error_json) if isinstance(row.error_json, str) else row.error_json
            sanitized = sanitize_runtime_error(error)
            if sanitized != error:
                connection.execute(
                    text("UPDATE runtime_trace_spans SET error_json = :error WHERE span_id = :span_id"),
                    {"span_id": row.span_id, "error": json.dumps(sanitized)},
                )
        connection.execute(
            text("UPDATE runtime_schema_migrations SET version = 2 WHERE name = 'runtime_trace_spans'")
        )

    @staticmethod
    def _record(row: RuntimeTraceSpan) -> TraceSpanRecord:
        return TraceSpanRecord(
            span_id=row.span_id,
            root_session_id=row.root_session_id,
            agent_id=row.agent_id,
            parent_span_id=row.parent_span_id,
            kind=row.kind,
            name=row.name,
            status=TraceSpanStatus(row.status),
            revision=row.revision,
            started_at=_as_utc(row.started_at),
            finished_at=_as_utc(row.finished_at) if row.finished_at is not None else None,
            duration_ms=row.duration_ms,
            usage=row.usage,
            error=row.error_json,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
        )

    def start(self, record: TraceSpanRecord) -> TraceSpanRecord:
        if record.status is not TraceSpanStatus.RUNNING:
            raise ValueError("new trace spans must start in running status")
        detached = TraceSpanRecord(**record.__dict__)
        with self._session_factory() as db, db.begin():
            db.add(
                RuntimeTraceSpan(
                    span_id=detached.span_id,
                    root_session_id=detached.root_session_id,
                    agent_id=detached.agent_id,
                    parent_span_id=detached.parent_span_id,
                    kind=detached.kind,
                    name=detached.name,
                    status=detached.status.value,
                    revision=detached.revision,
                    started_at=detached.started_at,
                    finished_at=detached.finished_at,
                    duration_ms=detached.duration_ms,
                    usage=_json_copy(detached.usage),
                    error_json=_json_copy(detached.error),
                    created_at=detached.created_at,
                    updated_at=detached.updated_at,
                )
            )
        return detached

    def get(self, span_id: str) -> TraceSpanRecord | None:
        with self._session_factory() as db:
            row = db.get(RuntimeTraceSpan, span_id)
            return self._record(row) if row is not None else None

    def finish(
        self,
        span_id: str,
        status: TraceSpanStatus,
        expected_revision: int,
        *,
        usage: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> TraceSpanRecord:
        if status not in TRACE_TERMINAL_STATUSES:
            raise ValueError("trace spans must finish in a terminal status")
        lost_update = False
        with self._session_factory() as db, db.begin():
            row = db.get(RuntimeTraceSpan, span_id)
            actual = row.revision if row is not None else None
            if row is None or actual != expected_revision:
                raise RuntimeRecordRevisionConflict("trace span", span_id, expected_revision, actual)
            ensure_trace_span_transition(TraceSpanStatus(row.status), status)
            now = _utc_now()
            duration_ms = _trace_duration_ms(_as_utc(row.started_at), now)
            values: dict[str, Any] = {
                "status": status.value,
                "revision": actual + 1,
                "finished_at": now,
                "duration_ms": duration_ms,
                "updated_at": now,
            }
            if usage is not None:
                values["usage"] = _json_copy(usage)
            if error is not None:
                values["error_json"] = sanitize_runtime_error(error)
            if self._before_compare_and_swap is not None:
                self._before_compare_and_swap()
            result = db.execute(
                update(RuntimeTraceSpan)
                .where(RuntimeTraceSpan.span_id == span_id, RuntimeTraceSpan.revision == expected_revision)
                .values(**values)
            )
            if result.rowcount != 1:
                lost_update = True
            else:
                db.expire_all()
                persisted = db.get(RuntimeTraceSpan, span_id)
                assert persisted is not None
                return self._record(persisted)
        assert lost_update
        current = self.get(span_id)
        raise RuntimeRecordRevisionConflict(
            "trace span", span_id, expected_revision, current.revision if current is not None else None
        )

    def list(
        self,
        root_session_id: str,
        *,
        agent_id: str | None = None,
        status: TraceSpanStatus | None = None,
    ) -> builtins.list[TraceSpanRecord]:
        with self._session_factory() as db:
            statement = select(RuntimeTraceSpan).where(
                RuntimeTraceSpan.root_session_id == root_session_id
            )
            if agent_id is not None:
                statement = statement.where(RuntimeTraceSpan.agent_id == agent_id)
            if status is not None:
                statement = statement.where(RuntimeTraceSpan.status == status.value)
            rows = db.scalars(statement.order_by(RuntimeTraceSpan.started_at, RuntimeTraceSpan.span_id)).all()
            return [self._record(row) for row in rows]

    def interrupt_open(self, root_session_id: str) -> builtins.list[TraceSpanRecord]:
        with self._session_factory() as db, db.begin():
            rows = db.scalars(
                select(RuntimeTraceSpan)
                .where(
                    RuntimeTraceSpan.root_session_id == root_session_id,
                    RuntimeTraceSpan.status == TraceSpanStatus.RUNNING.value,
                )
                .order_by(RuntimeTraceSpan.started_at, RuntimeTraceSpan.span_id)
            ).all()
            now = _utc_now()
            interrupted: list[TraceSpanRecord] = []
            for row in rows:
                result = db.execute(
                    update(RuntimeTraceSpan)
                    .where(RuntimeTraceSpan.span_id == row.span_id, RuntimeTraceSpan.revision == row.revision)
                    .values(
                        status=TraceSpanStatus.INTERRUPTED.value,
                        revision=row.revision + 1,
                        finished_at=now,
                        duration_ms=_trace_duration_ms(_as_utc(row.started_at), now),
                        updated_at=now,
                    )
                )
                if result.rowcount == 1:
                    db.expire(row)
                    interrupted.append(self._record(row))
            return interrupted


class SQLAlchemyWorktreeRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._before_compare_and_swap: Callable[[], None] | None = None

    @staticmethod
    def _record(row: RuntimeWorktree) -> WorktreeRecord:
        return WorktreeRecord(
            worktree_id=row.worktree_id,
            root_session_id=row.root_session_id,
            agent_id=row.agent_id,
            repository_root=row.repository_root,
            canonical_path=row.canonical_path,
            branch=row.branch,
            base_commit=row.base_commit,
            status=WorktreeStatus(row.status),
            revision=row.revision,
            details=row.details,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
            removed_at=_as_utc(row.removed_at) if row.removed_at is not None else None,
        )

    def create(self, record: WorktreeRecord) -> WorktreeRecord:
        detached = WorktreeRecord(**record.__dict__)
        with self._session_factory() as db, db.begin():
            db.add(
                RuntimeWorktree(
                    worktree_id=detached.worktree_id,
                    root_session_id=detached.root_session_id,
                    agent_id=detached.agent_id,
                    repository_root=detached.repository_root,
                    canonical_path=detached.canonical_path,
                    branch=detached.branch,
                    base_commit=detached.base_commit,
                    status=detached.status.value,
                    revision=detached.revision,
                    details=_json_copy(detached.details),
                    created_at=detached.created_at,
                    updated_at=detached.updated_at,
                    removed_at=detached.removed_at,
                )
            )
        return detached

    def get(self, worktree_id: str) -> WorktreeRecord | None:
        with self._session_factory() as db:
            row = db.get(RuntimeWorktree, worktree_id)
            return self._record(row) if row is not None else None

    def list(
        self,
        root_session_id: str,
        *,
        agent_id: str | None = None,
        status: WorktreeStatus | None = None,
    ) -> builtins.list[WorktreeRecord]:
        with self._session_factory() as db:
            statement = select(RuntimeWorktree).where(
                RuntimeWorktree.root_session_id == root_session_id
            )
            if agent_id is not None:
                statement = statement.where(RuntimeWorktree.agent_id == agent_id)
            if status is not None:
                statement = statement.where(RuntimeWorktree.status == status.value)
            rows = db.scalars(statement.order_by(RuntimeWorktree.created_at, RuntimeWorktree.worktree_id)).all()
            return [self._record(row) for row in rows]

    def update(self, worktree_id: str, expected_revision: int, **changes: Any) -> WorktreeRecord:
        allowed = {"agent_id", "canonical_path", "branch", "base_commit", "status", "details", "removed_at"}
        unknown = set(changes).difference(allowed)
        if unknown:
            raise TypeError(f"unsupported worktree fields: {sorted(unknown)!r}")
        lost_update = False
        with self._session_factory() as db, db.begin():
            row = db.get(RuntimeWorktree, worktree_id)
            actual = row.revision if row is not None else None
            if row is None or actual != expected_revision:
                raise RuntimeRecordRevisionConflict("worktree", worktree_id, expected_revision, actual)
            values: dict[str, Any] = {"revision": actual + 1, "updated_at": _utc_now()}
            if "status" in changes:
                target = changes["status"]
                if not isinstance(target, WorktreeStatus):
                    raise TypeError("status must be a WorktreeStatus")
                ensure_worktree_transition(WorktreeStatus(row.status), target)
                values["status"] = target.value
                if target is WorktreeStatus.REMOVED and "removed_at" not in changes:
                    values["removed_at"] = values["updated_at"]
            for field_name in {"agent_id", "canonical_path", "branch", "base_commit", "removed_at"}:
                if field_name in changes:
                    values[field_name] = changes[field_name]
            if "details" in changes:
                details = _json_copy(changes["details"])
                if not isinstance(details, dict):
                    raise TypeError("details must be a JSON object")
                values["details"] = details
            if self._before_compare_and_swap is not None:
                self._before_compare_and_swap()
            result = db.execute(
                update(RuntimeWorktree)
                .where(RuntimeWorktree.worktree_id == worktree_id, RuntimeWorktree.revision == expected_revision)
                .values(**values)
            )
            if result.rowcount != 1:
                lost_update = True
            else:
                db.expire_all()
                persisted = db.get(RuntimeWorktree, worktree_id)
                assert persisted is not None
                return self._record(persisted)
        assert lost_update
        current = self.get(worktree_id)
        raise RuntimeRecordRevisionConflict(
            "worktree", worktree_id, expected_revision, current.revision if current is not None else None
        )
