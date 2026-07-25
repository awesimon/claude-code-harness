"""Canonical Agent, TaskOutput, and TaskStop tools over SessionHarness."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from agents.types import AgentIsolationMode, AgentRequest
from state_core import AgentRecord, AgentStatus
from state_core.runtime_primitives import (
    EXECUTION_TASK_TERMINAL_STATUSES,
    MAX_EXECUTION_READ_BYTES,
)
from state_core.runtime_records import AGENT_TERMINAL_STATUSES

from .base import (
    Tool,
    ToolError,
    ToolExecutionError,
    ToolResult,
    ToolValidationError,
    get_active_tool_context,
    register_tool,
)


def _active_harness():
    from harness.session import SessionHarness

    harness = get_active_tool_context().get("session_harness")
    if not isinstance(harness, SessionHarness):
        raise ToolExecutionError("An active session harness is required")
    return harness


def _record_data(record: AgentRecord) -> dict[str, Any]:
    output = record.output if isinstance(record.output, dict) else {}
    termination_reason = record.termination_reason
    return {
        "task_id": record.agent_id,
        "agent_id": record.agent_id,
        "agent_type": record.agent_type,
        "description": record.description,
        "prompt": record.prompt,
        "status": record.status.value,
        "content": output.get("content", []),
        "output": output.get("output"),
        "total_tool_use_count": int(output.get("tool_count", 0)),
        "usage": dict(record.usage),
        "termination_reason": (
            termination_reason.value if termination_reason is not None else None
        ),
        "error": dict(record.error) if record.error is not None else None,
    }


def _execution_task_for_harness(harness: Any, task_id: str):
    record = harness.store.execution_tasks.get(task_id)
    if record is not None and record.root_session_id != harness.root_session_id:
        raise ToolExecutionError(f"shell task {task_id} does not belong to the active session")
    return record


@dataclass(frozen=True)
class AgentToolInput:
    prompt: str
    description: str | None = None
    subagent_type: str = "general-purpose"
    run_in_background: bool = False
    model: str | None = None
    cwd: str | None = None
    isolation: str | None = None


@register_tool
class AgentTool(Tool[AgentToolInput, dict[str, Any]]):
    name = "Agent"
    description = "Launch a specialized child agent"
    aliases = ("Task",)
    should_defer = True

    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "description": {"type": "string"},
            "subagent_type": {"type": "string", "default": "general-purpose"},
            "run_in_background": {"type": "boolean", "default": False},
            "model": {"type": "string"},
            "cwd": {"type": "string"},
            "isolation": {"type": "string", "enum": ["worktree"]},
        },
        "required": ["prompt"],
    }

    async def validate(self, input_data: AgentToolInput) -> ToolError | None:
        if not isinstance(input_data.prompt, str) or not input_data.prompt:
            return ToolValidationError("prompt is required")
        if input_data.description is not None and not isinstance(input_data.description, str):
            return ToolValidationError("description must be a string")
        if not isinstance(input_data.subagent_type, str) or not input_data.subagent_type:
            return ToolValidationError("subagent_type must be non-empty")
        if type(input_data.run_in_background) is not bool:
            return ToolValidationError("run_in_background must be a boolean")
        if input_data.model is not None and not isinstance(input_data.model, str):
            return ToolValidationError("model must be a string")
        if input_data.cwd is not None and not isinstance(input_data.cwd, str):
            return ToolValidationError("cwd must be a string")
        if input_data.isolation is not None and not isinstance(input_data.isolation, str):
            return ToolValidationError("isolation must be a string")
        if input_data.isolation not in (None, "worktree"):
            return ToolValidationError("isolation must be 'worktree' when provided")
        if input_data.cwd is not None and input_data.isolation is not None:
            return ToolValidationError("cwd and isolation are mutually exclusive")
        return None

    async def execute(self, input_data: AgentToolInput) -> ToolResult:
        harness = _active_harness()
        scheduler = harness.agent_scheduler
        record = await scheduler.spawn(
            AgentRequest(
                prompt=input_data.prompt,
                agent_type=input_data.subagent_type,
                description=input_data.description or input_data.prompt,
                background=input_data.run_in_background,
                model=input_data.model,
                cwd=input_data.cwd,
                isolation=(
                    AgentIsolationMode(input_data.isolation)
                    if input_data.isolation is not None
                    else None
                ),
            ),
            harness=harness,
        )
        data = _record_data(record)
        if input_data.run_in_background:
            data.update(
                {
                    "status": "async_launched",
                    "agent_status": record.status.value,
                }
            )
        return ToolResult.ok(data)

    def is_read_only(self) -> bool:
        return False


@dataclass(frozen=True)
class TaskOutputInput:
    task_id: str
    block: bool = True
    timeout: float = 30000
    cursor: int = 0
    max_bytes: int = MAX_EXECUTION_READ_BYTES
    tail: bool = False


@register_tool
class TaskOutputTool(Tool[TaskOutputInput, dict[str, Any]]):
    name = "TaskOutput"
    description = "Read status and output for a background agent task"
    aliases = ("AgentOutputTool", "BashOutputTool")
    should_defer = True

    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "block": {"type": "boolean", "default": True},
            "timeout": {
                "type": "number",
                "minimum": 0,
                "maximum": 600000,
                "default": 30000,
                "description": "Maximum wait in milliseconds",
            },
            "cursor": {"type": "integer", "minimum": 0, "default": 0},
            "max_bytes": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_EXECUTION_READ_BYTES,
                "default": MAX_EXECUTION_READ_BYTES,
            },
            "tail": {"type": "boolean", "default": False},
        },
        "required": ["task_id"],
    }

    async def validate(self, input_data: TaskOutputInput) -> ToolError | None:
        if not isinstance(input_data.task_id, str) or not input_data.task_id:
            return ToolValidationError("task_id is required")
        if type(input_data.block) is not bool:
            return ToolValidationError("block must be a boolean")
        if (
            isinstance(input_data.timeout, bool)
            or not isinstance(input_data.timeout, (int, float))
            or not math.isfinite(input_data.timeout)
            or input_data.timeout < 0
            or input_data.timeout > 600000
        ):
            return ToolValidationError("timeout must be between 0 and 600000 ms")
        if isinstance(input_data.cursor, bool) or not isinstance(input_data.cursor, int):
            return ToolValidationError("cursor must be a non-negative integer")
        if input_data.cursor < 0:
            return ToolValidationError("cursor must be a non-negative integer")
        if isinstance(input_data.max_bytes, bool) or not isinstance(input_data.max_bytes, int):
            return ToolValidationError("max_bytes must be a positive integer")
        if input_data.max_bytes <= 0 or input_data.max_bytes > MAX_EXECUTION_READ_BYTES:
            return ToolValidationError(
                f"max_bytes must be between 1 and {MAX_EXECUTION_READ_BYTES}"
            )
        if type(input_data.tail) is not bool:
            return ToolValidationError("tail must be a boolean")
        return None

    async def execute(self, input_data: TaskOutputInput) -> ToolResult:
        harness = _active_harness()
        execution_task = _execution_task_for_harness(harness, input_data.task_id)
        if execution_task is not None:
            from harness.execution_tasks import ExecutionTaskManager

            read = await ExecutionTaskManager.for_harness(harness).read(
                input_data.task_id,
                cursor=input_data.cursor,
                max_bytes=input_data.max_bytes,
                block=input_data.block,
                timeout=input_data.timeout / 1000,
                tail=input_data.tail,
            )
            return ToolResult.ok(
                {
                    "retrieval_status": read.retrieval_status,
                    "task": {
                        "task_id": read.record.task_id,
                        "status": read.record.status.value,
                        "output": read.data.decode("utf-8", errors="replace"),
                        "next_cursor": read.next_cursor,
                        "total_bytes": read.total_bytes,
                        "exit_code": read.record.exit_code,
                    },
                }
            )
        scheduler = harness.agent_scheduler
        record = scheduler.status(input_data.task_id)
        retrieval_status = "success" if record.status in AGENT_TERMINAL_STATUSES else "not_ready"
        if input_data.block and record.status not in AGENT_TERMINAL_STATUSES:
            from harness.agents import AgentWaitTimeout

            try:
                record = await scheduler.wait(input_data.task_id, input_data.timeout / 1000)
                retrieval_status = "success"
            except AgentWaitTimeout:
                record = scheduler.status(input_data.task_id)
                retrieval_status = "timeout"
        data = _record_data(record)
        data["retrieval_status"] = retrieval_status
        return ToolResult.ok(data)

    def is_read_only(self) -> bool:
        return True


@dataclass(frozen=True)
class TaskStopInput:
    task_id: str


@register_tool
class TaskStopTool(Tool[TaskStopInput, dict[str, Any]]):
    name = "TaskStop"
    description = "Stop a running background agent task"
    aliases = ("KillShell",)
    should_defer = True

    input_schema = {
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
    }

    async def validate(self, input_data: TaskStopInput) -> ToolError | None:
        if not isinstance(input_data.task_id, str) or not input_data.task_id:
            return ToolValidationError("task_id is required")
        return None

    async def execute(self, input_data: TaskStopInput) -> ToolResult:
        harness = _active_harness()
        execution_task = _execution_task_for_harness(harness, input_data.task_id)
        if execution_task is not None:
            from harness.execution_tasks import (
                ExecutionTaskManager,
                ExecutionTaskNotRunning,
            )

            if execution_task.status in EXECUTION_TASK_TERMINAL_STATUSES:
                raise ToolExecutionError(
                    f"shell task {input_data.task_id} is not running "
                    f"(status: {execution_task.status.value})"
                )
            manager = ExecutionTaskManager.for_harness(harness)
            if input_data.task_id not in manager._owned:
                raise ToolExecutionError(
                    f"shell task {input_data.task_id} is not owned by this runtime"
                )
            try:
                stopped = await manager.stop(input_data.task_id)
            except ExecutionTaskNotRunning as exc:
                raise ToolExecutionError(str(exc)) from exc
            return ToolResult.ok(
                {
                    "task_id": stopped.task_id,
                    "status": stopped.status.value,
                    "exit_code": stopped.exit_code,
                    "termination_reason": stopped.termination_reason,
                }
            )
        record = await harness.agent_scheduler.stop(input_data.task_id)
        return ToolResult.ok(_record_data(record))


@dataclass(frozen=True)
class AgentListInput:
    status: str | None = None
    background: bool | None = None


@register_tool
class AgentListTool(Tool[AgentListInput, list[dict[str, Any]]]):
    name = "agent_list"
    description = "List durable child agents in the active session"

    async def validate(self, input_data: AgentListInput) -> ToolError | None:
        if input_data.background is not None and type(input_data.background) is not bool:
            return ToolValidationError("background must be a boolean")
        if input_data.status is not None:
            if not isinstance(input_data.status, str):
                return ToolValidationError("status must be a string")
            try:
                AgentStatus(input_data.status)
            except ValueError:
                return ToolValidationError("status is not a valid agent status")
        return None

    async def execute(self, input_data: AgentListInput) -> ToolResult:
        status = AgentStatus(input_data.status) if input_data.status else None
        records = _active_harness().agent_scheduler.list(
            status=status, background=input_data.background
        )
        return ToolResult.ok([_record_data(record) for record in records])

    def is_read_only(self) -> bool:
        return True


@dataclass(frozen=True)
class AgentDestroyInput:
    agent_id: str


@register_tool
class AgentDestroyTool(Tool[AgentDestroyInput, dict[str, Any]]):
    name = "agent_destroy"
    description = "Stop a durable child agent"

    async def validate(self, input_data: AgentDestroyInput) -> ToolError | None:
        if not isinstance(input_data.agent_id, str) or not input_data.agent_id:
            return ToolValidationError("agent_id is required")
        return None

    async def execute(self, input_data: AgentDestroyInput) -> ToolResult:
        record = await _active_harness().agent_scheduler.stop(input_data.agent_id)
        return ToolResult.ok(_record_data(record))

    def is_destructive(self) -> bool:
        return True


__all__ = [
    "AgentDestroyInput",
    "AgentDestroyTool",
    "AgentListInput",
    "AgentListTool",
    "AgentTool",
    "AgentToolInput",
    "TaskOutputInput",
    "TaskOutputTool",
    "TaskStopInput",
    "TaskStopTool",
]
