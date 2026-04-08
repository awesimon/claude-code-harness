"""
ToolSearchTool - 工具搜索
允许LLM搜索和发现可用工具
"""

from dataclasses import dataclass
from typing import Optional, List

from .base import Tool, ToolResult, ToolError, ToolRegistry


@dataclass
class ToolSearchInput:
    """工具搜索输入"""
    query: str  # 搜索关键词
    category: Optional[str] = None  # 工具类别过滤


@dataclass
class ToolInfo:
    """工具信息"""
    name: str
    description: str
    category: str
    parameters: dict


class ToolSearchTool(Tool):
    """
    ToolSearchTool - 搜索可用工具

    允许搜索和发现系统中的可用工具，帮助LLM了解可以使用哪些工具
    以及如何使用它们。

    使用场景:
    - LLM需要查找特定功能的工具
    - 了解工具的参数和使用方法
    - 发现相关工具
    """

    name = "tool_search"
    description = "搜索可用工具，查找特定功能的工具及其使用方法"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，可以是工具名称、功能描述等"
            },
            "category": {
                "type": "string",
                "description": "工具类别过滤（可选）",
                "enum": ["file", "search", "agent", "web", "system", "communication"]
            }
        },
        "required": ["query"]
    }

    # 工具分类映射
    TOOL_CATEGORIES = {
        "file": ["read_file", "write_file", "edit_file", "glob", "grep"],
        "search": ["web_search", "web_fetch", "tool_search"],
        "agent": ["agent", "agent_list", "agent_destroy", "send_message", "message_history"],
        "web": ["web_search", "web_fetch"],
        "system": ["bash", "brief", "todo_write", "config_get", "config_set"],
        "communication": ["ask_user", "brief", "send_message"],
    }

    async def run(self, input_data: ToolSearchInput) -> ToolResult:
        """
        搜索工具

        Args:
            input_data: 搜索输入参数

        Returns:
            ToolResult: 包含匹配的工具列表
        """
        try:
            query = input_data.query.lower()
            all_tools = ToolRegistry.get_all_schemas()

            # 过滤工具
            matching_tools = []
            for tool_schema in all_tools:
                tool_name = tool_schema.get("name", "")
                tool_desc = tool_schema.get("description", "")

                # 检查类别过滤
                if input_data.category:
                    category_tools = self.TOOL_CATEGORIES.get(input_data.category, [])
                    if tool_name not in category_tools:
                        continue

                # 检查是否匹配查询
                if (query in tool_name.lower() or
                    query in tool_desc.lower()):
                    matching_tools.append({
                        "name": tool_name,
                        "description": tool_desc,
                        "schema": tool_schema.get("inputSchema", {}),
                    })

            # 按相关性排序（名称匹配优先）
            matching_tools.sort(
                key=lambda t: (0 if query in t["name"].lower() else 1, t["name"])
            )

            # 格式化输出
            if matching_tools:
                tool_list = "\n".join([
                    f"- **{t['name']}**: {t['description'][:100]}..."
                    for t in matching_tools[:10]  # 最多显示10个
                ])
                message = f"找到 {len(matching_tools)} 个匹配的工具:\n\n{tool_list}"
            else:
                message = f"未找到匹配 '{input_data.query}' 的工具"

            return ToolResult(
                success=True,
                data={
                    "query": input_data.query,
                    "category": input_data.category,
                    "tools": matching_tools,
                    "count": len(matching_tools)
                },
                message=message
            )

        except Exception as e:
            return ToolResult(
                success=False,
                error=ToolError(
                    message=f"搜索工具失败: {str(e)}",
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
