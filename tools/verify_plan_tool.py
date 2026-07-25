from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from state_core import SessionRuntime, TaskStatus

from .base import (
    Tool,
    ToolError,
    ToolResult,
    ToolValidationError,
    get_active_tool_context,
    register_tool,
)


@dataclass
class VerifyPlanInput:
    plan_id: str
    expected_steps: Optional[List[str]] = None
    strict: bool = False


@register_tool
class VerifyPlanExecutionTool(Tool[VerifyPlanInput, Dict[str, Any]]):
    """Verify plan execution completion."""

    name = "verify_plan_execution"
    description = "Verify that a plan was executed as expected (requires CLAUDE_CODE_VERIFY_PLAN=true)"
    version = "1.0"

    def __init__(self):
        import os
        self._enabled = os.environ.get("CLAUDE_CODE_VERIFY_PLAN", "").lower() == "true"

    def is_enabled(self) -> bool:
        return self._enabled

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {
                        "type": "string",
                        "description": "Plan ID to verify"
                    },
                    "expected_steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Expected steps in the plan"
                    },
                    "strict": {
                        "type": "boolean",
                        "default": False,
                        "description": "Whether to require exact step match"
                    }
                },
                "required": ["plan_id"]
            }
        }

    async def validate(self, input_data: VerifyPlanInput) -> Optional[ToolError]:
        if not self._enabled:
            return ToolValidationError(
                "verify_plan_execution tool is not enabled. "
                "Set CLAUDE_CODE_VERIFY_PLAN=true to enable."
            )
        if not input_data.plan_id:
            return ToolValidationError("plan_id is required")
        return None

    async def execute(self, input_data: VerifyPlanInput) -> ToolResult:
        runtime = get_active_tool_context().get("session_runtime")
        if not isinstance(runtime, SessionRuntime):
            return ToolResult.fail(
                ToolValidationError(
                    "session_runtime is required for plan verification"
                )
            )

        metadata = runtime.store.metadata.get(runtime.session_id, "api.plan")
        valid_plan_ids = {runtime.session_id}
        if runtime.state.plan.slug:
            valid_plan_ids.add(runtime.state.plan.slug)
        if metadata is not None and metadata.snapshot.get("id"):
            valid_plan_ids.add(str(metadata.snapshot["id"]))
        if input_data.plan_id not in valid_plan_ids:
            return ToolResult.fail(
                ToolValidationError(
                    f"Plan {input_data.plan_id} does not match the active session plan"
                )
            )

        tasks = runtime.list_tasks()
        expected_steps = (
            list(input_data.expected_steps)
            if input_data.expected_steps is not None
            else [task.subject for task in tasks]
        )
        tasks_by_subject = {task.subject: task for task in tasks}
        missing_steps = [
            step
            for step in expected_steps
            if step not in tasks_by_subject
            or tasks_by_subject[step].status is not TaskStatus.COMPLETED
        ]
        unexpected_steps = (
            [task.subject for task in tasks if task.subject not in expected_steps]
            if input_data.strict
            else []
        )
        completed_steps = len(expected_steps) - len(missing_steps)
        verified = bool(expected_steps) and not missing_steps and not unexpected_steps

        return ToolResult.ok(
            {
                "plan_id": input_data.plan_id,
                "verified": verified,
                "completed_steps": completed_steps,
                "total_steps": len(expected_steps),
                "missing_steps": missing_steps,
                "unexpected_steps": unexpected_steps,
            },
            message=(
                f"Plan {input_data.plan_id} verified: "
                f"{completed_steps}/{len(expected_steps)} steps completed"
            ),
        )

    def is_read_only(self) -> bool:
        return True
