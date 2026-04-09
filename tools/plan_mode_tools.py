"""
Plan Mode 工具模块
提供进入和退出计划模式的功能 - 新版本
与 plan/ 模块集成
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import logging

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool
from plan import (
    get_plan_mode_manager,
    PlanModeState,
    AlreadyInPlanModeError,
    NotInPlanModeError,
    NoPlanContentError,
    PlanApprovalRequiredError,
)

logger = logging.getLogger(__name__)


@dataclass
class EnterPlanModeInput:
    """进入计划模式工具的输入参数"""
    pass  # 不需要参数


@dataclass
class ExitPlanModeInput:
    """退出计划模式工具的输入参数"""
    allowed_prompts: Optional[List[Dict[str, str]]] = field(default_factory=list)


@register_tool
class EnterPlanModeTool(Tool[EnterPlanModeInput, Dict[str, Any]]):
    """
    进入计划模式工具

    当LLM需要为复杂任务设计实现方案时调用此工具。
    进入计划模式后：
    1. 系统进入只读探索状态
    2. LLM应该探索代码库并设计实现方案
    3. 不能进行任何文件写入操作
    4. 完成后使用 ExitPlanMode 提交计划
    """

    name = "EnterPlanMode"
    description = (
        "Requests permission to enter plan mode for complex tasks "
        "requiring exploration and design"
    )
    version = "2.0"

    def __init__(self):
        super().__init__()
        self._manager = get_plan_mode_manager()

    async def validate(self, input_data: EnterPlanModeInput) -> Optional[ToolError]:
        """验证输入"""
        # 不需要输入参数
        return None

    async def execute(self, input_data: EnterPlanModeInput) -> ToolResult:
        """
        执行进入计划模式

        注意：实际的会话ID从run方法的context参数传入
        这里只返回工具定义，真正的执行在QueryEngine中处理
        """
        # 实际执行在QueryEngine._execute_tools中处理
        # 这里返回一个标记，让QueryEngine知道要进入计划模式
        return ToolResult.ok(
            data={
                "action": "enter_plan_mode",
                "message": "Request to enter plan mode",
            },
            message="Enter plan mode request processed",
        )

    def is_read_only(self) -> bool:
        return True

    def is_destructive(self) -> bool:
        return False

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的JSON Schema描述"""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {},
                "description": "No parameters needed to enter plan mode"
            }
        }


@register_tool
class ExitPlanModeTool(Tool[ExitPlanModeInput, Dict[str, Any]]):
    """
    退出计划模式工具

    当LLM完成计划设计并准备提交审批时调用此工具。
    调用后会：
    1. 保存计划到文件
    2. 提交计划等待用户审批
    3. 用户批准后退出计划模式
    4. 恢复之前的权限模式
    """

    name = "ExitPlanMode"
    description = "Prompts the user to exit plan mode and start coding"
    version = "2.0"

    def __init__(self):
        super().__init__()
        self._manager = get_plan_mode_manager()

    async def validate(self, input_data: ExitPlanModeInput) -> Optional[ToolError]:
        """验证输入"""
        if input_data.allowed_prompts:
            for prompt in input_data.allowed_prompts:
                if not isinstance(prompt, dict) or "tool" not in prompt or "prompt" not in prompt:
                    return ToolValidationError(
                        "Each allowed_prompt must have 'tool' and 'prompt' fields"
                    )
        return None

    async def execute(self, input_data: ExitPlanModeInput) -> ToolResult:
        """
        执行退出计划模式

        注意：实际的会话ID从run方法的context参数传入
        这里只返回工具定义，真正的执行在QueryEngine中处理
        """
        return ToolResult.ok(
            data={
                "action": "exit_plan_mode",
                "allowed_prompts": input_data.allowed_prompts or [],
                "message": "Request to exit plan mode",
            },
            message="Exit plan mode request processed",
        )

    def is_read_only(self) -> bool:
        return False  # 会写入文件

    def is_destructive(self) -> bool:
        return False

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的JSON Schema描述"""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "allowed_prompts": {
                        "type": "array",
                        "description": "Prompt-based permissions needed to implement the plan",
                        "items": {
                            "type": "object",
                            "properties": {
                                "tool": {
                                    "type": "string",
                                    "enum": ["Bash", "Read", "Write", "Edit"],
                                    "description": "The tool this prompt applies to"
                                },
                                "prompt": {
                                    "type": "string",
                                    "description": "Semantic description of the action"
                                }
                            },
                            "required": ["tool", "prompt"]
                        }
                    }
                },
                "description": "Optional permissions for implementing the plan"
            }
        }
