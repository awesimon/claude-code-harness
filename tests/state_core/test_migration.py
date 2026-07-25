from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, BrokenBarrierError
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import state_core.migration as migration_module
from models import Base, Conversation, Message, Plan, Task, TaskStatus
from query_engine import QueryEngine
from state_core import (
    EventType,
    SessionRuntime,
    SessionRuntimeFactory,
    SQLAlchemyStateStore,
    TaskMutation,
    migrate_legacy_session,
)


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
    assert transcript[2].payload["name"] == "read_file"
    assert transcript[2].payload["input"] == {}
    assert transcript[3].payload["name"] == "read_file"
    assert transcript[3].payload["success"] is True
    assert transcript[3].payload["result"] == "ok"
    assert transcript[3].parent_event_id == transcript[2].id
    assert Path(second.state.plan.file_path).read_text() == "# Legacy plan"
    conversation_metadata = second.store.metadata.get(
        "legacy-session", "api.conversation"
    )
    plan_metadata = second.store.metadata.get("legacy-session", "api.plan")
    assert conversation_metadata is not None
    assert conversation_metadata.snapshot["title"] == "Legacy"
    assert plan_metadata is not None
    assert plan_metadata.snapshot["id"] == "legacy-plan"
    assert plan_metadata.snapshot["version"] == 1


def test_legacy_openai_tool_transcript_recovers_in_fresh_query_engine(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'openai-legacy.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db, db.begin():
        db.add(Conversation(id="legacy-openai", title="Legacy OpenAI"))
        db.add(
            Message(
                id="assistant-openai",
                conversation_id="legacy-openai",
                role="assistant",
                content="Reading",
                tool_calls=[
                    {
                        "id": "call-openai",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"file_path": "a.py"}',
                        },
                    }
                ],
                tool_results=[
                    {
                        "tool_call_id": "call-openai",
                        "name": "read_file",
                        "success": True,
                        "result": {"content": "source"},
                    }
                ],
            )
        )

    migrate_legacy_session("legacy-openai", factory, plan_root=tmp_path)
    fresh = QueryEngine(
        llm_service=AsyncMock(),
        session_runtime_factory=SessionRuntimeFactory(
            SQLAlchemyStateStore(factory)
        ),
        workspace_root=tmp_path,
    )
    fresh.resume_conversation("legacy-openai")

    turns = fresh.get_conversation("legacy-openai").messages
    assistant = next(turn for turn in turns if turn.role == "assistant")
    tool = next(turn for turn in turns if turn.role == "tool")
    call = assistant.tool_calls[0]
    observation = tool.tool_observations[0]
    assert (call.id, call.name, call.arguments) == (
        "call-openai",
        "read_file",
        {"file_path": "a.py"},
    )
    assert observation.tool_call_id == "call-openai"
    assert observation.name == "read_file"
    assert observation.result.success is True
    assert observation.result.data == {"content": "source"}


def test_legacy_anthropic_error_tool_result_recovers_with_call_parent(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'anthropic-legacy.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db, db.begin():
        db.add(Conversation(id="legacy-anthropic", title="Legacy Anthropic"))
        db.add(
            Message(
                id="assistant-anthropic",
                conversation_id="legacy-anthropic",
                role="assistant",
                content="Reading",
                tool_calls=[
                    {
                        "id": "toolu-anthropic",
                        "name": "read_file",
                        "input": {"file_path": "secret.py"},
                    }
                ],
                tool_results=[
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu-anthropic",
                        "is_error": True,
                        "content": "permission denied",
                    }
                ],
            )
        )

    runtime = migrate_legacy_session(
        "legacy-anthropic", factory, plan_root=tmp_path
    )
    call_event = next(
        event for event in runtime.events() if event.event_type is EventType.TOOL_CALL
    )
    result_event = next(
        event for event in runtime.events() if event.event_type is EventType.TOOL_RESULT
    )

    assert (
        result_event.payload["toolCallId"],
        result_event.payload["success"],
        result_event.parent_event_id,
    ) == ("toolu-anthropic", False, call_event.id)
    assert result_event.payload["result"] == "permission denied"

    fresh = QueryEngine(
        llm_service=AsyncMock(),
        session_runtime_factory=SessionRuntimeFactory(
            SQLAlchemyStateStore(factory)
        ),
        workspace_root=tmp_path,
    )
    fresh.resume_conversation("legacy-anthropic")
    turns = fresh.get_conversation("legacy-anthropic").messages
    assistant = next(turn for turn in turns if turn.role == "assistant")
    tool = next(turn for turn in turns if turn.role == "tool")
    call = assistant.tool_calls[0]
    observation = tool.tool_observations[0]
    assert (call.id, call.name, call.arguments) == (
        "toolu-anthropic",
        "read_file",
        {"file_path": "secret.py"},
    )
    assert observation.tool_call_id == "toolu-anthropic"
    assert observation.name == "read_file"
    assert observation.result.success is False
    assert observation.result.error.message == "permission denied"


def test_legacy_migration_retry_deduplicates_messages_and_completes_dependencies(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'retry.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db, db.begin():
        db.add(Conversation(id="retry-session", title="Retry"))
        db.add(
            Message(
                id="retry-message",
                conversation_id="retry-session",
                role="user",
                content="Only once",
            )
        )
        db.add_all(
            [
                Task(
                    id="retry-first",
                    conversation_id="retry-session",
                    subject="First",
                    description="First",
                    blocks=["retry-second"],
                ),
                Task(
                    id="retry-second",
                    conversation_id="retry-session",
                    subject="Second",
                    description="Second",
                    blocked_by=["retry-first"],
                ),
            ]
        )

    original_update = SessionRuntime.update_task
    failed = False

    def fail_first_dependency(self, task_id: str, mutation: TaskMutation):
        nonlocal failed
        if not failed and (mutation.add_blocks or mutation.add_blocked_by):
            failed = True
            raise RuntimeError("injected migration interruption")
        return original_update(self, task_id, mutation)

    monkeypatch.setattr(SessionRuntime, "update_task", fail_first_dependency)
    with pytest.raises(RuntimeError, match="injected migration interruption"):
        migrate_legacy_session("retry-session", factory, plan_root=tmp_path)
    monkeypatch.setattr(SessionRuntime, "update_task", original_update)

    recovered = migrate_legacy_session("retry-session", factory, plan_root=tmp_path)

    messages = [
        event
        for event in recovered.events()
        if event.event_type is EventType.USER_MESSAGE
        and event.payload.get("legacyMessageId") == "retry-message"
    ]
    assert len(messages) == 1
    by_legacy_id = {
        task.metadata["legacyId"]: task for task in recovered.list_tasks()
    }
    assert by_legacy_id["retry-first"].blocks == [
        by_legacy_id["retry-second"].id
    ]
    assert by_legacy_id["retry-second"].blocked_by == [
        by_legacy_id["retry-first"].id
    ]


def test_legacy_migration_retry_completes_partial_tool_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'tool-retry.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db, db.begin():
        db.add(Conversation(id="tool-retry", title="Tool retry"))
        db.add(
            Message(
                id="tool-message",
                conversation_id="tool-retry",
                role="assistant",
                content="Using tool",
                tool_calls=[{"id": "call-1", "name": "read_file", "input": {}}],
                tool_results=[{"tool_call_id": "call-1", "content": "done"}],
            )
        )

    original_append = SessionRuntime.append_event
    failed = False

    def fail_first_tool_call(self, event_type, payload, **kwargs):
        nonlocal failed
        if not failed and event_type is EventType.TOOL_CALL:
            failed = True
            raise RuntimeError("injected tool event interruption")
        return original_append(self, event_type, payload, **kwargs)

    monkeypatch.setattr(SessionRuntime, "append_event", fail_first_tool_call)
    with pytest.raises(RuntimeError, match="injected tool event interruption"):
        migrate_legacy_session("tool-retry", factory, plan_root=tmp_path)
    monkeypatch.setattr(SessionRuntime, "append_event", original_append)

    recovered = migrate_legacy_session("tool-retry", factory, plan_root=tmp_path)
    transcript = [
        event
        for event in recovered.events()
        if event.payload.get("legacyMessageId") == "tool-message"
    ]
    assert [event.event_type for event in transcript] == [
        EventType.ASSISTANT_MESSAGE,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
    ]


def test_legacy_plan_migration_retries_after_enter_plan_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'plan-retry.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db, db.begin():
        db.add(Conversation(id="plan-retry", title="Plan retry"))
        db.add(
            Plan(
                id="retry-plan",
                conversation_id="plan-retry",
                content="# Retry plan",
            )
        )

    original_save = migration_module.PlanFileStore.save
    failed = False

    def fail_first_save(self, slug: str, content: str):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("injected plan save interruption")
        return original_save(self, slug, content)

    monkeypatch.setattr(migration_module.PlanFileStore, "save", fail_first_save)
    with pytest.raises(RuntimeError, match="injected plan save interruption"):
        migrate_legacy_session("plan-retry", factory, plan_root=tmp_path)

    recovered = migrate_legacy_session("plan-retry", factory, plan_root=tmp_path)

    assert recovered.state.plan.file_path is not None
    assert Path(recovered.state.plan.file_path).read_text() == "# Retry plan"
    migrated = [
        event
        for event in recovered.events()
        if event.event_type is EventType.PLAN_TRANSITION
        and event.payload.get("action") == "migrate"
        and event.payload.get("legacyPlanId") == "retry-plan"
    ]
    assert len(migrated) == 1


def test_concurrent_legacy_migration_serializes_one_durable_import(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'concurrent.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db, db.begin():
        db.add(Conversation(id="concurrent-session", title="Concurrent"))
        db.add(
            Task(
                id="concurrent-task",
                conversation_id="concurrent-session",
                subject="Only once",
                description="Only once",
            )
        )

    barrier = Barrier(2)
    original_migrate_tasks = migration_module._migrate_legacy_tasks

    def synchronize_task_catalog(runtime, tasks):
        try:
            barrier.wait(timeout=0.5)
        except BrokenBarrierError:
            pass
        return original_migrate_tasks(runtime, tasks)

    monkeypatch.setattr(
        migration_module,
        "_migrate_legacy_tasks",
        synchronize_task_catalog,
    )

    def migrate():
        return migrate_legacy_session(
            "concurrent-session",
            factory,
            plan_root=tmp_path,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(migrate) for _ in range(2)]
        runtimes = [future.result(timeout=5) for future in futures]

    recovered = migrate_legacy_session(
        "concurrent-session",
        factory,
        plan_root=tmp_path,
    )
    tasks = recovered.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].metadata["legacyId"] == "concurrent-task"
    assert [runtime.list_tasks()[0].id for runtime in runtimes] == [
        tasks[0].id,
        tasks[0].id,
    ]
