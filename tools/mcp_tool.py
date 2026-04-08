"""
MCP (Model Context Protocol) 工具模块
提供与 MCP 服务器交互的功能
支持列出服务器、执行工具、获取提示等
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable, Awaitable
import json
import asyncio
from datetime import datetime

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool


# 全局 MCP 管理器实例
_mcp_manager: Optional['MCPManager'] = None


def get_mcp_manager() -> 'MCPManager':
    """获取全局 MCP 管理器实例"""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPManager()
    return _mcp_manager


def set_mcp_manager(manager: 'MCPManager') -> None:
    """设置全局 MCP 管理器"""
    global _mcp_manager
    _mcp_manager = manager


@dataclass
class MCPServer:
    """MCP 服务器信息"""
    name: str
    url: str
    description: Optional[str] = None
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPTool:
    """MCP 工具信息"""
    name: str
    description: str
    server: str
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPListServersInput:
    """列出 MCP 服务器的输入参数"""
    include_disabled: bool = False


@dataclass
class MCPExecuteToolInput:
    """执行 MCP 工具的输入参数"""
    server: str
    tool: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPListToolsInput:
    """列出 MCP 工具的输入参数"""
    server: Optional[str] = None


class MCPManager:
    """
    MCP 管理器 - 管理与 MCP 服务器的连接和交互

    功能:
    - 管理 MCP 服务器配置
    - 列出可用工具
    - 执行远程工具
    - 处理连接状态
    """

    def __init__(self):
        self._servers: Dict[str, MCPServer] = {}
        self._tools_cache: Dict[str, MCPTool] = {}
        self._connections: Dict[str, Any] = {}

    def add_server(self, server: MCPServer) -> None:
        """添加 MCP 服务器"""
        self._servers[server.name] = server

    def remove_server(self, name: str) -> bool:
        """移除 MCP 服务器"""
        if name in self._servers:
            del self._servers[name]
            return True
        return False

    def get_server(self, name: str) -> Optional[MCPServer]:
        """获取 MCP 服务器信息"""
        return self._servers.get(name)

    def list_servers(self, include_disabled: bool = False) -> List[MCPServer]:
        """列出所有 MCP 服务器"""
        servers = list(self._servers.values())
        if not include_disabled:
            servers = [s for s in servers if s.enabled]
        return servers

    async def list_tools(self, server_name: Optional[str] = None) -> List[MCPTool]:
        """
        列出可用的 MCP 工具

        Args:
            server_name: 指定服务器名称，为 None 则列出所有服务器的工具

        Returns:
            工具列表
        """
        tools = []

        servers = [self._servers.get(server_name)] if server_name else self._servers.values()

        for server in servers:
            if server is None or not server.enabled:
                continue

            # 这里应该实际连接服务器获取工具列表
            # 目前返回模拟数据
            tools.append(MCPTool(
                name="fetch_resource",
                description="从服务器获取资源",
                server=server.name,
                parameters={
                    "uri": {"type": "string", "description": "资源 URI"}
                }
            ))
            tools.append(MCPTool(
                name="query_data",
                description="查询数据",
                server=server.name,
                parameters={
                    "query": {"type": "string", "description": "查询语句"}
                }
            ))

        return tools

    async def execute_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        在 MCP 服务器上执行工具

        Args:
            server_name: 服务器名称
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            工具执行结果
        """
        server = self._servers.get(server_name)
        if not server:
            raise ToolExecutionError(f"MCP 服务器不存在: {server_name}")

        if not server.enabled:
            raise ToolExecutionError(f"MCP 服务器已禁用: {server_name}")

        # 这里应该实际连接服务器并执行工具
        # 目前返回模拟结果
        return {
            "server": server_name,
            "tool": tool_name,
            "arguments": arguments,
            "result": f"工具 {tool_name} 在服务器 {server_name} 上执行成功",
            "executed_at": datetime.now().isoformat()
        }


@register_tool
class MCPListServersTool(Tool[MCPListServersInput, List[Dict[str, Any]]]):
    """
    列出 MCP 服务器工具

    列出所有配置的 MCP 服务器。

    使用场景:
    - 查看可用的 MCP 服务器
    - 检查服务器连接状态
    """

    name = "mcp_list_servers"
    description = "列出所有配置的 MCP 服务器"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._manager: Optional[MCPManager] = None

    def _get_manager(self) -> MCPManager:
        """获取 MCP 管理器"""
        if self._manager is None:
            self._manager = get_mcp_manager()
        return self._manager

    async def execute(self, input_data: MCPListServersInput) -> ToolResult:
        """执行列出服务器操作"""
        try:
            manager = self._get_manager()
            servers = manager.list_servers(input_data.include_disabled)

            server_list = []
            for server in servers:
                server_list.append({
                    "name": server.name,
                    "url": server.url,
                    "description": server.description,
                    "enabled": server.enabled,
                    "metadata": server.metadata,
                })

            return ToolResult.ok(
                data=server_list,
                message=f"找到 {len(server_list)} 个 MCP 服务器",
                metadata={"count": len(server_list)}
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"列出 MCP 服务器失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "include_disabled": {
                    "type": "boolean",
                    "description": "是否包含已禁用的服务器",
                    "default": False
                }
            }
        }
        schema["returns"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "服务器名称"},
                    "url": {"type": "string", "description": "服务器 URL"},
                    "description": {"type": "string", "description": "服务器描述"},
                    "enabled": {"type": "boolean", "description": "是否启用"},
                    "metadata": {"type": "object", "description": "元数据"}
                }
            }
        }
        return schema


@register_tool
class MCPListToolsTool(Tool[MCPListToolsInput, List[Dict[str, Any]]]):
    """
    列出 MCP 工具工具

    列出指定 MCP 服务器或所有服务器上的可用工具。

    使用场景:
    - 查看可用的远程工具
    - 发现服务器功能
    """

    name = "mcp_list_tools"
    description = "列出 MCP 服务器上的可用工具"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._manager: Optional[MCPManager] = None

    def _get_manager(self) -> MCPManager:
        """获取 MCP 管理器"""
        if self._manager is None:
            self._manager = get_mcp_manager()
        return self._manager

    async def validate(self, input_data: MCPListToolsInput) -> Optional[ToolError]:
        """验证输入参数"""
        if input_data.server is not None:
            manager = self._get_manager()
            server = manager.get_server(input_data.server)
            if not server:
                return ToolValidationError(f"MCP 服务器不存在: {input_data.server}")
        return None

    async def execute(self, input_data: MCPListToolsInput) -> ToolResult:
        """执行列出工具操作"""
        try:
            manager = self._get_manager()
            tools = await manager.list_tools(input_data.server)

            tool_list = []
            for tool in tools:
                tool_list.append({
                    "name": tool.name,
                    "description": tool.description,
                    "server": tool.server,
                    "parameters": tool.parameters,
                })

            return ToolResult.ok(
                data=tool_list,
                message=f"找到 {len(tool_list)} 个 MCP 工具",
                metadata={
                    "count": len(tool_list),
                    "server": input_data.server
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"列出 MCP 工具失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "description": "指定服务器名称，为空则列出所有服务器的工具"
                }
            }
        }
        schema["returns"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "工具名称"},
                    "description": {"type": "string", "description": "工具描述"},
                    "server": {"type": "string", "description": "所属服务器"},
                    "parameters": {"type": "object", "description": "参数定义"}
                }
            }
        }
        return schema


@register_tool
class MCPExecuteToolTool(Tool[MCPExecuteToolInput, Dict[str, Any]]):
    """
    执行 MCP 工具工具

    在指定的 MCP 服务器上执行远程工具。

    使用场景:
    - 调用远程服务
    - 访问外部数据源
    - 执行分布式任务
    """

    name = "mcp_execute_tool"
    description = "在 MCP 服务器上执行远程工具"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._manager: Optional[MCPManager] = None

    def _get_manager(self) -> MCPManager:
        """获取 MCP 管理器"""
        if self._manager is None:
            self._manager = get_mcp_manager()
        return self._manager

    async def validate(self, input_data: MCPExecuteToolInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.server or not input_data.server.strip():
            return ToolValidationError("server（服务器名称）不能为空")

        if not input_data.tool or not input_data.tool.strip():
            return ToolValidationError("tool（工具名称）不能为空")

        manager = self._get_manager()
        server = manager.get_server(input_data.server)
        if not server:
            return ToolValidationError(f"MCP 服务器不存在: {input_data.server}")

        if not server.enabled:
            return ToolValidationError(f"MCP 服务器已禁用: {input_data.server}")

        return None

    async def execute(self, input_data: MCPExecuteToolInput) -> ToolResult:
        """执行 MCP 工具"""
        try:
            manager = self._get_manager()
            result = await manager.execute_tool(
                server_name=input_data.server.strip(),
                tool_name=input_data.tool.strip(),
                arguments=input_data.arguments
            )

            return ToolResult.ok(
                data=result,
                message=f"成功在服务器 {input_data.server} 上执行工具 {input_data.tool}",
                metadata={
                    "server": input_data.server,
                    "tool": input_data.tool,
                    "executed_at": result.get("executed_at")
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"执行 MCP 工具失败: {str(e)}")
            )

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "description": "MCP 服务器名称"
                },
                "tool": {
                    "type": "string",
                    "description": "要执行的工具名称"
                },
                "arguments": {
                    "type": "object",
                    "description": "工具参数",
                    "default": {}
                }
            },
            "required": ["server", "tool"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "服务器名称"},
                "tool": {"type": "string", "description": "工具名称"},
                "arguments": {"type": "object", "description": "执行参数"},
                "result": {"description": "执行结果"},
                "executed_at": {"type": "string", "description": "执行时间"}
            }
        }
        return schema
