from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.built_in import GENERAL_PURPOSE_AGENT
from agents.engine import AgentExecutor
from harness import (
    DeferredToolNotActive,
    DeferredToolUnavailable,
    PermissionMode,
    SessionHarnessFactory,
)
from harness.mcp import MCPToolDefinition
from models import Base
from query_engine import QueryEngine
from services.llm_service import ChatCompletionResponse
from state_core import AgentRecord, EventType, SessionRuntimeFactory, SQLAlchemyStateStore


class SequenceLLMService:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def chat_completion(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


@pytest.fixture
def runtime_factory(tmp_path: Path) -> SessionRuntimeFactory:
    engine = create_engine(f"sqlite:///{tmp_path / 'deferred.db'}")
    Base.metadata.create_all(engine)
    return SessionRuntimeFactory(
        SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))
    )


@pytest.fixture
def harness_factory(
    runtime_factory: SessionRuntimeFactory, tmp_path: Path
) -> SessionHarnessFactory:
    return SessionHarnessFactory(
        runtime_factory,
        workspace_root=tmp_path,
        permission_mode=PermissionMode.BYPASS,
    )


def _names(schemas: list[dict]) -> set[str]:
    return {schema["function"]["name"] for schema in schemas}


def _tool_call(name: str, arguments: dict) -> dict:
    return {
        "id": f"call-{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def test_deferred_schema_requires_exact_selection(
    harness_factory: SessionHarnessFactory,
) -> None:
    harness = harness_factory.create("root")

    assert "enter_worktree" not in harness.deferred_tools.visible_names()
    assert "tool_search" in harness.deferred_tools.visible_names()

    result = harness.deferred_tools.search("select:enter_worktree")

    assert result.selected == "enter_worktree"
    assert result.matches == ("enter_worktree",)
    assert "enter_worktree" in harness.deferred_tools.visible_names()
    assert any(
        event.event_type is EventType.TOOL_ACTIVATED
        and event.payload["tool"] == "enter_worktree"
        for event in harness.session_runtime.events()
    )


def test_selection_resolves_aliases_and_missing_names_do_not_activate(
    harness_factory: SessionHarnessFactory,
) -> None:
    harness = harness_factory.create("aliases")

    selected = harness.deferred_tools.search("select:EnterWorktree")
    missing = harness.deferred_tools.search("select:not_a_real_tool")

    assert selected.selected == "enter_worktree"
    assert missing.matches == ()
    assert missing.selected is None
    assert "not_a_real_tool" not in harness.deferred_tools.activations()


def test_keyword_search_uses_name_description_and_search_hint(
    harness_factory: SessionHarnessFactory,
) -> None:
    harness = harness_factory.create("search")

    result = harness.deferred_tools.search("isolated git", max_results=3)

    assert result.matches[0] == "enter_worktree"
    assert result.total_deferred_tools >= 1
    assert "enter_worktree" not in harness.deferred_tools.visible_names()


def test_activation_is_scoped_to_agent_and_restored(
    harness_factory: SessionHarnessFactory,
) -> None:
    root = harness_factory.create("scopes")
    left = root.child("left")
    right = root.child("right")
    left.deferred_tools.activate("enter_worktree")

    assert "enter_worktree" in left.deferred_tools.visible_names()
    assert "enter_worktree" not in root.deferred_tools.visible_names()
    assert "enter_worktree" not in right.deferred_tools.visible_names()

    resumed = harness_factory.resume("scopes", agent_id="left")
    assert "enter_worktree" in resumed.deferred_tools.visible_names()


@pytest.mark.asyncio
async def test_direct_unactivated_call_is_rejected_before_execution(
    harness_factory: SessionHarnessFactory,
) -> None:
    harness = harness_factory.create("direct")

    execution = await harness.tool_runtime.execute(
        "enter_worktree", {"name": "blocked"}, harness.runtime_context
    )

    assert execution.result.success is False
    assert "ToolSearch" in str(execution.result.error)
    assert not (harness.effective_cwd / ".claude" / "worktrees" / "blocked").exists()
    with pytest.raises(DeferredToolNotActive):
        harness.deferred_tools.require_active("enter_worktree")


def test_mcp_disconnect_hides_schema_but_keeps_activation_history(
    harness_factory: SessionHarnessFactory,
) -> None:
    harness = harness_factory.create("mcp")
    definition = MCPToolDefinition(
        name="echo",
        description="Echo text",
        server="test",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )
    harness.deferred_tools.register_mcp_tools("test", [definition])
    canonical = "mcp__test__echo"
    harness.deferred_tools.activate(canonical)
    assert canonical in harness.deferred_tools.visible_names()

    harness.deferred_tools.set_mcp_server_available("test", False)

    assert canonical in harness.deferred_tools.activations()
    assert canonical not in harness.deferred_tools.visible_names()
    with pytest.raises(DeferredToolUnavailable) as error:
        harness.deferred_tools.require_active(canonical)
    assert error.value.category == "mcp_unavailable"

    harness.deferred_tools.set_mcp_server_available("test", True)
    assert canonical in harness.deferred_tools.visible_names()


@pytest.mark.asyncio
async def test_dynamic_mcp_tool_executes_through_tool_runtime(
    harness_factory: SessionHarnessFactory,
) -> None:
    class FakeMCP:
        def __init__(self) -> None:
            self.calls = []

        async def call_tool(self, server, name, arguments):
            self.calls.append((server, name, arguments))
            return {"echo": arguments["text"]}

    harness = harness_factory.create("mcp-execute")
    fake = FakeMCP()
    harness._services["mcp"] = fake
    definition = MCPToolDefinition(
        name="echo",
        description="Echo text",
        server="test",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
    )
    harness.deferred_tools.register_mcp_tools("test", [definition])
    canonical = harness.deferred_tools.activate("mcp__test__echo")

    execution = await harness.tool_runtime.execute(
        canonical, {"text": "ok"}, harness.runtime_context
    )

    assert execution.result.success is True
    assert execution.result.data == {"echo": "ok"}
    assert fake.calls == [("test", "echo", {"text": "ok"})]


def test_child_inherits_parent_mcp_definitions_but_not_activation(
    harness_factory: SessionHarnessFactory,
) -> None:
    root = harness_factory.create("mcp-scopes")
    definition = MCPToolDefinition(
        name="echo",
        description="Echo text",
        server="root-server",
        input_schema={"type": "object", "properties": {}},
    )
    root.deferred_tools.register_mcp_tools("root-server", [definition])
    root.deferred_tools.activate("mcp__root-server__echo")
    child = root.child("child")

    assert "mcp__root-server__echo" not in child.deferred_tools.visible_names()
    child.deferred_tools.activate("mcp__root-server__echo")
    assert "mcp__root-server__echo" in child.deferred_tools.visible_names()


def test_query_engine_rebuilds_visible_schemas_after_tool_search(
    runtime_factory: SessionRuntimeFactory, tmp_path: Path
) -> None:
    llm = SequenceLLMService([])
    engine = QueryEngine(
        llm_service=llm,
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=runtime_factory,
    )
    conversation_id = engine.create_conversation("query-visible")

    before = _names(engine._build_tools_schema(conversation_id))
    engine._session_harness(conversation_id).deferred_tools.activate("enter_worktree")
    after = _names(engine._build_tools_schema(conversation_id))

    assert "tool_search" in before
    assert "enter_worktree" not in before
    assert "enter_worktree" in after


@pytest.mark.asyncio
async def test_query_engine_fetches_schemas_immediately_before_each_model_turn(
    runtime_factory: SessionRuntimeFactory, tmp_path: Path
) -> None:
    llm = SequenceLLMService(
        [
            ChatCompletionResponse(
                id="select",
                model="test",
                content="",
                tool_calls=[
                    _tool_call("tool_search", {"query": "select:enter_worktree"})
                ],
            ),
            ChatCompletionResponse(
                id="done", model="test", content="done", finish_reason="stop"
            ),
        ]
    )
    engine = QueryEngine(
        llm_service=llm,
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=runtime_factory,
    )
    conversation_id = engine.create_conversation("query-turns")

    _ = [event async for event in engine.chat(conversation_id, "load it")]

    assert "enter_worktree" not in _names(llm.requests[0].tools)
    assert "enter_worktree" in _names(llm.requests[1].tools)


@pytest.mark.asyncio
async def test_agent_fetches_schemas_immediately_before_each_model_turn(
    harness_factory: SessionHarnessFactory, tmp_path: Path
) -> None:
    root = harness_factory.create("agent-root")
    child = root.child("agent-1")
    llm = SequenceLLMService(
        [
            ChatCompletionResponse(
                id="select",
                model="test",
                content="",
                tool_calls=[
                    _tool_call("tool_search", {"query": "select:enter_worktree"})
                ],
            ),
            ChatCompletionResponse(
                id="done",
                model="test",
                content="Scope: tools\nResult: done",
                finish_reason="stop",
            ),
        ]
    )
    executor = AgentExecutor(GENERAL_PURPOSE_AGENT, llm_service=llm)
    record = AgentRecord(
        "agent-1",
        "agent-root",
        "general-purpose",
        "load a tool",
        "load",
        False,
        str(tmp_path),
        {},
    )

    await executor.run(record, child)

    assert "enter_worktree" not in _names(llm.requests[0].tools)
    assert "enter_worktree" in _names(llm.requests[1].tools)
