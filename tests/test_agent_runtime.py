import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents.built_in import EXPLORE_AGENT
from agents.engine import AgentExecutor, SpawnAgentManager
from agents.types import AgentExecutionConfig, AgentToolResult
from services.llm_service import ChatCompletionResponse


class SequenceLLMService:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def chat_completion(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def agent_result(agent_id: str, reason: str = "completed") -> AgentToolResult:
    return AgentToolResult(
        agent_id=agent_id,
        agent_type="Explore",
        content=[{"type": "text", "text": reason}],
        total_tool_use_count=0,
        total_duration_ms=1,
        total_tokens=0,
        usage={},
        termination_reason=reason,
    )


class StubExecutor:
    counter = 0

    def __init__(self, *, block=None, **_kwargs):
        type(self).counter += 1
        self.agent_id = f"stub-{type(self).counter}"
        self.block = block
        self.aborted = False
        self.context = SimpleNamespace(
            agent_type="Explore",
            status="running",
            tool_use_count=0,
            started_at=None,
            completed_at=None,
        )

    async def execute(self):
        if self.block is not None:
            await self.block.wait()
        self.context.status = "completed"
        return agent_result(self.agent_id)

    def abort(self):
        self.aborted = True
        self.context.status = "killed"


class AgentExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_builtin_agent_resolves_canonical_tools(self):
        executor = AgentExecutor(EXPLORE_AGENT, "inspect")

        names = {executor.tool_name(tool) for tool in executor._resolve_tools()}

        self.assertEqual(names, {"read_file", "glob", "grep", "bash"})

    async def test_tool_calls_are_retained_in_followup_llm_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "sample.txt"
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
                                    "arguments": json.dumps({"file_path": str(file_path)}),
                                },
                            }
                        ],
                    ),
                    ChatCompletionResponse(
                        id="two",
                        model="test",
                        content="Scope: inspected\nResult: done",
                        finish_reason="stop",
                    ),
                ]
            )
            executor = AgentExecutor(
                EXPLORE_AGENT,
                "inspect",
                config=AgentExecutionConfig(workspace_root=Path(temp_dir)),
                llm_service=llm,
            )

            result = await executor.execute()

        followup = llm.requests[1].messages
        assistant = next(message for message in followup if message.role == "assistant")
        tool_result = next(message for message in followup if message.role == "tool")
        self.assertEqual(assistant.tool_calls[0]["id"], "call-1")
        self.assertEqual(tool_result.tool_call_id, "call-1")
        self.assertEqual(result.termination_reason, "completed")


class SpawnAgentManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreground_spawn_executes_before_returning(self):
        executors = []

        def factory(**kwargs):
            executor = StubExecutor(**kwargs)
            executors.append(executor)
            return executor

        manager = SpawnAgentManager(executor_factory=factory)

        agent_id = await manager.spawn_agent("Explore", "inspect", is_async=False)

        self.assertEqual(executors[0].context.status, "completed")
        self.assertEqual((await manager.wait_for_agent(agent_id)).termination_reason, "completed")

    async def test_background_spawn_can_be_waited(self):
        release = asyncio.Event()
        manager = SpawnAgentManager(
            executor_factory=lambda **kwargs: StubExecutor(block=release, **kwargs)
        )

        agent_id = await manager.spawn_agent("Explore", "inspect", is_async=True)
        self.assertEqual(manager.get_agent_status(agent_id)["status"], "running")
        release.set()

        result = await manager.wait_for_agent(agent_id, timeout=1)

        self.assertEqual(result.termination_reason, "completed")

    async def test_abort_cancels_background_execution_task(self):
        release = asyncio.Event()
        executors = []

        def factory(**kwargs):
            executor = StubExecutor(block=release, **kwargs)
            executors.append(executor)
            return executor

        manager = SpawnAgentManager(executor_factory=factory)
        agent_id = await manager.spawn_agent("Explore", "inspect", is_async=True)
        await asyncio.sleep(0)

        manager.abort_agent(agent_id)
        result = await manager.wait_for_agent(agent_id, timeout=1)

        self.assertTrue(executors[0].aborted)
        self.assertEqual(result.termination_reason, "cancelled")
        self.assertEqual(manager.get_agent_status(agent_id)["status"], "killed")


if __name__ == "__main__":
    unittest.main()
