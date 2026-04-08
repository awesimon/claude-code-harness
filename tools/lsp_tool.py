"""
LSP工具模块 - 语言服务器协议支持

提供代码智能功能，包括:
- 跳转到定义
- 查找引用
- 悬停提示
- 文档符号
- 工作区符号
- 调用层次结构

注意: 这是一个基础框架实现，完整的LSP功能需要连接到实际的LSP服务器。
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
import os
import subprocess

from .base import Tool, ToolResult, ToolError, ToolValidationError, ToolExecutionError, register_tool


@dataclass
class LSPInput:
    """LSP工具输入参数"""
    operation: str  # 操作类型
    file_path: str  # 文件路径
    line: int  # 行号（1-based）
    character: int  # 字符偏移（1-based）


# LSP支持的操作
LSP_OPERATIONS = [
    "goToDefinition",      # 跳转到定义
    "findReferences",      # 查找引用
    "hover",               # 悬停提示
    "documentSymbol",      # 文档符号
    "workspaceSymbol",     # 工作区符号
    "goToImplementation",  # 跳转到实现
    "prepareCallHierarchy", # 准备调用层次结构
    "incomingCalls",       # 入站调用
    "outgoingCalls",       # 出站调用
]


@register_tool
class LSPTool(Tool[LSPInput, Dict[str, Any]]):
    """
    LSP工具 - 代码智能

    提供基于语言服务器协议的代码分析功能。
    需要连接到LSP服务器才能正常工作。

    使用场景:
    - 代码导航（跳转到定义）
    - 理解代码结构（查找引用）
    - 获取类型信息（悬停提示）
    - 浏览符号（文档/工作区符号）

    支持的编程语言取决于可用的LSP服务器配置。

    示例:
    - 跳转到定义: {"operation": "goToDefinition", "file_path": "/path/to/file.py", "line": 10, "character": 5}
    - 查找引用: {"operation": "findReferences", "file_path": "/path/to/file.py", "line": 10, "character": 5}
    """

    name = "lsp"
    description = "代码智能工具（跳转到定义、查找引用、悬停提示等）"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._lsp_enabled = os.environ.get("ENABLE_LSP_TOOL", "").lower() in ("true", "1", "yes")

    def is_enabled(self) -> bool:
        """检查LSP是否启用"""
        return self._lsp_enabled

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
                        "enum": LSP_OPERATIONS,
                        "description": "LSP操作类型"
                    },
                    "file_path": {
                        "type": "string",
                        "description": "文件路径（绝对路径或相对路径）"
                    },
                    "line": {
                        "type": "integer",
                        "description": "行号（1-based，与编辑器显示一致）",
                        "minimum": 1
                    },
                    "character": {
                        "type": "integer",
                        "description": "字符偏移（1-based，与编辑器显示一致）",
                        "minimum": 1
                    }
                },
                "required": ["operation", "file_path", "line", "character"]
            },
            "returns": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "description": "执行的操作"},
                    "result": {"type": "string", "description": "格式化的结果"},
                    "file_path": {"type": "string", "description": "操作的文件路径"},
                    "result_count": {"type": "number", "description": "结果数量"},
                    "file_count": {"type": "number", "description": "涉及文件数量"}
                }
            }
        }

    async def validate(self, input_data: LSPInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not self.is_enabled():
            return ToolValidationError(
                "LSP工具未启用。设置环境变量 ENABLE_LSP_TOOL=true 来启用。"
            )

        if input_data.operation not in LSP_OPERATIONS:
            return ToolValidationError(
                f"无效的操作: {input_data.operation}。有效操作: {', '.join(LSP_OPERATIONS)}"
            )

        # 检查文件是否存在
        abs_path = os.path.abspath(input_data.file_path)
        if not os.path.exists(abs_path):
            return ToolValidationError(f"文件不存在: {input_data.file_path}")

        if not os.path.isfile(abs_path):
            return ToolValidationError(f"路径不是文件: {input_data.file_path}")

        return None

    async def execute(self, input_data: LSPInput) -> ToolResult:
        """执行LSP操作"""
        try:
            abs_path = os.path.abspath(input_data.file_path)

            # 这里应该实际连接到LSP服务器并执行操作
            # 目前返回模拟结果，说明工具已调用

            # 模拟不同的操作结果
            if input_data.operation == "goToDefinition":
                result = self._mock_go_to_definition(abs_path, input_data.line, input_data.character)
            elif input_data.operation == "findReferences":
                result = self._mock_find_references(abs_path, input_data.line, input_data.character)
            elif input_data.operation == "hover":
                result = self._mock_hover(abs_path, input_data.line, input_data.character)
            elif input_data.operation == "documentSymbol":
                result = self._mock_document_symbol(abs_path)
            elif input_data.operation == "workspaceSymbol":
                result = self._mock_workspace_symbol()
            elif input_data.operation in ["prepareCallHierarchy", "incomingCalls", "outgoingCalls"]:
                result = self._mock_call_hierarchy(input_data.operation)
            else:
                result = {"message": "操作已记录，但LSP服务器未连接"}

            return ToolResult.ok(
                data={
                    "operation": input_data.operation,
                    "result": result.get("message", ""),
                    "file_path": input_data.file_path,
                    "result_count": result.get("count", 0),
                    "file_count": result.get("file_count", 1),
                },
                message=f"LSP操作 {input_data.operation} 已执行",
                metadata={
                    "lsp_connected": False,
                    "note": "这是一个框架实现，需要连接实际的LSP服务器获取真实结果"
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"LSP操作失败: {str(e)}")
            )

    def _mock_go_to_definition(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """模拟跳转到定义"""
        return {
            "message": f"定义位置:\n  {file_path}:{line}:{character}\n\n注意: 这是模拟结果，连接LSP服务器后可获取真实定义位置。",
            "count": 1,
            "file_count": 1
        }

    def _mock_find_references(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """模拟查找引用"""
        return {
            "message": f"引用位置:\n  - {file_path}:{line}:1 (1 occurrence)\n\n注意: 这是模拟结果，连接LSP服务器后可获取真实引用。",
            "count": 1,
            "file_count": 1
        }

    def _mock_hover(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """模拟悬停提示"""
        return {
            "message": f"悬停信息:\n  位置: {file_path}:{line}:{character}\n  类型信息: (需要LSP服务器)\n\n注意: 连接LSP服务器后可获取真实类型信息。",
            "count": 1,
            "file_count": 1
        }

    def _mock_document_symbol(self, file_path: str) -> Dict[str, Any]:
        """模拟文档符号"""
        return {
            "message": f"文档符号:\n  文件: {os.path.basename(file_path)}\n  符号: (需要LSP服务器分析)\n\n注意: 连接LSP服务器后可获取真实符号列表。",
            "count": 0,
            "file_count": 1
        }

    def _mock_workspace_symbol(self) -> Dict[str, Any]:
        """模拟工作区符号"""
        return {
            "message": "工作区符号:\n  (需要LSP服务器索引)\n\n注意: 连接LSP服务器后可搜索整个工作区的符号。",
            "count": 0,
            "file_count": 0
        }

    def _mock_call_hierarchy(self, operation: str) -> Dict[str, Any]:
        """模拟调用层次结构"""
        return {
            "message": f"调用层次结构 ({operation}):\n  (需要LSP服务器分析)\n\n注意: 连接LSP服务器后可获取真实的调用关系。",
            "count": 0,
            "file_count": 0
        }

    def is_read_only(self) -> bool:
        """LSP工具是只读的"""
        return True

    def is_concurrency_safe(self) -> bool:
        """支持并发执行"""
        return True
