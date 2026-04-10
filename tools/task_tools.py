"""
任务管理工具模块
提供 TaskCreateTool、TaskUpdateTool、TaskGetTool、TaskListTool 等任务管理功能
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import logging

from .base import Tool, ToolResult, ToolError, register_tool
from models import TaskStatus
from services.task_service import TaskService
from schemas import TaskCreate, TaskUpdate

logger = logging.getLogger(__name__)


# ============================================================================
# Database-based Task Tools (New - using TaskService)
# ============================================================================

@dataclass
class TaskCreateInput:
    """创建数据库任务的输入参数"""
    subject: str
    description: str
    active_form: Optional[str] = None
    conversation_id: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


@dataclass
class TaskUpdateInput:
    """更新数据库任务的输入参数"""
    task_id: str
    subject: Optional[str] = None
    description: Optional[str] = None
    active_form: Optional[str] = None
    status: Optional[str] = None  # 'pending', 'in_progress', 'completed'
    owner: Optional[str] = None
    add_blocks: Optional[List[str]] = field(default_factory=list)
    add_blocked_by: Optional[List[str]] = field(default_factory=list)
    meta: Optional[Dict[str, Any]] = None


@dataclass
class TaskGetInput:
    """获取数据库任务的输入参数"""
    task_id: str


@dataclass
class TaskListInput:
    """列出数据库任务的输入参数"""
    conversation_id: Optional[str] = None
    status: Optional[str] = None
    owner: Optional[str] = None


@register_tool
class TaskCreateTool(Tool[TaskCreateInput, Dict[str, Any]]):
    """
    创建任务工具

    在任务列表中创建一个新任务。任务用于跟踪需要完成的工作项。
    """

    name = "TaskCreate"
    description = "Create a new task in the task list"
    input_type = TaskCreateInput

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Brief title for the task"},
                "description": {"type": "string", "description": "What needs to be done"},
                "active_form": {"type": "string", "description": "Present continuous form for spinner (e.g., 'Running tests')"},
                "conversation_id": {"type": "string", "description": "Optional conversation ID to associate with"},
                "meta": {"type": "object", "description": "Optional metadata"}
            },
            "required": ["subject", "description"]
        }

    def get_output_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "subject": {"type": "string"}
                    }
                }
            }
        }

    async def execute(self, input_data: TaskCreateInput) -> ToolResult:
        """执行创建任务操作"""
        try:
            from models import SessionLocal
            db = SessionLocal()
            try:
                service = TaskService(db)
                task = service.create_task(TaskCreate(
                    subject=input_data.subject,
                    description=input_data.description,
                    active_form=input_data.active_form,
                    conversation_id=input_data.conversation_id,
                    meta=input_data.meta or {}
                ))

                return ToolResult(
                    success=True,
                    data={"task": {"id": task.id, "subject": task.subject}},
                    message=f"Task '{task.subject}' created with ID {task.id}"
                )
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Failed to create task: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to create task: {str(e)}"
            )


@register_tool
class TaskUpdateTool(Tool[TaskUpdateInput, Dict[str, Any]]):
    """
    更新任务工具

    更新现有任务的字段，包括状态、负责人、依赖关系等。
    特殊状态 'deleted' 用于删除任务。
    """

    name = "TaskUpdate"
    description = "Update an existing task"
    input_type = TaskUpdateInput

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The ID of the task to update"},
                "subject": {"type": "string", "description": "New subject for the task"},
                "description": {"type": "string", "description": "New description for the task"},
                "active_form": {"type": "string", "description": "Present continuous form for spinner"},
                "status": {"type": "string", "description": "New status: 'pending', 'in_progress', 'completed', or 'deleted'"},
                "owner": {"type": "string", "description": "New owner for the task"},
                "add_blocks": {"type": "array", "items": {"type": "string"}, "description": "Task IDs that this task blocks"},
                "add_blocked_by": {"type": "array", "items": {"type": "string"}, "description": "Task IDs that block this task"},
                "meta": {"type": "object", "description": "Metadata to merge"}
            },
            "required": ["task_id"]
        }

    def get_output_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "task_id": {"type": "string"},
                "updated_fields": {"type": "array", "items": {"type": "string"}},
                "error": {"type": "string"},
                "status_change": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string"},
                        "to": {"type": "string"}
                    }
                }
            }
        }

    async def execute(self, input_data: TaskUpdateInput) -> ToolResult:
        """执行更新任务操作"""
        try:
            from models import SessionLocal
            db = SessionLocal()
            try:
                service = TaskService(db)

                # Check if task exists
                existing = service.get_task(input_data.task_id)
                if not existing:
                    return ToolResult(
                        success=False,
                        error=f"Task {input_data.task_id} not found"
                    )

                old_status = existing.status

                # Handle delete
                if input_data.status == 'deleted':
                    service.delete_task(input_data.task_id)
                    return ToolResult(
                        success=True,
                        data={
                            "success": True,
                            "task_id": input_data.task_id,
                            "updated_fields": ["status"],
                            "status_change": {"from": old_status.value, "to": "deleted"}
                        },
                        message=f"Task {input_data.task_id} deleted"
                    )

                # Build updates
                updates = TaskUpdate()
                updated_fields = []

                if input_data.subject is not None:
                    updates.subject = input_data.subject
                    updated_fields.append("subject")
                if input_data.description is not None:
                    updates.description = input_data.description
                    updated_fields.append("description")
                if input_data.active_form is not None:
                    updates.active_form = input_data.active_form
                    updated_fields.append("active_form")
                if input_data.status is not None:
                    updates.status = input_data.status
                    updated_fields.append("status")
                if input_data.owner is not None:
                    updates.owner = input_data.owner
                    updated_fields.append("owner")
                if input_data.add_blocks:
                    updates.blocks = list(set((existing.blocks or []) + input_data.add_blocks))
                    updated_fields.append("blocks")
                if input_data.add_blocked_by:
                    updates.blocked_by = list(set((existing.blocked_by or []) + input_data.add_blocked_by))
                    updated_fields.append("blocked_by")
                if input_data.meta:
                    existing_meta = existing.meta or {}
                    existing_meta.update(input_data.meta)
                    updates.meta = existing_meta
                    updated_fields.append("meta")

                updated = service.update_task(input_data.task_id, updates)

                if not updated:
                    return ToolResult(
                        success=False,
                        error=f"Failed to update task {input_data.task_id}"
                    )

                result_data = {
                    "success": True,
                    "task_id": input_data.task_id,
                    "updated_fields": updated_fields
                }

                if "status" in updated_fields:
                    result_data["status_change"] = {
                        "from": old_status.value if hasattr(old_status, 'value') else str(old_status),
                        "to": updated.status.value if hasattr(updated.status, 'value') else str(updated.status)
                    }

                return ToolResult(
                    success=True,
                    data=result_data,
                    message=f"Task {input_data.task_id} updated"
                )
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Failed to update task: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to update task: {str(e)}"
            )


@register_tool
class TaskGetTool(Tool[TaskGetInput, Dict[str, Any]]):
    """
    获取任务工具

    通过 ID 检索任务的详细信息。
    """

    name = "TaskGet"
    description = "Retrieve a task by ID"
    input_type = TaskGetInput

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The ID of the task to retrieve"}
            },
            "required": ["task_id"]
        }

    def get_output_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "subject": {"type": "string"},
                        "description": {"type": "string"},
                        "status": {"type": "string"},
                        "blocks": {"type": "array", "items": {"type": "string"}},
                        "blocked_by": {"type": "array", "items": {"type": "string"}}
                    }
                }
            }
        }

    async def execute(self, input_data: TaskGetInput) -> ToolResult:
        """执行获取任务操作"""
        try:
            from models import SessionLocal
            db = SessionLocal()
            try:
                service = TaskService(db)
                task = service.get_task(input_data.task_id)

                if not task:
                    return ToolResult(
                        success=True,
                        data={"task": None},
                        message=f"Task {input_data.task_id} not found"
                    )

                return ToolResult(
                    success=True,
                    data={
                        "task": {
                            "id": task.id,
                            "subject": task.subject,
                            "description": task.description,
                            "status": task.status.value if hasattr(task.status, 'value') else str(task.status),
                            "blocks": task.blocks or [],
                            "blocked_by": task.blocked_by or []
                        }
                    }
                )
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Failed to get task: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to get task: {str(e)}"
            )


@register_tool
class TaskListTool(Tool[TaskListInput, Dict[str, Any]]):
    """
    列出任务工具

    列出所有任务，支持按状态、负责人、对话 ID 过滤。
    """

    name = "TaskList"
    description = "List all tasks with optional filters"
    input_type = TaskListInput

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Filter by conversation ID"},
                "status": {"type": "string", "description": "Filter by status: 'pending', 'in_progress', 'completed'"},
                "owner": {"type": "string", "description": "Filter by owner/agent ID"}
            }
        }

    def get_output_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "subject": {"type": "string"},
                            "description": {"type": "string"},
                            "status": {"type": "string"},
                            "owner": {"type": "string"},
                            "blocks": {"type": "array", "items": {"type": "string"}},
                            "blocked_by": {"type": "array", "items": {"type": "string"}}
                        }
                    }
                }
            }
        }

    async def execute(self, input_data: TaskListInput) -> ToolResult:
        """执行列出任务操作"""
        try:
            from models import SessionLocal
            db = SessionLocal()
            try:
                service = TaskService(db)
                tasks = service.list_tasks(
                    conversation_id=input_data.conversation_id,
                    status=input_data.status,
                    owner=input_data.owner
                )

                return ToolResult(
                    success=True,
                    data={
                        "tasks": [
                            {
                                "id": t.id,
                                "subject": t.subject,
                                "description": t.description,
                                "status": t.status.value if hasattr(t.status, 'value') else str(t.status),
                                "owner": t.owner,
                                "blocks": t.blocks or [],
                                "blocked_by": t.blocked_by or []
                            }
                            for t in tasks
                        ]
                    },
                    message=f"Found {len(tasks)} tasks"
                )
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Failed to list tasks: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to list tasks: {str(e)}"
            )
