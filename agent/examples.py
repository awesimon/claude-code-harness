"""
Example usage of the Python Agent Management System.

This demonstrates how to use the Coordinator pattern for parallel task execution,
similar to Claude Code's coordinator mode.
"""

import asyncio
import random
from typing import Any

from python_api.agent import (
    Coordinator,
    CoordinatorConfig,
    AgentConfig,
    AgentCapabilities,
    Task,
    TaskConfig,
    TaskPriority,
    ExecutionPlan,
)


async def example_basic_task() -> None:
    """Example: Execute a basic task."""
    print("=== Example: Basic Task ===")

    async with Coordinator() as coordinator:
        # Define a simple task executor
        async def research_task() -> str:
            await asyncio.sleep(0.5)  # Simulate work
            return "Research findings: Found the bug in auth module"

        # Spawn the task
        task = await coordinator.spawn_task(
            description="Investigate auth bug",
            executor=research_task,
            priority=TaskPriority.HIGH,
        )

        # Wait for completion
        await asyncio.sleep(1)

        # Get results
        results = coordinator.get_statistics()
        print(f"Statistics: {results}")


async def example_parallel_tasks() -> None:
    """Example: Execute multiple tasks in parallel."""
    print("\n=== Example: Parallel Tasks ===")

    config = CoordinatorConfig(max_workers=3)

    async with Coordinator(config) as coordinator:
        # Define multiple task executors
        async def research_auth() -> str:
            await asyncio.sleep(0.3)
            return "Auth module: null pointer at line 42"

        async def research_tests() -> str:
            await asyncio.sleep(0.4)
            return "Tests: 3 test files found, 1 failing"

        async def research_deps() -> str:
            await asyncio.sleep(0.2)
            return "Dependencies: jwt library v2.1.0"

        # Spawn parallel tasks (like multiple AGENT_TOOL calls)
        tasks = await coordinator.spawn_parallel(
            descriptions=[
                "Research auth bug",
                "Research test coverage",
                "Research dependencies",
            ],
            executors=[research_auth, research_tests, research_deps],
            priority=TaskPriority.HIGH,
        )

        # Wait for all tasks
        await asyncio.sleep(1)

        # Synthesize results
        task_results = {
            task.id: task.result for task in tasks
            if task.result
        }

        synthesis = await coordinator.synthesize_results(task_results)
        print(f"Synthesis:\n{synthesis}")


async def example_execution_plan() -> None:
    """Example: Execute tasks in phases with dependencies."""
    print("\n=== Example: Execution Plan ===")

    async with Coordinator(CoordinatorConfig(max_workers=2)) as coordinator:
        # Phase 1: Research tasks (parallel)
        async def research1() -> str:
            await asyncio.sleep(0.2)
            return "Research 1 complete"

        async def research2() -> str:
            await asyncio.sleep(0.3)
            return "Research 2 complete"

        task1 = Task(description="Research A", priority=TaskPriority.HIGH)
        task1.set_executor(research1)

        task2 = Task(description="Research B", priority=TaskPriority.HIGH)
        task2.set_executor(research2)

        # Phase 2: Implementation (depends on research)
        async def implement() -> str:
            await asyncio.sleep(0.4)
            return "Implementation complete"

        task3 = Task(description="Implement fix", priority=TaskPriority.NORMAL)
        task3.set_executor(implement)

        # Create execution plan
        plan = ExecutionPlan()
        plan.add_phase([task1, task2])
        plan.add_phase([task3])
        plan.add_dependency(task3.id, [task1.id, task2.id])

        # Execute plan
        results = await coordinator.execute_plan(plan)

        print(f"Plan execution results:")
        for task_id, result in results.items():
            status = "SUCCESS" if result.success else "FAILED"
            print(f"  {task_id}: {status}")


async def example_worker_management() -> None:
    """Example: Create and manage workers directly."""
    print("\n=== Example: Worker Management ===")

    async with Coordinator() as coordinator:
        # Create custom workers
        worker_config = AgentConfig(
            name="specialist-worker",
            description="Worker with special capabilities",
            tools={
                AgentCapabilities.BASH,
                AgentCapabilities.FILE_READ,
                AgentCapabilities.FILE_EDIT,
            },
            max_concurrent_tasks=2,
        )

        result = await coordinator.create_worker(
            name="my-specialist",
            config=worker_config,
        )

        if result.is_ok():
            worker = result.unwrap()
            print(f"Created worker: {worker.id} ({worker.name})")
            print(f"Tools: {worker.config.tools}")

        # Show all workers
        stats = coordinator.get_statistics()
        print(f"Total workers: {stats['workers']}")


async def example_message_handling() -> None:
    """Example: Handle task notifications."""
    print("\n=== Example: Message Handling ===")

    async with Coordinator() as coordinator:
        notifications = []

        # Add message handler
        async def handle_notification(notification) -> None:
            notifications.append(notification)
            print(f"  Notification: {notification.summary}")

        coordinator.add_message_handler(handle_notification)

        # Execute tasks
        async def task_with_result() -> str:
            await asyncio.sleep(0.2)
            return "Task completed successfully"

        await coordinator.spawn_task(
            description="Test task",
            executor=task_with_result,
        )

        # Wait for processing
        await asyncio.sleep(0.5)

        print(f"Total notifications: {len(notifications)}")


async def example_error_handling() -> None:
    """Example: Handle task failures."""
    print("\n=== Example: Error Handling ===")

    async with Coordinator() as coordinator:
        async def failing_task() -> str:
            await asyncio.sleep(0.1)
            raise ValueError("Something went wrong!")

        async def success_task() -> str:
            await asyncio.sleep(0.2)
            return "This task succeeds"

        # Mix of success and failure
        tasks = await coordinator.spawn_parallel(
            descriptions=["Failing task", "Success task"],
            executors=[failing_task, success_task],
        )

        await asyncio.sleep(0.5)

        for task in tasks:
            if task.result:
                if task.result.success:
                    print(f"  {task.description}: SUCCESS - {task.result.data}")
                else:
                    print(f"  {task.description}: FAILED - {task.result.error}")


async def example_research_implementation_verification() -> None:
    """
    Example: Complete workflow matching Claude Code coordinator pattern.

    Phases:
    1. Research (parallel workers)
    2. Synthesis (coordinator understands findings)
    3. Implementation (worker implements)
    4. Verification (worker verifies)
    """
    print("\n=== Example: Research-Implementation-Verification Workflow ===")

    async with Coordinator(CoordinatorConfig(max_workers=3)) as coordinator:

        # Phase 1: Research (parallel)
        print("\n[Phase 1] Research...")

        async def investigate_bug() -> dict:
            await asyncio.sleep(0.3)
            return {
                "file": "src/auth/validate.ts",
                "line": 42,
                "issue": "null pointer when session expires",
            }

        async def check_tests() -> dict:
            await asyncio.sleep(0.2)
            return {
                "test_files": ["auth.test.ts", "session.test.ts"],
                "coverage": "85%",
            }

        research_tasks = await coordinator.spawn_parallel(
            descriptions=["Investigate auth bug", "Check test coverage"],
            executors=[investigate_bug, check_tests],
        )

        await asyncio.sleep(0.5)

        # Phase 2: Synthesis (coordinator synthesizes)
        print("\n[Phase 2] Synthesis...")

        research_results = {
            task.id: task.result for task in research_tasks if task.result
        }

        synthesis = await coordinator.synthesize_results(research_results)
        print(synthesis)

        # Phase 3: Implementation
        print("\n[Phase 3] Implementation...")

        async def implement_fix() -> str:
            await asyncio.sleep(0.4)
            return "Fixed null pointer, added null check at line 42"

        implementation = await coordinator.spawn_task(
            description="Fix null pointer in auth/validate.ts:42",
            executor=implement_fix,
            priority=TaskPriority.HIGH,
        )

        await asyncio.sleep(0.5)

        # Phase 4: Verification
        print("\n[Phase 4] Verification...")

        async def verify_fix() -> dict:
            await asyncio.sleep(0.3)
            return {
                "tests_passed": True,
                "typecheck": "OK",
                "edge_cases_tested": ["expired session", "null user"],
            }

        verification = await coordinator.spawn_task(
            description="Verify the fix works correctly",
            executor=verify_fix,
            priority=TaskPriority.HIGH,
        )

        await asyncio.sleep(0.4)

        if verification.result and verification.result.success:
            print(f"Verification passed: {verification.result.data}")
        else:
            print("Verification failed!")

        print("\n=== Workflow Complete ===")


async def main() -> None:
    """Run all examples."""
    await example_basic_task()
    await example_parallel_tasks()
    await example_execution_plan()
    await example_worker_management()
    await example_message_handling()
    await example_error_handling()
    await example_research_implementation_verification()

    print("\n" + "="*50)
    print("All examples completed!")
    print("="*50)


if __name__ == "__main__":
    asyncio.run(main())
