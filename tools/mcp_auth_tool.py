from typing import Any, Dict, Optional
from dataclasses import dataclass

from .base import Tool, ToolResult, ToolError, register_tool
from .mcp_tool import get_mcp_manager


@dataclass
class McpAuthInput:
    server: str


@register_tool
class McpAuthTool(Tool[McpAuthInput, Dict[str, Any]]):
    """Authenticate with an MCP server."""

    name = "mcp_authenticate"
    description = "Start OAuth flow for an MCP server that requires authentication"
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
                        "description": "MCP server name to authenticate"
                    }
                },
                "required": ["server"]
            }
        }

    async def validate(self, input_data: McpAuthInput) -> Optional[ToolError]:
        if not input_data.server:
            return ToolError("Server name is required", tool_name=self.name)
        return None

    async def run(self, input_data: McpAuthInput) -> ToolResult:
        # Mock implementation - would start OAuth flow in real implementation
        return ToolResult(
            success=True,
            data={
                "status": "auth_url",
                "server": input_data.server,
                "authUrl": f"https://example.com/oauth/authorize?client_id={input_data.server}",
                "message": f"Please open the authorization URL to authenticate with {input_data.server}"
            },
            message=f"Authentication started for {input_data.server}. Please complete the OAuth flow."
        )
