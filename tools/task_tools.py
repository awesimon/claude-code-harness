"""Node-compatible Task V2 tools backed only by :class:`SessionRuntime`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, cast

from state_core import NewTask, SessionRuntime, TaskMutation, TaskStatus

from .base import (
    Tool,
    ToolError,
    ToolResult,
    ToolValidationError,
    get_active_tool_context,
    register_tool,
)


@dataclass
class TaskCreateInput:
    subject: str
    description: str
    active_form: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class TaskGetInput:
    task_id: str


@dataclass
class TaskListInput:
    pass


@dataclass
class TaskUpdateInput:
    task_id: str
    subject: str | None = None
    description: str | None = None
    active_form: str | None = None
    status: str | None = None
    owner: str | None = None
    add_blocks: list[str] = field(default_factory=list)
    add_blocked_by: list[str] = field(default_factory=list)
    metadata: dict[str, Any] | None = None


_CREATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "A brief title for the task"},
        "description": {"type": "string", "description": "What needs to be done"},
        "activeForm": {
            "type": "string",
            "description": "Present continuous form shown while the task is in progress",
        },
        "metadata": {
            "type": "object",
            "description": "Arbitrary metadata to attach to the task",
        },
    },
    "required": ["subject", "description"],
    "additionalProperties": False,
}

_GET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"taskId": {"type": "string", "description": "The task ID"}},
    "required": ["taskId"],
    "additionalProperties": False,
}

_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_UPDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "taskId": {"type": "string", "description": "The task ID"},
        "subject": {"type": "string"},
        "description": {"type": "string"},
        "activeForm": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["pending", "in_progress", "completed", "deleted"],
        },
        "owner": {"type": "string"},
        "addBlocks": {"type": "array", "items": {"type": "string"}},
        "addBlockedBy": {"type": "array", "items": {"type": "string"}},
        "metadata": {
            "type": "object",
            "description": "Metadata keys to merge; null deletes a key",
        },
    },
    "required": ["taskId"],
    "additionalProperties": False,
}


def _context_values(context: Any | None) -> Mapping[str, Any]:
    if context is None:
        return cast(Mapping[str, Any], get_active_tool_context())
    if isinstance(context, Mapping):
        return cast(Mapping[str, Any], context)
    metadata = getattr(context, "metadata", None)
    return cast(Mapping[str, Any], metadata) if isinstance(metadata, Mapping) else {}


def _runtime(context: Any | None = None) -> SessionRuntime | None:
    runtime = _context_values(context).get("session_runtime")
    return runtime if isinstance(runtime, SessionRuntime) else None


def _normalize_aliases(input_data: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(input_data)
    for node_name, python_name in (
        ("taskId", "task_id"),
        ("activeForm", "active_form"),
        ("addBlocks", "add_blocks"),
        ("addBlockedBy", "add_blocked_by"),
    ):
        if node_name not in normalized:
            continue
        if python_name in normalized:
            raise TypeError(f"provide only one of {node_name!r} and {python_name!r}")
        normalized[python_name] = normalized.pop(node_name)
    return normalized


def _get_wire(task: Any) -> dict[str, Any]:
    return {
        "id": task.id,
        "subject": task.subject,
        "description": task.description,
        "status": task.status.value,
        "blocks": list(task.blocks),
        "blockedBy": list(task.blocked_by),
    }


def _list_wire(task: Any, resolved: set[str]) -> dict[str, Any]:
    wire = {
        "id": task.id,
        "subject": task.subject,
        "status": task.status.value,
        "owner": task.owner,
        "blockedBy": [task_id for task_id in task.blocked_by if task_id not in resolved],
    }
    return wire


def _has_mutation(mutation: TaskMutation) -> bool:
    return any(
        value is not None
        for value in (
            mutation.subject,
            mutation.description,
            mutation.active_form,
            mutation.status,
            mutation.owner,
            mutation.metadata,
        )
    ) or any(
        (
            mutation.add_blocks,
            mutation.add_blocked_by,
            mutation.remove_blocks,
            mutation.remove_blocked_by,
        )
    )


class _TaskTool(Tool[Any, dict[str, Any]]):
    should_defer = True
    is_concurrency_safe = True

    async def run(
        self,
        input_data: Any,
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        if isinstance(input_data, Mapping):
            try:
                input_data = _normalize_aliases(input_data)
            except (TypeError, ValueError) as exc:
                return ToolResult.fail(ToolValidationError(f"Invalid input data: {exc}"))
        return await super().run(input_data, context)

    def is_enabled(self, context: Any | None = None) -> bool:
        runtime = _runtime(context)
        return runtime is not None and runtime.task_mode.value == "task_v2"

    def _require_runtime(self) -> SessionRuntime:
        runtime = _runtime()
        if runtime is None:
            raise ToolValidationError("session_runtime is required for Task V2 tools")
        if runtime.task_mode.value != "task_v2":
            raise ToolValidationError("Task V2 is disabled in TodoWrite compatibility mode")
        return runtime


@register_tool
class TaskCreateTool(_TaskTool):
    name = "TaskCreate"
    description = "Create a task in the current session task list"
    input_type = TaskCreateInput

    def get_input_schema(self) -> dict[str, Any]:
        return _CREATE_SCHEMA

    async def validate(self, input_data: TaskCreateInput) -> ToolError | None:
        if not input_data.subject or not input_data.description:
            return ToolValidationError("subject and description are required")
        return None

    async def execute(self, input_data: TaskCreateInput) -> ToolResult:
        try:
            task = self._require_runtime().create_task(
                NewTask(
                    subject=input_data.subject,
                    description=input_data.description,
                    active_form=input_data.active_form,
                    metadata=input_data.metadata or {},
                )
            )
            return ToolResult.ok({"task": {"id": task.id, "subject": task.subject}})
        except Exception as exc:
            return ToolResult.fail(exc)


@register_tool
class TaskGetTool(_TaskTool):
    name = "TaskGet"
    description = "Retrieve a task by ID"
    input_type = TaskGetInput

    def get_input_schema(self) -> dict[str, Any]:
        return _GET_SCHEMA

    def is_read_only(self) -> bool:
        return True

    async def execute(self, input_data: TaskGetInput) -> ToolResult:
        try:
            task = self._require_runtime().get_task(input_data.task_id)
            return ToolResult.ok({"task": _get_wire(task) if task is not None else None})
        except Exception as exc:
            return ToolResult.fail(exc)


@register_tool
class TaskListTool(_TaskTool):
    name = "TaskList"
    description = "List all tasks in the current session"
    input_type = TaskListInput

    def get_input_schema(self) -> dict[str, Any]:
        return _LIST_SCHEMA

    def is_read_only(self) -> bool:
        return True

    async def execute(self, input_data: TaskListInput) -> ToolResult:
        try:
            tasks = [
                task
                for task in self._require_runtime().list_tasks()
                if not task.metadata.get("_internal")
            ]
            resolved = {task.id for task in tasks if task.status is TaskStatus.COMPLETED}
            return ToolResult.ok({"tasks": [_list_wire(task, resolved) for task in tasks]})
        except Exception as exc:
            return ToolResult.fail(exc)


@register_tool
class TaskUpdateTool(_TaskTool):
    name = "TaskUpdate"
    description = "Update an existing task"
    input_type = TaskUpdateInput

    def get_input_schema(self) -> dict[str, Any]:
        return _UPDATE_SCHEMA

    async def validate(self, input_data: TaskUpdateInput) -> ToolError | None:
        if input_data.status is not None and input_data.status not in {
            "pending",
            "in_progress",
            "completed",
            "deleted",
        }:
            return ToolValidationError("status must be pending, in_progress, completed, or deleted")
        return None

    async def execute(self, input_data: TaskUpdateInput) -> ToolResult:
        try:
            runtime = self._require_runtime()
            existing = runtime.get_task(input_data.task_id)
            if existing is None:
                return ToolResult.ok(
                    {
                        "success": False,
                        "taskId": input_data.task_id,
                        "updatedFields": [],
                        "error": "Task not found",
                    }
                )

            if input_data.status == "deleted":
                deleted = runtime.delete_task(input_data.task_id)
                data: dict[str, Any] = {
                    "success": deleted,
                    "taskId": input_data.task_id,
                    "updatedFields": ["deleted"] if deleted else [],
                }
                if deleted:
                    data["statusChange"] = {
                        "from": existing.status.value,
                        "to": "deleted",
                    }
                else:
                    data["error"] = "Failed to delete task"
                return ToolResult.ok(data)

            fields: list[str] = []
            add_blocks = [
                task_id for task_id in input_data.add_blocks if task_id not in existing.blocks
            ]
            add_blocked_by = [
                task_id
                for task_id in input_data.add_blocked_by
                if task_id not in existing.blocked_by
            ]
            mutation = TaskMutation(
                subject=(
                    input_data.subject
                    if input_data.subject is not None and input_data.subject != existing.subject
                    else None
                ),
                description=(
                    input_data.description
                    if input_data.description is not None
                    and input_data.description != existing.description
                    else None
                ),
                active_form=(
                    input_data.active_form
                    if input_data.active_form is not None
                    and input_data.active_form != existing.active_form
                    else None
                ),
                owner=(
                    input_data.owner
                    if input_data.owner is not None and input_data.owner != existing.owner
                    else None
                ),
                status=(
                    TaskStatus(input_data.status)
                    if input_data.status is not None and input_data.status != existing.status.value
                    else None
                ),
                add_blocks=add_blocks,
                add_blocked_by=add_blocked_by,
                metadata=input_data.metadata,
            )
            for name, value in (
                ("subject", mutation.subject),
                ("description", mutation.description),
                ("activeForm", mutation.active_form),
                ("owner", mutation.owner),
                ("status", mutation.status),
            ):
                if value is not None:
                    fields.append(name)
            if add_blocks:
                fields.append("blocks")
            if add_blocked_by:
                fields.append("blockedBy")
            if mutation.metadata is not None:
                fields.append("metadata")

            if not fields:
                return ToolResult.ok(
                    {
                        "success": True,
                        "taskId": input_data.task_id,
                        "updatedFields": [],
                        "statusChange": None,
                    }
                )

            requested_status = mutation.status
            claim_transition = input_data.owner is not None and input_data.status == "in_progress"
            if (
                claim_transition
                and existing.owner is not None
                and existing.owner != input_data.owner
            ):
                return ToolResult.ok(
                    {
                        "success": False,
                        "taskId": input_data.task_id,
                        "updatedFields": [],
                        "error": f"Task already claimed by {existing.owner}",
                    }
                )

            claimed_task = None
            claim_owner = mutation.owner
            claim_requested = (
                claim_transition
                and claim_owner is not None
                and requested_status is TaskStatus.IN_PROGRESS
                and existing.owner is None
                and existing.status is TaskStatus.PENDING
            )
            if claim_requested:
                assert claim_owner is not None
                claim = runtime.claim_task(input_data.task_id, claim_owner)
                if not claim.success:
                    owner = f" by {claim.current_owner}" if claim.current_owner else ""
                    return ToolResult.ok(
                        {
                            "success": False,
                            "taskId": input_data.task_id,
                            "updatedFields": [],
                            "error": f"Task claim failed{owner}: {claim.reason}",
                        }
                    )
                claimed_task = claim.task
                mutation.owner = None
                mutation.status = None

            updated = (
                runtime.update_task(input_data.task_id, mutation)
                if _has_mutation(mutation)
                else claimed_task
            )
            if updated is None:
                return ToolResult.ok(
                    {
                        "success": False,
                        "taskId": input_data.task_id,
                        "updatedFields": [],
                        "error": "Task not found",
                    }
                )
            status_change = (
                {"from": existing.status.value, "to": requested_status.value}
                if requested_status is not None
                else None
            )
            return ToolResult.ok(
                {
                    "success": True,
                    "taskId": input_data.task_id,
                    "updatedFields": fields,
                    "statusChange": status_change,
                }
            )
        except Exception as exc:
            return ToolResult.fail(exc)


# Compatibility aliases refer to the canonical implementations; they are not
# separate registrations or alternate state owners.
task_create = TaskCreateTool
task_get = TaskGetTool
task_list = TaskListTool
task_update = TaskUpdateTool
