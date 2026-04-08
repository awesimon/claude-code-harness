"""
验证计划执行工具模块

用于验证计划是否被正确执行，检查任务完成情况。
类似于TypeScript版本的 VerifyPlanExecutionTool。
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List

from .base import Tool, ToolResult, ToolError, ToolValidationError, ToolExecutionError, register_tool


@dataclass
class VerifyPlanInput:
    """验证计划输入"""
    plan_id: str  # 计划ID
    expected_steps: Optional[List[str]] = None  # 预期步骤
    strict: bool = False  # 是否严格验证


@register_tool
class VerifyPlanExecutionTool(Tool[VerifyPlanInput, Dict[str, Any]]):
    """
    验证计划执行工具

    验证计划是否按照预期执行完成。
    检查任务状态、步骤完成情况等。

    使用场景:
    - 验证计划执行结果
    - 检查任务完成状态
    - 识别未完成的步骤

    注意: 此工具需要环境变量 CLAUDE_CODE_VERIFY_PLAN=true 启用
    """

    name = "verify_plan_execution"
    description = "验证计划是否按预期执行完成"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._enabled = False  # 默认禁用，需要环境变量启用

    def is_enabled(self) -> bool:
        """检查工具是否启用"""
        import os
        return os.environ.get("CLAUDE_CODE_VERIFY_PLAN", "").lower() == "true"

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
                        "description": "要验证的计划ID"
                    },
                    "expected_steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "预期执行的步骤列表（可选）"
                    },
                    "strict": {
                        "type": "boolean",
                        "description": "是否严格验证（所有步骤必须完全匹配）",
                        "default": False
                    }
                },
                "required": ["plan_id"]
            },
            "returns": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "计划ID"},
                    "verified": {"type": "boolean", "description": "是否验证通过"},
                    "completed_steps": {"type": "number", "description": "已完成步骤数"},
                    "total_steps": {"type": "number", "description": "总步骤数"},
                    "missing_steps": {"type": "array", "items": {"type": "string"}, "description": "缺失的步骤"},
                    "message": {"type": "string", "description": "验证结果消息"}
                }
            }
        }

    async def validate(self, input_data: VerifyPlanInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not self.is_enabled():
            return ToolValidationError(
                "验证计划执行工具未启用。设置环境变量 CLAUDE_CODE_VERIFY_PLAN=true 来启用。"
            )

        if not input_data.plan_id or not input_data.plan_id.strip():
            return ToolValidationError("plan_id（计划ID）不能为空")

        return None

    async def execute(self, input_data: VerifyPlanInput) -> ToolResult:
        """执行验证操作"""
        try:
            # 这里应该查询计划执行状态
            # 目前返回模拟结果

            # 模拟验证结果
            verified = True
            completed_steps = 3
            total_steps = 3
            missing_steps = []

            if input_data.expected_steps:
                total_steps = len(input_data.expected_steps)
                # 模拟部分步骤完成
                completed_steps = total_steps - 0
                missing_steps = []

            message = f"计划 {input_data.plan_id} 验证"
            if verified:
                message += f"通过：已完成 {completed_steps}/{total_steps} 个步骤"
            else:
                message += f"失败：缺少 {len(missing_steps)} 个步骤"

            return ToolResult.ok(
                data={
                    "plan_id": input_data.plan_id,
                    "verified": verified,
                    "completed_steps": completed_steps,
                    "total_steps": total_steps,
                    "missing_steps": missing_steps,
                    "message": message
                },
                message=message,
                metadata={
                    "plan_id": input_data.plan_id,
                    "strict": input_data.strict,
                    "note": "这是一个框架实现，实际使用需要计划管理系统的支持"
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"验证计划执行失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        return True
