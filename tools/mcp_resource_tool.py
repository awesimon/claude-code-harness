from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .base import Tool, ToolExecutionError, ToolResult, ToolValidationError, register_tool
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
    name = "mcp_list_resources"
    description = "List available resources from MCP servers"
    version = "1.0"

    async def execute(self, input_data: ListMcpResourcesInput) -> ToolResult:
        try:
            resources = await get_mcp_manager().list_resources(input_data.server)
            data = [
                {
                    "name": item.name,
                    "uri": item.uri,
                    "mimeType": item.mime_type,
                    "description": item.description,
                    "server": item.server,
                }
                for item in resources
            ]
            return ToolResult.ok(data=data, message=f"Found {len(data)} resources")
        except Exception as exc:
            return ToolResult.fail(ToolExecutionError(f"List MCP resources failed: {exc}"))

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
                        "description": "Server name to list resources from (omit for all servers)",
                    }
                },
            },
        }


@register_tool
class ReadMcpResourceTool(Tool[ReadMcpResourceInput, Dict[str, Any]]):
    name = "mcp_read_resource"
    description = "Read a resource from an MCP server"
    version = "1.0"

    async def validate(self, input_data: ReadMcpResourceInput):
        if not input_data.server.strip():
            return ToolValidationError("Server name is required")
        if not input_data.uri.strip():
            return ToolValidationError("Resource URI is required")
        return None

    async def execute(self, input_data: ReadMcpResourceInput) -> ToolResult:
        try:
            content = await get_mcp_manager().read_resource(
                input_data.server.strip(), input_data.uri.strip()
            )
            return ToolResult.ok(
                data={
                    "uri": content.uri,
                    "content": content.text if content.text is not None else content.blob,
                    "mimeType": content.mime_type,
                },
                message=f"Read resource {input_data.uri}",
            )
        except Exception as exc:
            return ToolResult.fail(ToolExecutionError(f"Read MCP resource failed: {exc}"))

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
                    "server": {"type": "string", "description": "MCP server name"},
                    "uri": {"type": "string", "description": "Resource URI"},
                },
                "required": ["server", "uri"],
            },
        }
