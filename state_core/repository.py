"""Storage-neutral repository contracts for the state-core domain."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import (
    ClaimResult,
    CommitResult,
    NewTask,
    PendingEventBatch,
    SessionEvent,
    SessionSnapshot,
    SessionState,
    TaskItem,
    TaskMutation,
)


@runtime_checkable
class StateRepository(Protocol):
    """Persist state and its events through a single atomic commit boundary."""

    def create_session(self, state: SessionState) -> SessionState: ...

    def load_session(self, session_id: str) -> SessionState | None: ...

    def delete_session(self, session_id: str) -> bool: ...

    def commit(
        self,
        state: SessionState,
        batch: PendingEventBatch,
        expected_revision: int,
    ) -> CommitResult:
        """Atomically persist state and assign IDs to its pending event batch.

        Implementations must call ``batch.validate_state(state)`` first. After
        loading every referenced persisted parent and its owning session, they
        must call ``batch.validate_existing_parents(parent_sessions)`` before
        writing state, assigning event IDs, or committing the transaction.
        """
        ...

    def list_events(self, session_id: str, after_id: int = 0) -> list[SessionEvent]: ...

    def save_snapshot(self, snapshot: SessionSnapshot) -> None: ...

    def latest_snapshot(self, session_id: str) -> SessionSnapshot | None: ...


@runtime_checkable
class TaskRepository(Protocol):
    def create(self, task_list_id: str, task: NewTask) -> TaskItem: ...

    def get(self, task_list_id: str, task_id: str) -> TaskItem | None: ...

    def list(self, task_list_id: str) -> list[TaskItem]: ...

    def update(
        self,
        task_list_id: str,
        task_id: str,
        mutation: TaskMutation,
    ) -> TaskItem | None: ...

    def claim(self, task_list_id: str, task_id: str, owner: str) -> ClaimResult: ...

    def unassign(self, task_list_id: str, task_id: str) -> TaskItem | None: ...

    def delete(self, task_list_id: str, task_id: str) -> bool: ...

    def delete_list(self, task_list_id: str) -> int: ...
