"""
SleepTool - 延迟执行工具
用于在工具调用之间添加延迟
"""

import asyncio
from dataclasses import dataclass

from .base import Tool, ToolResult, ToolError


@dataclass
class SleepInput:
    """睡眠输入"""
    seconds: float  # 睡眠时间（秒）
    reason: str = ""  # 延迟原因


class SleepTool(Tool):
    """
    SleepTool - 延迟执行

    在工具调用之间添加延迟，用于：
    - 等待外部资源就绪
    - 避免API速率限制
    - 模拟用户思考时间

    注意：此工具会阻塞执行，请谨慎使用
    """

    name = "sleep"
    description = "暂停执行指定的时间（秒）"
    input_schema = {
        "type": "object",
        "properties": {
            "seconds": {
                "type": "number",
                "description": "睡眠的时间（秒），最大300秒",
                "minimum": 0.1,
                "maximum": 300
            },
            "reason": {
                "type": "string",
                "description": "延迟的原因（用于日志记录）"
            }
        },
        "required": ["seconds"]
    }

    MAX_SLEEP = 300  # 最大睡眠时间5分钟

    async def run(self, input_data: SleepInput) -> ToolResult:
        """
        执行睡眠

        Args:
            input_data: 睡眠输入参数

        Returns:
            ToolResult: 包含睡眠结果
        """
        try:
            # 限制最大睡眠时间
            seconds = min(input_data.seconds, self.MAX_SLEEP)

            # 执行睡眠
            await asyncio.sleep(seconds)

            return ToolResult(
                success=True,
                data={
                    "seconds": seconds,
                    "reason": input_data.reason
                },
                message=f"已暂停 {seconds} 秒" +
                        (f" ({input_data.reason})" if input_data.reason else "")
            )

        except Exception as e:
            return ToolResult(
                success=False,
                error=ToolError(
                    message=f"睡眠失败: {str(e)}",
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
