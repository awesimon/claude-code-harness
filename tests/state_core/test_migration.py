from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Conversation, Message, Plan, Task, TaskStatus
from state_core import EventType, migrate_legacy_session


def test_legacy_migration_is_idempotent_and_preserves_order(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db, db.begin():
        db.add(Conversation(id="legacy-session", title="Legacy"))
        db.add_all(
            [
                Message(
                    id="message-1",
                    conversation_id="legacy-session",
                    role="user",
                    content="Build it",
                ),
                Message(
                    id="message-2",
                    conversation_id="legacy-session",
                    role="assistant",
                    content="Reading",
                    tool_calls=[{"id": "call-1", "name": "read_file", "input": {}}],
                    tool_results=[{"tool_call_id": "call-1", "content": "ok"}],
                ),
            ]
        )
        db.add_all(
            [
                Task(
                    id="legacy-task-1",
                    conversation_id="legacy-session",
                    subject="First",
                    description="First task",
                    status=TaskStatus.COMPLETED,
                    blocks=["legacy-task-2"],
                    blocked_by=[],
                    meta={"source": "legacy"},
                ),
                Task(
                    id="legacy-task-2",
                    conversation_id="legacy-session",
                    subject="Second",
                    description="Second task",
                    status=TaskStatus.PENDING,
                    blocks=[],
                    blocked_by=["legacy-task-1"],
                    meta={},
                ),
            ]
        )
        db.add(
            Plan(
                id="legacy-plan",
                conversation_id="legacy-session",
                content="# Legacy plan",
            )
        )

    first = migrate_legacy_session("legacy-session", factory, plan_root=tmp_path)
    first_event_count = len(first.events())
    second = migrate_legacy_session("legacy-session", factory, plan_root=tmp_path)

    assert len(second.events()) == first_event_count
    assert [task.metadata["legacyId"] for task in second.list_tasks()] == [
        "legacy-task-1",
        "legacy-task-2",
    ]
    assert second.list_tasks()[0].blocks == [second.list_tasks()[1].id]
    assert second.list_tasks()[1].blocked_by == [second.list_tasks()[0].id]
    transcript = [
        event
        for event in second.events()
        if event.event_type
        in {
            EventType.USER_MESSAGE,
            EventType.ASSISTANT_MESSAGE,
            EventType.TOOL_CALL,
            EventType.TOOL_RESULT,
        }
    ]
    assert [event.event_type for event in transcript] == [
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
    ]
    assert transcript[2].payload["toolCallId"] == "call-1"
    assert transcript[3].payload["toolCallId"] == "call-1"
    assert Path(second.state.plan.file_path).read_text() == "# Legacy plan"
