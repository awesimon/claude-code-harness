"""Canonical plan-mode tools backed by durable session state."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from state_core import PlanState, SessionRuntime
from state_core.plan_files import PlanFileStore
from state_core.runtime import plan_slug
from tools.base import (
    Tool,
    ToolResult,
    ToolValidationError,
    get_active_tool_context,
    register_tool,
)


@dataclass
class EnterPlanModeInput:
    pass


@dataclass
class ExitPlanModeInput:
    plan: str | None = None
    allowed_prompts: list[dict[str, str]] = field(default_factory=list)


def _runtime() -> SessionRuntime:
    runtime = get_active_tool_context().get("session_runtime")
    if not isinstance(runtime, SessionRuntime):
        raise ToolValidationError("session_runtime is required for plan mode")
    return runtime


@register_tool
class EnterPlanModeTool(Tool[EnterPlanModeInput, dict[str, Any]]):
    name = "EnterPlanMode"
    description = "Enter read-only plan mode for implementation design"
    input_type = EnterPlanModeInput

    def get_input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(self, input_data: EnterPlanModeInput) -> ToolResult:
        try:
            runtime = _runtime()
            context = get_active_tool_context()
            permission_mode = context.get("current_mode", runtime.state.permission_mode)
            permission_value = getattr(permission_mode, "value", permission_mode)
            runtime.enter_plan(str(permission_value))
            return ToolResult.ok(
                {
                    "message": "Entered plan mode",
                    "state": runtime.state.plan.state.value,
                    "planFilePath": runtime.state.plan.file_path,
                }
            )
        except Exception as exc:
            return ToolResult.fail(exc)

    def is_read_only(self) -> bool:
        return True


@register_tool
class ExitPlanModeTool(Tool[ExitPlanModeInput, dict[str, Any]]):
    name = "ExitPlanMode"
    description = "Submit the current plan for approval and exit after approval"
    input_type = ExitPlanModeInput

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan": {"type": "string"},
                "allowedPrompts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string"},
                            "prompt": {"type": "string"},
                        },
                        "required": ["tool", "prompt"],
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        }

    async def run(self, input_data: Any, context: dict[str, Any] | None = None) -> ToolResult:
        if isinstance(input_data, Mapping) and "allowedPrompts" in input_data:
            normalized = dict(input_data)
            if "allowed_prompts" in normalized:
                return ToolResult.fail(
                    ToolValidationError("provide only one of allowedPrompts and allowed_prompts")
                )
            normalized["allowed_prompts"] = normalized.pop("allowedPrompts")
            input_data = normalized
        return await super().run(input_data, context)

    async def validate(self, input_data: ExitPlanModeInput):
        for index, prompt in enumerate(input_data.allowed_prompts):
            if not isinstance(prompt, dict) or set(prompt) != {"tool", "prompt"}:
                return ToolValidationError(
                    f"allowed_prompts[{index}] must contain exactly tool and prompt"
                )
        return None

    async def execute(self, input_data: ExitPlanModeInput) -> ToolResult:
        try:
            runtime = _runtime()
            context = get_active_tool_context()
            root = Path(context.get("workspace_root") or ".").resolve()
            content = input_data.plan or ""
            slug = runtime.state.plan.slug or plan_slug(runtime.session_id)
            path = PlanFileStore(root).save(slug, content)
            runtime.submit_plan(content, input_data.allowed_prompts, file_path=path)

            runtime_context = context.get("runtime_context")
            callback = context.get("approval_callback") or getattr(
                runtime_context, "approval_callback", None
            )
            if callback is None:
                return ToolResult.ok(
                    {
                        "plan": content,
                        "filePath": path,
                        "state": PlanState.PENDING_APPROVAL.value,
                        "awaitingApproval": True,
                    }
                )
            decision = callback(
                {
                    "kind": "plan",
                    "content": content,
                    "filePath": path,
                    "allowedPrompts": input_data.allowed_prompts,
                }
            )
            if hasattr(decision, "__await__"):
                decision = await decision
            if not decision:
                runtime.reject_plan()
                return ToolResult.ok(
                    {
                        "plan": content,
                        "filePath": path,
                        "state": PlanState.PLANNING.value,
                        "approved": False,
                    }
                )
            runtime.approve_plan()
            runtime.exit_plan()
            return ToolResult.ok(
                {
                    "plan": content,
                    "filePath": path,
                    "state": PlanState.IDLE.value,
                    "approved": True,
                    "permissionMode": runtime.state.permission_mode,
                }
            )
        except Exception as exc:
            return ToolResult.fail(exc)


def register_plan_mode_tools() -> None:
    """Compatibility hook; decorators already register canonical instances."""
