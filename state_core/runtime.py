"""Single authoritative runtime facade for session-scoped state."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .sqlalchemy_store import SQLAlchemyStateStore
from .types import (
    EventType,
    NewTask,
    PendingEventBatch,
    PendingSessionEvent,
    PlanState,
    SessionHealth,
    SessionSnapshot,
    SessionState,
    TaskItem,
    TaskMode,
    TaskMutation,
)


class SessionRuntime:
    """Owns one session's state and durable task list.

    Callers mutate through this object so task-v2, TodoWrite and plan mode do
    not accidentally maintain competing in-memory stores.
    """

    def __init__(self, session_id: str, store: SQLAlchemyStateStore) -> None:
        self.session_id = session_id
        self.store = store
        state = store.states.load_session(session_id)
        if state is None:
            state = SessionState.new(session_id)
            state.task_list_id = session_id
            store.states.create_session(state)
        elif state.task_list_id is None:
            state.task_list_id = session_id
            result = store.states.commit(
                state,
                PendingEventBatch(
                    session_id,
                    (self._event(EventType.TASK_MUTATION, {"action": "initialize"}),),
                ),
                expected_revision=state.revision,
            )
            state = result.state
        self.state = state

    @classmethod
    def recover(cls, session_id: str, store: SQLAlchemyStateStore) -> "SessionRuntime":
        """Restore durable state without replaying external tool work."""

        try:
            runtime = cls(session_id, store)
        except (KeyError, TypeError, ValueError):
            runtime = cls.__new__(cls)
            runtime.session_id = session_id
            runtime.store = store
            runtime.state = SessionState.new(session_id)
            runtime.state.health = SessionHealth.RECOVERY_REQUIRED
            return runtime
        state = runtime.state
        after_id = 0
        try:
            snapshot = store.states.latest_snapshot(session_id)
        except (KeyError, TypeError, ValueError):
            snapshot = None
        if snapshot is not None:
            state = snapshot.state
            after_id = snapshot.last_event_id

        events = store.states.list_events(session_id, after_id=after_id)
        seen = {
            event.id
            for event in store.states.list_events(session_id, after_id=0)
            if event.id <= after_id
        }
        valid = True
        for event in events:
            if event.parent_event_id is not None and event.parent_event_id not in seen:
                valid = False
                break
            seen.add(event.id)
            event_state = event.payload.get("state")
            if event_state is not None:
                try:
                    state = SessionState.from_dict(event_state)
                    state.revision += 1
                    state.last_event_id = event.id
                except (KeyError, TypeError, ValueError):
                    valid = False
                    break

        runtime.state = state
        if not valid:
            runtime.state.health = SessionHealth.RECOVERY_REQUIRED
            return runtime

        persisted = store.states.load_session(session_id)
        if persisted is not None:
            runtime.state = persisted

        interrupted = False
        agents = dict(runtime.state.agents)
        for agent_id, raw in agents.items():
            if not isinstance(raw, dict):
                continue
            if raw.get("status") in {"active", "in_progress", "running", "starting"}:
                agents[agent_id] = {**raw, "status": "interrupted"}
                interrupted = True
        if interrupted:
            runtime.state.agents = agents
            runtime.state.interrupted_at = datetime.now(timezone.utc)
            runtime._persist(
                EventType.EXECUTION_INTERRUPTED,
                {
                    "agents": [
                        key for key, value in agents.items() if value.get("status") == "interrupted"
                    ]
                },
            )
        return runtime

    @classmethod
    def from_session_factory(cls, session_id: str, session_factory: Any) -> "SessionRuntime":
        return cls(session_id, SQLAlchemyStateStore(session_factory))

    @property
    def task_list_id(self) -> str:
        return self.state.task_list_id or self.session_id

    @property
    def task_mode(self) -> TaskMode:
        return self.state.task_mode

    def _event(self, event_type: EventType, payload: Mapping[str, Any]) -> PendingSessionEvent:
        return PendingSessionEvent(
            sequence=0,
            session_id=self.session_id,
            event_type=event_type,
            payload=dict(payload),
        )

    def _persist(self, event_type: EventType, payload: Mapping[str, Any]) -> None:
        if self.state.health is SessionHealth.RECOVERY_REQUIRED:
            raise RuntimeError("session requires recovery before writes are allowed")
        event_payload = dict(payload)
        event_payload["state"] = self.state.to_dict()
        batch = PendingEventBatch(self.session_id, (self._event(event_type, event_payload),))
        result = self.store.states.commit(self.state, batch, expected_revision=self.state.revision)
        self.state = result.state

    def append_event(
        self,
        event_type: EventType,
        payload: Mapping[str, Any],
        *,
        parent_event_id: int | None = None,
    ) -> None:
        if self.state.health is SessionHealth.RECOVERY_REQUIRED:
            raise RuntimeError("session requires recovery before writes are allowed")
        self.state.transcript_cursor += 1
        event_payload = dict(payload)
        event_payload["state"] = self.state.to_dict()
        event = PendingSessionEvent(
            sequence=0,
            session_id=self.session_id,
            event_type=event_type,
            payload=event_payload,
            parent_event_id=parent_event_id,
        )
        result = self.store.states.commit(
            self.state,
            PendingEventBatch(self.session_id, (event,)),
            expected_revision=self.state.revision,
        )
        self.state = result.state

    def checkpoint(self) -> SessionSnapshot:
        snapshot = SessionSnapshot(
            session_id=self.session_id,
            last_event_id=self.state.last_event_id,
            state=self.state,
        )
        self.store.states.save_snapshot(snapshot)
        return snapshot

    def events(self, after_id: int = 0):
        return self.store.states.list_events(self.session_id, after_id=after_id)

    def enable_task_v2(self) -> None:
        if self.state.task_mode is TaskMode.TASK_V2:
            return
        self.state.task_mode = TaskMode.TASK_V2
        self.state.todos = {}
        self._persist(EventType.TASK_MUTATION, {"action": "enable_task_v2"})

    def enable_todo_v1(self) -> None:
        if self.state.task_mode is TaskMode.TODO_V1:
            return
        self.state.task_mode = TaskMode.TODO_V1
        self._persist(EventType.TODO_REPLACED, {"action": "enable_todo_v1"})

    def create_task(self, task: NewTask) -> TaskItem:
        if self.task_mode is not TaskMode.TASK_V2:
            raise RuntimeError("Task V2 is disabled while TodoWrite compatibility mode is active")
        item = self.store.tasks.create(self.task_list_id, task)
        self._persist(EventType.TASK_MUTATION, {"action": "create", "task": item.to_dict()})
        return item

    def get_task(self, task_id: str) -> TaskItem | None:
        if self.task_mode is not TaskMode.TASK_V2:
            return None
        return self.store.tasks.get(self.task_list_id, task_id)

    def list_tasks(self) -> list[TaskItem]:
        if self.task_mode is not TaskMode.TASK_V2:
            return []
        return self.store.tasks.list(self.task_list_id)

    def update_task(self, task_id: str, mutation: TaskMutation) -> TaskItem | None:
        if self.task_mode is not TaskMode.TASK_V2:
            return None
        item = self.store.tasks.update(self.task_list_id, task_id, mutation)
        if item is not None:
            self._persist(EventType.TASK_MUTATION, {"action": "update", "task": item.to_dict()})
        return item

    def claim_task(self, task_id: str, owner: str):
        if self.task_mode is not TaskMode.TASK_V2:
            raise RuntimeError("Task V2 is disabled while TodoWrite compatibility mode is active")
        result = self.store.tasks.claim(self.task_list_id, task_id, owner)
        if result.success and result.task is not None:
            self._persist(
                EventType.TASK_MUTATION,
                {"action": "claim", "task": result.task.to_dict()},
            )
        return result

    def unassign_task(self, task_id: str) -> TaskItem | None:
        if self.task_mode is not TaskMode.TASK_V2:
            return None
        task = self.store.tasks.unassign(self.task_list_id, task_id)
        if task is not None:
            self._persist(
                EventType.TASK_MUTATION,
                {"action": "unassign", "task": task.to_dict()},
            )
        return task

    def delete_task(self, task_id: str) -> bool:
        if self.task_mode is not TaskMode.TASK_V2:
            return False
        deleted = self.store.tasks.delete(self.task_list_id, task_id)
        if deleted:
            self._persist(EventType.TASK_MUTATION, {"action": "delete", "taskId": task_id})
        return deleted

    def replace_todos(
        self, todos: list[dict[str, Any]], scope: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if self.task_mode is not TaskMode.TODO_V1:
            raise RuntimeError("TodoWrite compatibility mode is disabled while Task V2 is active")
        old = [dict(item) for item in self.state.todos.get(scope, [])]
        submitted = [dict(item) for item in todos]
        new = (
            []
            if submitted and all(item.get("status") == "completed" for item in submitted)
            else submitted
        )
        self.state.todos[scope] = new
        self._persist(EventType.TODO_REPLACED, {"scope": scope, "todos": submitted})
        return old, submitted

    def update_agent_lifecycle(self, agent_id: str, status: str, **details: Any) -> None:
        agents = dict(self.state.agents)
        previous = agents.get(agent_id)
        agents[agent_id] = {
            **(previous if isinstance(previous, dict) else {}),
            **details,
            "status": status,
        }
        self.state.agents = agents
        self._persist(
            EventType.AGENT_LIFECYCLE,
            {"agentId": agent_id, "status": status, **details},
        )

    def enter_plan(self, permission_mode: str) -> None:
        if self.state.plan.state is not PlanState.IDLE:
            raise RuntimeError("session is already in plan mode")
        self.state.pre_plan_permission_mode = permission_mode
        self.state.permission_mode = "plan"
        self.state.plan.transition_to(PlanState.PLANNING)
        self._persist(EventType.PLAN_TRANSITION, {"from": "idle", "to": "planning"})

    def save_plan_draft(
        self,
        content: str,
        file_path: str,
        allowed_prompts: list[dict[str, str]] | None = None,
    ) -> None:
        self.state.plan.slug = self.state.plan.slug or plan_slug(self.session_id)
        self.state.plan.file_path = file_path
        if allowed_prompts is not None:
            self.state.plan.allowed_prompts = [dict(item) for item in allowed_prompts]
        self._persist(
            EventType.PLAN_TRANSITION,
            {"action": "save_draft", "content": content},
        )

    def submit_plan(
        self,
        content: str,
        allowed_prompts: list[dict[str, str]],
        *,
        file_path: str | None = None,
    ) -> None:
        if self.state.plan.state is not PlanState.PLANNING:
            raise RuntimeError("plan is not being drafted")
        if not content.strip():
            raise ValueError("plan content must not be empty")
        slug = self.state.plan.slug or plan_slug(self.session_id)
        self.state.plan.slug = slug
        self.state.plan.file_path = file_path or str(Path("plans") / f"{slug}.md")
        self.state.plan.allowed_prompts = [dict(item) for item in allowed_prompts]
        self.state.plan.submitted_at = datetime.now(timezone.utc)
        self.state.plan.transition_to(PlanState.PENDING_APPROVAL)
        self._persist(
            EventType.PLAN_TRANSITION,
            {"from": "planning", "to": "pending_approval", "content": content},
        )

    def approve_plan(self, approver: str = "user") -> None:
        if self.state.plan.state is not PlanState.PENDING_APPROVAL:
            raise RuntimeError("plan is not pending approval")
        self.state.plan.approved_by = approver
        self.state.plan.approved_at = datetime.now(timezone.utc)
        self.state.plan.transition_to(PlanState.APPROVED)
        self._persist(EventType.PLAN_TRANSITION, {"from": "pending_approval", "to": "approved"})

    def reject_plan(self) -> None:
        if self.state.plan.state is not PlanState.PENDING_APPROVAL:
            raise RuntimeError("plan is not pending approval")
        self.state.plan.transition_to(PlanState.PLANNING)
        self._persist(EventType.PLAN_TRANSITION, {"from": "pending_approval", "to": "planning"})

    def exit_plan(self) -> None:
        if self.state.plan.state is not PlanState.APPROVED:
            raise RuntimeError("plan must be approved before exit")
        self.state.plan.transition_to(PlanState.IDLE)
        self.state.permission_mode = self.state.pre_plan_permission_mode or "default"
        self.state.pre_plan_permission_mode = None
        self._persist(EventType.PLAN_TRANSITION, {"from": "approved", "to": "idle"})


def plan_slug(value: str) -> str:
    value = "-".join(value.lower().split())
    return (
        "".join(char if char.isalnum() or char == "-" else "-" for char in value).strip("-")
        or "plan"
    )


class SessionRuntimeFactory:
    """Creates and resumes runtime handles over one durable store."""

    def __init__(self, store: SQLAlchemyStateStore) -> None:
        self.store = store

    def create(self, session_id: str) -> SessionRuntime:
        return SessionRuntime(session_id, self.store)

    def resume(self, session_id: str) -> SessionRuntime:
        return SessionRuntime.recover(session_id, self.store)
