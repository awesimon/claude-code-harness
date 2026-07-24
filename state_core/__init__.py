"""Public domain contract for durable session state."""

from .migration import migrate_legacy_session
from .repository import StateRepository, TaskRepository
from .runtime import SessionRuntime, SessionRuntimeFactory
from .sqlalchemy_store import (
    SQLAlchemyStateRepository,
    SQLAlchemyStateStore,
    SQLAlchemyTaskRepository,
)
from .types import (
    ClaimResult,
    CommitResult,
    EventType,
    InvalidTaskDependency,
    InvalidTransition,
    NewTask,
    PendingEventBatch,
    PendingSessionEvent,
    Plan,
    PlanState,
    RevisionConflict,
    SessionEvent,
    SessionHealth,
    SessionSnapshot,
    SessionState,
    StateCoreError,
    TaskItem,
    TaskMode,
    TaskMutation,
    TaskStatus,
)

__all__ = [
    "ClaimResult",
    "CommitResult",
    "EventType",
    "InvalidTaskDependency",
    "InvalidTransition",
    "migrate_legacy_session",
    "NewTask",
    "PendingEventBatch",
    "PendingSessionEvent",
    "Plan",
    "PlanState",
    "RevisionConflict",
    "SessionEvent",
    "SessionRuntime",
    "SessionRuntimeFactory",
    "SessionHealth",
    "SessionSnapshot",
    "SessionState",
    "SQLAlchemyStateRepository",
    "SQLAlchemyStateStore",
    "SQLAlchemyTaskRepository",
    "StateCoreError",
    "StateRepository",
    "TaskItem",
    "TaskMode",
    "TaskMutation",
    "TaskRepository",
    "TaskStatus",
]
