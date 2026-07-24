import asyncio
import gc
import tempfile
import unittest
import weakref
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

from harness import (
    CancellationToken,
    PermissionMode,
    RuntimeContext,
    TerminationReason,
    ToolRuntime,
)
from tools.base import Tool, ToolResult, get_active_tool_context
from tools.bash_tool import BashTool


@dataclass
class ValueInput:
    value: str = "ok"
    file_path: str | None = None


class ReadTool(Tool[ValueInput, dict]):
    name = "read_test"
    description = "read test"

    async def execute(self, input_data: ValueInput) -> ToolResult:
        return ToolResult.ok({"value": input_data.value})

    def is_read_only(self) -> bool:
        return True


class WriteTool(ReadTool):
    name = "write_test"

    def is_read_only(self) -> bool:
        return False

    def is_destructive(self) -> bool:
        return True


class SlowTool(ReadTool):
    name = "slow_test"

    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.cancelled = False

    async def execute(self, input_data: ValueInput) -> ToolResult:
        self.started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return ToolResult.ok({"value": input_data.value})


class AttributeFlagTool(ReadTool):
    name = "attribute_flag_test"
    is_read_only = True
    is_destructive = False
    requires_confirmation = False


@dataclass
class PathInput:
    file_path: str


class PathEchoTool(Tool[PathInput, dict]):
    name = "path_echo_test"
    description = "echo a normalized path"

    async def execute(self, input_data: PathInput) -> ToolResult:
        return ToolResult.ok({"file_path": input_data.file_path})

    def is_read_only(self) -> bool:
        return True


class ContextProbeTool(Tool[ValueInput, dict]):
    name = "context_probe"
    description = "return active context"

    async def execute(self, input_data: ValueInput) -> ToolResult:
        context = get_active_tool_context()
        return ToolResult.ok(
            {
                "session_id": context["session_id"],
                "current_mode": context["current_mode"],
                "workspace_root": context["workspace_root"],
                "runtime_context_id": id(context["runtime_context"]),
            }
        )

    def is_read_only(self) -> bool:
        return True


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.killed = False
        self.started = asyncio.Event()
        self._released = asyncio.Event()

    async def communicate(self):
        self.started.set()
        await self._released.wait()
        return b"", b""

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._released.set()

    async def wait(self):
        return self.returncode


class LocalRegistry:
    def __init__(self, *tools: Tool):
        self.tools = {tool.name: tool for tool in tools}

    def get(self, name: str):
        return self.tools.get(name)

    def resolve_name(self, name: str):
        return name if name in self.tools else None


class ToolRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_read_only_tool_runs_without_approval(self):
        runtime = ToolRuntime(registry=LocalRegistry(ReadTool()))
        context = RuntimeContext(workspace_root=self.workspace)

        execution = await runtime.execute("read_test", {"value": "read"}, context)

        self.assertEqual(execution.termination_reason, TerminationReason.COMPLETED)
        self.assertEqual(execution.result.data, {"value": "read"})

    async def test_destructive_tool_requires_and_uses_approval(self):
        requests = []

        async def approve(request):
            requests.append(request)
            return True

        runtime = ToolRuntime(registry=LocalRegistry(WriteTool()))
        context = RuntimeContext(
            workspace_root=self.workspace,
            approval_callback=approve,
        )

        execution = await runtime.execute("write_test", {"value": "write"}, context)

        self.assertTrue(execution.result.success)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].tool_name, "write_test")

    async def test_destructive_tool_fails_closed_without_approval_callback(self):
        runtime = ToolRuntime(registry=LocalRegistry(WriteTool()))
        context = RuntimeContext(workspace_root=self.workspace)

        execution = await runtime.execute("write_test", {}, context)

        self.assertFalse(execution.result.success)
        self.assertEqual(execution.termination_reason, TerminationReason.PERMISSION_DENIED)

    async def test_bypass_skips_confirmation_but_not_workspace_boundary(self):
        runtime = ToolRuntime(registry=LocalRegistry(WriteTool()))
        context = RuntimeContext(
            workspace_root=self.workspace,
            permission_mode=PermissionMode.BYPASS,
        )

        allowed = await runtime.execute("write_test", {}, context)
        denied = await runtime.execute(
            "write_test",
            {"file_path": str(self.workspace.parent / "outside.txt")},
            context,
        )

        self.assertTrue(allowed.result.success)
        self.assertEqual(denied.termination_reason, TerminationReason.PERMISSION_DENIED)

    async def test_workspace_boundary_covers_working_directory(self):
        runtime = ToolRuntime(registry=LocalRegistry(BashTool()))
        context = RuntimeContext(
            workspace_root=self.workspace,
            permission_mode=PermissionMode.BYPASS,
        )

        execution = await runtime.execute(
            "bash",
            {"command": "pwd", "working_dir": str(self.workspace.parent)},
            context,
        )

        self.assertEqual(execution.termination_reason, TerminationReason.PERMISSION_DENIED)

    async def test_workspace_boundary_rejects_explicit_external_bash_path(self):
        runtime = ToolRuntime(registry=LocalRegistry(BashTool()))
        context = RuntimeContext(
            workspace_root=self.workspace,
            permission_mode=PermissionMode.BYPASS,
        )

        execution = await runtime.execute(
            "bash",
            {"command": "cat /etc/passwd"},
            context,
        )

        self.assertEqual(execution.termination_reason, TerminationReason.PERMISSION_DENIED)

    async def test_boolean_tool_traits_are_supported(self):
        runtime = ToolRuntime(registry=LocalRegistry(AttributeFlagTool()))
        context = RuntimeContext(workspace_root=self.workspace)

        execution = await runtime.execute("attribute_flag_test", {}, context)

        self.assertTrue(execution.result.success)

    async def test_parent_cancellation_propagates_to_child_token(self):
        parent = CancellationToken()
        child = CancellationToken(parent=parent)

        parent.cancel()

        self.assertTrue(child.cancelled)

    async def test_parent_does_not_retain_disposed_child_token(self):
        parent = CancellationToken()
        callback_count = len(parent._callbacks)
        child = CancellationToken(parent=parent)
        reference = weakref.ref(child)

        del child
        gc.collect()

        self.assertIsNone(reference())
        self.assertEqual(len(parent._callbacks), callback_count)

    async def test_callback_failure_does_not_block_sibling_cancellation(self):
        parent = CancellationToken()
        child = CancellationToken(parent=parent)

        def fail():
            raise RuntimeError("callback failure")

        parent.add_callback(fail)
        parent.cancel()

        self.assertTrue(child.cancelled)

    async def test_cancelled_error_callback_does_not_block_tasks_or_descendants(self):
        parent = CancellationToken()
        child = CancellationToken(parent=parent)
        task = parent.track(asyncio.create_task(asyncio.sleep(60)))

        def cancel_callback():
            raise asyncio.CancelledError()

        parent.add_callback(cancel_callback)
        parent.cancel()

        self.assertTrue(child.cancelled)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(task.cancelled())

    async def test_runtime_context_values_override_spoofed_metadata(self):
        runtime = ToolRuntime(registry=LocalRegistry(ContextProbeTool()))
        context = RuntimeContext(
            session_id="actual-session",
            workspace_root=self.workspace,
            permission_mode=PermissionMode.BYPASS,
            metadata={
                "session_id": "spoofed",
                "current_mode": "spoofed",
                "workspace_root": "spoofed",
                "runtime_context": "spoofed",
            },
        )

        execution = await runtime.execute("context_probe", {}, context)

        self.assertTrue(execution.result.success)
        self.assertEqual(execution.result.data["session_id"], "actual-session")
        self.assertEqual(execution.result.data["current_mode"], "bypass")
        self.assertEqual(execution.result.data["workspace_root"], str(self.workspace))
        self.assertEqual(execution.result.data["runtime_context_id"], id(context))

    async def test_relative_tool_paths_are_resolved_from_workspace(self):
        runtime = ToolRuntime(registry=LocalRegistry(PathEchoTool()))
        context = RuntimeContext(workspace_root=self.workspace)

        execution = await runtime.execute(
            "path_echo_test",
            {"file_path": "nested/file.txt"},
            context,
        )

        self.assertEqual(
            execution.result.data["file_path"],
            str(self.workspace / "nested/file.txt"),
        )

    async def test_cancelling_bash_kills_its_subprocess(self):
        process = FakeProcess()
        token = CancellationToken()
        runtime = ToolRuntime(registry=LocalRegistry(BashTool()), default_timeout=5)
        context = RuntimeContext(
            workspace_root=self.workspace,
            permission_mode=PermissionMode.BYPASS,
            cancellation=token,
        )

        with patch(
            "tools.bash_tool.asyncio.create_subprocess_shell",
            new=AsyncMock(return_value=process),
        ):
            task = asyncio.create_task(
                runtime.execute("bash", {"command": "sleep 60"}, context)
            )
            await process.started.wait()
            token.cancel()
            execution = await task

        self.assertEqual(execution.termination_reason, TerminationReason.CANCELLED)
        self.assertTrue(process.killed)

    async def test_tool_timeout_has_distinct_termination_reason(self):
        slow = SlowTool()
        runtime = ToolRuntime(registry=LocalRegistry(slow), default_timeout=0.01)
        context = RuntimeContext(workspace_root=self.workspace)

        execution = await runtime.execute("slow_test", {}, context)

        self.assertEqual(execution.termination_reason, TerminationReason.TIMEOUT)
        self.assertTrue(slow.cancelled)

    async def test_cancellation_interrupts_the_running_tool_task(self):
        slow = SlowTool()
        token = CancellationToken()
        runtime = ToolRuntime(registry=LocalRegistry(slow), default_timeout=5)
        context = RuntimeContext(workspace_root=self.workspace, cancellation=token)

        execution_task = asyncio.create_task(runtime.execute("slow_test", {}, context))
        await slow.started.wait()
        token.cancel()
        execution = await execution_task

        self.assertEqual(execution.termination_reason, TerminationReason.CANCELLED)
        self.assertTrue(slow.cancelled)


if __name__ == "__main__":
    unittest.main()
