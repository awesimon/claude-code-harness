from typing import Any, Dict, Optional, List
from dataclasses import dataclass, field

from .base import Tool, ToolResult, ToolError, register_tool


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
            return ToolError(
                "verify_plan_execution tool is not enabled. "
                "Set CLAUDE_CODE_VERIFY_PLAN=true to enable.",
                tool_name=self.name
            )
        if not input_data.plan_id:
            return ToolError("plan_id is required", tool_name=self.name)
        return None

    async def execute(self, input_data: VerifyPlanInput) -> ToolResult:
        # Mock implementation
        verified = True
        completed_steps = len(input_data.expected_steps) if input_data.expected_steps else 3
        total_steps = completed_steps

        return ToolResult(
            success=True,
            data={
                "plan_id": input_data.plan_id,
                "verified": verified,
                "completed_steps": completed_steps,
                "total_steps": total_steps,
                "missing_steps": []
            },
            message=f"Plan {input_data.plan_id} verified: {completed_steps}/{total_steps} steps completed"
        )

    def is_read_only(self) -> bool:
        return True
