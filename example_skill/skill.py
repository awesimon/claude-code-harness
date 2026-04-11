"""
示例 Skill
展示如何创建自定义工具
"""

from datetime import datetime
from pydantic import BaseModel, Field
from tools.base import Tool, ToolResult, register_tool


class HelloWorldInput(BaseModel):
    """Hello World 输入"""
    name: str = Field("World", description="要问候的名称")


@register_tool
class HelloWorldTool(Tool[HelloWorldInput, ToolResult]):
    """Hello World 工具"""

    name = "hello_world"
    description = "Say hello to someone"
    input_model = HelloWorldInput

    async def execute(self, input_data: HelloWorldInput) -> ToolResult:
        return ToolResult(
            success=True,
            data={"message": f"Hello, {input_data.name}!"},
            message=f"Greeted {input_data.name}"
        )


class GetTimeInput(BaseModel):
    """获取时间输入"""
    pass


@register_tool
class GetTimeTool(Tool[GetTimeInput, ToolResult]):
    """获取当前时间"""

    name = "get_time"
    description = "Get current date and time"
    input_model = GetTimeInput

    async def execute(self, input_data: GetTimeInput) -> ToolResult:
        now = datetime.now()
        return ToolResult(
            success=True,
            data={
                "datetime": now.isoformat(),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S")
            },
            message=f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        )
