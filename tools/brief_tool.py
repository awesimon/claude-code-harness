"""
BriefTool - 向用户发送消息
主要用于向用户展示信息、总结结果或提供反馈
"""

from dataclasses import dataclass
from typing import Optional

from .base import Tool, ToolResult, ToolError


@dataclass
class BriefInput:
    """Brief工具输入"""
    message: str
    title: Optional[str] = None
    level: str = "info"  # info, success, warning, error


class BriefTool(Tool):
    """
    BriefTool - 向用户发送消息

    用于向用户展示信息、总结结果、提供反馈或报告进度。
    这是主要的用户输出通道，与AskUserQuestionTool（用于提问）不同。

    使用场景:
    - 任务完成后的总结报告
    - 向用户展示重要信息
    - 提供操作反馈
    - 显示进度更新
    """

    name = "brief"
    description = "向用户发送消息，用于展示信息、总结结果或提供反馈"
    input_schema = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "要发送给用户的消息内容"
            },
            "title": {
                "type": "string",
                "description": "消息标题（可选）",
                "default": None
            },
            "level": {
                "type": "string",
                "description": "消息级别: info, success, warning, error",
                "enum": ["info", "success", "warning", "error"],
                "default": "info"
            }
        },
        "required": ["message"]
    }

    async def run(self, input_data: BriefInput) -> ToolResult:
        """
        执行Brief工具

        Args:
            input_data: Brief输入参数

        Returns:
            ToolResult: 包含发送的消息
        """
        try:
            # 构建消息
            content = input_data.message
            if input_data.title:
                content = f"**{input_data.title}**\n\n{content}"

            # 添加级别标记
            level_emoji = {
                "info": "ℹ️",
                "success": "✅",
                "warning": "⚠️",
                "error": "❌"
            }.get(input_data.level, "ℹ️")

            formatted_message = f"{level_emoji} {content}"

            return ToolResult(
                success=True,
                data={
                    "message": input_data.message,
                    "title": input_data.title,
                    "level": input_data.level,
                    "formatted": formatted_message
                },
                message=formatted_message
            )

        except Exception as e:
            return ToolResult(
                success=False,
                error=ToolError(
                    message=f"发送消息失败: {str(e)}",
                    tool_name=self.name
                )
            )

    def get_schema(self) -> dict:
        """获取工具schema"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
