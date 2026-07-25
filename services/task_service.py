"""Legacy REST adapter over the authoritative durable task repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from harness import SessionHarnessFactory
from models import Conversation as LegacyConversation
from models import Task as LegacyTask
from schemas import TaskClaimResponse, TaskCreate, TaskResponse, TaskUpdate
from state_core import (
    NewTask,
    SessionRuntimeFactory,
    SQLAlchemyStateStore,
    TaskMutation,
    TaskStatus,
    migrate_legacy_global_tasks,
    migrate_legacy_session,
)

_CONVERSATION_NAMESPACE = "api.conversation"
TaskKey = tuple[str, str, str]


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
        self._harnesses = SessionHarnessFactory(
            SessionRuntimeFactory(self._store), workspace_root=Path.cwd()
        )

    def _conversation_deleted(self, session_id: str) -> bool:
        if session_id == "global":
            return False
        metadata = self._store.metadata.get(session_id, _CONVERSATION_NAMESPACE)
        return metadata is not None and bool(metadata.snapshot.get("deleted"))

    def _ensure_runtime(self, session_id: str, *, create: bool = False):
        if self._conversation_deleted(session_id):
            return None
        state = self._store.states.load_session(session_id)
        if session_id == "global":
            has_legacy_tasks = self.db.scalar(
                select(LegacyTask.id)
                .where(LegacyTask.conversation_id.is_(None))
                .limit(1)
            ) is not None
            if state is not None or has_legacy_tasks or create:
                return migrate_legacy_global_tasks(self._factory)
            return None
        if state is not None:
            return self._harnesses.resume(session_id).session_runtime
        if self.db.get(LegacyConversation, session_id) is not None:
            return migrate_legacy_session(
                session_id,
                self._factory,
                plan_root=Path.cwd(),
            )
        if create:
            return self._harnesses.create(session_id).session_runtime
        return None

    def _runtime(self, session_id: str):
        runtime = self._ensure_runtime(session_id, create=True)
        assert runtime is not None
        return runtime

    def _scopes(self, conversation_id: str | None = None) -> list[tuple[str, str]]:
        if conversation_id is not None:
            runtime = self._ensure_runtime(conversation_id)
            return [] if runtime is None else [(conversation_id, runtime.task_list_id)]

        legacy_conversation_ids = self.db.scalars(
            select(LegacyTask.conversation_id).distinct()
        ).all()
        for session_id in legacy_conversation_ids:
            self._ensure_runtime(session_id or "global")

        return sorted(
            {
                (state.session_id, state.task_list_id or state.session_id)
                for state in self._store.states.list_sessions()
                if not self._conversation_deleted(state.session_id)
            }
        )

    def _task_catalog(self) -> dict[TaskKey, Any]:
        return {
            (session_id, task_list_id, task.id): task
            for session_id, task_list_id in self._scopes()
            for task in self._store.tasks.list(task_list_id)
        }

    @staticmethod
    def _stable_id(task: Any) -> str | None:
        stable_id = (
            task.metadata.get("legacyId")
            or task.metadata.get("apiTaskId")
            or task.metadata.get("apiPublicId")
        )
        return str(stable_id) if stable_id is not None else None

    def _public_index(
        self,
        catalog: dict[TaskKey, Any] | None = None,
    ) -> dict[TaskKey, str]:
        catalog = catalog if catalog is not None else self._task_catalog()
        public_ids: dict[TaskKey, str] = {}
        claimed: set[str] = set()

        for key in sorted(catalog):
            stable_id = self._stable_id(catalog[key])
            if stable_id is not None:
                public_ids[key] = stable_id
                claimed.add(stable_id)

        for key in sorted(catalog):
            if key in public_ids:
                continue
            session_id, _, internal_id = key
            candidate = f"{session_id}:{internal_id}"
            if candidate in claimed:
                base = f"runtime:{session_id}:{internal_id}"
                candidate = base
                suffix = 2
                while candidate in claimed:
                    candidate = f"runtime:{suffix}:{session_id}:{internal_id}"
                    suffix += 1
            updated = self._runtime(session_id).update_task(
                internal_id,
                TaskMutation(metadata={"apiPublicId": candidate}),
            )
            if updated is not None:
                catalog[key] = updated
            public_ids[key] = candidate
            claimed.add(candidate)
        return public_ids

    def _public_id(
        self,
        session_id: str,
        task_list_id: str,
        task: Any,
        *,
        public_ids: dict[TaskKey, str] | None = None,
    ) -> str:
        index = public_ids if public_ids is not None else self._public_index()
        return index[(session_id, task_list_id, task.id)]

    def _locate_in_scope(
        self,
        session_id: str,
        task_list_id: str,
        public_task_id: str,
        *,
        catalog: dict[TaskKey, Any] | None = None,
        public_ids: dict[TaskKey, str] | None = None,
    ):
        catalog = catalog if catalog is not None else self._task_catalog()
        public_ids = public_ids if public_ids is not None else self._public_index(catalog)
        matches = [
            task
            for key, task in catalog.items()
            if key[:2] == (session_id, task_list_id)
            and public_ids[key] == public_task_id
        ]
        return matches[0] if len(matches) == 1 else None

    def _locate(self, task_id: str) -> tuple[str, str, Any] | None:
        catalog = self._task_catalog()
        public_ids = self._public_index(catalog)
        matches = [key for key, public_id in public_ids.items() if public_id == task_id]
        if len(matches) != 1:
            return None
        session_id, task_list_id, internal_id = matches[0]
        return session_id, task_list_id, catalog[(session_id, task_list_id, internal_id)]

    def _internal_ids(
        self,
        session_id: str,
        task_list_id: str,
        task_ids: list[str],
    ) -> list[str]:
        catalog = self._task_catalog()
        public_ids = self._public_index(catalog)
        return [
            (target.id if target is not None else task_id)
            for task_id in task_ids
            for target in [
                self._locate_in_scope(
                    session_id,
                    task_list_id,
                    task_id,
                    catalog=catalog,
                    public_ids=public_ids,
                )
            ]
        ]

    def _relation_ids(
        self,
        session_id: str,
        task_list_id: str,
        task_ids: list[str],
        *,
        public_ids: dict[TaskKey, str] | None = None,
    ) -> list[str]:
        public_ids = public_ids if public_ids is not None else self._public_index()
        return [
            public_ids.get((session_id, task_list_id, task_id), task_id)
            for task_id in task_ids
        ]

    def _view(
        self,
        session_id: str,
        task_list_id: str,
        task: Any,
        *,
        public_ids: dict[TaskKey, str] | None = None,
    ) -> TaskView:
        public_ids = public_ids if public_ids is not None else self._public_index()
        now = datetime.now(timezone.utc)
        return TaskView(
            id=self._public_id(
                session_id, task_list_id, task, public_ids=public_ids
            ),
            conversation_id=None if session_id == "global" else session_id,
            subject=task.subject,
            description=task.description,
            active_form=task.active_form,
            owner=task.owner,
            status=task.status.value,
            blocks=self._relation_ids(
                session_id,
                task_list_id,
                list(task.blocks),
                public_ids=public_ids,
            ),
            blocked_by=self._relation_ids(
                session_id,
                task_list_id,
                list(task.blocked_by),
                public_ids=public_ids,
            ),
            meta=dict(task.metadata),
            created_at=now,
            updated_at=now,
        )

    def create_task(self, task_data: TaskCreate) -> TaskView:
        session_id = task_data.conversation_id or "global"
        runtime = self._runtime(session_id)
        occupied_ids = set(self._public_index().values())
        api_task_id = str(uuid4())
        while api_task_id in occupied_ids:
            api_task_id = str(uuid4())
        metadata = {
            key: value
            for key, value in task_data.meta.items()
            if key not in {"apiPublicId", "apiTaskId", "legacyId"}
        }
        metadata["apiTaskId"] = api_task_id
        task = runtime.create_task(
            NewTask(
                subject=task_data.subject,
                description=task_data.description,
                active_form=task_data.active_form,
                metadata=metadata,
            )
        )
        mutation = TaskMutation(
            owner=task_data.owner,
            status=(
                TaskStatus(task_data.status.value)
                if task_data.status.value != TaskStatus.PENDING.value
                else None
            ),
            add_blocks=self._internal_ids(
                session_id, runtime.task_list_id, list(task_data.blocks)
            ),
            add_blocked_by=self._internal_ids(
                session_id, runtime.task_list_id, list(task_data.blocked_by)
            ),
        )
        if any([mutation.owner, mutation.status, mutation.add_blocks, mutation.add_blocked_by]):
            task = runtime.update_task(task.id, mutation) or task
        return self._view(session_id, runtime.task_list_id, task)

    def get_task(self, task_id: str) -> TaskView | None:
        located = self._locate(task_id)
        return self._view(*located) if located is not None else None

    def list_tasks(
        self,
        conversation_id: str | None = None,
        status: str | None = None,
        owner: str | None = None,
    ) -> list[TaskView]:
        catalog = self._task_catalog()
        public_ids = self._public_index(catalog)
        views = [
            self._view(
                session_id,
                task_list_id,
                task,
                public_ids=public_ids,
            )
            for (session_id, task_list_id, _), task in catalog.items()
            if conversation_id is None or session_id == conversation_id
        ]
        if status is not None:
            views = [task for task in views if task.status == status]
        if owner is not None:
            views = [task for task in views if task.owner == owner]
        return views

    def update_task(self, task_id: str, updates: TaskUpdate) -> TaskView | None:
        located = self._locate(task_id)
        if located is None:
            return None
        session_id, task_list_id, current = located
        data = updates.model_dump(exclude_unset=True)
        desired_blocks = list(current.blocks)
        if "blocks" in data and data["blocks"] is not None:
            desired_blocks = self._internal_ids(
                session_id, task_list_id, list(data["blocks"])
            )
        desired_blocked_by = list(current.blocked_by)
        if "blocked_by" in data and data["blocked_by"] is not None:
            desired_blocked_by = self._internal_ids(
                session_id, task_list_id, list(data["blocked_by"])
            )
        metadata = data.get("meta")
        if metadata is not None:
            metadata = dict(metadata)
            for key in ("legacyId", "apiTaskId", "apiPublicId"):
                if current.metadata.get(key) is not None:
                    metadata[key] = current.metadata[key]
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
            metadata=metadata,
        )
        task = self._runtime(session_id).update_task(current.id, mutation)
        return self._view(session_id, task_list_id, task) if task is not None else None

    def delete_task(self, task_id: str) -> bool:
        located = self._locate(task_id)
        return located is not None and self._runtime(located[0]).delete_task(located[2].id)

    def claim_task(
        self, task_id: str, agent_id: str, check_agent_busy: bool = False
    ) -> TaskClaimResponse:
        located = self._locate(task_id)
        if located is None:
            return TaskClaimResponse(success=False, reason="task_not_found")
        session_id, task_list_id, current = located
        if check_agent_busy:
            busy = [
                task.id
                for task in self.list_tasks(session_id, owner=agent_id)
                if task.status != TaskStatus.COMPLETED.value and task.id != task_id
            ]
            if busy:
                return TaskClaimResponse(success=False, reason="agent_busy", busy_with_tasks=busy)
        result = self._runtime(session_id).claim_task(current.id, agent_id)
        view = (
            self._view(session_id, task_list_id, result.task)
            if result.task is not None
            else None
        )
        return TaskClaimResponse(
            success=result.success,
            reason=result.reason,
            task=TaskResponse.model_validate(view) if view is not None else None,
            blocked_by_tasks=(
                list(view.blocked_by)
                if result.reason == "blocked" and view is not None
                else None
            ),
        )

    def unassign_task(self, task_id: str) -> TaskView | None:
        located = self._locate(task_id)
        if located is None:
            return None
        session_id, task_list_id, current = located
        updated = self._runtime(session_id).unassign_task(current.id)
        return (
            self._view(session_id, task_list_id, updated)
            if updated is not None
            else None
        )

    def block_task(self, from_task_id: str, to_task_id: str) -> bool:
        located = self._locate(from_task_id)
        target = self._locate(to_task_id)
        if located is None or target is None or located[:2] != target[:2]:
            return False
        return (
            self._runtime(located[0]).update_task(
                located[2].id, TaskMutation(add_blocks=[target[2].id])
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
