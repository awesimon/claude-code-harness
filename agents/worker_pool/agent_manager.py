"""WorkerPoolManager: 线程池/进程池风格的 Worker Agent 生命周期管理。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Callable, Awaitable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from .agent import Agent, AgentConfig
from .task import Task, TaskResult
from .enums import AgentStatus, Result


class WorkerPoolManager:
    """
    WorkerPoolManager handles the creation, destruction, and coordination of Agents.

    Similar to the coordinator mode in Claude Code, this class manages:
    - Creating and destroying worker agents
    - Parallel task execution
    - Result collection and aggregation
    - State monitoring
    """

    def __init__(
        self,
        max_agents: int = 10,
        use_process_pool: bool = False,
        max_workers: Optional[int] = None,
    ):
        self.max_agents = max_agents
        self.use_process_pool = use_process_pool

        # Agent storage
        self._agents: Dict[str, Agent] = {}
        self._agents_lock = asyncio.Lock()

        # Agent type tracking
        self._agents_by_type: Dict[str, List[str]] = {}

        # Pool executors for parallel execution
        if use_process_pool:
            self._executor: Optional[ProcessPoolExecutor] = ProcessPoolExecutor(
                max_workers=max_workers
            )
        else:
            self._executor: Optional[ThreadPoolExecutor] = ThreadPoolExecutor(
                max_workers=max_workers
            )

        # Callbacks
        self._on_agent_create: Optional[Callable[[Agent], Awaitable[None]]] = None
        self._on_agent_destroy: Optional[Callable[[Agent], Awaitable[None]]] = None
        self._on_task_complete: Optional[Callable[[Agent, Task], Awaitable[None]]] = None
        self._on_task_fail: Optional[Callable[[Agent, Task, str], Awaitable[None]]] = None

        # Running state
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None

    async def create_agent(
        self,
        config: Optional[AgentConfig] = None,
        agent_type: str = "worker",
        start: bool = True,
    ) -> Result[Agent]:
        """
        Create a new agent.

        Args:
            config: Agent configuration
            agent_type: Type of agent (worker, coordinator, etc.)
            start: Whether to start the agent immediately

        Returns:
            Result containing the created Agent or error message
        """
        async with self._agents_lock:
            if len(self._agents) >= self.max_agents:
                return Result.fail(f"Maximum number of agents ({self.max_agents}) reached")

        # Create agent
        agent = Agent(config=config, agent_type=agent_type)

        # Set up agent callbacks
        async def on_task_complete(task: Task) -> None:
            if self._on_task_complete:
                await self._on_task_complete(agent, task)

        async def on_task_fail(task: Task, error: str) -> None:
            if self._on_task_fail:
                await self._on_task_fail(agent, task, error)

        agent.set_callbacks(
            on_task_complete=on_task_complete,
            on_task_fail=on_task_fail,
        )

        # Store agent
        async with self._agents_lock:
            self._agents[agent.id] = agent
            if agent_type not in self._agents_by_type:
                self._agents_by_type[agent_type] = []
            self._agents_by_type[agent_type].append(agent.id)

        # Start agent if requested
        if start:
            await agent.start()

        # Trigger callback
        if self._on_agent_create:
            try:
                await self._on_agent_create(agent)
            except Exception as e:
                print(f"Agent create callback error: {e}")

        return Result.ok(agent)

    async def create_agents(
        self,
        count: int,
        config: Optional[AgentConfig] = None,
        agent_type: str = "worker",
        start: bool = True,
    ) -> Result[List[Agent]]:
        """Create multiple agents at once."""
        agents = []
        for _ in range(count):
            result = await self.create_agent(config, agent_type, start)
            if result.is_ok():
                agents.append(result.unwrap())
            else:
                # Clean up created agents on failure
                for agent in agents:
                    await self.destroy_agent(agent.id)
                return Result.fail(f"Failed to create agent: {result.error}")
        return Result.ok(agents)

    async def destroy_agent(
        self,
        agent_id: str,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> Result[bool]:
        """
        Destroy an agent and clean up resources.

        Args:
            agent_id: ID of the agent to destroy
            wait: Whether to wait for pending tasks
            timeout: Timeout for waiting

        Returns:
            Result indicating success or failure
        """
        async with self._agents_lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return Result.fail(f"Agent {agent_id} not found")

        # Stop the agent
        await agent.stop(wait=wait, timeout=timeout)

        # Remove from storage
        async with self._agents_lock:
            del self._agents[agent_id]
            agent_type = agent.agent_type
            if agent_type in self._agents_by_type:
                self._agents_by_type[agent_type] = [
                    aid for aid in self._agents_by_type[agent_type] if aid != agent_id
                ]

        # Trigger callback
        if self._on_agent_destroy:
            try:
                await self._on_agent_destroy(agent)
            except Exception as e:
                print(f"Agent destroy callback error: {e}")

        return Result.ok(True)

    async def destroy_all_agents(
        self,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> None:
        """Destroy all agents."""
        agent_ids = list(self._agents.keys())
        for agent_id in agent_ids:
            await self.destroy_agent(agent_id, wait=wait, timeout=timeout)

    async def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get an agent by ID."""
        async with self._agents_lock:
            return self._agents.get(agent_id)

    async def get_agents_by_type(self, agent_type: str) -> List[Agent]:
        """Get all agents of a specific type."""
        async with self._agents_lock:
            agent_ids = self._agents_by_type.get(agent_type, [])
            return [self._agents[aid] for aid in agent_ids if aid in self._agents]

    async def get_all_agents(self) -> List[Agent]:
        """Get all agents."""
        async with self._agents_lock:
            return list(self._agents.values())

    async def assign_task(self, agent_id: str, task: Task) -> Result[bool]:
        """Assign a task to a specific agent."""
        agent = await self.get_agent(agent_id)
        if not agent:
            return Result.fail(f"Agent {agent_id} not found")

        await agent.assign_task(task)
        return Result.ok(True)

    async def assign_tasks_parallel(
        self,
        tasks: List[Task],
        agent_type: str = "worker",
    ) -> Result[List[Task]]:
        """
        Assign tasks to available agents in parallel.

        Args:
            tasks: List of tasks to assign
            agent_type: Type of agents to use

        Returns:
            Result containing the list of assigned tasks
        """
        agents = await self.get_agents_by_type(agent_type)
        if not agents:
            return Result.fail(f"No agents of type '{agent_type}' available")

        # Distribute tasks round-robin
        for i, task in enumerate(tasks):
            agent = agents[i % len(agents)]
            await agent.assign_task(task)

        return Result.ok(tasks)

    async def broadcast_task(
        self,
        task_factory: Callable[[], Task],
        agent_type: str = "worker",
    ) -> Result[List[Task]]:
        """
        Broadcast the same task to all agents of a specific type.

        Args:
            task_factory: Factory function that creates a task
            agent_type: Type of agents to broadcast to

        Returns:
            Result containing the list of created tasks
        """
        agents = await self.get_agents_by_type(agent_type)
        if not agents:
            return Result.fail(f"No agents of type '{agent_type}' available")

        tasks = []
        for _ in agents:
            task = task_factory()
            tasks.append(task)

        for agent, task in zip(agents, tasks):
            await agent.assign_task(task)

        return Result.ok(tasks)

    async def wait_for_all_tasks(self, timeout: Optional[float] = None) -> bool:
        """Wait for all agents to complete their tasks."""
        agents = await self.get_all_agents()
        if not agents:
            return True

        tasks = [agent.wait_all() for agent in agents]
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def collect_results(
        self,
        agent_type: Optional[str] = None,
    ) -> Dict[str, List[TaskResult]]:
        """
        Collect results from agents.

        Args:
            agent_type: Optional filter by agent type

        Returns:
            Dictionary mapping agent IDs to their task results
        """
        if agent_type:
            agents = await self.get_agents_by_type(agent_type)
        else:
            agents = await self.get_all_agents()

        results = {}
        for agent in agents:
            agent_results = []
            for task in agent.completed_tasks:
                if task.result:
                    agent_results.append(task.result)
            if agent_results:
                results[agent.id] = agent_results

        return results

    def get_statistics(self) -> Dict[str, Any]:
        """Get overall agent manager statistics."""
        total_agents = len(self._agents)
        agents_by_status: Dict[str, int] = {}
        total_tasks = 0

        for agent in self._agents.values():
            status = agent.status.value
            agents_by_status[status] = agents_by_status.get(status, 0) + 1
            total_tasks += agent.total_tasks_executed

        return {
            "total_agents": total_agents,
            "agents_by_status": agents_by_status,
            "agents_by_type": {k: len(v) for k, v in self._agents_by_type.items()},
            "total_tasks_executed": total_tasks,
        }

    def set_callbacks(
        self,
        on_agent_create: Optional[Callable[[Agent], Awaitable[None]]] = None,
        on_agent_destroy: Optional[Callable[[Agent], Awaitable[None]]] = None,
        on_task_complete: Optional[Callable[[Agent, Task], Awaitable[None]]] = None,
        on_task_fail: Optional[Callable[[Agent, Task, str], Awaitable[None]]] = None,
    ) -> None:
        """Set manager lifecycle callbacks."""
        self._on_agent_create = on_agent_create
        self._on_agent_destroy = on_agent_destroy
        self._on_task_complete = on_task_complete
        self._on_task_fail = on_task_fail

    async def start_monitoring(self, interval: float = 5.0) -> None:
        """Start background monitoring of agents."""
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop(interval))

    async def stop_monitoring(self) -> None:
        """Stop background monitoring."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self, interval: float) -> None:
        """Background monitoring loop."""
        while self._running:
            try:
                stats = self.get_statistics()
                # Could log or report stats here
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Monitor loop error: {e}")
                await asyncio.sleep(interval)

    async def shutdown(self, wait: bool = True, timeout: Optional[float] = None) -> None:
        """Shutdown the agent manager and all agents."""
        await self.stop_monitoring()
        await self.destroy_all_agents(wait=wait, timeout=timeout)

        # Shutdown executor
        if self._executor:
            self._executor.shutdown(wait=wait)

    async def start(self) -> None:
        """Start the agent manager."""
        await self.start_monitoring()

    async def stop(self, wait: bool = True, timeout: Optional[float] = None) -> None:
        """Stop the agent manager."""
        await self.shutdown(wait=wait, timeout=timeout)

    def __repr__(self) -> str:
        return f"WorkerPoolManager(agents={len(self._agents)})"
