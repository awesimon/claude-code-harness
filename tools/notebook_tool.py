"""
Jupyter Notebook 编辑工具模块
提供 Notebook 的读取、编辑、保存功能
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, List
import json
import asyncio

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool


@dataclass
class NotebookEditInput:
    """Notebook 编辑工具的输入参数"""
    notebook_path: str
    new_source: str
    cell_id: Optional[str] = None  # 单元格 ID，可选
    cell_type: Optional[str] = None  # 单元格类型: code 或 markdown，默认保持原类型
    edit_mode: str = "replace"  # 编辑模式: replace, insert, delete


@register_tool
class NotebookEditTool(Tool[NotebookEditInput, Dict[str, Any]]):
    """
    编辑 Jupyter Notebook 工具

    支持编辑 Notebook 中的单元格，包括替换、插入、删除操作
    使用 nbformat 兼容的格式处理 notebook
    """

    name = "notebook_edit"
    description = "编辑 Jupyter Notebook 文件，支持单元格的增删改"
    version = "1.0"

    VALID_EDIT_MODES = ["replace", "insert", "delete"]
    VALID_CELL_TYPES = ["code", "markdown"]

    async def validate(self, input_data: NotebookEditInput) -> Optional[ToolError]:
        if not input_data.notebook_path:
            return ToolValidationError("notebook_path 不能为空")

        if not input_data.new_source and input_data.edit_mode != "delete":
            return ToolValidationError("new_source 不能为空（delete 模式除外）")

        if input_data.edit_mode not in self.VALID_EDIT_MODES:
            return ToolValidationError(
                f"无效的 edit_mode: {input_data.edit_mode}，必须是: {self.VALID_EDIT_MODES}"
            )

        if input_data.cell_type and input_data.cell_type not in self.VALID_CELL_TYPES:
            return ToolValidationError(
                f"无效的 cell_type: {input_data.cell_type}，必须是: {self.VALID_CELL_TYPES}"
            )

        path = Path(input_data.notebook_path)

        # 对于 replace 和 delete 模式，文件必须存在
        if input_data.edit_mode in ["replace", "delete"]:
            if not path.exists():
                return ToolValidationError(f"Notebook 文件不存在: {input_data.notebook_path}")
            if not path.is_file():
                return ToolValidationError(f"路径不是文件: {input_data.notebook_path}")

            # 验证文件是否为有效的 JSON
            try:
                content = await asyncio.to_thread(path.read_text, encoding='utf-8')
                notebook = json.loads(content)
                if "cells" not in notebook:
                    return ToolValidationError("无效的 Notebook 文件: 缺少 cells 字段")
            except json.JSONDecodeError:
                return ToolValidationError("无效的 Notebook 文件: JSON 格式错误")
            except Exception as e:
                return ToolValidationError(f"读取 Notebook 文件失败: {str(e)}")

        return None

    async def execute(self, input_data: NotebookEditInput) -> ToolResult:
        path = Path(input_data.notebook_path)

        try:
            # 加载或创建 notebook
            if path.exists():
                content = await asyncio.to_thread(path.read_text, encoding='utf-8')
                notebook = json.loads(content)
            else:
                # 创建新的 notebook 结构
                notebook = {
                    "cells": [],
                    "metadata": {},
                    "nbformat": 4,
                    "nbformat_minor": 2,
                }

            cells = notebook.get("cells", [])
            edit_mode = input_data.edit_mode

            if edit_mode == "replace":
                result = await self._replace_cell(cells, input_data)
            elif edit_mode == "insert":
                result = await self._insert_cell(cells, input_data)
            elif edit_mode == "delete":
                result = await self._delete_cell(cells, input_data)
            else:
                return ToolResult.error(
                    ToolValidationError(f"不支持的编辑模式: {edit_mode}")
                )

            if isinstance(result, ToolError):
                return ToolResult.error(result)

            # 更新 notebook
            notebook["cells"] = cells

            # 确保父目录存在
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)

            # 保存 notebook
            await asyncio.to_thread(
                path.write_text,
                json.dumps(notebook, indent=1, ensure_ascii=False),
                encoding='utf-8'
            )

            return ToolResult.ok(
                data={
                    "notebook_path": str(path),
                    "cell_count": len(cells),
                    "operation": edit_mode,
                },
                message=f"成功{self._get_operation_name(edit_mode)} Notebook",
                metadata={
                    "notebook_path": str(path),
                    "cell_count": len(cells),
                    "operation": edit_mode,
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"编辑 Notebook 失败: {str(e)}")
            )

    async def _replace_cell(
        self,
        cells: List[Dict[str, Any]],
        input_data: NotebookEditInput
    ) -> Optional[ToolError]:
        """替换单元格内容"""
        cell_id = input_data.cell_id

        if cell_id is not None:
            # 通过 ID 查找单元格
            for cell in cells:
                if cell.get("id") == cell_id:
                    cell["source"] = input_data.new_source
                    if input_data.cell_type:
                        cell["cell_type"] = input_data.cell_type
                        # 更新 outputs（如果是 code 转 markdown，清空 outputs）
                        if input_data.cell_type == "markdown":
                            cell["outputs"] = []
                            cell["execution_count"] = None
                    return None
            return ToolValidationError(f"未找到指定单元格: {cell_id}")
        else:
            # 没有指定 cell_id，替换最后一个单元格或创建新单元格
            if cells:
                cells[-1]["source"] = input_data.new_source
                if input_data.cell_type:
                    cells[-1]["cell_type"] = input_data.cell_type
            else:
                # 创建新单元格
                cell_type = input_data.cell_type or "code"
                new_cell = self._create_cell(cell_type, input_data.new_source)
                cells.append(new_cell)
            return None

    async def _insert_cell(
        self,
        cells: List[Dict[str, Any]],
        input_data: NotebookEditInput
    ) -> Optional[ToolError]:
        """插入新单元格"""
        cell_id = input_data.cell_id
        cell_type = input_data.cell_type or "code"
        new_cell = self._create_cell(cell_type, input_data.new_source)

        if cell_id is not None:
            # 在指定单元格之后插入
            for i, cell in enumerate(cells):
                if cell.get("id") == cell_id:
                    cells.insert(i + 1, new_cell)
                    return None
            # 未找到指定单元格，添加到末尾
            cells.append(new_cell)
        else:
            # 没有指定 cell_id，添加到末尾
            cells.append(new_cell)

        return None

    async def _delete_cell(
        self,
        cells: List[Dict[str, Any]],
        input_data: NotebookEditInput
    ) -> Optional[ToolError]:
        """删除单元格"""
        cell_id = input_data.cell_id

        if cell_id is None:
            return ToolValidationError("delete 模式必须指定 cell_id")

        for i, cell in enumerate(cells):
            if cell.get("id") == cell_id:
                del cells[i]
                return None

        return ToolValidationError(f"未找到指定单元格: {cell_id}")

    def _create_cell(self, cell_type: str, source: str) -> Dict[str, Any]:
        """创建新单元格"""
        import uuid

        cell_id = str(uuid.uuid4())[:8]

        if cell_type == "code":
            return {
                "cell_type": "code",
                "execution_count": None,
                "id": cell_id,
                "metadata": {},
                "outputs": [],
                "source": source,
            }
        else:  # markdown
            return {
                "cell_type": "markdown",
                "id": cell_id,
                "metadata": {},
                "source": source,
            }

    def _get_operation_name(self, edit_mode: str) -> str:
        """获取操作的中文名称"""
        names = {
            "replace": "修改",
            "insert": "插入",
            "delete": "删除",
        }
        return names.get(edit_mode, edit_mode)

    def is_destructive(self) -> bool:
        return True
