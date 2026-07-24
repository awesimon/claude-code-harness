from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base
from schemas import (
    ConversationCreate,
    MessageCreate,
    PlanCreate,
    TaskCreate,
    TaskUpdate,
)
from services.conversation_service import ConversationService
from services.plan_service import PlanService
from services.task_service import TaskService
from state_core import EventType, SessionRuntime, SQLAlchemyStateStore


def test_legacy_services_delegate_mutations_to_state_core(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'adapters.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as db:
        conversations = ConversationService(db)
        conversation = conversations.create_conversation(ConversationCreate(title="Durable"))
        message = conversations.add_message(
            conversation.id, MessageCreate(role="user", content="hello")
        )
        assert message.content == "hello"

        tasks = TaskService(db)
        first = tasks.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                subject="First",
                description="First task",
            )
        )
        second = tasks.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                subject="Second",
                description="Second task",
            )
        )
        tasks.update_task(first.id, TaskUpdate(blocks=[second.id]))
        assert tasks.get_task(second.id).blocked_by == [first.id]
        tasks.update_task(first.id, TaskUpdate(blocks=[]))
        assert tasks.get_task(second.id).blocked_by == []

        claim = tasks.claim_task(first.id, "agent-1")
        assert claim.success
        unassigned = tasks.unassign_task(first.id)
        assert unassigned.owner is None
        assert unassigned.status == "pending"

        plans = PlanService(db)
        plans.enter_plan_mode(conversation.id)
        plan = plans.create_or_update_plan(
            PlanCreate(conversation_id=conversation.id, content="# Plan")
        )
        submitted = plans.exit_plan_mode(conversation.id, plan.content, [])
        assert submitted["state"] == "pending_approval"

    runtime = SessionRuntime(conversation.id, SQLAlchemyStateStore(factory))
    assert runtime.get_task(first.id) is not None
    assert runtime.state.plan.state.value == "pending_approval"
    assert [event.event_type for event in runtime.events()][0] is EventType.USER_MESSAGE
