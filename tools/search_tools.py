"""
搜索工具模块
提供文件搜索和内容搜索功能
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any
import asyncio
import fnmatch
import re

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool


@dataclass
class GlobInput:
    """Glob文件搜索工具的输入参数"""
    pattern: str
    path: Optional[str] = None  # 搜索目录，默认为当前目录
    exclude: Optional[List[str]] = None  # 排除模式


@dataclass
class GrepInput:
    """Grep内容搜索工具的输入参数"""
    pattern: str
    path: Optional[str] = None  # 搜索路径
    output_mode: str = "content"  # content, files_with_matches, count
    glob: Optional[str] = None    # 文件过滤模式
    case_sensitive: bool = False


@register_tool
class GlobTool(Tool[GlobInput, List[str]]):
    """文件路径搜索工具 - 使用glob模式"""

    name = "glob"
    description = "使用glob模式搜索文件路径。用于查找特定类型的文件，如'**/*.py'查找所有Python文件。"
    version = "1.0"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "glob搜索模式，如 '**/*.py' 或 '*.json'"
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索目录，默认为当前目录",
                        "default": None
                    },
                    "exclude": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "排除模式列表",
                        "default": None
                    }
                },
                "required": ["pattern"]
            }
        }

    async def validate(self, input_data: GlobInput) -> Optional[ToolError]:
        if not input_data.pattern:
            return ToolValidationError("pattern 不能为空")
        return None

    async def execute(self, input_data: GlobInput) -> ToolResult:
        search_path = Path(input_data.path or ".").resolve()
        exclude_patterns = input_data.exclude or []

        try:
            # 使用异步执行阻塞IO
            matches = await self._glob_search(search_path, input_data.pattern, exclude_patterns)

            return ToolResult.ok(
                data=matches,
                message=f"找到 {len(matches)} 个匹配文件",
                metadata={
                    "pattern": input_data.pattern,
                    "search_path": str(search_path),
                    "exclude_patterns": exclude_patterns,
                    "match_count": len(matches),
                }
            )
        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"搜索失败: {str(e)}")
            )

    async def _glob_search(
        self,
        search_path: Path,
        pattern: str,
        exclude_patterns: List[str]
    ) -> List[str]:
        """执行glob搜索"""
        matches = []

        # 如果pattern包含/，说明是相对路径模式
        if '/' in pattern:
            # 在指定路径下搜索
            full_pattern = search_path / pattern
            for path in search_path.rglob(pattern.split('/')[-1]):
                if path.match(pattern):
                    str_path = str(path.relative_to(search_path))
                    if not self._should_exclude(str_path, exclude_patterns):
                        matches.append(str(path))
        else:
            # 简单模式，在所有子目录中搜索
            for path in search_path.rglob(pattern):
                str_path = str(path.relative_to(search_path))
                if not self._should_exclude(str_path, exclude_patterns):
                    matches.append(str(path))

        # 排序并去重
        matches = sorted(set(matches))
        return matches

    def _should_exclude(self, path: str, exclude_patterns: List[str]) -> bool:
        """检查路径是否应该被排除"""
        for pattern in exclude_patterns:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern):
                return True
        return False

    def is_read_only(self) -> bool:
        return True


@register_tool
class GrepTool(Tool[GrepInput, Any]):
    """文件内容搜索工具 - 使用正则表达式"""

    name = "grep"
    description = "在文件中搜索匹配正则表达式的内容。用于查找代码中的函数、变量或特定模式。"
    version = "1.0"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "正则表达式搜索模式"
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索路径（文件或目录），默认为当前目录",
                        "default": None
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["content", "files_with_matches", "count"],
                        "description": "输出模式：content返回匹配内容，files_with_matches返回文件列表，count返回匹配数",
                        "default": "content"
                    },
                    "glob": {
                        "type": "string",
                        "description": "文件过滤模式，如 '*.py'",
                        "default": None
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "是否区分大小写",
                        "default": False
                    }
                },
                "required": ["pattern"]
            }
        }

    async def validate(self, input_data: GrepInput) -> Optional[ToolError]:
        if not input_data.pattern:
            return ToolValidationError("pattern 不能为空")
        if input_data.output_mode not in ["content", "files_with_matches", "count"]:
            return ToolValidationError(f"无效的output_mode: {input_data.output_mode}")
        return None

    async def execute(self, input_data: GrepInput) -> ToolResult:
        search_path = Path(input_data.path or ".").resolve()

        try:
            # 编译正则表达式
            flags = 0 if input_data.case_sensitive else re.IGNORECASE
            regex = re.compile(input_data.pattern, flags)

            # 执行搜索
            results = await self._grep_search(
                search_path,
                regex,
                input_data.glob,
                input_data.output_mode
            )

            if input_data.output_mode == "files_with_matches":
                message = f"找到 {len(results)} 个匹配文件"
            elif input_data.output_mode == "count":
                total_matches = sum(r.get("match_count", 0) for r in results.values())
                message = f"找到 {total_matches} 处匹配（在 {len(results)} 个文件中）"
            else:
                total_matches = sum(len(r.get("matches", [])) for r in results)
                message = f"找到 {total_matches} 处匹配（在 {len(results)} 个文件中）"

            return ToolResult.ok(
                data=results,
                message=message,
                metadata={
                    "pattern": input_data.pattern,
                    "search_path": str(search_path),
                    "output_mode": input_data.output_mode,
                    "case_sensitive": input_data.case_sensitive,
                }
            )
        except re.error as e:
            return ToolResult.error(
                ToolValidationError(f"无效的正则表达式: {str(e)}")
            )
        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"搜索失败: {str(e)}")
            )

    async def _grep_search(
        self,
        search_path: Path,
        regex: re.Pattern,
        glob_pattern: Optional[str],
        output_mode: str
    ) -> Any:
        """执行grep搜索"""

        if output_mode == "files_with_matches":
            matches = set()
        elif output_mode == "count":
            matches = {}
        else:
            matches = []

        # 确定搜索的文件
        if search_path.is_file():
            files = [search_path]
        else:
            pattern = glob_pattern or "**/*"
            files = list(search_path.rglob(pattern))
            files = [f for f in files if f.is_file()]

        for file_path in files:
            try:
                # 跳过二进制文件和大文件
                stat = file_path.stat()
                if stat.st_size > 10 * 1024 * 1024:  # 跳过大于10MB的文件
                    continue

                content = await asyncio.to_thread(file_path.read_text, encoding='utf-8', errors='ignore')
                lines = content.split('\n')

                file_matches = []
                for line_num, line in enumerate(lines, 1):
                    if regex.search(line):
                        if output_mode == "files_with_matches":
                            matches.add(str(file_path))
                            break
                        elif output_mode == "content":
                            file_matches.append({
                                "line_number": line_num,
                                "content": line,
                            })

                if output_mode == "content" and file_matches:
                    matches.append({
                        "file_path": str(file_path),
                        "matches": file_matches,
                    })
                elif output_mode == "count" and file_matches:
                    matches[str(file_path)] = {
                        "match_count": len(file_matches),
                    }

            except (UnicodeDecodeError, IOError):
                # 跳过无法读取的文件
                continue

        if output_mode == "files_with_matches":
            return sorted(matches)
        elif output_mode == "count":
            return matches
        else:
            return matches

    def is_read_only(self) -> bool:
        return True
