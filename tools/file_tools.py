"""
文件操作工具模块
提供读取、写入、编辑文件的功能
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any
import asyncio

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool


@dataclass
class ReadFileInput:
    """读取文件工具的输入参数"""
    file_path: str
    offset: Optional[int] = None  # 起始行号（1-based）
    limit: Optional[int] = None   # 读取行数限制


@dataclass
class WriteFileInput:
    """写入文件工具的输入参数"""
    file_path: str
    content: str
    overwrite: bool = False


@dataclass
class EditFileInput:
    """编辑文件工具的输入参数"""
    file_path: str
    old_string: str
    new_string: str


@register_tool
class ReadFileTool(Tool[ReadFileInput, str]):
    """读取文件内容工具"""

    name = "read_file"
    description = "读取指定文件的内容，支持行范围限制。用于查看代码文件、配置文件、日志等。"
    version = "1.0"

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的JSON Schema描述"""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "要读取的文件路径（绝对路径或相对路径）"
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（1-based），可选",
                        "default": None
                    },
                    "limit": {
                        "type": "integer",
                        "description": "读取行数限制，可选",
                        "default": None
                    }
                },
                "required": ["file_path"]
            }
        }

    async def validate(self, input_data: ReadFileInput) -> Optional[ToolError]:
        path = Path(input_data.file_path)
        if not path.exists():
            return ToolValidationError(f"文件不存在: {input_data.file_path}")
        if not path.is_file():
            return ToolValidationError(f"路径不是文件: {input_data.file_path}")
        if not path.is_absolute():
            # 转换为绝对路径
            path = path.resolve()
        return None

    async def execute(self, input_data: ReadFileInput) -> ToolResult:
        path = Path(input_data.file_path).resolve()

        try:
            # 读取文件内容
            content = await asyncio.to_thread(path.read_text, encoding='utf-8')
            lines = content.split('\n')
            total_lines = len(lines)

            # 处理行范围
            start = (input_data.offset or 1) - 1  # 转换为0-based
            limit = input_data.limit or total_lines
            end = min(start + limit, total_lines)

            if start < 0 or start >= total_lines:
                return ToolResult.error(
                    ToolValidationError(f"起始行号超出范围: {input_data.offset}, 文件共 {total_lines} 行")
                )

            selected_lines = lines[start:end]
            result_content = '\n'.join(selected_lines)

            return ToolResult.ok(
                data=result_content,
                message=f"成功读取文件 {path.name} ({start+1}-{end}/{total_lines} 行)",
                metadata={
                    "file_path": str(path),
                    "total_lines": total_lines,
                    "start_line": start + 1,
                    "end_line": end,
                    "lines_read": end - start,
                }
            )
        except UnicodeDecodeError:
            return ToolResult.error(
                ToolExecutionError(f"文件编码错误，无法作为文本读取: {path}")
            )
        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"读取文件失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        return True


@register_tool
class WriteFileTool(Tool[WriteFileInput, str]):
    """写入文件内容工具"""

    name = "write_file"
    description = "将内容写入指定文件，可选择是否覆盖。用于创建新文件或覆盖现有文件。"
    version = "1.0"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "要写入的文件路径"
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的文件内容"
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "是否覆盖已存在的文件",
                        "default": False
                    }
                },
                "required": ["file_path", "content"]
            }
        }

    async def validate(self, input_data: WriteFileInput) -> Optional[ToolError]:
        path = Path(input_data.file_path)
        if path.exists() and not input_data.overwrite:
            return ToolValidationError(
                f"文件已存在: {input_data.file_path}，设置 overwrite=True 以覆盖"
            )
        return None

    async def execute(self, input_data: WriteFileInput) -> ToolResult:
        path = Path(input_data.file_path).resolve()

        try:
            # 确保父目录存在
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)

            # 写入文件
            await asyncio.to_thread(path.write_text, input_data.content, encoding='utf-8')

            return ToolResult.ok(
                data=str(path),
                message=f"成功写入文件: {path.name}",
                metadata={
                    "file_path": str(path),
                    "bytes_written": len(input_data.content.encode('utf-8')),
                    "lines_written": len(input_data.content.split('\n')),
                }
            )
        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"写入文件失败: {str(e)}")
            )

    def is_destructive(self) -> bool:
        return True


@register_tool
class EditFileTool(Tool[EditFileInput, str]):
    """编辑文件内容工具 - 使用字符串替换"""

    name = "edit_file"
    description = "在文件中替换指定的字符串。old_string 必须是文件中存在的唯一字符串。"
    version = "1.0"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "要编辑的文件路径"
                    },
                    "old_string": {
                        "type": "string",
                        "description": "文件中要替换的原始字符串（必须唯一）"
                    },
                    "new_string": {
                        "type": "string",
                        "description": "用于替换的新字符串"
                    }
                },
                "required": ["file_path", "old_string", "new_string"]
            }
        }

    async def validate(self, input_data: EditFileInput) -> Optional[ToolError]:
        path = Path(input_data.file_path)
        if not path.exists():
            return ToolValidationError(f"文件不存在: {input_data.file_path}")
        if not path.is_file():
            return ToolValidationError(f"路径不是文件: {input_data.file_path}")
        if not input_data.old_string:
            return ToolValidationError("old_string 不能为空")
        return None

    async def execute(self, input_data: EditFileInput) -> ToolResult:
        path = Path(input_data.file_path).resolve()

        try:
            # 读取文件内容
            content = await asyncio.to_thread(path.read_text, encoding='utf-8')

            # 检查old_string是否存在
            if input_data.old_string not in content:
                return ToolResult.error(
                    ToolValidationError(
                        f"未找到要替换的字符串",
                        details={
                            "old_string": input_data.old_string[:100] + "..." if len(input_data.old_string) > 100 else input_data.old_string,
                            "file_path": str(path),
                        }
                    )
                )

            # 检查是否有多个匹配
            matches = content.count(input_data.old_string)
            if matches > 1:
                return ToolResult.error(
                    ToolValidationError(
                        f"找到 {matches} 处匹配，请提供更具体的字符串以确保唯一性",
                        details={"matches": matches}
                    )
                )

            # 执行替换
            new_content = content.replace(input_data.old_string, input_data.new_string, 1)

            # 写回文件
            await asyncio.to_thread(path.write_text, new_content, encoding='utf-8')

            return ToolResult.ok(
                data=str(path),
                message=f"成功编辑文件: {path.name}",
                metadata={
                    "file_path": str(path),
                    "old_string_length": len(input_data.old_string),
                    "new_string_length": len(input_data.new_string),
                    "total_length": len(new_content),
                }
            )
        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"编辑文件失败: {str(e)}")
            )

    def is_destructive(self) -> bool:
        return True
