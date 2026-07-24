from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents import AgentExecutionResult
from harness import AgentScheduler, ToolRuntime
from models import Base
from query_engine import QueryEngine, ToolCall, ToolObservation
from services.llm_service import ChatCompletionResponse
from state_core import EventType, SessionRuntimeFactory, SQLAlchemyStateStore, TaskMode
from tools.base import Tool, ToolResult
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
        class OutputTool(Tool[dict, dict]):
            name = "web_search"
            input_type = dict

            async def execute(self, input_data):
                return ToolResult.ok(
                    WebSearchOutput(
                        query="python",
                        results=[{"title": "Python", "url": "https://python.org"}],
                        duration_seconds=0.25,
                    )
                )

            def is_read_only(self):
                return True

        class Registry:
            tool = OutputTool()

            @classmethod
            def resolve_name(cls, name):
                return "web_search" if name == "web_search" else None

            @classmethod
            def get(cls, name):
                return cls.tool if name == "web_search" else None

        harness = self._session_harness(conversation_id)
        execution = await ToolRuntime(Registry).execute(
            "web_search",
            tool_calls[0].arguments,
            harness.runtime_context,
            tool_call_id=tool_calls[0].id,
        )
        return [
            ToolObservation(
                tool_call_id=tool_calls[0].id,
                name="web_search",
                result=execution.result,
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

    assert {"agent", "task_output", "task_stop"}.issubset(todo_names)


def test_query_engine_uses_session_harness_as_only_runtime_lookup(
    runtime_factory: SessionRuntimeFactory, tmp_path: Path
) -> None:
    engine = QueryEngine(
        llm_service=StaticLLM(),
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=runtime_factory,
    )
    engine.create_conversation("harness-runtime")

    harness = engine._session_harness("harness-runtime")

    assert engine._session_runtime("harness-runtime") is harness.session_runtime
    assert not hasattr(engine, "_agent_manager")
    assert not hasattr(engine, "_session_runtimes")
    assert not hasattr(engine, "_tool_runtime")


def test_clear_conversation_rotates_the_session_harness(
    runtime_factory: SessionRuntimeFactory, tmp_path: Path
) -> None:
    engine = QueryEngine(
        llm_service=StaticLLM(),
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=runtime_factory,
    )
    engine.create_conversation("clear-harness")
    previous = engine._session_harness("clear-harness")

    engine.clear_conversation("clear-harness")
    current = engine._session_harness("clear-harness")

    assert previous.runtime_context.cancellation.cancelled
    assert current is not previous
    assert not current.runtime_context.cancellation.cancelled


@pytest.mark.asyncio
async def test_query_engine_finds_agent_across_owned_session_roots(
    runtime_factory: SessionRuntimeFactory, tmp_path: Path
) -> None:
    class CompletingRunner:
        async def run(self, record, child_harness):
            return AgentExecutionResult(content="done", output="done")

    engine = QueryEngine(
        llm_service=StaticLLM(),
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=runtime_factory,
    )
    engine.create_conversation("first-root")
    engine.create_conversation("second-root")
    first = AgentScheduler(engine._session_harness("first-root"), runner=CompletingRunner())
    second = AgentScheduler(engine._session_harness("second-root"), runner=CompletingRunner())

    agent_id = await engine.spawn_agent(
        "second-root", "Explore", "inspect", is_async=False
    )

    assert engine.get_agent_status(agent_id)["status"] == "completed"
    await first.shutdown()
    await second.shutdown()


@pytest.mark.asyncio
async def test_query_engine_abort_delegates_to_session_scheduler(
    runtime_factory: SessionRuntimeFactory, tmp_path: Path
) -> None:
    class BlockingRunner:
        def __init__(self) -> None:
            self.release = asyncio.Event()

        async def run(self, record, child_harness):
            await self.release.wait()
            return AgentExecutionResult(content="done")

    runner = BlockingRunner()
    engine = QueryEngine(
        llm_service=StaticLLM(),
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=runtime_factory,
    )
    engine.create_conversation("abort-root")
    scheduler = AgentScheduler(engine._session_harness("abort-root"), runner=runner)
    agent_id = await engine.spawn_agent(
        "abort-root", "Explore", "inspect", is_async=True
    )

    abort = engine.abort_agent(agent_id)
    assert abort is not None
    await abort

    assert engine.get_agent_status(agent_id)["status"] == "cancelled"
    runner.release.set()
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_query_engine_agent_tool_executes_with_active_harness(
    runtime_factory: SessionRuntimeFactory, tmp_path: Path
) -> None:
    class CompletingRunner:
        async def run(self, record, child_harness):
            return AgentExecutionResult(content="done", output="done")

    engine = QueryEngine(
        llm_service=StaticLLM(),
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=runtime_factory,
    )
    engine.create_conversation("agent-tool-root")
    scheduler = AgentScheduler(
        engine._session_harness("agent-tool-root"), runner=CompletingRunner()
    )

    observations = await engine._execute_tools(
        [
            ToolCall(
                id="agent-call",
                name="agent",
                arguments={
                    "prompt": "inspect",
                    "description": "Inspect",
                    "subagent_type": "Explore",
                },
            )
        ],
        "agent-tool-root",
    )

    assert observations[0].result.success
    assert observations[0].result.data["status"] == "completed"
    await scheduler.shutdown()


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
