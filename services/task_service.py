"""Legacy REST adapter over the authoritative durable task repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from schemas import TaskClaimResponse, TaskCreate, TaskResponse, TaskUpdate
from state_core import NewTask, SessionRuntime, SQLAlchemyStateStore, TaskMutation, TaskStatus
from state_core.sqlalchemy_store import RuntimeTask


@dataclass
class TaskView:
    id: str
    conversation_id: str | None
    subject: str
    description: str
    active_form: str | None
    owner: str | None
    status: str
    blocks: list[str]
    blocked_by: list[str]
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TaskService:
    def __init__(self, db: Session):
        self.db = db
        self._factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
        self._store = SQLAlchemyStateStore(self._factory)

    def _runtime(self, task_list_id: str) -> SessionRuntime:
        return SessionRuntime(task_list_id, self._store)

    def _locate(self, task_id: str) -> tuple[str, Any] | None:
        row = self.db.scalar(
            select(RuntimeTask)
            .where(RuntimeTask.task_id == task_id)
            .order_by(RuntimeTask.task_list_id)
            .limit(1)
        )
        if row is None:
            return None
        return row.task_list_id, self._store.tasks.get(row.task_list_id, task_id)

    @staticmethod
    def _view(task_list_id: str, task: Any) -> TaskView:
        now = datetime.now(timezone.utc)
        return TaskView(
            id=task.id,
            conversation_id=None if task_list_id == "global" else task_list_id,
            subject=task.subject,
            description=task.description,
            active_form=task.active_form,
            owner=task.owner,
            status=task.status.value,
            blocks=list(task.blocks),
            blocked_by=list(task.blocked_by),
            meta=dict(task.metadata),
            created_at=now,
            updated_at=now,
        )

    def create_task(self, task_data: TaskCreate) -> TaskView:
        task_list_id = task_data.conversation_id or "global"
        task = self._runtime(task_list_id).create_task(
            NewTask(
                subject=task_data.subject,
                description=task_data.description,
                active_form=task_data.active_form,
                metadata=task_data.meta,
            )
        )
        mutation = TaskMutation(
            owner=task_data.owner,
            status=(
                TaskStatus(task_data.status.value)
                if task_data.status.value != TaskStatus.PENDING.value
                else None
            ),
            add_blocks=list(task_data.blocks),
            add_blocked_by=list(task_data.blocked_by),
        )
        if any([mutation.owner, mutation.status, mutation.add_blocks, mutation.add_blocked_by]):
            task = self._runtime(task_list_id).update_task(task.id, mutation) or task
        return self._view(task_list_id, task)

    def get_task(self, task_id: str) -> TaskView | None:
        located = self._locate(task_id)
        return self._view(*located) if located is not None else None

    def list_tasks(
        self,
        conversation_id: str | None = None,
        status: str | None = None,
        owner: str | None = None,
    ) -> list[TaskView]:
        rows = self.db.scalars(
            select(RuntimeTask).where(
                RuntimeTask.task_list_id == (conversation_id or RuntimeTask.task_list_id)
            )
        ).all()
        views = [
            self._view(row.task_list_id, self._store.tasks.get(row.task_list_id, row.task_id))
            for row in rows
        ]
        if status is not None:
            views = [task for task in views if task.status == status]
        if owner is not None:
            views = [task for task in views if task.owner == owner]
        return sorted(views, key=lambda task: (task.conversation_id or "", int(task.id)))

    def update_task(self, task_id: str, updates: TaskUpdate) -> TaskView | None:
        located = self._locate(task_id)
        if located is None:
            return None
        task_list_id, current = located
        data = updates.model_dump(exclude_unset=True)
        desired_blocks = (
            data["blocks"] if "blocks" in data and data["blocks"] is not None else current.blocks
        )
        desired_blocked_by = (
            data["blocked_by"]
            if "blocked_by" in data and data["blocked_by"] is not None
            else current.blocked_by
        )
        mutation = TaskMutation(
            subject=data.get("subject"),
            description=data.get("description"),
            active_form=data.get("active_form"),
            owner=data.get("owner"),
            status=TaskStatus(data["status"].value) if data.get("status") else None,
            add_blocks=[item for item in desired_blocks if item not in current.blocks],
            remove_blocks=[item for item in current.blocks if item not in desired_blocks],
            add_blocked_by=[item for item in desired_blocked_by if item not in current.blocked_by],
            remove_blocked_by=[
                item for item in current.blocked_by if item not in desired_blocked_by
            ],
            metadata=data.get("meta"),
        )
        task = self._runtime(task_list_id).update_task(task_id, mutation)
        return self._view(task_list_id, task) if task is not None else None

    def delete_task(self, task_id: str) -> bool:
        located = self._locate(task_id)
        return located is not None and self._runtime(located[0]).delete_task(task_id)

    def claim_task(
        self, task_id: str, agent_id: str, check_agent_busy: bool = False
    ) -> TaskClaimResponse:
        located = self._locate(task_id)
        if located is None:
            return TaskClaimResponse(success=False, reason="task_not_found")
        task_list_id, _ = located
        if check_agent_busy:
            busy = [
                task.id
                for task in self.list_tasks(task_list_id, owner=agent_id)
                if task.status != TaskStatus.COMPLETED.value and task.id != task_id
            ]
            if busy:
                return TaskClaimResponse(success=False, reason="agent_busy", busy_with_tasks=busy)
        result = self._runtime(task_list_id).claim_task(task_id, agent_id)
        view = self._view(task_list_id, result.task) if result.task is not None else None
        return TaskClaimResponse(
            success=result.success,
            reason=result.reason,
            task=TaskResponse.model_validate(view) if view is not None else None,
            blocked_by_tasks=(
                list(result.task.blocked_by)
                if result.reason == "blocked" and result.task is not None
                else None
            ),
        )

    def unassign_task(self, task_id: str) -> TaskView | None:
        located = self._locate(task_id)
        if located is None:
            return None
        task_list_id, _ = located
        updated = self._runtime(task_list_id).unassign_task(task_id)
        return self._view(task_list_id, updated) if updated is not None else None

    def block_task(self, from_task_id: str, to_task_id: str) -> bool:
        located = self._locate(from_task_id)
        target = self._locate(to_task_id)
        if located is None or target is None or located[0] != target[0]:
            return False
        return (
            self._runtime(located[0]).update_task(
                from_task_id, TaskMutation(add_blocks=[to_task_id])
            )
            is not None
        )

    def get_agent_statuses(self) -> list[dict[str, Any]]:
        owners: dict[str, list[str]] = {}
        for task in self.list_tasks():
            if task.owner and task.status != TaskStatus.COMPLETED.value:
                owners.setdefault(task.owner, []).append(task.id)
        return [
            {"agent_id": owner, "name": owner, "status": "busy", "current_tasks": ids}
            for owner, ids in owners.items()
        ]

    def get_next_available_task(self, agent_id: str) -> TaskView | None:
        return next(
            (
                task
                for task in self.list_tasks()
                if task.status == TaskStatus.PENDING.value and not task.blocked_by
            ),
            None,
        )
