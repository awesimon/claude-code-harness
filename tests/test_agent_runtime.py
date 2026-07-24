from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.built_in import EXPLORE_AGENT
from agents.engine import AgentExecutor
from harness import SessionHarnessFactory
from models import Base
from services.llm_service import ChatCompletionResponse
from state_core import AgentRecord, SessionRuntimeFactory, SQLAlchemyStateStore


class SequenceLLMService:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def chat_completion(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def child_harness(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'executor.db'}")
    Base.metadata.create_all(engine)
    store = SQLAlchemyStateStore(sessionmaker(bind=engine))
    root = SessionHarnessFactory(
        SessionRuntimeFactory(store), workspace_root=tmp_path
    ).create("root")
    return root.child("agent-1")


def record(tmp_path: Path) -> AgentRecord:
    return AgentRecord(
        "agent-1", "root", "Explore", "inspect", "inspect files", False, str(tmp_path), {}
    )


@pytest.mark.asyncio
async def test_builtin_agent_resolves_canonical_tools(tmp_path: Path) -> None:
    executor = AgentExecutor(EXPLORE_AGENT)
    names = {executor.tool_name(tool) for tool in executor._resolve_tools()}
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
