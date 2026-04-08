"""
待办事项管理工具模块
提供待办事项的创建、更新和管理功能
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import json
import asyncio
from pathlib import Path
import tempfile

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool


class TodoStatus(str, Enum):
    """待办事项状态"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TodoPriority(str, Enum):
    """待办事项优先级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class TodoItem:
    """待办事项数据结构"""
    id: str
    content: str
    status: TodoStatus = TodoStatus.PENDING
    priority: TodoPriority = TodoPriority.MEDIUM
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class TodoWriteInput:
    """待办事项写入工具的输入参数"""
    todos: List[Dict[str, Any]]  # 待办事项列表，每项包含 id, content, status, priority
    storage_key: Optional[str] = None  # 存储键名，用于区分不同会话的待办事项


@dataclass
class TodoWriteOutput:
    """待办事项写入工具的输出"""
    old_todos: List[Dict[str, Any]]
    new_todos: List[Dict[str, Any]]


@register_tool
class TodoWriteTool(Tool[TodoWriteInput, TodoWriteOutput]):
    """
    管理待办事项工具

    管理待办事项列表，支持增删改查操作
    返回旧的待办事项列表和新的待办事项列表
    数据存储在临时文件或内存中
    """

    name = "todo_write"
    description = "管理待办事项列表，支持增删改查"
    version = "1.0"

    def __init__(self):
        super().__init__()
        # 使用临时目录存储待办事项
        self.storage_dir = Path(tempfile.gettempdir()) / "claude_todos"
        self._memory_storage: Dict[str, List[Dict[str, Any]]] = {}

    def _get_storage_path(self, key: str) -> Path:
        """获取存储文件路径"""
        return self.storage_dir / f"{key}.json"

    async def _load_todos(self, storage_key: str) -> List[Dict[str, Any]]:
        """从存储加载待办事项"""
        # 优先从内存加载
        if storage_key in self._memory_storage:
            return self._memory_storage[storage_key]

        # 从文件加载
        storage_path = self._get_storage_path(storage_key)
        if storage_path.exists():
            try:
                content = await asyncio.to_thread(storage_path.read_text, encoding='utf-8')
                return json.loads(content)
            except Exception:
                return []
        return []

    async def _save_todos(self, storage_key: str, todos: List[Dict[str, Any]]) -> None:
        """保存待办事项到存储"""
        # 保存到内存
        self._memory_storage[storage_key] = todos

        # 保存到文件
        storage_path = self._get_storage_path(storage_key)
        await asyncio.to_thread(self.storage_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(
            storage_path.write_text,
            json.dumps(todos, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )

    async def validate(self, input_data: TodoWriteInput) -> Optional[ToolError]:
        if input_data.todos is None:
            return ToolValidationError("todos 不能为空")

        # 验证每个待办事项的结构
        for i, todo in enumerate(input_data.todos):
            if not isinstance(todo, dict):
                return ToolValidationError(f"待办事项[{i}] 必须是字典类型")

            if "id" not in todo:
                return ToolValidationError(f"待办事项[{i}] 缺少 'id' 字段")

            if "content" not in todo:
                return ToolValidationError(f"待办事项[{i}] 缺少 'content' 字段")

            # 验证状态值
            status = todo.get("status")
            if status and status not in [s.value for s in TodoStatus]:
                return ToolValidationError(
                    f"待办事项[{i}] 无效的状态值 '{status}'，必须是: {[s.value for s in TodoStatus]}"
                )

            # 验证优先级值
            priority = todo.get("priority")
            if priority and priority not in [p.value for p in TodoPriority]:
                return ToolValidationError(
                    f"待办事项[{i}] 无效的优先级值 '{priority}'，必须是: {[p.value for p in TodoPriority]}"
                )

        return None

    async def execute(self, input_data: TodoWriteInput) -> ToolResult:
        import datetime

        storage_key = input_data.storage_key or "default"

        try:
            # 加载旧的待办事项
            old_todos = await self._load_todos(storage_key)

            # 处理新的待办事项
            new_todos = []
            now = datetime.datetime.now().isoformat()

            for todo in input_data.todos:
                todo_item = {
                    "id": str(todo["id"]),
                    "content": str(todo["content"]),
                    "status": todo.get("status", TodoStatus.PENDING.value),
                    "priority": todo.get("priority", TodoPriority.MEDIUM.value),
                    "updated_at": now,
                }

                # 保留创建时间（如果是已有事项）
                old_todo = next((t for t in old_todos if t["id"] == todo_item["id"]), None)
                if old_todo and "created_at" in old_todo:
                    todo_item["created_at"] = old_todo["created_at"]
                else:
                    todo_item["created_at"] = now

                new_todos.append(todo_item)

            # 保存新的待办事项
            await self._save_todos(storage_key, new_todos)

            # 统计信息
            status_counts = {}
            for todo in new_todos:
                status = todo["status"]
                status_counts[status] = status_counts.get(status, 0) + 1

            return ToolResult.ok(
                data={
                    "old_todos": old_todos,
                    "new_todos": new_todos,
                },
                message=f"成功更新待办事项: 共 {len(new_todos)} 项 ({status_counts.get('completed', 0)} 已完成)",
                metadata={
                    "total_count": len(new_todos),
                    "status_counts": status_counts,
                    "storage_key": storage_key,
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"更新待办事项失败: {str(e)}")
            )

    def is_destructive(self) -> bool:
        return True
