"""Agent model for the Agent Management System."""

from __future__ import annotations

import uuid
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Callable, Awaitable
from enum import Enum

from .enums import AgentStatus, TaskStatus
from .task import Task, TaskResult
from .task_queue import TaskQueue


@dataclass
class AgentConfig:
    """Configuration for an Agent."""
    name: Optional[str] = None
    description: Optional[str] = None
    tools: Set[str] = field(default_factory=set)
    max_concurrent_tasks: int = 1
    timeout: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentCapabilities:
    """Capabilities available to an Agent."""

    BASH = "bash"
    FILE_READ = "file_read"
    FILE_EDIT = "file_edit"
    SKILL = "skill"
    SEND_MESSAGE = "send_message"
    WEB_SEARCH = "web_search"

    DEFAULT_WORKER_TOOLS = {
        BASH,
        FILE_READ,
        FILE_EDIT,
    }

    ALL_TOOLS = {
        BASH,
        FILE_READ,
        FILE_EDIT,
        SKILL,
        SEND_MESSAGE,
        WEB_SEARCH,
    }


class Agent:
    """
    Agent class representing a worker that can execute tasks.

    Inspired by workerAgent.ts in Claude Code's coordinator mode,
    this class manages the lifecycle and execution of tasks.
    """

    _id_counter = 0

    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        agent_type: str = "worker",
    ):
        Agent._id_counter += 1
        self.id = f"agent-{Agent._id_counter:06d}-{uuid.uuid4().hex[:8]}"

        self.config = config or AgentConfig()
        self.agent_type = agent_type
        self.name = self.config.name or f"{agent_type}-{self.id[:8]}"

        # Status
        self.status = AgentStatus.IDLE
        self._status_lock = asyncio.Lock()

        # Task management
        self.task_queue = TaskQueue(max_concurrent=self.config.max_concurrent_tasks)
        self.current_tasks: Dict[str, Task] = {}
        self.completed_tasks: List[Task] = []
        self.failed_tasks: List[Task] = []

        # Execution
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_tasks)
        self._running = False
        self._main_task: Optional[asyncio.Task] = None

        # Callbacks
        self._on_task_complete: Optional[Callable[[Task], Awaitable[None]]] = None
        self._on_task_fail: Optional[Callable[[Task, str], Awaitable[None]]] = None
        self._on_status_change: Optional[Callable[[AgentStatus, AgentStatus], Awaitable[None]]] = None

        # Metrics
        self.created_at = time.time()
        self.last_active_at: Optional[float] = None
        self.total_tasks_executed = 0

    async def _update_status(self, new_status: AgentStatus) -> None:
        """Thread-safe status update with callback."""
        async with self._status_lock:
            old_status = self.status
            if old_status != new_status:
                self.status = new_status
                if self._on_status_change:
                    try:
                        await self._on_status_change(old_status, new_status)
                    except Exception as e:
                        print(f"Status change callback error: {e}")

    def has_tool(self, tool_name: str) -> bool:
        """Check if agent has access to a specific tool."""
        return tool_name in self.config.tools

    def add_tool(self, tool_name: str) -> None:
        """Add a tool to the agent's capabilities."""
        self.config.tools.add(tool_name)

    def remove_tool(self, tool_name: str) -> None:
        """Remove a tool from the agent's capabilities."""
        self.config.tools.discard(tool_name)

    async def assign_task(self, task: Task) -> None:
        """Assign a task to this agent."""
        task.agent_id = self.id

        # Set up task callbacks
        async def on_complete(t: Task) -> None:
            await self._handle_task_complete(t)

        async def on_fail(t: Task, error: str) -> None:
            await self._handle_task_fail(t, error)

        task.on_complete(on_complete)
        task.on_fail(on_fail)

        await self.task_queue.add_task(task)

    async def assign_tasks(self, tasks: List[Task]) -> None:
        """Assign multiple tasks to this agent."""
        for task in tasks:
            await self.assign_task(task)

    async def _handle_task_complete(self, task: Task) -> None:
        """Handle task completion."""
        self.current_tasks.pop(task.id, None)
        self.completed_tasks.append(task)
        self.total_tasks_executed += 1
        self.last_active_at = time.time()

        await self.task_queue.mark_complete(task)

        if self._on_task_complete:
            try:
                await self._on_task_complete(task)
            except Exception as e:
                print(f"Task complete callback error: {e}")

        await self._check_idle()

    async def _handle_task_fail(self, task: Task, error: str) -> None:
        """Handle task failure."""
        self.current_tasks.pop(task.id, None)
        self.failed_tasks.append(task)
        self.last_active_at = time.time()

        await self.task_queue.mark_failed(task, error)

        if self._on_task_fail:
            try:
                await self._on_task_fail(task, error)
            except Exception as e:
                print(f"Task fail callback error: {e}")

        await self._check_idle()

    async def _check_idle(self) -> None:
        """Check if agent should transition to idle state."""
        running = await self.task_queue.get_running_count()
        pending = await self.task_queue.get_pending_count()

        if running == 0 and pending == 0:
            await self._update_status(AgentStatus.IDLE)

    async def start(self) -> None:
        """Start the agent's task processing loop."""
        if self._running:
            return

        self._running = True
        self._main_task = asyncio.create_task(self._process_loop())
        await self._update_status(AgentStatus.IDLE)

    async def stop(self, wait: bool = True, timeout: Optional[float] = None) -> None:
        """Stop the agent's task processing loop."""
        self._running = False

        if wait and self._main_task:
            try:
                await asyncio.wait_for(self._main_task, timeout=timeout)
            except asyncio.TimeoutError:
                self._main_task.cancel()
                try:
                    await self._main_task
                except asyncio.CancelledError:
                    pass

        await self._update_status(AgentStatus.STOPPED)

    async def _process_loop(self) -> None:
        """Main processing loop for the agent."""
        while self._running:
            try:
                # Get next task
                task = await self.task_queue.get_next_task()

                if task is None:
                    # No tasks available, wait a bit
                    await asyncio.sleep(0.1)
                    continue

                # Execute task with semaphore for concurrency control
                async with self._semaphore:
                    await self._execute_task(task)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Agent {self.id} processing error: {e}")
                await asyncio.sleep(0.1)

    async def _execute_task(self, task: Task) -> None:
        """Execute a single task."""
        await self._update_status(AgentStatus.BUSY)
        await self.task_queue.mark_running(task.id)
        self.current_tasks[task.id] = task

        try:
            await task.execute()
        except Exception as e:
            print(f"Task execution error: {e}")
            await self.task_queue.mark_failed(task, str(e))

    async def wait_for_task(self, task_id: str, timeout: Optional[float] = None) -> bool:
        """Wait for a specific task to complete."""
        return await self.task_queue.wait_for_task(task_id, timeout)

    async def wait_all(self, timeout: Optional[float] = None) -> bool:
        """Wait for all tasks to complete."""
        return await self.task_queue.wait_all(timeout)

    def set_callbacks(
        self,
        on_task_complete: Optional[Callable[[Task], Awaitable[None]]] = None,
        on_task_fail: Optional[Callable[[Task, str], Awaitable[None]]] = None,
        on_status_change: Optional[Callable[[AgentStatus, AgentStatus], Awaitable[None]]] = None,
    ) -> None:
        """Set agent lifecycle callbacks."""
        self._on_task_complete = on_task_complete
        self._on_task_fail = on_task_fail
        self._on_status_change = on_status_change

    def get_statistics(self) -> Dict[str, Any]:
        """Get agent statistics."""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.agent_type,
            "status": self.status.value,
            "tools": list(self.config.tools),
            "current_tasks": len(self.current_tasks),
            "completed_tasks": len(self.completed_tasks),
            "failed_tasks": len(self.failed_tasks),
            "total_tasks_executed": self.total_tasks_executed,
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert agent to dictionary representation."""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.agent_type,
            "status": self.status.value,
            "config": {
                "description": self.config.description,
                "tools": list(self.config.tools),
                "max_concurrent_tasks": self.config.max_concurrent_tasks,
            },
            "statistics": self.get_statistics(),
        }

    def __repr__(self) -> str:
        return f"Agent({self.id}, {self.name}, {self.status.value})"

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Agent):
            return NotImplemented
        return self.id == other.id
