from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from .base import Tool, ToolResult, ToolValidationError, register_tool
from .mcp_tool import get_mcp_manager


@dataclass
class McpAuthInput:
    server: str


@register_tool
class McpAuthTool(Tool[McpAuthInput, Dict[str, Any]]):
    name = "mcp_authenticate"
    description = "Return authentication status for an MCP server"
    version = "1.0"

    async def validate(self, input_data: McpAuthInput):
        if not input_data.server.strip():
            return ToolValidationError("Server name is required")
        return None

    async def execute(self, input_data: McpAuthInput) -> ToolResult:
        record = get_mcp_manager().status(input_data.server.strip())
        if record is None:
            return ToolResult.fail(ToolValidationError(f"MCP server not found: {input_data.server}"))
        data = {
            "status": "connected" if record.status.value == "connected" else "unavailable",
            "server": record.name,
            "authUrl": None,
            "message": "OAuth is not required or is managed by the configured HTTP client",
        }
        return ToolResult.ok(data=data, message=data["message"])

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {"server": {"type": "string", "description": "MCP server name"}},
                "required": ["server"],
            },
        }
