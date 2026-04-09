"""
Agent 工具模块
实现 AgentTool，对齐 Claude Code 的 AgentTool.tsx
"""
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from tools.base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool
from agents.types import AgentExecutionConfig
from agents.engine import get_agent_manager
from agents.built_in import get_agent_by_type

logger = logging.getLogger(__name__)


@dataclass
class AgentToolInput:
    """Agent 工具输入"""
    prompt: str
    subagent_type: Optional[str] = None  # Agent 类型，如 "Explore", "Plan"


@register_tool
class AgentTool(Tool[AgentToolInput, Dict[str, Any]]):
    """
    Agent 工具

    用于创建和执行子Agent，对齐 Claude Code 的 AgentTool
    """

    name = "Agent"
    description = (
        "Launch a specialized agent to handle complex, multi-step tasks autonomously. "
        "Each agent type has specific capabilities and tools available to it."
    )
    prompt = """Use the Agent tool to delegate complex tasks to specialized agents.

Available agent types:
- Explore: Fast agent for searching and exploring codebases (read-only)
- Plan: Software architect agent for designing implementation plans (read-only)
- general-purpose: General-purpose agent for research and multi-step tasks
- Code: Specialized agent for code implementation
- Test: Specialized agent for writing tests

When to use:
- When you need to search for code across multiple files
- When you need to design an implementation plan
- When you need to perform multi-step research
- When you want to parallelize independent tasks

Example usage:
{
  "prompt": "Find all API endpoints in the codebase",
  "subagent_type": "Explore"
}"""

    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The task description for the agent"
            },
            "subagent_type": {
                "type": "string",
                "description": "The type of agent to launch (Explore, Plan, general-purpose, Code, Test)",
                "enum": ["Explore", "Plan", "general-purpose", "Code", "Test"]
            }
        },
        "required": ["prompt"]
    }

    output_schema = {
        "type": "object",
        "properties": {
            "agent_id": {"type": "string"},
            "agent_type": {"type": "string"},
            "content": {"type": "array"},
            "total_tool_use_count": {"type": "integer"},
            "total_duration_ms": {"type": "integer"},
            "total_tokens": {"type": "integer"},
        },
        "required": ["agent_id", "content"]
    }

    is_read_only = False
    should_defer = True

    def __init__(self):
        self.manager = get_agent_manager()

    async def validate_input(self, input_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """验证输入"""
        if "prompt" not in input_data or not input_data["prompt"]:
            return False, "prompt is required"

        # 验证 subagent_type
        if "subagent_type" in input_data:
            agent_type = input_data["subagent_type"]
            agent_def = get_agent_by_type(agent_type)
            if not agent_def:
                return False, f"Unknown agent type: {agent_type}"

        return True, None

    async def check_permissions(
        self,
        input_data: Dict[str, Any],
        context: Dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """检查权限"""
        # 检查是否在 Fork 子Agent中（防止递归）
        from agents.fork import is_in_fork_child

        session_id = context.get("session_id")
        # TODO: 获取会话消息并检查

        return True, None

    async def execute(self, input_data: AgentToolInput) -> ToolResult:
        """
        执行 Agent 工具

        这是 execute 方法的具体实现
        """
        prompt = input_data.prompt if hasattr(input_data, 'prompt') else input_data.get("prompt", "")
        agent_type = input_data.subagent_type if hasattr(input_data, 'subagent_type') else input_data.get("subagent_type", "general-purpose")

        try:
            # 获取 Agent 定义
            agent_def = get_agent_by_type(agent_type)
            if not agent_def:
                return ToolResult.error(
                    ToolValidationError(f"Unknown agent type: {agent_type}")
                )

            # 创建并执行 Agent
            agent_id = await self.manager.spawn_agent(
                agent_type=agent_type,
                prompt=prompt,
                parent_session_id=None,
                config=AgentExecutionConfig(),
                is_async=False,
            )

            # 获取执行器并执行
            executor = self.manager._agents.get(agent_id)
            if not executor:
                return ToolResult.error(
                    ToolExecutionError(f"Failed to create agent: {agent_id}")
                )

            result = await executor.execute()

            return ToolResult.success({
                "agent_id": result.agent_id,
                "agent_type": result.agent_type,
                "content": result.content,
                "total_tool_use_count": result.total_tool_use_count,
                "total_duration_ms": result.total_duration_ms,
                "total_tokens": result.total_tokens,
                "usage": result.usage,
            })

        except Exception as e:
            logger.error(f"Agent tool execution failed: {e}")
            return ToolResult.error(e)

    def get_tool_result_for_llm(
        self,
        output: Dict[str, Any],
        tool_use_id: str
    ) -> Dict[str, Any]:
        """将工具结果转换为 LLM 可用的格式"""
        content = output.get("content", [])
        text_content = "\n".join([c.get("text", "") for c in content if c.get("type") == "text"])

        return {
            "type": "tool_result",
            "content": text_content,
            "tool_use_id": tool_use_id,
        }
