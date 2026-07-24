"""Todo compatibility tool backed by durable session runtime state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from state_core import SessionRuntime

from .base import Tool, ToolResult, ToolValidationError, get_active_tool_context, register_tool


@dataclass
class TodoWriteInput:
    todos: list[dict[str, Any]]


def _runtime(context: Mapping[str, Any] | None = None) -> SessionRuntime | None:
    runtime = (context or get_active_tool_context()).get("session_runtime")
    return runtime if isinstance(runtime, SessionRuntime) else None


@register_tool
class TodoWriteTool(Tool[TodoWriteInput, dict[str, Any]]):
    name = "TodoWrite"
    description = "Replace the current session todo list"
    input_type = TodoWriteInput

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "minLength": 1},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                            "activeForm": {"type": "string", "minLength": 1},
                        },
                        "required": ["content", "status", "activeForm"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["todos"],
            "additionalProperties": False,
        }

    async def validate(self, input_data: TodoWriteInput):
        if input_data.todos is None:
            return ToolValidationError("todos is required")
        for index, todo in enumerate(input_data.todos):
            if not isinstance(todo, dict):
                return ToolValidationError(f"todos[{index}] must be an object")
            if set(todo) != {"content", "status", "activeForm"}:
                return ToolValidationError(
                    f"todos[{index}] must contain exactly content, status, and activeForm"
                )
            if not todo["content"] or not todo["activeForm"]:
                return ToolValidationError(f"todos[{index}] text fields must not be empty")
            if todo["status"] not in {"pending", "in_progress", "completed"}:
                return ToolValidationError(f"todos[{index}] has invalid status")
        return None

    def is_destructive(self) -> bool:
        return False

    def requires_confirmation(self) -> bool:
        return False

    def is_enabled(self, context: dict[str, Any] | None = None) -> bool:
        runtime = _runtime(context)
        return runtime is not None and runtime.task_mode.value == "todo_v1"

    async def execute(self, input_data: TodoWriteInput) -> ToolResult:
        runtime = _runtime()
        if runtime is None:
            return ToolResult.fail("session_runtime is required for TodoWrite")
        if runtime.task_mode.value != "todo_v1":
            return ToolResult.fail("TodoWrite is disabled while Task V2 is active")
        scope = get_active_tool_context().get("agent_id") or runtime.session_id
        try:
            old_todos, submitted = runtime.replace_todos(input_data.todos, scope)
            return ToolResult.ok({"oldTodos": old_todos, "newTodos": submitted})
        except Exception as exc:
            return ToolResult.fail(exc)
