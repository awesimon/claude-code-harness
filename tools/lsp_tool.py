from typing import Any, Dict, Optional
from dataclasses import dataclass

from .base import Tool, ToolExecutionError, ToolResult, ToolValidationError


@dataclass
class LSPInput:
    operation: str  # goToDefinition, findReferences, hover, documentSymbol, etc.
    file_path: str
    line: int
    character: int


class LSPTool(Tool):
    """LSP Tool for code intelligence operations."""

    name = "lsp"
    description = "Language Server Protocol operations: goToDefinition, findReferences, hover, documentSymbol, workspaceSymbol, etc."
    version = "1.0"

    SUPPORTED_OPERATIONS = [
        "goToDefinition",
        "findReferences",
        "hover",
        "documentSymbol",
        "workspaceSymbol",
        "goToImplementation",
        "prepareCallHierarchy",
        "incomingCalls",
        "outgoingCalls"
    ]

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": self.SUPPORTED_OPERATIONS,
                        "description": "LSP operation to perform"
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Absolute or relative path to the file"
                    },
                    "line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Line number (1-based, as shown in editors)"
                    },
                    "character": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Character offset (1-based, as shown in editors)"
                    }
                },
                "required": ["operation", "file_path", "line", "character"]
            },
            "returns": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string"},
                    "result": {"type": "string", "description": "Formatted result of the LSP operation"},
                    "file_path": {"type": "string"},
                    "result_count": {"type": "integer", "description": "Number of results"},
                    "file_count": {"type": "integer", "description": "Number of files containing results"}
                }
            }
        }

    async def validate(self, input_data: LSPInput) -> Optional[ToolValidationError]:
        if input_data.operation not in self.SUPPORTED_OPERATIONS:
            return ToolValidationError(
                f"Invalid operation: {input_data.operation}. "
                f"Supported operations: {', '.join(self.SUPPORTED_OPERATIONS)}",
                details={"tool_name": self.name},
            )
        import os
        abs_path = os.path.abspath(input_data.file_path)
        if not os.path.exists(abs_path):
            return ToolValidationError(
                f"File does not exist: {input_data.file_path}",
                details={"tool_name": self.name},
            )
        if not os.path.isfile(abs_path):
            return ToolValidationError(
                f"Path is not a file: {input_data.file_path}",
                details={"tool_name": self.name},
            )
        return None

    async def execute(self, input_data: LSPInput) -> ToolResult:
        """Fail closed until a real language-server transport is configured."""
        message = "LSP tool is unsupported because no language-server transport is configured"
        return ToolResult.fail(
            ToolExecutionError(
                message,
                details={"tool_name": self.name, "reason": "unsupported"},
            ),
            message=message,
        )

    def is_read_only(self) -> bool:
        return True

    def is_concurrency_safe(self) -> bool:
        return True
