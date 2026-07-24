from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base
from query_engine import QueryEngine
from services.llm_service import ChatCompletionResponse
from state_core import EventType, SessionRuntimeFactory, SQLAlchemyStateStore, TaskMode


class StaticLLM:
    async def chat_completion(self, request):
        return ChatCompletionResponse(
            id="response",
            model="test",
            content="done",
            finish_reason="stop",
        )


@pytest.fixture
def runtime_factory(tmp_path: Path) -> SessionRuntimeFactory:
    engine = create_engine(f"sqlite:///{tmp_path / 'query-engine.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return SessionRuntimeFactory(SQLAlchemyStateStore(factory))


@pytest.mark.asyncio
async def test_query_engine_persists_and_resumes_transcript(
    runtime_factory: SessionRuntimeFactory, tmp_path: Path
) -> None:
    first = QueryEngine(
        llm_service=StaticLLM(),
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=runtime_factory,
    )
    first.create_conversation("durable-query")
    events = [event async for event in first.chat("durable-query", "hello")]
    assert events[-1]["content"] == "done"

    second = QueryEngine(
        llm_service=StaticLLM(),
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=runtime_factory,
    )
    second.resume_conversation("durable-query")

    assert [turn.role for turn in second.get_conversation("durable-query").messages] == [
        "user",
        "assistant",
    ]
    runtime = second._session_runtime("durable-query")
    assert [event.event_type for event in runtime.events()] == [
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
    ]


def test_query_engine_exposes_exactly_one_task_mode(
    runtime_factory: SessionRuntimeFactory, tmp_path: Path
) -> None:
    engine = QueryEngine(
        llm_service=StaticLLM(),
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=runtime_factory,
    )
    engine.create_conversation("tool-mode")
    task_names = {item["function"]["name"] for item in engine._build_tools_schema("tool-mode")}
    assert "task_create" in task_names
    assert "todo_write" not in task_names

    runtime = engine._session_runtime("tool-mode")
    runtime.enable_todo_v1()
    assert runtime.task_mode is TaskMode.TODO_V1
    todo_names = {item["function"]["name"] for item in engine._build_tools_schema("tool-mode")}
    assert "todo_write" in todo_names
    assert "task_create" not in todo_names


def test_resume_rebuilds_assistant_tool_call_and_result_order(
    runtime_factory: SessionRuntimeFactory, tmp_path: Path
) -> None:
    runtime = runtime_factory.create("tool-transcript")
    runtime.append_event(EventType.ASSISTANT_MESSAGE, {"content": "checking"})
    runtime.append_event(
        EventType.TOOL_CALL,
        {"toolCallId": "call-1", "name": "read_file", "input": {"file_path": "a.py"}},
    )
    runtime.append_event(
        EventType.TOOL_RESULT,
        {"toolCallId": "call-1", "name": "read_file", "success": True, "result": "ok"},
    )
    engine = QueryEngine(
        llm_service=StaticLLM(),
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=runtime_factory,
    )

    engine.resume_conversation("tool-transcript")

    turns = engine.get_conversation("tool-transcript").messages
    assert turns[0].tool_calls[0].id == "call-1"
    assert turns[1].tool_observations[0].tool_call_id == "call-1"
    assert turns[1].tool_observations[0].result.data == "ok"
