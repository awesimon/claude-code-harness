"""
MCP资源工具模块

提供访问MCP服务器资源的功能：
- 列出MCP资源
- 读取MCP资源
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from .base import Tool, ToolResult, ToolError, ToolValidationError, ToolExecutionError, register_tool
from .mcp_tool import get_mcp_manager


@dataclass
class ListMcpResourcesInput:
    """列出MCP资源输入"""
    server: Optional[str] = None  # 指定服务器，None表示所有服务器


@dataclass
class ReadMcpResourceInput:
    """读取MCP资源输入"""
    server: str  # 服务器名称
    uri: str     # 资源URI


@register_tool
class ListMcpResourcesTool(Tool[ListMcpResourcesInput, List[Dict[str, Any]]]):
    """
    列出MCP资源工具

    列出指定MCP服务器或所有服务器上可用的资源。
    资源是MCP服务器暴露的数据源，如文件、配置、API端点等。

    使用场景:
    - 发现MCP服务器提供的资源
    - 浏览可用数据
    - 准备读取资源
    """

    name = "mcp_list_resources"
    description = "列出MCP服务器上的可用资源"
    version = "1.0"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "指定服务器名称，为空则列出所有服务器的资源"
                    }
                }
            },
            "returns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "资源名称"},
                        "uri": {"type": "string", "description": "资源URI"},
                        "mime_type": {"type": "string", "description": "MIME类型"},
                        "description": {"type": "string", "description": "资源描述"},
                        "server": {"type": "string", "description": "所属服务器"}
                    }
                }
            }
        }

    async def execute(self, input_data: ListMcpResourcesInput) -> ToolResult:
        """执行列出资源操作"""
        try:
            manager = get_mcp_manager()

            # 获取服务器列表
            if input_data.server:
                server = manager.get_server(input_data.server)
                if not server:
                    return ToolResult.error(
                        ToolValidationError(f"MCP服务器不存在: {input_data.server}")
                    )
                servers = [server]
            else:
                servers = manager.list_servers()

            # 收集资源（模拟数据）
            resources = []
            for server in servers:
                if not server.enabled:
                    continue

                # 这里应该实际连接服务器获取资源列表
                # 目前返回模拟数据
                resources.append({
                    "name": "example_resource",
                    "uri": f"mcp://{server.name}/example",
                    "mime_type": "application/json",
                    "description": f"示例资源 - {server.name}",
                    "server": server.name
                })

            return ToolResult.ok(
                data=resources,
                message=f"找到 {len(resources)} 个MCP资源",
                metadata={
                    "count": len(resources),
                    "server": input_data.server
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"列出MCP资源失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        return True


@register_tool
class ReadMcpResourceTool(Tool[ReadMcpResourceInput, Dict[str, Any]]):
    """
    读取MCP资源工具

    读取指定MCP服务器上的资源内容。
    资源可以是文件、配置数据、API响应等。

    使用场景:
    - 获取配置文件内容
    - 读取数据源
    - 获取API数据
    """

    name = "mcp_read_resource"
    description = "读取MCP服务器上的资源内容"
    version = "1.0"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "MCP服务器名称"
                    },
                    "uri": {
                        "type": "string",
                        "description": "资源URI"
                    }
                },
                "required": ["server", "uri"]
            },
            "returns": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "资源URI"},
                    "content": {"type": "string", "description": "资源内容"},
                    "mime_type": {"type": "string", "description": "MIME类型"},
                    "server": {"type": "string", "description": "服务器名称"}
                }
            }
        }

    async def validate(self, input_data: ReadMcpResourceInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.server or not input_data.server.strip():
            return ToolValidationError("server（服务器名称）不能为空")

        if not input_data.uri or not input_data.uri.strip():
            return ToolValidationError("uri（资源URI）不能为空")

        manager = get_mcp_manager()
        server = manager.get_server(input_data.server)
        if not server:
            return ToolValidationError(f"MCP服务器不存在: {input_data.server}")

        if not server.enabled:
            return ToolValidationError(f"MCP服务器已禁用: {input_data.server}")

        return None

    async def execute(self, input_data: ReadMcpResourceInput) -> ToolResult:
        """执行读取资源操作"""
        try:
            # 这里应该实际连接服务器获取资源
            # 目前返回模拟数据
            content = f"模拟资源内容\n\nURI: {input_data.uri}\nServer: {input_data.server}\n\n（实际使用需要连接MCP服务器）"

            return ToolResult.ok(
                data={
                    "uri": input_data.uri,
                    "content": content,
                    "mime_type": "text/plain",
                    "server": input_data.server
                },
                message=f"已读取资源: {input_data.uri}",
                metadata={
                    "uri": input_data.uri,
                    "server": input_data.server,
                    "note": "这是模拟结果，连接MCP服务器后可获取真实资源内容"
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"读取MCP资源失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        return True
