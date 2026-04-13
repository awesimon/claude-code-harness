"""Tests for the Python Agent Management System."""

import asyncio
import pytest
from agents.worker_pool import (
    Coordinator,
    Agent,
    AgentConfig,
    AgentStatus,
    Task,
    TaskConfig,
    TaskStatus,
    TaskPriority,
    ExecutionPlan,
    AgentManager,
)


@pytest.fixture
async def coordinator():
    """Create a coordinator for testing."""
    async with Coordinator() as c:
        yield c


@pytest.mark.asyncio
async def test_agent_creation():
    """Test agent creation."""
    manager = AgentManager()

    config = AgentConfig(name="test-agent", max_concurrent_tasks=2)
    result = await manager.create_agent(config)

    assert result.is_ok()
    agent = result.unwrap()
    assert agent.name == "test-agent"
    assert agent.status == AgentStatus.IDLE
    assert agent.config.max_concurrent_tasks == 2

    await manager.shutdown()


@pytest.mark.asyncio
async def test_task_execution():
    """Test task execution."""
    agent = Agent(AgentConfig(max_concurrent_tasks=1))
    await agent.start()

    async def simple_task():
        await asyncio.sleep(0.1)
        return "success"

    task = Task(description="Test task")
    task.set_executor(simple_task)
    await agent.assign_task(task)

    # Wait for task
    await asyncio.sleep(0.3)

    assert task.status == TaskStatus.COMPLETED
    assert task.result is not None
    assert task.result.success
    assert task.result.data == "success"

    await agent.stop()


@pytest.mark.asyncio
async def test_parallel_tasks():
    """Test parallel task execution."""
    async with Coordinator() as coord:
        results = []

        async def task1():
            await asyncio.sleep(0.1)
            results.append(1)
            return "result1"

        async def task2():
            await asyncio.sleep(0.1)
            results.append(2)
            return "result2"

        tasks = await coord.spawn_parallel(
            descriptions=["Task 1", "Task 2"],
            executors=[task1, task2],
        )

        # Wait for completion
        await asyncio.sleep(0.3)

        assert len(tasks) == 2
        assert all(t.result is not None for t in tasks)
        assert all(t.result.success for t in tasks)


@pytest.mark.asyncio
async def test_task_priority():
    """Test task priority ordering."""
    execution_order = []

    async def high_priority():
        execution_order.append("high")
        return "high"

    async def low_priority():
        execution_order.append("low")
        return "low"

    agent = Agent(AgentConfig(max_concurrent_tasks=1))
    await agent.start()

    # Add low priority first
    task_low = Task(description="Low", priority=TaskPriority.LOW)
    task_low.set_executor(low_priority)
    await agent.assign_task(task_low)

    # Add high priority second
    task_high = Task(description="High", priority=TaskPriority.HIGH)
    task_high.set_executor(high_priority)
    await agent.assign_task(task_high)

    await asyncio.sleep(0.3)

    assert task_high.priority.value > task_low.priority.value

    await agent.stop()


@pytest.mark.asyncio
async def test_task_failure():
    """Test task failure handling."""
    async def failing_task():
        raise ValueError("Test error")

    agent = Agent(AgentConfig())
    await agent.start()

    task = Task(description="Failing task")
    task.set_executor(failing_task)
    await agent.assign_task(task)

    await asyncio.sleep(0.2)

    assert task.status == TaskStatus.FAILED
    assert task.result is not None
    assert not task.result.success
    assert "Test error" in task.result.error

    await agent.stop()


@pytest.mark.asyncio
async def test_task_timeout():
    """Test task timeout handling."""
    async def slow_task():
        await asyncio.sleep(10)
        return "slow"

    task = Task(
        description="Slow task",
        config=TaskConfig(timeout=0.2, max_retries=0)
    )
    task.set_executor(slow_task)

    result = await task.execute()

    assert not result.success
    assert "timed out" in result.error


@pytest.mark.asyncio
async def test_execution_plan():
    """Test execution plan with phases."""
    async with Coordinator() as coord:
        executed = []

        async def phase1_task():
            executed.append("phase1")
            return "p1"

        async def phase2_task():
            executed.append("phase2")
            return "p2"

        task1 = Task(description="Phase 1")
        task1.set_executor(phase1_task)

        task2 = Task(description="Phase 2")
        task2.set_executor(phase2_task)

        plan = ExecutionPlan()
        plan.add_phase([task1])
        plan.add_phase([task2])
        plan.add_dependency(task2.id, [task1.id])

        results = await coord.execute_plan(plan)

        assert len(results) == 2
        assert all(r.success for r in results.values())


@pytest.mark.asyncio
async def test_task_dependencies():
    """Test task dependency resolution."""
    from agents.worker_pool.task_queue import TaskQueue

    queue = TaskQueue()
    execution_order = []

    async def task_a():
        execution_order.append("A")
        return "A"

    async def task_b():
        execution_order.append("B")
        return "B"

    async def task_c():
        execution_order.append("C")
        return "C"

    task_a_obj = Task(description="Task A")
    task_a_obj.set_executor(task_a)

    task_b_obj = Task(description="Task B")
    task_b_obj.set_executor(task_b)

    task_c_obj = Task(description="Task C", config=TaskConfig(dependencies=[task_a_obj.id, task_b_obj.id]))
    task_c_obj.set_executor(task_c)

    # Add in reverse order
    await queue.add_task(task_c_obj)
    await queue.add_task(task_b_obj)
    await queue.add_task(task_a_obj)

    # A and B should be available, C should not
    next_task = await queue.get_next_task()
    assert next_task in [task_a_obj, task_b_obj]

    # Complete the task
    if next_task:
        await queue.mark_complete(next_task)

    # Get next task
    next_task = await queue.get_next_task()
    assert next_task in [task_a_obj, task_b_obj]

    if next_task:
        await queue.mark_complete(next_task)

    # Now C should be available
    next_task = await queue.get_next_task()
    assert next_task == task_c_obj


@pytest.mark.asyncio
async def test_coordinator_statistics():
    """Test coordinator statistics."""
    async with Coordinator() as coord:
        stats = coord.get_statistics()

        assert "phase" in stats
        assert "active_tasks" in stats
        assert "workers" in stats


@pytest.mark.asyncio
async def test_message_notification():
    """Test task notification system."""
    from agents.worker_pool.coordinator import TaskNotification

    notification = TaskNotification(
        task_id="agent-123",
        status=TaskStatus.COMPLETED,
        summary="Task completed",
        result="Success!",
        usage={"duration_ms": 1000}
    )

    xml = notification.to_xml()

    assert "<task-notification>" in xml
    assert "agent-123" in xml
    assert "completed" in xml
    assert "Success!" in xml


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
