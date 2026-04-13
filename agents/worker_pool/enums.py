"""
Agent Management System for Claude Code Python API

This module provides a Python implementation of the Agent coordination system
inspired by Claude Code's TypeScript coordinator mode.
"""

from enum import Enum, auto
from typing import Any, Optional, TypeVar, Generic


class AgentStatus(Enum):
    """Status of an Agent lifecycle."""
    IDLE = "idle"
    BUSY = "busy"
    COMPLETED = "completed"
    ERROR = "error"
    STOPPED = "stopped"


class TaskStatus(Enum):
    """Status of a Task lifecycle."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @staticmethod
    def is_terminal(status: "TaskStatus") -> bool:
        """Check if the task is in a terminal state."""
        return status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)


class TaskType(Enum):
    """Type of task that can be executed."""
    LOCAL_BASH = "local_bash"
    LOCAL_AGENT = "local_agent"
    REMOTE_AGENT = "remote_agent"
    IN_PROCESS_TEAMMATE = "in_process_teammate"
    LOCAL_WORKFLOW = "local_workflow"
    MONITOR_MCP = "monitor_mcp"
    DREAM = "dream"


class TaskPriority(Enum):
    """Priority levels for tasks."""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


T = TypeVar("T")


class Result(Generic[T]):
    """Generic result wrapper for operations."""

    def __init__(
        self,
        success: bool,
        data: Optional[T] = None,
        error: Optional[str] = None,
    ):
        self.success = success
        self.data = data
        self.error = error

    @classmethod
    def ok(cls, data: T) -> "Result[T]":
        return cls(success=True, data=data)

    @classmethod
    def fail(cls, error: str) -> "Result[T]":
        return cls(success=False, error=error)

    def is_ok(self) -> bool:
        return self.success

    def is_err(self) -> bool:
        return not self.success

    def unwrap(self) -> T:
        if self.data is None:
            raise ValueError(f"Cannot unwrap failed result: {self.error}")
        return self.data


__all__ = [
    "AgentStatus",
    "TaskStatus",
    "TaskType",
    "TaskPriority",
    "Result",
]
