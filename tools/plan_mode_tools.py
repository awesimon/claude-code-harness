"""
Plan Mode 工具模块
提供进入和退出计划模式的功能
与 plan_service 和 models 集成
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import logging

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool

logger = logging.getLogger(__name__)


@dataclass
class EnterPlanModeInput:
    """进入计划模式工具的输入参数"""
    pass  # 不需要参数


@dataclass
class ExitPlanModeInput:
    """退出计划模式工具的输入参数"""
    plan: Optional[str] = None  # 计划内容
    allowedPrompts: Optional[List[Dict[str, str]]] = field(default_factory=list)  # 允许的提示列表


@dataclass
class AllowedPrompt:
    """允许的提示"""
    tool: str
    prompt: str


@register_tool
class EnterPlanModeTool(Tool[EnterPlanModeInput, Dict[str, Any]]):
    """
    进入计划模式工具

    当LLM需要为复杂任务设计实现方案时调用此工具。
    进入计划模式后：
    1. 系统设置 conversation.state = 'planning'
    2. LLM应该探索代码库并设计实现方案
    3. 不能进行任何文件写入操作（只读工具）
    4. 完成后使用 ExitPlanMode 提交计划
    """

    name = "EnterPlanMode"
    description = (
        "Requests permission to enter plan mode for complex tasks "
        "requiring exploration and design"
    )
    version = "1.0"

    async def validate(self, input_data: EnterPlanModeInput) -> Optional[ToolError]:
        """验证输入"""
        # 不需要输入参数
        return None

    async def execute(self, input_data: EnterPlanModeInput) -> ToolResult:
        """
        执行进入计划模式

        注意：实际的会话ID从run方法的context参数传入
        这里返回一个标记，让QueryEngine知道要进入计划模式
        """
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
            },
            "returns": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Confirmation message"},
                    "state": {"type": "string", "description": "New conversation state (planning)"}
                }
            }
        }


@register_tool
class ExitPlanModeTool(Tool[ExitPlanModeInput, Dict[str, Any]]):
    """
    退出计划模式工具

    当LLM完成计划设计并准备提交时调用此工具。
    调用后会：
    1. 保存计划内容到数据库（使用 PlanService）
    2. 设置 conversation.state = 'normal'
    3. 返回计划内容和文件路径
    """

    name = "ExitPlanMode"
    description = "Exits plan mode and saves the plan to the database"
    version = "1.0"

    async def validate(self, input_data: ExitPlanModeInput) -> Optional[ToolError]:
        """验证输入"""
        if input_data.allowedPrompts:
            for prompt in input_data.allowedPrompts:
                if not isinstance(prompt, dict) or "tool" not in prompt or "prompt" not in prompt:
                    return ToolValidationError(
                        "Each allowedPrompt must have 'tool' and 'prompt' fields"
                    )
                if prompt["tool"] not in ["Bash", "Read", "Write", "Edit"]:
                    return ToolValidationError(
                        f"Invalid tool '{prompt['tool']}'. Must be one of: Bash, Read, Write, Edit"
                    )
        return None

    async def execute(self, input_data: ExitPlanModeInput) -> ToolResult:
        """
        执行退出计划模式

        注意：实际的会话ID从run方法的context参数传入
        这里返回一个标记，让QueryEngine知道要退出计划模式
        """
        return ToolResult.ok(
            data={
                "action": "exit_plan_mode",
                "plan": input_data.plan,
                "allowed_prompts": input_data.allowedPrompts or [],
                "message": "Request to exit plan mode",
            },
            message="Exit plan mode request processed",
        )

    def is_read_only(self) -> bool:
        return False  # 会写入数据库

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
                    "plan": {
                        "type": "string",
                        "description": "The plan content to save (optional)"
                    },
                    "allowedPrompts": {
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
                "description": "Optional plan content and permissions for implementing the plan"
            },
            "returns": {
                "type": "object",
                "properties": {
                    "plan": {"type": ["string", "null"], "description": "The saved plan content"},
                    "filePath": {"type": "string", "description": "Path to the saved plan file"},
                    "state": {"type": "string", "description": "New conversation state (normal)"}
                }
            }
        }
