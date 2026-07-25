import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents import AgentExecutionResult, AgentIsolationMode, AgentRequest
from harness import AgentScheduler, SessionHarnessFactory
from models import Base
from state_core import SQLAlchemyStateStore, SessionRuntimeFactory
from tools.agent_runtime_tools import AgentTool, TaskOutputTool, TaskStopTool
from tools.agent_runtime_tools import AgentDestroyTool, AgentListTool
from tools.base import ToolRegistry


class BlockingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, record, child_harness):
        self.started.set()
        await self.release.wait()
        return AgentExecutionResult(content="done", output="done")


class CompletingRunner:
    async def run(self, record, child_harness):
        return AgentExecutionResult(content="done", output={"answer": "done"})


@pytest.fixture
def harness(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agent-tools.db'}")
    Base.metadata.create_all(engine)
    runtime_factory = SessionRuntimeFactory(
        SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))
    )
    return SessionHarnessFactory(runtime_factory, workspace_root=tmp_path).create("tool-session")


@pytest.mark.asyncio
async def test_agent_tool_uses_active_harness(harness) -> None:
    runner = BlockingRunner()
    scheduler = AgentScheduler(harness, runner=runner)

    result = await AgentTool().run(
        {
            "prompt": "inspect",
            "description": "Inspect",
            "subagent_type": "Explore",
            "run_in_background": True,
        },
        {"session_harness": harness},
    )

    assert result.success
    assert result.data["status"] == "async_launched"
    assert result.data["agent_id"]
    assert scheduler.status(result.data["agent_id"]).status.value == "running"
    runner.release.set()
    await scheduler.wait(result.data["agent_id"])
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_agent_tool_returns_foreground_result(harness) -> None:
    scheduler = AgentScheduler(harness, runner=CompletingRunner())

    result = await AgentTool().run(
        {
            "prompt": "inspect",
            "description": "Inspect",
            "subagent_type": "Explore",
        },
        {"session_harness": harness},
    )

    assert result.success
    assert result.data["status"] == "completed"
    assert result.data["content"] == [{"type": "text", "text": "done"}]
    assert result.data["output"] == {"answer": "done"}
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_task_output_and_stop_share_scheduler(harness) -> None:
    runner = BlockingRunner()
    scheduler = AgentScheduler(harness, runner=runner)
    launched = await AgentTool().run(
        {
            "prompt": "inspect",
            "description": "Inspect",
            "subagent_type": "Explore",
            "run_in_background": True,
        },
        {"session_harness": harness},
    )
    agent_id = launched.data["agent_id"]

    status = await TaskOutputTool().run(
        {"task_id": agent_id, "block": False},
        {"session_harness": harness},
    )
    stopped = await TaskStopTool().run(
        {"task_id": agent_id},
        {"session_harness": harness},
    )

    assert status.success
    assert status.data["status"] == "running"
    assert stopped.success
    assert stopped.data["status"] == "cancelled"
    runner.release.set()
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_task_output_reports_blocking_timeout(harness) -> None:
    runner = BlockingRunner()
    scheduler = AgentScheduler(harness, runner=runner)
    launched = await AgentTool().run(
        {
            "prompt": "inspect",
            "description": "Inspect",
            "subagent_type": "Explore",
            "run_in_background": True,
        },
        {"session_harness": harness},
    )

    result = await TaskOutputTool().run(
        {"task_id": launched.data["agent_id"], "block": True, "timeout": 0},
        {"session_harness": harness},
    )

    assert result.success
    assert result.data["retrieval_status"] == "timeout"
    assert result.data["status"] == "running"
    await scheduler.stop(launched.data["agent_id"])
    runner.release.set()
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_task_stop_runs_through_harness_pipeline_without_approval(harness) -> None:
    runner = BlockingRunner()
    scheduler = AgentScheduler(harness, runner=runner)
    launched = await AgentTool().run(
        {
            "prompt": "inspect",
            "description": "Inspect",
            "subagent_type": "Explore",
            "run_in_background": True,
        },
        {"session_harness": harness},
    )
    harness.deferred_tools.activate("task_stop")

    execution = await harness.tool_runtime.execute(
        "TaskStop",
        {"task_id": launched.data["agent_id"]},
        harness.runtime_context,
    )

    assert execution.result.success
    assert execution.result.data["status"] == "cancelled"
    runner.release.set()
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_agent_tool_rejects_non_boolean_background_flag(harness) -> None:
    scheduler = AgentScheduler(harness, runner=BlockingRunner())

    result = await AgentTool().run(
        {
            "prompt": "inspect",
            "description": "Inspect",
            "subagent_type": "Explore",
            "run_in_background": "false",
        },
        {"session_harness": harness},
    )

    assert not result.success
    assert "boolean" in result.message
    for record in scheduler.list():
        await scheduler.stop(record.agent_id)
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_agent_tool_passes_isolation_as_typed_request_field(
    harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    class CapturingScheduler:
        def __init__(self) -> None:
            self.request = None

        async def spawn(self, request, *, harness):
            self.request = request
            return SimpleNamespace(
                agent_id="isolated",
                agent_type=request.agent_type,
                description=request.description,
                prompt=request.prompt,
                status=SimpleNamespace(value="completed"),
                output={},
                usage={},
                termination_reason=None,
                error=None,
            )

    scheduler = CapturingScheduler()
    monkeypatch.setattr(
        AgentScheduler,
        "for_harness",
        classmethod(lambda cls, target: scheduler),
    )

    result = await AgentTool().run(
        {"prompt": "inspect", "isolation": "worktree"},
        {"session_harness": harness},
    )

    assert result.success
    assert scheduler.request.isolation is AgentIsolationMode.WORKTREE
    assert scheduler.request.definition_metadata == {}


@pytest.mark.asyncio
async def test_nested_agent_tool_uses_calling_child_scope(harness) -> None:
    runner = BlockingRunner()
    scheduler = AgentScheduler(harness, runner=runner, root_concurrency=2, per_parent_concurrency=2)
    parent = await scheduler.spawn(AgentRequest("parent", "Explore", "Parent", background=True))
    parent_harness = harness.child(parent.agent_id)

    nested = await AgentTool().run(
        {
            "prompt": "nested",
            "description": "Nested",
            "subagent_type": "Explore",
            "run_in_background": True,
        },
        {"session_harness": parent_harness},
    )

    nested_record = scheduler.status(nested.data["agent_id"])
    assert nested_record.parent_agent_id == parent.agent_id
    await scheduler.stop(nested_record.agent_id)
    await scheduler.stop(parent.agent_id)
    runner.release.set()
    await scheduler.shutdown()


def test_agent_runtime_tool_aliases_resolve_to_canonical_tools() -> None:
    expected = {
        "Agent": "agent",
        "Task": "agent",
        "TaskOutput": "task_output",
        "AgentOutputTool": "task_output",
        "TaskStop": "task_stop",
        "KillShell": "task_stop",
    }

    for alias, canonical in expected.items():
        assert ToolRegistry.resolve_name(alias) == canonical


def test_legacy_agent_tool_module_reexports_canonical_tools() -> None:
    from tools.agent_tool import (
        AgentDestroyTool as LegacyAgentDestroyTool,
        AgentListTool as LegacyAgentListTool,
        AgentTool as LegacyAgentTool,
    )

    assert LegacyAgentTool is AgentTool
    assert LegacyAgentListTool is AgentListTool
    assert LegacyAgentDestroyTool is AgentDestroyTool


@pytest.mark.asyncio
async def test_agent_tool_requires_an_active_session_harness() -> None:
    result = await AgentTool().run(
        {"prompt": "inspect", "description": "Inspect", "subagent_type": "Explore"},
        {},
    )

    assert not result.success
    assert "session harness" in result.message.lower()


@pytest.mark.asyncio
async def test_agent_tool_rejects_spoofed_session_harness() -> None:
    result = await AgentTool().run(
        {"prompt": "inspect", "description": "Inspect", "subagent_type": "Explore"},
        {"session_harness": object()},
    )

    assert not result.success
    assert "active session harness" in result.message.lower()


@pytest.mark.asyncio
async def test_task_output_rejects_non_boolean_block(harness) -> None:
    result = await TaskOutputTool().run(
        {"task_id": "missing", "block": "false", "timeout": 0},
        {"session_harness": harness},
    )

    assert not result.success
    assert "block must be a boolean" in result.message
