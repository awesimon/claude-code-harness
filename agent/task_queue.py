"""TaskQueue: Priority-based task queue management with dependency resolution."""

from __future__ import annotations

import asyncio
import heapq
from typing import Dict, List, Optional, Set, Callable, Awaitable
from collections import defaultdict

from .task import Task
from .enums import TaskStatus


class TaskQueue:
    """
    Priority-based task queue with dependency management.

    Similar to task management in Claude Code's coordinator mode,
    this handles queuing, prioritization, and dependency resolution.
    """

    def __init__(self, max_concurrent: int = 10):
        self.max_concurrent = max_concurrent

        # Priority queue (min-heap, so higher priority = lower value)
        self._queue: List[Task] = []
        self._queue_lock = asyncio.Lock()

        # Task storage
        self._tasks: Dict[str, Task] = {}
        self._tasks_lock = asyncio.Lock()

        # Dependency tracking: task_id -> set of dependent task_ids
        self._dependencies: Dict[str, Set[str]] = defaultdict(set)
        self._depends_on: Dict[str, Set[str]] = defaultdict(set)

        # Execution tracking
        self._running: Set[str] = set()
        self._completed: Set[str] = set()
        self._failed: Set[str] = set()

        # Event for queue changes
        self._queue_event = asyncio.Event()
        self._queue_event.set()  # Initially set to allow immediate processing

        # Callbacks
        self._on_task_complete: Optional[Callable[[Task], Awaitable[None]]] = None
        self._on_task_fail: Optional[Callable[[Task, str], Awaitable[None]]] = None

    async def add_task(self, task: Task) -> None:
        """Add a task to the queue."""
        async with self._tasks_lock:
            self._tasks[task.id] = task

            # Set up dependencies
            if task.config.dependencies:
                for dep_id in task.config.dependencies:
                    self._depends_on[task.id].add(dep_id)
                    self._dependencies[dep_id].add(task.id)

        # Check if dependencies are already satisfied
        if await self._can_execute(task):
            async with self._queue_lock:
                heapq.heappush(self._queue, task)
            self._queue_event.set()

    async def add_tasks(self, tasks: List[Task]) -> None:
        """Add multiple tasks to the queue."""
        for task in tasks:
            await self.add_task(task)

    async def get_next_task(self) -> Optional[Task]:
        """Get the next task that can be executed."""
        async with self._queue_lock:
            while self._queue:
                task = heapq.heappop(self._queue)

                # Verify task can still be executed
                if await self._can_execute(task):
                    return task
                else:
                    # Put back if dependencies not met
                    heapq.heappush(self._queue, task)
                    return None
            return None

    async def _can_execute(self, task: Task) -> bool:
        """Check if a task's dependencies are satisfied."""
        async with self._tasks_lock:
            for dep_id in self._depends_on[task.id]:
                if dep_id not in self._completed:
                    return False
            return True

    async def mark_running(self, task_id: str) -> None:
        """Mark a task as running."""
        async with self._tasks_lock:
            self._running.add(task_id)

    async def mark_complete(self, task: Task) -> None:
        """Mark a task as completed and notify dependents."""
        async with self._tasks_lock:
            self._running.discard(task.id)
            self._completed.add(task.id)

            # Check if any dependent tasks can now run
            dependents = self._dependencies.get(task.id, set()).copy()

        # Trigger callbacks
        if self._on_task_complete:
            try:
                await self._on_task_complete(task)
            except Exception as e:
                print(f"Task completion callback error: {e}")

        # Check dependents
        for dep_id in dependents:
            dep_task = await self.get_task(dep_id)
            if dep_task and await self._can_execute(dep_task):
                async with self._queue_lock:
                    if dep_task not in self._queue:
                        heapq.heappush(self._queue, dep_task)
                self._queue_event.set()

    async def mark_failed(self, task: Task, error: str) -> None:
        """Mark a task as failed."""
        async with self._tasks_lock:
            self._running.discard(task.id)
            self._failed.add(task.id)

        if self._on_task_fail:
            try:
                await self._on_task_fail(task, error)
            except Exception as e:
                print(f"Task failure callback error: {e}")

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        async with self._tasks_lock:
            return self._tasks.get(task_id)

    async def get_all_tasks(self) -> List[Task]:
        """Get all tasks."""
        async with self._tasks_lock:
            return list(self._tasks.values())

    async def get_pending_count(self) -> int:
        """Get count of pending tasks."""
        async with self._queue_lock:
            return len(self._queue)

    async def get_running_count(self) -> int:
        """Get count of running tasks."""
        async with self._tasks_lock:
            return len(self._running)

    def is_empty(self) -> bool:
        """Check if queue is empty."""
        return len(self._queue) == 0 and len(self._running) == 0

    async def wait_for_task(self, task_id: str, timeout: Optional[float] = None) -> bool:
        """Wait for a specific task to complete."""
        start_time = asyncio.get_event_loop().time()

        while True:
            async with self._tasks_lock:
                if task_id in self._completed:
                    return True
                if task_id in self._failed:
                    return False

            if timeout:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed >= timeout:
                    return False

            await asyncio.sleep(0.1)

    async def wait_all(self, timeout: Optional[float] = None) -> bool:
        """Wait for all tasks to complete."""
        start_time = asyncio.get_event_loop().time()

        while True:
            async with self._queue_lock:
                async with self._tasks_lock:
                    if len(self._queue) == 0 and len(self._running) == 0:
                        return True

            if timeout:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed >= timeout:
                    return False

            await asyncio.sleep(0.1)

    def set_callbacks(
        self,
        on_complete: Optional[Callable[[Task], Awaitable[None]]] = None,
        on_fail: Optional[Callable[[Task, str], Awaitable[None]]] = None,
    ) -> None:
        """Set task lifecycle callbacks."""
        self._on_task_complete = on_complete
        self._on_task_fail = on_fail

    def get_statistics(self) -> dict:
        """Get queue statistics."""
        return {
            "pending": len(self._queue),
            "running": len(self._running),
            "completed": len(self._completed),
            "failed": len(self._failed),
            "total": len(self._tasks),
        }

    def clear(self) -> None:
        """Clear all tasks from the queue."""
        self._queue.clear()
        self._tasks.clear()
        self._dependencies.clear()
        self._depends_on.clear()
        self._running.clear()
        self._completed.clear()
        self._failed.clear()
