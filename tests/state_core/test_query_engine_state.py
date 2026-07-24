from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base
from query_engine import QueryEngine, ToolObservation
from services.llm_service import ChatCompletionResponse
from state_core import EventType, SessionRuntimeFactory, SQLAlchemyStateStore, TaskMode
from tools.base import ToolResult
from tools.web_search_tool import WebSearchOutput


class StaticLLM:
    async def chat_completion(self, request):
        return ChatCompletionResponse(
            id="response",
            model="test",
            content="done",
            finish_reason="stop",
        )


def tool_call_response() -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="tool-call",
        model="test",
        content="",
        tool_calls=[
            {
                "id": "call-search",
                "type": "function",
                "function": {
                    "name": "web_search",
                    "arguments": '{"query": "python"}',
                },
            }
        ],
        finish_reason="tool_calls",
    )


def completed_response() -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="completed",
        model="test",
        content="done",
        finish_reason="stop",
    )


class ToolCallingLLM:
    def __init__(self) -> None:
        self.responses = [tool_call_response(), completed_response()]
        self.requests = []

    async def chat_completion(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


class StreamingToolCallingLLM:
    def __init__(self) -> None:
        self.responses = [tool_call_response(), completed_response()]
        self.requests = []

    async def chat_completion_stream(self, request):
        self.requests.append(request)
        yield self.responses.pop(0)


class MalformedStreamingToolCallingLLM:
    def __init__(self) -> None:
        malformed = tool_call_response()
        malformed.tool_calls[0]["function"]["arguments"] = '{"query": "unterminated'
        self.responses = [malformed, completed_response()]
        self.requests = []

    async def chat_completion_stream(self, request):
        self.requests.append(request)
        yield self.responses.pop(0)


class WebSearchResultEngine(QueryEngine):
    async def _execute_tools(self, tool_calls, conversation_id=None):
        return [
            ToolObservation(
                tool_call_id=tool_calls[0].id,
                name="web_search",
                result=ToolResult.ok(
                    WebSearchOutput(
                        query="python",
                        results=[{"title": "Python", "url": "https://python.org"}],
                        duration_seconds=0.25,
                    )
                ),
                execution_time=0.25,
            )
        ]


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("llm_service", "chat_method"),
    [
        (ToolCallingLLM, "chat"),
        (StreamingToolCallingLLM, "chat_stream"),
    ],
)
async def test_web_search_result_is_json_at_transcript_boundary(
    runtime_factory: SessionRuntimeFactory,
    tmp_path: Path,
    llm_service,
    chat_method: str,
) -> None:
    llm = llm_service()
    engine = WebSearchResultEngine(
        llm_service=llm,
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=runtime_factory,
    )
    engine.create_conversation("web-search-json")

    events = [
        event
        async for event in getattr(engine, chat_method)("web-search-json", "search python")
    ]

    expected = {
        "query": "python",
        "results": [{"title": "Python", "url": "https://python.org"}],
        "duration_seconds": 0.25,
    }
    emitted = next(event for event in events if event["type"] == "tool_result")
    assert emitted["result"] == expected
    persisted = next(
        event
        for event in engine._session_runtime("web-search-json").events()
        if event.event_type is EventType.TOOL_RESULT
    )
    assert persisted.payload["result"] == expected
    tool_message = next(message for message in llm.requests[1].messages if message.role == "tool")
    assert json.loads(tool_message.content) == expected


@pytest.mark.asyncio
async def test_malformed_streaming_tool_input_enters_tool_validation_loop(
    runtime_factory: SessionRuntimeFactory,
    tmp_path: Path,
) -> None:
    llm = MalformedStreamingToolCallingLLM()
    engine = QueryEngine(
        llm_service=llm,
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=runtime_factory,
    )
    engine.create_conversation("malformed-tool-input")

    events = [
        event
        async for event in engine.chat_stream(
            "malformed-tool-input",
            "search python",
        )
    ]

    assert not any(event["type"] == "error" for event in events)
    failed_result = next(event for event in events if event["type"] == "tool_result")
    assert failed_result["success"] is False
    assert "Invalid input data" in failed_result["result"]
    assert events[-1]["content"] == "done"
    assert len(llm.requests) == 2

    tool_call = next(
        event
        for event in engine._session_runtime("malformed-tool-input").events()
        if event.event_type is EventType.TOOL_CALL
    )
    assert tool_call.payload["input"] == {}
