from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base
from state_core import EventType, SessionHealth, SessionRuntime, SQLAlchemyStateStore
from state_core.sqlalchemy_store import RuntimeEvent, RuntimeSession, RuntimeSnapshot


@pytest.fixture
def runtime_store(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return SessionRuntime("recover-me", SQLAlchemyStateStore(factory)), factory


def test_transcript_preserves_tool_call_ids_and_order(runtime_store) -> None:
    runtime, _ = runtime_store
    runtime.append_event(EventType.ASSISTANT_MESSAGE, {"content": "calling tools"})
    parent_id = runtime.events()[-1].id
    runtime.append_event(
        EventType.TOOL_CALL,
        {"toolCallId": "call-1", "name": "read_file", "input": {"path": "a.py"}},
        parent_event_id=parent_id,
    )
    runtime.append_event(
        EventType.TOOL_RESULT,
        {"toolCallId": "call-1", "content": "result"},
        parent_event_id=runtime.events()[-1].id,
    )

    events = runtime.events()
    assert [event.event_type for event in events] == [
        EventType.ASSISTANT_MESSAGE,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
    ]
    assert events[1].payload["toolCallId"] == "call-1"
    assert events[2].payload["toolCallId"] == "call-1"


def test_corrupt_snapshot_falls_back_to_durable_session_and_events(runtime_store) -> None:
    runtime, factory = runtime_store
    runtime.append_event(EventType.USER_MESSAGE, {"content": "hello"})
    with factory() as db, db.begin():
        db.add(
            RuntimeSnapshot(
                session_id=runtime.session_id,
                last_event_id=runtime.state.last_event_id,
                revision=runtime.state.revision,
                state={"broken": True},
                checksum="bad",
                valid=True,
                created_at=datetime.now(timezone.utc),
            )
        )

    recovered = SessionRuntime.recover(runtime.session_id, runtime.store)

    assert recovered.state.health is SessionHealth.READY
    assert recovered.state.revision == runtime.state.revision
    assert recovered.events()[-1].payload["content"] == "hello"


def test_invalid_event_state_fails_closed_and_denies_writes(runtime_store) -> None:
    runtime, factory = runtime_store
    runtime.append_event(EventType.USER_MESSAGE, {"content": "valid"})
    with factory() as db, db.begin():
        db.add(
            RuntimeEvent(
                session_id=runtime.session_id,
                event_type=EventType.CHECKPOINT.value,
                payload={"state": {"broken": True}},
                parent_event_id=None,
                created_at=datetime.now(timezone.utc),
            )
        )

    recovered = SessionRuntime.recover(runtime.session_id, runtime.store)

    assert recovered.state.health is SessionHealth.RECOVERY_REQUIRED
    with pytest.raises(RuntimeError, match="requires recovery"):
        recovered.append_event(EventType.USER_MESSAGE, {"content": "denied"})


def test_invalid_persisted_session_fails_closed(runtime_store) -> None:
    runtime, factory = runtime_store
    with factory() as db, db.begin():
        row = db.get(RuntimeSession, runtime.session_id)
        row.state = {"broken": True}

    recovered = SessionRuntime.recover(runtime.session_id, runtime.store)

    assert recovered.state.health is SessionHealth.RECOVERY_REQUIRED
    with pytest.raises(RuntimeError, match="requires recovery"):
        recovered.append_event(EventType.USER_MESSAGE, {"content": "denied"})


def test_recovery_marks_running_agents_interrupted_without_replaying_work(runtime_store) -> None:
    runtime, _ = runtime_store
    runtime.state.agents = {
        "worker-1": {"status": "running", "toolCallId": "mutating-call"},
        "worker-2": {"status": "completed"},
    }
    runtime.append_event(EventType.AGENT_LIFECYCLE, {"agentId": "worker-1", "status": "running"})

    recovered = SessionRuntime.recover(runtime.session_id, runtime.store)

    assert recovered.state.agents["worker-1"]["status"] == "interrupted"
    assert recovered.state.agents["worker-2"]["status"] == "completed"
    assert recovered.events()[-1].event_type is EventType.EXECUTION_INTERRUPTED
