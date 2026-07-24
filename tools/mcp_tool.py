"""Public MCP tools backed by the current session harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from harness.mcp import (
    MCPConnectionManager,
    MCPServerConfig,
    MCPServerRecord,
    MCPToolDefinition,
)

from .base import (
    Tool,
    ToolExecutionError,
    ToolResult,
    ToolValidationError,
    get_active_tool_context,
    register_tool,
)


MCPManager = MCPConnectionManager
MCPServer = MCPServerConfig
MCPTool = MCPToolDefinition


def get_mcp_manager() -> MCPConnectionManager:
    harness = get_active_tool_context().get("session_harness")
    if harness is None:
        raise ToolExecutionError("MCP tools require a session harness")
    return harness.mcp


def set_mcp_manager(_manager: MCPConnectionManager) -> None:
    raise RuntimeError("global MCP managers are not supported; configure the session harness")


@dataclass
class MCPListServersInput:
    include_disabled: bool = False


@dataclass
class MCPExecuteToolInput:
    server: str
    tool: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPListToolsInput:
    server: Optional[str] = None


def _server_wire(record: MCPServerRecord) -> dict[str, Any]:
    return {
        "name": record.name,
        "url": None,
        "description": None,
        "enabled": record.status.value not in {"failed", "disconnected"},
        "metadata": {
            "status": record.status.value,
            "transport": record.transport.value,
            "agent_id": record.agent_id,
            "error": record.error,
        },
    }


@register_tool
class MCPListServersTool(Tool[MCPListServersInput, List[Dict[str, Any]]]):
    name = "mcp_list_servers"
    description = "列出所有配置的 MCP 服务器"
    version = "1.0"

    async def execute(self, input_data: MCPListServersInput) -> ToolResult:
        try:
            records = get_mcp_manager().list_servers()
            if not input_data.include_disabled:
                records = [record for record in records if _server_wire(record)["enabled"]]
            data = [_server_wire(record) for record in records]
            return ToolResult.ok(data=data, message=f"找到 {len(data)} 个 MCP 服务器", metadata={"count": len(data)})
        except Exception as exc:
            return ToolResult.fail(ToolExecutionError(f"列出 MCP 服务器失败: {exc}"))

    def is_read_only(self) -> bool:
        return True

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "include_disabled": {
                        "type": "boolean",
                        "description": "是否包含已断开或失败的服务器",
                        "default": False,
                    }
                },
            },
        }


@register_tool
class MCPListToolsTool(Tool[MCPListToolsInput, List[Dict[str, Any]]]):
    name = "mcp_list_tools"
    description = "列出 MCP 服务器上的可用工具"
    version = "1.0"

    async def execute(self, input_data: MCPListToolsInput) -> ToolResult:
        try:
            tools = await get_mcp_manager().list_tools(input_data.server)
            data = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "server": tool.server,
                    "parameters": tool.input_schema,
                }
                for tool in tools
            ]
            return ToolResult.ok(
                data=data,
                message=f"找到 {len(data)} 个 MCP 工具",
                metadata={"count": len(data), "server": input_data.server},
            )
        except Exception as exc:
            return ToolResult.fail(ToolExecutionError(f"列出 MCP 工具失败: {exc}"))

    def is_read_only(self) -> bool:
        return True

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
                        "description": "指定服务器名称，为空则列出所有服务器的工具",
                    }
                },
            },
        }


@register_tool
class MCPExecuteToolTool(Tool[MCPExecuteToolInput, Dict[str, Any]]):
    name = "mcp_execute_tool"
    description = "在 MCP 服务器上执行远程工具"
    version = "1.0"

    async def validate(self, input_data: MCPExecuteToolInput):
        if not input_data.server.strip():
            return ToolValidationError("server（服务器名称）不能为空")
        if not input_data.tool.strip():
            return ToolValidationError("tool（工具名称）不能为空")
        return None

    async def execute(self, input_data: MCPExecuteToolInput) -> ToolResult:
        try:
            result = await get_mcp_manager().call_tool(
                input_data.server.strip(),
                input_data.tool.strip(),
                input_data.arguments,
            )
            return ToolResult.ok(
                data=result,
                message=f"成功在服务器 {input_data.server} 上执行工具 {input_data.tool}",
                metadata={"server": input_data.server, "tool": input_data.tool},
            )
        except Exception as exc:
            return ToolResult.fail(ToolExecutionError(f"执行 MCP 工具失败: {exc}"))

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "MCP 服务器名称"},
                    "tool": {"type": "string", "description": "要执行的工具名称"},
                    "arguments": {
                        "type": "object",
                        "description": "工具参数",
                        "default": {},
                    },
                },
                "required": ["server", "tool"],
            },
        }
