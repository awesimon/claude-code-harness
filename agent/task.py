"""Task model and management for Agent system."""

from __future__ import annotations

import uuid
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Callable, Awaitable, Dict, List
from enum import Enum
import asyncio
from concurrent.futures import ThreadPoolExecutor

from .enums import TaskStatus, TaskType, TaskPriority, Result


@dataclass
class TaskConfig:
    """Configuration for task execution."""
    timeout: Optional[float] = None
    max_retries: int = 0
    retry_delay: float = 1.0
    parallel: bool = False
    dependencies: List[str] = field(default_factory=list)


@dataclass
class TaskResult:
    """Result of task execution."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    attempts: int = 0

    @classmethod
    def success(cls, data: Any, duration_ms: float = 0.0) -> TaskResult:
        return cls(success=True, data=data, duration_ms=duration_ms)

    @classmethod
    def failure(cls, error: str, duration_ms: float = 0.0) -> TaskResult:
        return cls(success=False, error=error, duration_ms=duration_ms)


class Task:
    """
    Represents a unit of work to be executed by an Agent.

    Similar to Task.ts in Claude Code, this manages the lifecycle
    of a task from pending to completion.
    """

    _id_counter = 0
    _lock = asyncio.Lock()

    def __init__(
        self,
        description: str,
        task_type: TaskType = TaskType.LOCAL_AGENT,
        priority: TaskPriority = TaskPriority.NORMAL,
        config: Optional[TaskConfig] = None,
        tool_use_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ):
        Task._id_counter += 1
        prefix = self._get_id_prefix(task_type)
        self.id = f"{prefix}{Task._id_counter:06d}-{uuid.uuid4().hex[:8]}"

        self.description = description
        self.task_type = task_type
        self.priority = priority
        self.config = config or TaskConfig()
        self.tool_use_id = tool_use_id
        self.agent_id = agent_id

        # Status tracking
        self.status = TaskStatus.PENDING
        self._status_lock = asyncio.Lock()

        # Timing
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.total_paused_ms: float = 0.0

        # Result
        self.result: Optional[TaskResult] = None

        # Callbacks
        self._on_complete_callbacks: List[Callable[[Task], Awaitable[None]]] = []
        self._on_fail_callbacks: List[Callable[[Task, str], Awaitable[None]]] = []

        # Execution
        self._executor: Optional[Callable[[], Awaitable[Any]]] = None
        self._abort_event = asyncio.Event()

    def _get_id_prefix(self, task_type: TaskType) -> str:
        """Get task ID prefix based on type."""
        prefixes = {
            TaskType.LOCAL_BASH: "b",
            TaskType.LOCAL_AGENT: "a",
            TaskType.REMOTE_AGENT: "r",
            TaskType.IN_PROCESS_TEAMMATE: "t",
            TaskType.LOCAL_WORKFLOW: "w",
            TaskType.MONITOR_MCP: "m",
            TaskType.DREAM: "d",
        }
        return prefixes.get(task_type, "x")

    def set_executor(self, executor: Callable[[], Awaitable[Any]]) -> None:
        """Set the async function that executes this task."""
        self._executor = executor

    def on_complete(self, callback: Callable[[Task], Awaitable[None]]) -> None:
        """Register a callback for task completion."""
        self._on_complete_callbacks.append(callback)

    def on_fail(self, callback: Callable[[Task, str], Awaitable[None]]) -> None:
        """Register a callback for task failure."""
        self._on_fail_callbacks.append(callback)

    async def _update_status(self, status: TaskStatus) -> None:
        """Thread-safe status update."""
        async with self._status_lock:
            self.status = status

    async def execute(self) -> TaskResult:
        """
        Execute the task with retry logic and timeout.

        Returns:
            TaskResult containing success status and data or error.
        """
        if self._executor is None:
            raise ValueError(f"Task {self.id} has no executor set")

        await self._update_status(TaskStatus.IN_PROGRESS)
        self.start_time = time.time() * 1000

        attempts = 0
        last_error: Optional[str] = None

        while attempts <= self.config.max_retries:
            attempts += 1
            attempt_start = time.time() * 1000

            try:
                # Check if aborted
                if self._abort_event.is_set():
                    result = TaskResult.failure("Task was aborted")
                    await self._update_status(TaskStatus.CANCELLED)
                    self.result = result
                    return result

                # Execute with timeout if specified
                if self.config.timeout:
                    task_future = asyncio.wait_for(
                        self._executor(),
                        timeout=self.config.timeout
                    )
                else:
                    task_future = self._executor()

                data = await task_future

                duration_ms = (time.time() * 1000) - self.start_time
                result = TaskResult.success(data, duration_ms)
                result.attempts = attempts

                self.result = result
                self.end_time = time.time() * 1000
                await self._update_status(TaskStatus.COMPLETED)

                # Trigger callbacks
                for callback in self._on_complete_callbacks:
                    try:
                        await callback(self)
                    except Exception as e:
                        print(f"Callback error: {e}")

                return result

            except asyncio.TimeoutError:
                last_error = f"Task timed out after {self.config.timeout}s"
                duration_ms = (time.time() * 1000) - attempt_start

                if attempts > self.config.max_retries:
                    result = TaskResult.failure(last_error, duration_ms)
                    result.attempts = attempts
                    self.result = result
                    self.end_time = time.time() * 1000
                    await self._update_status(TaskStatus.FAILED)

                    for callback in self._on_fail_callbacks:
                        try:
                            await callback(self, last_error)
                        except Exception as e:
                            print(f"Callback error: {e}")

                    return result

                await asyncio.sleep(self.config.retry_delay)

            except Exception as e:
                last_error = str(e)
                duration_ms = (time.time() * 1000) - attempt_start

                if attempts > self.config.max_retries:
                    result = TaskResult.failure(last_error, duration_ms)
                    result.attempts = attempts
                    self.result = result
                    self.end_time = time.time() * 1000
                    await self._update_status(TaskStatus.FAILED)

                    for callback in self._on_fail_callbacks:
                        try:
                            await callback(self, last_error)
                        except Exception as cb_e:
                            print(f"Callback error: {cb_e}")

                    return result

                await asyncio.sleep(self.config.retry_delay)

        # Should not reach here
        result = TaskResult.failure("Unknown error", 0)
        await self._update_status(TaskStatus.FAILED)
        return result

    def abort(self) -> None:
        """Signal the task to abort."""
        self._abort_event.set()

    def is_terminal(self) -> bool:
        """Check if task is in terminal state."""
        return TaskStatus.is_terminal(self.status)

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary representation."""
        return {
            "id": self.id,
            "description": self.description,
            "type": self.task_type.value,
            "status": self.status.value,
            "priority": self.priority.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.result.duration_ms if self.result else None,
            "agent_id": self.agent_id,
            "tool_use_id": self.tool_use_id,
        }

    def __repr__(self) -> str:
        return f"Task({self.id}, {self.description[:30]}, {self.status.value})"

    def __lt__(self, other: Task) -> bool:
        """For priority queue ordering (higher priority = lower value)."""
        if not isinstance(other, Task):
            return NotImplemented
        return self.priority.value > other.priority.value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Task):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
