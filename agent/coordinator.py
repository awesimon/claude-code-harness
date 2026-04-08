"""Coordinator: High-level task orchestration for Agent Management System."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable, Awaitable, Union
from enum import Enum

from .enums import AgentStatus, TaskStatus, TaskPriority, Result
from .agent import Agent, AgentConfig, AgentCapabilities
from .agent_manager import AgentManager
from .task import Task, TaskConfig, TaskResult
from .task_queue import TaskQueue


class CoordinatorPhase(Enum):
    """Phases of the coordinator workflow."""
    IDLE = "idle"
    RESEARCH = "research"
    SYNTHESIS = "synthesis"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"
    COMPLETE = "complete"


@dataclass
class CoordinatorConfig:
    """Configuration for the Coordinator."""
    max_workers: int = 10
    default_agent_type: str = "worker"
    default_tools: set = field(default_factory=lambda: AgentCapabilities.DEFAULT_WORKER_TOOLS)
    auto_start_agents: bool = True
    enable_monitoring: bool = False
    monitor_interval: float = 5.0


@dataclass
class TaskNotification:
    """
    Task notification format (similar to <task-notification> in Claude Code).

    Format:
        <task-notification>
        <task-id>{agentId}</task-id>
        <status>completed|failed|killed</status>
        <summary>{human-readable status summary}</summary>
        <result>{agent's final text response}</result>
        <usage>
          <total_tokens>N</total_tokens>
          <tool_uses>N</tool_uses>
          <duration_ms>N</duration_ms>
        </usage>
        </task-notification>
    """
    task_id: str
    status: TaskStatus
    summary: str
    result: Optional[Any] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=lambda: time.time())

    def to_xml(self) -> str:
        """Convert notification to XML format."""
        xml = f"""<task-notification>
<task-id>{self.task_id}</task-id>
<status>{self.status.value}</status>
<summary>{self.summary}</summary>"""

        if self.result is not None:
            xml += f"\n<result>{self.result}</result>"

        if self.usage:
            xml += "\n<usage>"
            for key, value in self.usage.items():
                xml += f"\n  <{key}>{value}</{key}>"
            xml += "\n</usage>"

        xml += "\n</task-notification>"
        return xml

    @classmethod
    def from_task(cls, task: Task, agent_id: Optional[str] = None) -> TaskNotification:
        """Create notification from a completed task."""
        if task.status == TaskStatus.COMPLETED:
            status = TaskStatus.COMPLETED
            summary = f'Agent "{task.description}" completed'
        elif task.status == TaskStatus.FAILED:
            status = TaskStatus.FAILED
            summary = f'Agent "{task.description}" failed'
        else:
            status = TaskStatus.CANCELLED
            summary = f'Agent "{task.description}" was stopped'

        return cls(
            task_id=agent_id or task.agent_id or task.id,
            status=status,
            summary=summary,
            result=task.result.data if task.result and task.result.success else None,
            usage={
                "total_tokens": 0,  # Would come from actual LLM usage
                "tool_uses": 0,
                "duration_ms": task.result.duration_ms if task.result else 0,
            } if task.result else {},
        )


@dataclass
class ExecutionPlan:
    """
    Execution plan with phases and dependencies.

    Similar to the task workflow phases in Claude Code coordinator mode:
    - Research: Workers investigate in parallel
    - Synthesis: Coordinator understands findings
    - Implementation: Workers make changes
    - Verification: Workers verify changes
    """
    phases: List[List[Task]] = field(default_factory=list)
    dependencies: Dict[str, List[str]] = field(default_factory=dict)
    phase_names: List[str] = field(default_factory=list)

    def add_phase(self, tasks: List[Task], name: Optional[str] = None) -> None:
        """Add a phase of tasks."""
        self.phases.append(tasks)
        self.phase_names.append(name or f"phase_{len(self.phases)}")

    def add_dependency(self, task_id: str, depends_on: List[str]) -> None:
        """Add a dependency: task_id depends on tasks in depends_on."""
        self.dependencies[task_id] = depends_on

    def get_all_tasks(self) -> List[Task]:
        """Get all tasks in the plan."""
        all_tasks = []
        for phase in self.phases:
            all_tasks.extend(phase)
        return all_tasks


class Coordinator:
    """
    Coordinator for high-level task orchestration.

    Inspired by Claude Code's coordinator mode (coordinatorMode.ts),
    this class manages:
    - Creating and coordinating worker agents
    - Parallel task execution
    - Task decomposition and synthesis
    - Message passing between agents
    - Phased execution (Research -> Synthesis -> Implementation -> Verification)
    """

    def __init__(self, config: Optional[CoordinatorConfig] = None):
        self.config = config or CoordinatorConfig()
        self._agent_manager = AgentManager(max_agents=self.config.max_workers)

        # State
        self._phase = CoordinatorPhase.IDLE
        self._phase_lock = asyncio.Lock()

        # Message handling
        self._message_handlers: List[Callable[[TaskNotification], Awaitable[None]]] = []

        # Task tracking
        self._active_tasks: Dict[str, Task] = {}
        self._task_notifications: List[TaskNotification] = []

        # Statistics
        self._start_time: Optional[float] = None

    async def __aenter__(self) -> Coordinator:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.shutdown()

    async def start(self) -> None:
        """Start the coordinator."""
        self._start_time = time.time()

        if self.config.enable_monitoring:
            await self._agent_manager.start_monitoring(
                interval=self.config.monitor_interval
            )

    async def shutdown(self, wait: bool = True, timeout: Optional[float] = None) -> None:
        """Shutdown the coordinator and all agents."""
        await self._agent_manager.shutdown(wait=wait, timeout=timeout)

    async def _update_phase(self, phase: CoordinatorPhase) -> None:
        """Update the current phase."""
        async with self._phase_lock:
            self._phase = phase

    def get_phase(self) -> CoordinatorPhase:
        """Get current phase."""
        return self._phase

    async def create_worker(
        self,
        name: Optional[str] = None,
        config: Optional[AgentConfig] = None,
        start: bool = True,
    ) -> Result[Agent]:
        """
        Create a worker agent (equivalent to AGENT_TOOL in Claude Code).

        Args:
            name: Optional name for the worker
            config: Agent configuration
            start: Whether to start the agent immediately

        Returns:
            Result containing the created Agent
        """
        if config is None:
            config = AgentConfig(
                name=name,
                tools=self.config.default_tools.copy(),
                max_concurrent_tasks=1,
            )

        return await self._agent_manager.create_agent(
            config=config,
            agent_type=self.config.default_agent_type,
            start=start,
        )

    async def spawn_task(
        self,
        description: str,
        executor: Callable[[], Awaitable[Any]],
        priority: TaskPriority = TaskPriority.NORMAL,
        config: Optional[TaskConfig] = None,
    ) -> Task:
        """
        Spawn a single task (using a worker agent).

        Args:
            description: Task description
            executor: Async function that executes the task
            priority: Task priority
            config: Optional task configuration

        Returns:
            The created Task
        """
        # Ensure we have a worker
        workers = await self._agent_manager.get_agents_by_type(
            self.config.default_agent_type
        )

        if not workers:
            result = await self.create_worker(start=True)
            if result.is_err():
                raise RuntimeError(f"Failed to create worker: {result.error}")
            worker = result.unwrap()
        else:
            # Find least busy worker
            worker = min(workers, key=lambda w: w.task_queue.get_pending_count())

        # Create task
        task = Task(
            description=description,
            priority=priority,
            config=config,
        )
        task.set_executor(executor)

        # Set up notification callback
        async def on_complete(t: Task) -> None:
            await self._handle_task_complete(t)

        async def on_fail(t: Task, error: str) -> None:
            await self._handle_task_fail(t, error)

        task.on_complete(on_complete)
        task.on_fail(on_fail)

        # Track task
        self._active_tasks[task.id] = task

        # Assign to worker
        await worker.assign_task(task)

        return task

    async def spawn_parallel(
        self,
        descriptions: List[str],
        executors: List[Callable[[], Awaitable[Any]]],
        priority: TaskPriority = TaskPriority.NORMAL,
        config: Optional[TaskConfig] = None,
    ) -> List[Task]:
        """
        Spawn multiple tasks in parallel.

        Similar to making multiple AGENT_TOOL calls in Claude Code coordinator mode.

        Args:
            descriptions: List of task descriptions
            executors: List of async executor functions
            priority: Task priority for all tasks
            config: Optional task configuration

        Returns:
            List of created Tasks
        """
        tasks = []
        for desc, executor in zip(descriptions, executors):
            task = await self.spawn_task(desc, executor, priority, config)
            tasks.append(task)
        return tasks

    async def send_message(
        self,
        to_agent_id: str,
        message: str,
    ) -> Result[bool]:
        """
        Send a message to an existing worker (equivalent to SendMessageTool).

        Args:
            to_agent_id: ID of the agent to send to
            message: Message content

        Returns:
            Result indicating success
        """
        agent = await self._agent_manager.get_agent(to_agent_id)
        if not agent:
            return Result.fail(f"Agent {to_agent_id} not found")

        # In a real implementation, this would queue a message for the agent
        # For now, we just return success
        return Result.ok(True)

    async def stop_task(self, task_id: str) -> Result[bool]:
        """
        Stop a running task (equivalent to TaskStopTool).

        Args:
            task_id: ID of the task to stop

        Returns:
            Result indicating success
        """
        task = self._active_tasks.get(task_id)
        if not task:
            return Result.fail(f"Task {task_id} not found")

        if task.is_terminal():
            return Result.fail(f"Task {task_id} is already in terminal state")

        task.abort()
        return Result.ok(True)

    async def execute_plan(self, plan: ExecutionPlan) -> Dict[str, TaskResult]:
        """
        Execute an execution plan with phases and dependencies.

        Args:
            plan: ExecutionPlan containing phases and dependencies

        Returns:
            Dictionary mapping task IDs to their results
        """
        results: Dict[str, TaskResult] = {}

        # Execute phases sequentially
        for i, phase in enumerate(plan.phases):
            phase_name = plan.phase_names[i]

            # Update phase state
            if phase_name == "research":
                await self._update_phase(CoordinatorPhase.RESEARCH)
            elif phase_name == "implementation":
                await self._update_phase(CoordinatorPhase.IMPLEMENTATION)
            elif phase_name == "verification":
                await self._update_phase(CoordinatorPhase.VERIFICATION)

            # Execute phase tasks in parallel
            tasks = []
            for task in phase:
                # Set up dependencies
                if task.id in plan.dependencies:
                    task.config.dependencies = plan.dependencies[task.id]

                # Execute the task
                worker_result = await self.create_worker()
                if worker_result.is_ok():
                    worker = worker_result.unwrap()
                    await worker.assign_task(task)
                    tasks.append(task)

            # Wait for phase completion
            await asyncio.gather(
                *[task.execute() for task in tasks],
                return_exceptions=True
            )

            # Collect results
            for task in tasks:
                if task.result:
                    results[task.id] = task.result

        await self._update_phase(CoordinatorPhase.COMPLETE)
        return results

    async def synthesize_results(
        self,
        task_results: Dict[str, TaskResult],
        prompt_template: Optional[str] = None,
    ) -> str:
        """
        Synthesize results from multiple tasks.

        This is the coordinator's key role - understanding worker findings
        before directing follow-up work.

        Args:
            task_results: Dictionary of task ID to result
            prompt_template: Optional custom synthesis template

        Returns:
            Synthesized summary string
        """
        # Default synthesis
        if prompt_template is None:
            parts = ["## Synthesized Findings\n"]

            for task_id, result in task_results.items():
                if result.success:
                    parts.append(f"\n**Task {task_id}:**")
                    parts.append(f"  {result.data}")
                else:
                    parts.append(f"\n**Task {task_id}:** FAILED")
                    parts.append(f"  Error: {result.error}")

            parts.append("\n---")
            parts.append("\nKey findings have been synthesized and can be used to direct follow-up work.")

            return "\n".join(parts)
        else:
            # Custom synthesis
            return prompt_template.format(
                results=task_results,
                count=len(task_results),
            )

    async def _handle_task_complete(self, task: Task) -> None:
        """Handle task completion notification."""
        notification = TaskNotification.from_task(task)
        self._task_notifications.append(notification)

        for handler in self._message_handlers:
            try:
                await handler(notification)
            except Exception as e:
                print(f"Message handler error: {e}")

    async def _handle_task_fail(self, task: Task, error: str) -> None:
        """Handle task failure notification."""
        notification = TaskNotification.from_task(task)
        self._task_notifications.append(notification)

        for handler in self._message_handlers:
            try:
                await handler(notification)
            except Exception as e:
                print(f"Message handler error: {e}")

    def add_message_handler(
        self,
        handler: Callable[[TaskNotification], Awaitable[None]],
    ) -> None:
        """Add a handler for task notifications."""
        self._message_handlers.append(handler)

    def remove_message_handler(
        self,
        handler: Callable[[TaskNotification], Awaitable[None]],
    ) -> None:
        """Remove a message handler."""
        if handler in self._message_handlers:
            self._message_handlers.remove(handler)

    async def wait_for_task(self, task_id: str, timeout: Optional[float] = None) -> bool:
        """Wait for a specific task to complete."""
        task = self._active_tasks.get(task_id)
        if not task:
            return False

        start_time = time.time()
        while not task.is_terminal():
            if timeout and (time.time() - start_time) > timeout:
                return False
            await asyncio.sleep(0.1)

        return True

    async def wait_all(self, timeout: Optional[float] = None) -> bool:
        """Wait for all active tasks to complete."""
        return await self._agent_manager.wait_for_all_tasks(timeout)

    def get_statistics(self) -> Dict[str, Any]:
        """Get coordinator statistics."""
        manager_stats = self._agent_manager.get_statistics()

        duration = 0.0
        if self._start_time:
            duration = time.time() - self._start_time

        return {
            "phase": self._phase.value,
            "active_tasks": len(self._active_tasks),
            "notifications": len(self._task_notifications),
            "workers": manager_stats["total_agents"],
            "total_tasks": manager_stats["total_tasks_executed"],
            "duration_seconds": duration,
        }

    def get_notifications(self) -> List[TaskNotification]:
        """Get all task notifications."""
        return self._task_notifications.copy()

    async def collect_results(self) -> Dict[str, List[TaskResult]]:
        """Collect results from all workers."""
        return await self._agent_manager.collect_results()

    def __repr__(self) -> str:
        return f"Coordinator(phase={self._phase.value}, tasks={len(self._active_tasks)})"
