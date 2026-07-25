"""Foreground and durable background shell execution tools."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from harness.execution_tasks import ExecutionTaskLaunchError, ExecutionTaskManager

from .base import (
    Tool,
    ToolError,
    ToolExecutionError,
    ToolPermissionError,
    ToolResult,
    ToolTimeoutError,
    ToolValidationError,
    effective_tool_cwd,
    get_active_tool_context,
    register_tool,
    resolve_tool_path,
)


@dataclass
class BashInput:
    command: str
    timeout: Optional[float] = 120.0
    description: Optional[str] = None
    working_dir: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    run_in_background: bool = False


DANGEROUS_COMMANDS = (
    "rm -rf /",
    "rm -rf /*",
    "> /dev/sda",
    "dd if=/dev/zero",
    "mkfs",
    "format",
    "del /f /s /q",
)
MAX_BASH_TIMEOUT_SECONDS = 600.0


async def _kill_foreground(process: asyncio.subprocess.Process) -> None:
    if getattr(process, "pid", None) is None:
        if process.returncode is None:
            process.kill()
            await process.wait()
        return
    await ExecutionTaskManager._terminate_process_group(process)


async def _settle_foreground_communication(
    process: asyncio.subprocess.Process,
    communication: asyncio.Task[tuple[bytes, bytes]],
) -> None:
    if not communication.done():
        done, _ = await asyncio.wait({communication}, timeout=0.2)
        if communication not in done:
            for stream in (process.stdout, process.stderr):
                transport = getattr(stream, "_transport", None)
                if transport is not None:
                    transport.close()
            communication.cancel()
    await asyncio.gather(communication, return_exceptions=True)


@register_tool
class BashTool(Tool[BashInput, Dict[str, Any]]):
    name = "bash"
    description = (
        "Execute a Bash command in the foreground or launch it as a durable "
        "background shell task."
    )
    version = "2.0"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Bash command to execute",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Command timeout in seconds",
                        "default": 120.0,
                        "exclusiveMinimum": 0,
                        "maximum": MAX_BASH_TIMEOUT_SECONDS,
                    },
                    "description": {
                        "type": "string",
                        "description": "Short task description",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Command working directory",
                    },
                    "env": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Explicit subprocess environment",
                    },
                    "run_in_background": {
                        "type": "boolean",
                        "default": False,
                        "description": "Launch without waiting for completion",
                    },
                },
                "required": ["command"],
            },
        }

    async def validate(self, input_data: BashInput) -> Optional[ToolError]:
        if not isinstance(input_data.command, str) or not input_data.command.strip():
            return ToolValidationError("command must be a non-empty string")
        if (
            isinstance(input_data.timeout, bool)
            or not isinstance(input_data.timeout, (int, float))
            or not math.isfinite(input_data.timeout)
            or input_data.timeout <= 0
            or input_data.timeout > MAX_BASH_TIMEOUT_SECONDS
        ):
            return ToolValidationError(
                f"timeout must be between 0 and {MAX_BASH_TIMEOUT_SECONDS:g} seconds"
            )
        if type(input_data.run_in_background) is not bool:
            return ToolValidationError("run_in_background must be a boolean")
        command = input_data.command.strip()
        for dangerous in DANGEROUS_COMMANDS:
            if dangerous in command:
                return ToolPermissionError(
                    f"Command contains a blocked dangerous operation: {dangerous}"
                )
        return None

    async def execute(self, input_data: BashInput) -> ToolResult:
        command = input_data.command.strip()
        timeout = float(input_data.timeout)
        working_dir = (
            resolve_tool_path(input_data.working_dir)
            if input_data.working_dir
            else effective_tool_cwd()
        )
        if input_data.run_in_background:
            context = get_active_tool_context()
            harness = context.get("session_harness")
            if harness is None:
                return ToolResult.fail(
                    ToolExecutionError("session_harness is required for background Bash execution")
                )
            manager = ExecutionTaskManager.for_harness(harness)
            try:
                record = await manager.launch(
                    command,
                    description=input_data.description or command,
                    timeout=timeout,
                    cwd=working_dir,
                    environment=input_data.env,
                    cancellation=context.get("cancellation"),
                )
            except ExecutionTaskLaunchError as exc:
                return ToolResult.fail(ToolExecutionError(str(exc)))
            data = {
                "stdout": "",
                "stderr": "",
                "return_code": 0,
                "command": command,
                "status": record.status.value,
                "task_id": record.task_id,
                "background_task_id": record.task_id,
            }
            return ToolResult.ok(
                data,
                input_data.description or "Command started in background",
                metadata={"task_id": record.task_id, "task_type": "shell"},
            )

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(working_dir),
                env=input_data.env,
                start_new_session=True,
            )
            communication = asyncio.create_task(process.communicate())
            try:
                done, _ = await asyncio.wait({communication}, timeout=timeout)
            except asyncio.CancelledError:
                await _kill_foreground(process)
                await _settle_foreground_communication(process, communication)
                raise
            if communication not in done:
                await _kill_foreground(process)
                await _settle_foreground_communication(process, communication)
                return ToolResult.fail(ToolTimeoutError(timeout))
            stdout, stderr = await communication
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ToolResult.fail(ToolExecutionError(f"Command execution failed: {exc}"))

        stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
        stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
        result = {
            "stdout": stdout_text,
            "stderr": stderr_text,
            "return_code": process.returncode,
            "command": command,
        }
        message = (
            input_data.description or "Command completed successfully"
            if process.returncode == 0
            else f"Command completed with return code: {process.returncode}"
        )
        return ToolResult.ok(
            result,
            message,
            metadata={
                "return_code": process.returncode,
                "stdout_length": len(stdout_text),
                "stderr_length": len(stderr_text),
            },
        )

    def is_destructive(self) -> bool:
        return True

    def requires_confirmation(self) -> bool:
        return True


@dataclass
class BashBatchInput:
    commands: List[str]
    timeout: Optional[float] = 120.0
    stop_on_error: bool = True


@register_tool
class BashBatchTool(Tool[BashBatchInput, List[Dict[str, Any]]]):
    name = "bash_batch"
    description = "Execute Bash commands sequentially"
    version = "1.0"

    async def validate(self, input_data: BashBatchInput) -> Optional[ToolError]:
        if not input_data.commands:
            return ToolValidationError("commands must not be empty")
        return None

    async def execute(self, input_data: BashBatchInput) -> ToolResult:
        results: list[dict[str, Any]] = []
        tool = BashTool()
        for index, command in enumerate(input_data.commands):
            result = await tool.run(
                BashInput(
                    command=command,
                    timeout=input_data.timeout,
                    description=f"Batch command [{index + 1}/{len(input_data.commands)}]",
                ),
                get_active_tool_context(),
            )
            results.append(
                {
                    "command": command,
                    "success": result.success,
                    "data": result.data if result.success else None,
                    "error": str(result.error) if result.error else None,
                }
            )
            if input_data.stop_on_error and not result.success:
                break
        success_count = sum(1 for result in results if result["success"])
        return ToolResult.ok(
            results,
            f"Batch execution complete: {success_count}/{len(results)} succeeded",
            metadata={
                "total_commands": len(input_data.commands),
                "success_count": success_count,
                "failed_count": len(results) - success_count,
            },
        )

    def is_destructive(self) -> bool:
        return True

    def requires_confirmation(self) -> bool:
        return True
