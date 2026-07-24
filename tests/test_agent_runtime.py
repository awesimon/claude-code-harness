from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.built_in import EXPLORE_AGENT, GENERAL_PURPOSE_AGENT
from agents.engine import AgentExecutor
from agents.types import CustomAgentDefinition
from harness import SessionHarnessFactory
from models import Base
from services.llm_service import ChatCompletionResponse
from state_core import AgentRecord, SessionRuntimeFactory, SQLAlchemyStateStore
from tools.base import Tool, ToolResult, ToolSpec, canonical_tool_name


class SequenceLLMService:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def chat_completion(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def child_harness(tmp_path: Path, registry=None):
    engine = create_engine(f"sqlite:///{tmp_path / 'executor.db'}")
    Base.metadata.create_all(engine)
    store = SQLAlchemyStateStore(sessionmaker(bind=engine))
    root = SessionHarnessFactory(
        SessionRuntimeFactory(store), workspace_root=tmp_path, tool_registry=registry
    ).create("root")
    return root.child("agent-1")


def record(tmp_path: Path) -> AgentRecord:
    return AgentRecord(
        "agent-1", "root", "Explore", "inspect", "inspect files", False, str(tmp_path), {}
    )


@pytest.mark.asyncio
async def test_builtin_agent_resolves_canonical_tools(tmp_path: Path) -> None:
    executor = AgentExecutor(EXPLORE_AGENT)
    child = child_harness(tmp_path)
    names = {
        executor.tool_name(tool, child.tool_runtime.registry)
        for tool in executor._resolve_tools(child)
    }
    assert names == {"read_file", "glob", "grep", "bash"}


@pytest.mark.asyncio
async def test_executor_uses_child_harness_for_tool_cwd_and_history(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello", encoding="utf-8")
    llm = SequenceLLMService(
        [
            ChatCompletionResponse(
                id="one",
                model="test",
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"file_path": "sample.txt"}),
                        },
                    }
                ],
            ),
            ChatCompletionResponse(
                id="two",
                model="test",
                content="Scope: inspected\nResult: done",
                finish_reason="stop",
                usage={"total_tokens": 4},
            ),
        ]
    )
    child = child_harness(tmp_path)
    executor = AgentExecutor(EXPLORE_AGENT, llm_service=llm)

    result = await executor.run(record(tmp_path), child)

    followup = llm.requests[1].messages
    assistant = next(message for message in followup if message.role == "assistant")
    tool_result = next(message for message in followup if message.role == "tool")
    assert assistant.tool_calls[0]["id"] == "call-1"
    assert tool_result.tool_call_id == "call-1"
    assert result.termination_reason == "completed"
    assert result.usage == {"total_tokens": 4}
    assert result.tool_count == 1


@pytest.mark.asyncio
async def test_executor_uses_definition_tool_filter_and_child_cancellation(
    tmp_path: Path,
) -> None:
    child = child_harness(tmp_path)
    child.runtime_context.cancellation.cancel()
    executor = AgentExecutor(EXPLORE_AGENT, llm_service=SequenceLLMService([]))

    with pytest.raises(asyncio.CancelledError):
        await executor.run(record(tmp_path), child)


class ContextTool(Tool[dict, dict]):
    input_type = dict

    def __init__(self, name: str, enabled: bool) -> None:
        self.name = name
        self.enabled = enabled
        self.calls = 0

    def is_enabled(self, context=None) -> bool:
        return bool(
            self.enabled
            and context
            and context.get("agent_id") == "agent-1"
            and context.get("session_runtime") is not None
        )

    async def execute(self, input_data: dict) -> ToolResult:
        self.calls += 1
        return ToolResult.ok({"registry": self.name})


class IsolatedRegistry:
    def __init__(self, *tools: Tool) -> None:
        self.tools = {canonical_tool_name(tool.name): tool for tool in tools}

    def list_tools(self):
        return list(self.tools)

    def resolve_name(self, name: str):
        canonical = canonical_tool_name(name)
        return canonical if canonical in self.tools else None

    def get(self, name: str):
        canonical = self.resolve_name(name)
        return self.tools.get(canonical) if canonical else None

    def get_spec(self, name: str):
        canonical = self.resolve_name(name)
        tool = self.get(name)
        if canonical is None or tool is None:
            return None
        return ToolSpec.from_tool(tool, name=canonical)


@pytest.mark.asyncio
async def test_executor_uses_child_registry_and_context_enabled_filter(
    tmp_path: Path,
) -> None:
    enabled = ContextTool("OnlyEnabled", True)
    disabled = ContextTool("Hidden", False)
    registry = IsolatedRegistry(enabled, disabled)
    child = child_harness(tmp_path, registry)
    definition = CustomAgentDefinition(
        agent_type="isolated",
        when_to_use="isolated tools",
        tools=["OnlyEnabled", "Hidden"],
    )
    llm = SequenceLLMService(
        [
            ChatCompletionResponse(
                id="one",
                model="test",
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "only_enabled", "arguments": "{}"},
                    },
                    {
                        "id": "call-2",
                        "type": "function",
                        "function": {"name": "hidden", "arguments": "{}"},
                    },
                ],
            ),
            ChatCompletionResponse(
                id="two", model="test", content="done", finish_reason="stop"
            ),
        ]
    )
    isolated_record = AgentRecord(
        "agent-1",
        "root",
        "isolated",
        "use tool",
        "isolated",
        False,
        str(tmp_path),
        {},
    )

    result = await AgentExecutor(definition, llm_service=llm).run(
        isolated_record, child
    )

    schema_names = {
        item["function"]["name"] for item in llm.requests[0].tools
    }
    assert schema_names == {"only_enabled"}
    assert enabled.calls == 1
    assert disabled.calls == 0
    assert result.tool_count == 2


def test_executor_tool_visibility_tracks_todo_task_mode(tmp_path: Path) -> None:
    child = child_harness(tmp_path)
    executor = AgentExecutor(GENERAL_PURPOSE_AGENT)
    registry = child.tool_runtime.registry

    task_mode = {
        executor.tool_name(tool, registry) for tool in executor._resolve_tools(child)
    }
    assert "task_create" in task_mode
    assert "todo_write" not in task_mode

    child.session_runtime.enable_todo_v1()
    todo_mode = {
        executor.tool_name(tool, registry) for tool in executor._resolve_tools(child)
    }
    assert "todo_write" in todo_mode
    assert "task_create" not in todo_mode
