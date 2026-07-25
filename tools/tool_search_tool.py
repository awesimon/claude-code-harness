"""Public ToolSearch adapter over the active harness registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import (
    Tool,
    ToolResult,
    ToolValidationError,
    get_active_tool_context,
    register_tool,
)


@dataclass
class ToolSearchInput:
    query: str
    category: str | None = None
    max_results: int = 5


@register_tool
class ToolSearchTool(Tool[ToolSearchInput, dict[str, Any]]):
    name = "tool_search"
    aliases = ("ToolSearch",)
    description = (
        'Find deferred tools by keyword. Use "select:<tool_name>" to load a schema.'
    )
    input_type = ToolSearchInput

    async def validate(self, input_data: ToolSearchInput):
        if not input_data.query.strip():
            return ToolValidationError("query must be non-empty")
        if (
            isinstance(input_data.max_results, bool)
            or input_data.max_results <= 0
            or input_data.max_results > 20
        ):
            return ToolValidationError("max_results must be between 1 and 20")
        return None

    async def execute(self, input_data: ToolSearchInput) -> ToolResult:
        harness = get_active_tool_context().get("session_harness")
        if harness is None:
            return ToolResult.fail(
                ToolValidationError("ToolSearch requires an active session harness")
            )
        result = harness.deferred_tools.search(
            input_data.query, max_results=input_data.max_results
        )
        tools = []
        for name in result.matches:
            tool = harness.deferred_tools.resolve_tool(name)
            tools.append(
                {
                    "name": name,
                    "description": getattr(tool, "description", "") if tool else "",
                    "search_hint": getattr(tool, "search_hint", None) if tool else None,
                }
            )
        data = {
            "query": result.query,
            "category": input_data.category,
            "matches": list(result.matches),
            "total_deferred_tools": result.total_deferred_tools,
            "selected": result.selected,
            "tools": tools,
            "count": len(result.matches),
        }
        return ToolResult.ok(data, f"Found {len(result.matches)} matching tools")

    def is_read_only(self) -> bool:
        return True

    def get_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": 'Keywords or exact "select:<tool_name>" selection',
                    },
                    "category": {"type": "string"},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        }
