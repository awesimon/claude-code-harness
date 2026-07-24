from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from models import Base
from state_core import (
    AgentRecord,
    AgentStatus,
    AgentTerminationReason,
    InvalidAgentTransition,
    RuntimeRecordRevisionConflict,
    SQLAlchemyStateStore,
    TraceSpanRecord,
    TraceSpanStatus,
    WorktreeRecord,
    WorktreeStatus,
)


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime-records.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def store(session_factory: sessionmaker[Session]) -> SQLAlchemyStateStore:
    return SQLAlchemyStateStore(session_factory)


def agent(agent_id: str, **overrides: object) -> AgentRecord:
    values: dict[str, object] = {
        "agent_id": agent_id,
        "root_session_id": "root-1",
        "agent_type": "worker",
        "prompt": "Do the work",
        "description": "A durable worker",
        "is_background": False,
        "effective_cwd": "/repo",
        "definition_snapshot": {"model": "test", "nested": {"version": 1}},
    }
    values.update(overrides)
    return AgentRecord(**values)  # type: ignore[arg-type]


def test_agent_lifecycle_persists_across_new_store_and_json_is_detached(
    store: SQLAlchemyStateStore, session_factory: sessionmaker[Session]
) -> None:
    definition = {"model": "test", "nested": {"version": 1}}
    created = store.agents.create(agent("agent-1", definition_snapshot=definition))
    definition["nested"]["version"] = 99
    assert created.definition_snapshot["nested"]["version"] == 1

    running = store.agents.transition(
        created.agent_id, AgentStatus.RUNNING, created.revision
    )
    completed = store.agents.transition(
        running.agent_id,
        AgentStatus.COMPLETED,
        running.revision,
        termination_reason=AgentTerminationReason.COMPLETED,
        output={"result": ["ok"]},
        usage={"total_tokens": 3},
    )
    completed.output["result"].append("mutated")

    restarted_store = SQLAlchemyStateStore(session_factory)
    persisted = restarted_store.agents.get("agent-1")
    assert persisted is not None
    assert persisted.status is AgentStatus.COMPLETED
    assert persisted.termination_reason is AgentTerminationReason.COMPLETED
    assert persisted.output == {"result": ["ok"]}
    assert persisted.usage == {"total_tokens": 3}
    assert persisted.revision == 2


def test_terminal_agent_cannot_restart_invalid_transitions_and_stale_revisions_rejected(
    store: SQLAlchemyStateStore,
) -> None:
    created = store.agents.create(agent("agent-1"))
    with pytest.raises(InvalidAgentTransition):
        store.agents.transition(created.agent_id, AgentStatus.COMPLETED, created.revision)

    running = store.agents.transition(created.agent_id, AgentStatus.RUNNING, created.revision)
    completed = store.agents.transition(
        running.agent_id, AgentStatus.COMPLETED, running.revision
    )
    with pytest.raises(InvalidAgentTransition):
        store.agents.transition(completed.agent_id, AgentStatus.RUNNING, completed.revision)
    with pytest.raises(RuntimeRecordRevisionConflict):
        store.agents.transition(completed.agent_id, AgentStatus.FAILED, running.revision)


def test_agent_listing_and_reconciliation_only_interrupts_non_live_open_agents(
    store: SQLAlchemyStateStore,
) -> None:
    parent = store.agents.create(agent("parent", is_background=True))
    child = store.agents.create(agent("child", parent_agent_id=parent.agent_id))
    other_root = store.agents.create(agent("other", root_session_id="root-2"))
    running = store.agents.transition(child.agent_id, AgentStatus.RUNNING, child.revision)

    assert [item.agent_id for item in store.agents.list("root-1", is_background=True)] == ["parent"]
    assert [item.agent_id for item in store.agents.list("root-1", parent_agent_id="parent")] == [
        "child"
    ]
    assert [item.agent_id for item in store.agents.list("root-1", status=AgentStatus.RUNNING)] == [
        "child"
    ]

    reconciled = store.agents.reconcile("root-1", {running.agent_id})
    assert [item.agent_id for item in reconciled] == ["parent"]
    assert reconciled[0].status is AgentStatus.INTERRUPTED
    assert reconciled[0].termination_reason is AgentTerminationReason.INTERRUPTED
    assert store.agents.get(running.agent_id).status is AgentStatus.RUNNING  # type: ignore[union-attr]
    assert store.agents.get(other_root.agent_id).status is AgentStatus.PENDING  # type: ignore[union-attr]


def test_metadata_put_get_uses_revision_checks_and_detaches_json(store: SQLAlchemyStateStore) -> None:
    value = {"config": {"attempt": 1}}
    created = store.metadata.put("root-1", "harness", value)
    value["config"]["attempt"] = 2
    assert created.snapshot == {"config": {"attempt": 1}}

    loaded = store.metadata.get("root-1", "harness")
    assert loaded == created
    assert loaded is not None
    loaded.snapshot["config"]["attempt"] = 3
    assert store.metadata.get("root-1", "harness").snapshot == {"config": {"attempt": 1}}  # type: ignore[union-attr]

    updated = store.metadata.put("root-1", "harness", {"config": {"attempt": 4}}, 0)
    assert updated.revision == 1
    with pytest.raises(RuntimeRecordRevisionConflict):
        store.metadata.put("root-1", "harness", {"stale": True}, 0)


def test_trace_start_finish_and_interrupt_open_spans(store: SQLAlchemyStateStore) -> None:
    root = store.traces.start(
        TraceSpanRecord(span_id="span-root", root_session_id="root-1", kind="agent", name="root")
    )
    child = store.traces.start(
        TraceSpanRecord(
            span_id="span-child",
            root_session_id="root-1",
            agent_id="agent-1",
            parent_span_id=root.span_id,
            kind="tool",
            name="command",
        )
    )
    finished = store.traces.finish(
        child.span_id, TraceSpanStatus.COMPLETED, child.revision, usage={"tokens": 5}
    )
    interrupted = store.traces.interrupt_open("root-1")

    assert finished.status is TraceSpanStatus.COMPLETED
    assert finished.duration_ms == int(
        (finished.finished_at - finished.started_at).total_seconds() * 1000
    )
    assert [span.span_id for span in interrupted] == [root.span_id]
    assert interrupted[0].status is TraceSpanStatus.INTERRUPTED
    assert interrupted[0].duration_ms == int(
        (interrupted[0].finished_at - interrupted[0].started_at).total_seconds() * 1000
    )
    assert [span.span_id for span in store.traces.list("root-1")] == ["span-root", "span-child"]


def test_trace_errors_are_sanitized_before_persistence(store: SQLAlchemyStateStore) -> None:
    started = store.traces.start(
        TraceSpanRecord(span_id="span-error", root_session_id="root-1", kind="tool", name="request")
    )

    finished = store.traces.finish(
        started.span_id,
        TraceSpanStatus.FAILED,
        started.revision,
        error={
            "Authorization": "Bearer secret",
            "context": {"api_key": "nested secret", "safe": "kept"},
        },
    )
    persisted = store.traces.get(started.span_id)

    expected = {
        "Authorization": "[REDACTED]",
        "context": {"api_key": "[REDACTED]", "safe": "kept"},
    }
    assert finished.error == expected
    assert persisted is not None
    assert persisted.error == expected


def test_trace_repository_adds_duration_column_to_existing_sqlite_table(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pre-duration.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE runtime_trace_spans ("
                "span_id VARCHAR PRIMARY KEY, root_session_id VARCHAR NOT NULL, "
                "agent_id VARCHAR, parent_span_id VARCHAR, kind VARCHAR NOT NULL, "
                "name VARCHAR NOT NULL, status VARCHAR NOT NULL, revision INTEGER NOT NULL, "
                "started_at DATETIME NOT NULL, finished_at DATETIME, usage JSON NOT NULL, "
                "error_json JSON, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
            )
        )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    store = SQLAlchemyStateStore(session_factory)
    started = store.traces.start(
        TraceSpanRecord(span_id="legacy-span", root_session_id="root-1", kind="tool", name="request")
    )
    finished = store.traces.finish(
        started.span_id, TraceSpanStatus.COMPLETED, started.revision
    )

    assert finished.duration_ms is not None


def test_worktree_create_update_get_and_stale_revision(store: SQLAlchemyStateStore) -> None:
    details = {"lease": {"owner": "agent-1"}}
    created = store.worktrees.create(
        WorktreeRecord(
            worktree_id="wt-1",
            root_session_id="root-1",
            agent_id="agent-1",
            repository_root="/repo",
            canonical_path="/repo/.worktrees/wt-1",
            branch="codex/wt-1",
            base_commit="abc123",
            status=WorktreeStatus.CREATING,
            details=details,
        )
    )
    details["lease"]["owner"] = "changed"
    updated = store.worktrees.update(
        created.worktree_id,
        created.revision,
        status=WorktreeStatus.READY,
        details={"lease": {"owner": "agent-1"}, "prepared": True},
    )
    assert updated.status is WorktreeStatus.READY
    assert store.worktrees.get(created.worktree_id) == updated
    assert [item.worktree_id for item in store.worktrees.list("root-1", agent_id="agent-1")] == [
        "wt-1"
    ]
    with pytest.raises(RuntimeRecordRevisionConflict):
        store.worktrees.update(created.worktree_id, created.revision, branch="stale")
