"""
Plan Mode 工具实现
EnterPlanModeTool 和 ExitPlanModeTool
"""
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from tools.base import Tool, ToolResult
from plan.manager import get_plan_mode_manager, PlanModeManager
from plan.types import AlreadyInPlanModeError

logger = logging.getLogger(__name__)


@dataclass
class EnterPlanModeInput:
    """EnterPlanModeTool 输入"""
    pass  # 不需要参数


@dataclass
class EnterPlanModeOutput:
    """EnterPlanModeTool 输出"""
    message: str
    state: str
    plan_file_path: Optional[str] = None


class EnterPlanModeTool(Tool):
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
    prompt = """Use this tool when you need to plan the implementation strategy for a task.

Plan mode is appropriate when:
- The task requires exploring the codebase first
- You need to design an architectural approach
- The implementation has multiple options with trade-offs
- You want to get user approval before making changes

When in plan mode:
- You should explore and analyze the codebase
- Design a concrete implementation plan
- DO NOT write or edit any files
- Use ExitPlanMode when ready to present your plan"""

    input_schema = {
        "type": "object",
        "properties": {},
        "description": "No parameters needed to enter plan mode"
    }

    output_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "state": {"type": "string"},
            "plan_file_path": {"type": "string"}
        },
        "required": ["message", "state"]
    }

    is_read_only = True
    should_defer = True

    def __init__(self):
        self.manager = get_plan_mode_manager()

    async def validate_input(self, input_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """验证输入"""
        # 不需要输入参数
        return True, None

    async def check_permissions(
        self,
        input_data: Dict[str, Any],
        context: Dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """
        检查权限

        返回:
            (是否允许, 如果不允许则返回原因)
        """
        session_id = context.get("session_id")
        if not session_id:
            return False, "No session ID provided"

        # 检查是否已经在计划模式中
        if self.manager.is_in_plan_mode(session_id):
            return False, "Already in plan mode"

        return True, None

    async def run(self, input_data: Dict[str, Any], context: Dict[str, Any]) -> ToolResult:
        """
        执行进入计划模式

        Args:
            input_data: 空字典
            context: 包含 session_id 等上下文

        Returns:
            ToolResult 包含确认消息
        """
        session_id = context.get("session_id")
        if not session_id:
            return ToolResult.error(Exception("No session ID provided"))

        try:
            result = await self.manager.enter_plan_mode(
                session_id=session_id,
                previous_mode=context.get("current_mode", "default")
            )

            return ToolResult.success({
                "message": result["message"],
                "state": result["state"],
                "plan_file_path": result.get("plan_file_path"),
            })

        except AlreadyInPlanModeError as e:
            return ToolResult.error(e)
        except Exception as e:
            logger.error(f"Error entering plan mode: {e}")
            return ToolResult.error(e)

    def get_tool_result_for_llm(
        self,
        output: Dict[str, Any],
        tool_use_id: str
    ) -> Dict[str, Any]:
        """
        将工具结果转换为LLM可用的格式

        这是 mapToolResultToToolResultBlockParam 的Python版本
        """
        message = output.get("message", "Entered plan mode")

        instructions = f"""{message}

In plan mode, you should:
1. Thoroughly explore the codebase to understand existing patterns
2. Identify similar features and architectural approaches
3. Consider multiple approaches and their trade-offs
4. Use AskUserQuestion if you need to clarify the approach
5. Design a concrete implementation strategy
6. When ready, use ExitPlanMode to present your plan for approval

Remember: DO NOT write or edit any files yet. This is a read-only exploration and planning phase."""

        return {
            "type": "tool_result",
            "content": instructions,
            "tool_use_id": tool_use_id,
        }


@dataclass
class ExitPlanModeInput:
    """ExitPlanModeTool 输入"""
    allowed_prompts: Optional[List[Dict[str, str]]] = None


@dataclass
class ExitPlanModeOutput:
    """ExitPlanModeTool 输出"""
    plan: Optional[str]
    is_agent: bool
    file_path: Optional[str]
    has_task_tool: bool = False
    plan_was_edited: bool = False
    awaiting_leader_approval: bool = False
    request_id: Optional[str] = None


class ExitPlanModeTool(Tool):
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
    prompt = """Use this tool when you have finished designing your implementation plan and are ready to present it for approval.

Before calling this tool:
1. Make sure you have thoroughly explored the codebase
2. Have a concrete implementation plan
3. Considered trade-offs and alternatives
4. Identified critical files and dependencies

The plan will be saved to a file and presented to the user for approval.
Once approved, you can start implementing the plan."""

    input_schema = {
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
                            "description": "Semantic description of the action, e.g. 'run tests', 'install dependencies'"
                        }
                    },
                    "required": ["tool", "prompt"]
                }
            }
        },
        "description": "Optional permissions for implementing the plan"
    }

    output_schema = {
        "type": "object",
        "properties": {
            "plan": {"type": ["string", "null"]},
            "is_agent": {"type": "boolean"},
            "file_path": {"type": "string"},
            "has_task_tool": {"type": "boolean"},
            "plan_was_edited": {"type": "boolean"},
            "awaiting_leader_approval": {"type": "boolean"},
            "request_id": {"type": ["string", "null"]}
        },
        "required": ["plan", "is_agent", "file_path"]
    }

    is_read_only = False  # 会写入文件
    should_defer = True

    def __init__(self):
        self.manager = get_plan_mode_manager()

    async def validate_input(self, input_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """验证输入"""
        # allowed_prompts 是可选的
        if "allowed_prompts" in input_data:
            prompts = input_data["allowed_prompts"]
            if not isinstance(prompts, list):
                return False, "allowed_prompts must be a list"
            for p in prompts:
                if not isinstance(p, dict) or "tool" not in p or "prompt" not in p:
                    return False, "Each allowed_prompt must have 'tool' and 'prompt' fields"
        return True, None

    async def check_permissions(
        self,
        input_data: Dict[str, Any],
        context: Dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """检查权限"""
        session_id = context.get("session_id")
        if not session_id:
            return False, "No session ID provided"

        # 检查是否在计划模式中
        if not self.manager.is_in_plan_mode(session_id):
            return False, (
                "You are not in plan mode. This tool is only for exiting plan mode "
                "after writing a plan. If your plan was already approved, continue with implementation."
            )

        return True, None

    async def run(self, input_data: Dict[str, Any], context: Dict[str, Any]) -> ToolResult:
        """
        执行退出计划模式

        Args:
            input_data: 可能包含 allowed_prompts
            context: 包含 session_id 等上下文

        Returns:
            ToolResult 包含计划内容和审批状态
        """
        session_id = context.get("session_id")
        if not session_id:
            return ToolResult.error(Exception("No session ID provided"))

        try:
            # 1. 提交计划等待审批
            allowed_prompts = input_data.get("allowed_prompts", [])
            submit_result = await self.manager.submit_plan_for_approval(
                session_id=session_id,
                allowed_prompts=allowed_prompts
            )

            # 2. 获取计划内容
            plan_context = self.manager.get_plan_context(session_id)
            plan_content = plan_context.plan_content if plan_context else None
            file_path = plan_context.plan_file_path if plan_context else None

            # 注意：实际的审批流程由前端/用户处理
            # 这里返回等待审批的状态

            return ToolResult.success({
                "plan": plan_content,
                "is_agent": False,  # 可以扩展支持Agent
                "file_path": file_path,
                "has_task_tool": False,  # 可以检测Agent工具
                "plan_was_edited": plan_context.is_edited if plan_context else False,
                "awaiting_leader_approval": False,  # 简化版本，直接等待用户审批
                "request_id": None,
                "_pending_approval": True,  # 标记等待审批
                "_submit_result": submit_result,
            })

        except Exception as e:
            logger.error(f"Error exiting plan mode: {e}")
            return ToolResult.error(e)

    def get_tool_result_for_llm(
        self,
        output: Dict[str, Any],
        tool_use_id: str
    ) -> Dict[str, Any]:
        """将工具结果转换为LLM可用的格式"""
        plan = output.get("plan")
        file_path = output.get("file_path")
        is_edited = output.get("plan_was_edited", False)

        if not plan or plan.strip() == "":
            return {
                "type": "tool_result",
                "content": "User has approved exiting plan mode. You can now proceed.",
                "tool_use_id": tool_use_id,
            }

        plan_label = "Approved Plan (edited by user)" if is_edited else "Approved Plan"

        content = f"""User has approved your plan. You can now start coding. Start with updating your todo list if applicable

Your plan has been saved to: {file_path}
You can refer back to it if needed during implementation.

## {plan_label}:
{plan}"""

        return {
            "type": "tool_result",
            "content": content,
            "tool_use_id": tool_use_id,
        }


# 工具注册函数
def register_plan_mode_tools():
    """注册Plan Mode相关工具到ToolRegistry"""
    from tools.base import ToolRegistry

    ToolRegistry.register(EnterPlanModeTool())
    ToolRegistry.register(ExitPlanModeTool())

    logger.info("Plan Mode tools registered")
