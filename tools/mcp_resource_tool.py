from typing import Any, Dict, Optional, List
from dataclasses import dataclass, field

from .base import Tool, ToolResult, ToolError, register_tool
from .mcp_tool import get_mcp_manager


@dataclass
class ListMcpResourcesInput:
    server: Optional[str] = None


@dataclass
class ReadMcpResourceInput:
    server: str
    uri: str


@register_tool
class ListMcpResourcesTool(Tool[ListMcpResourcesInput, List[Dict[str, Any]]]):
    """List MCP server resources."""

    name = "mcp_list_resources"
    description = "List available resources from MCP servers"
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
                        "description": "Server name to list resources from (omit for all servers)"
                    }
                }
            }
        }

    async def validate(self, input_data: ListMcpResourcesInput) -> Optional[ToolError]:
        if input_data.server:
            manager = get_mcp_manager()
            if not manager.get_server(input_data.server):
                return ToolError(f"MCP server not found: {input_data.server}", tool_name=self.name)
        return None

    async def execute(self, input_data: ListMcpResourcesInput) -> ToolResult:
        # Mock implementation
        resources = [
            {
                "name": "example_resource",
                "uri": f"mcp://{input_data.server or 'default'}/example",
                "mimeType": "application/json",
                "description": "Example MCP resource"
            }
        ]
        return ToolResult(
            success=True,
            data=resources,
            message=f"Found {len(resources)} resources"
        )

    def is_read_only(self) -> bool:
        return True


@register_tool
class ReadMcpResourceTool(Tool[ReadMcpResourceInput, Dict[str, Any]]):
    """Read an MCP server resource."""

    name = "mcp_read_resource"
    description = "Read a resource from an MCP server"
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
                        "description": "MCP server name"
                    },
                    "uri": {
                        "type": "string",
                        "description": "Resource URI"
                    }
                },
                "required": ["server", "uri"]
            }
        }

    async def validate(self, input_data: ReadMcpResourceInput) -> Optional[ToolError]:
        if not input_data.server:
            return ToolError("Server name is required", tool_name=self.name)
        if not input_data.uri:
            return ToolError("Resource URI is required", tool_name=self.name)
        return None

    async def execute(self, input_data: ReadMcpResourceInput) -> ToolResult:
        # Mock implementation
        content = f"Mock content for resource {input_data.uri} from server {input_data.server}"
        return ToolResult(
            success=True,
            data={
                "uri": input_data.uri,
                "content": content,
                "mimeType": "text/plain"
            },
            message=f"Read resource {input_data.uri}"
        )

    def is_read_only(self) -> bool:
        return True
