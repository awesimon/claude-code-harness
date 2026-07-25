"""Public hook configuration tools backed by session metadata."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from harness.hooks import HookEvent, HookRuntime

from .base import Tool, ToolResult, get_active_tool_context, register_tool


HOOK_EVENTS = [event.value for event in HookEvent]

_EVENT_DESCRIPTIONS = {
    "PreToolUse": "Before tool execution - can modify or block tool calls",
    "PostToolUse": "After tool execution - can observe tool results",
    "PostToolUseFailure": "After tool execution fails",
    "PermissionDenied": "After auto mode classifier denies a tool call",
    "Notification": "When notifications are sent",
    "UserPromptSubmit": "When the user submits a prompt",
    "SessionStart": "When a new session is started",
    "Stop": "Right before Claude concludes its response",
    "StopFailure": "When the turn ends due to an API error",
    "SubagentStart": "When a subagent is started",
    "SubagentStop": "Right before a subagent concludes its response",
    "PreCompact": "Before conversation compaction",
    "PostCompact": "After conversation compaction",
    "SessionEnd": "When a session is ending",
    "PermissionRequest": "When a permission decision is requested",
    "Setup": "Repository setup hooks",
    "TeammateIdle": "When a teammate is about to go idle",
    "TaskCreated": "When a task is created",
    "TaskCompleted": "When a task is completed",
    "Elicitation": "When an MCP server requests user input",
    "ElicitationResult": "After an MCP elicitation response",
    "ConfigChange": "When configuration changes",
    "InstructionsLoaded": "When instructions are loaded",
    "WorktreeCreate": "Before an isolated worktree is created",
    "WorktreeRemove": "Before an isolated worktree is removed",
    "CwdChanged": "After the effective working directory changes",
    "FileChanged": "When a watched file changes",
}


def _runtime() -> HookRuntime | None:
    harness = get_active_tool_context().get("session_harness")
    runtime = getattr(harness, "hooks", None)
    return runtime if isinstance(runtime, HookRuntime) else None


def _missing_runtime() -> ToolResult:
    return ToolResult.fail("session_harness is required for hook configuration")


class HooksListInput(BaseModel):
    pass


@register_tool
class HooksListTool(Tool[HooksListInput, ToolResult]):
    name = "hooks_list"
    description = "List hook configurations for the current session"
    input_model = HooksListInput

    async def execute(self, input_data: HooksListInput) -> ToolResult:
        runtime = _runtime()
        if runtime is None:
            return _missing_runtime()
        hooks = [
            {
                "index": index,
                "event": hook.event.value,
                "command": hook.command,
                "matcher": hook.matcher,
                "hook_id": hook.hook_id,
                "timeout": hook.timeout,
                "fail_closed": hook.fail_closed,
            }
            for index, hook in enumerate(runtime.list())
        ]
        message = f"{len(hooks)} hook(s) configured" if hooks else "No hooks configured"
        return ToolResult.ok({"hooks": hooks, "count": len(hooks)}, message)


class HooksAddInput(BaseModel):
    event: str = Field(..., description=f"Hook event type. One of: {', '.join(HOOK_EVENTS)}")
    command: str = Field(..., min_length=1, description="Command to execute when the hook fires")
    matcher: str | None = Field(None, description="Optional regular-expression matcher")
    timeout: float = Field(600.0, gt=0, description="Execution timeout in seconds")
    output_limit: int = Field(64 * 1024, gt=0, description="Combined output bound per stream")
    fail_closed: bool | None = Field(None, description="Override the event failure policy")


@register_tool
class HooksAddTool(Tool[HooksAddInput, ToolResult]):
    name = "hooks_add"
    description = "Add a hook configuration to the current session"
    input_model = HooksAddInput

    async def execute(self, input_data: HooksAddInput) -> ToolResult:
        runtime = _runtime()
        if runtime is None:
            return _missing_runtime()
        try:
            hook = runtime.add(
                input_data.event,
                input_data.command,
                matcher=input_data.matcher,
                timeout=input_data.timeout,
                output_limit=input_data.output_limit,
                fail_closed=input_data.fail_closed,
            )
        except (ValueError, TypeError) as exc:
            return ToolResult.fail(exc)
        matcher = f" (matcher: {hook.matcher})" if hook.matcher else ""
        return ToolResult.ok(
            {
                "event": hook.event.value,
                "command": hook.command,
                "matcher": hook.matcher,
                "hook_id": hook.hook_id,
            },
            f"Hook added for '{hook.event.value}'{matcher}",
        )


class HooksRemoveInput(BaseModel):
    index: int = Field(..., ge=0, description="Hook index from hooks_list")


@register_tool
class HooksRemoveTool(Tool[HooksRemoveInput, ToolResult]):
    name = "hooks_remove"
    description = "Remove a hook from the current session by index"
    input_model = HooksRemoveInput

    async def execute(self, input_data: HooksRemoveInput) -> ToolResult:
        runtime = _runtime()
        if runtime is None:
            return _missing_runtime()
        try:
            runtime.remove(input_data.index)
        except IndexError:
            return ToolResult(
                success=False,
                data=None,
                message=f"Hook with index {input_data.index} not found",
                error=None,
            )
        return ToolResult.ok(
            {"index": input_data.index},
            f"Hook at index {input_data.index} removed",
        )


class HooksEventsInput(BaseModel):
    pass


@register_tool
class HooksEventsTool(Tool[HooksEventsInput, ToolResult]):
    name = "hooks_events"
    description = "List all available hook event types"
    input_model = HooksEventsInput

    async def execute(self, input_data: HooksEventsInput) -> ToolResult:
        events: list[dict[str, Any]] = [
            {"name": event, "description": _EVENT_DESCRIPTIONS.get(event, "")}
            for event in HOOK_EVENTS
        ]
        return ToolResult.ok(
            {"events": events, "count": len(events)},
            f"{len(events)} hook events available",
        )
