from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from models import Base
from state_core import SessionRuntime, SQLAlchemyStateStore, TaskMode
from tools.base import ToolRegistry
from tools.task_tools import TaskCreateTool, TaskGetTool, TaskListTool, TaskUpdateTool


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'task-tools.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def runtime(session_factory: sessionmaker[Session]) -> SessionRuntime:
    return SessionRuntime("task-tool-session", SQLAlchemyStateStore(session_factory))


def context(runtime: SessionRuntime, *, agent_id: str | None = None) -> dict[str, object]:
    return {"session_runtime": runtime, "agent_id": agent_id}


async def create_task(
    runtime: SessionRuntime,
    subject: str,
    *,
    description: str | None = None,
    active_form: str | None = None,
    metadata: dict[str, object] | None = None,
) -> str:
    payload: dict[str, object] = {
        "subject": subject,
        "description": description or f"Description for {subject}",
    }
    if active_form is not None:
        payload["activeForm"] = active_form
    if metadata is not None:
        payload["metadata"] = metadata
    result = await TaskCreateTool().run(payload, context(runtime))
    assert result.success, result.message
    return str(result.data["task"]["id"])


@pytest.mark.asyncio
async def test_task_create_accepts_node_names_and_returns_node_output(
    runtime: SessionRuntime,
) -> None:
    payload = {
        "subject": "Implement",
        "description": "Build state core",
        "activeForm": "Implementing",
        "metadata": {"source": "plan"},
    }

    result = await TaskCreateTool().run(payload, context(runtime))

    assert result.success
    assert result.data == {"task": {"id": "1", "subject": "Implement"}}
    assert payload["activeForm"] == "Implementing"
    task = runtime.get_task("1")
    assert task is not None
    assert task.active_form == "Implementing"
    assert task.metadata == {"source": "plan"}


@pytest.mark.asyncio
async def test_task_get_has_exact_node_public_shape(runtime: SessionRuntime) -> None:
    task_id = await create_task(
        runtime,
        "Inspect",
        description="Inspect the implementation",
        active_form="Inspecting",
        metadata={"private": True},
    )

    result = await TaskGetTool().run({"taskId": task_id}, context(runtime))

    assert result.success
    assert result.data == {
        "task": {
            "id": task_id,
            "subject": "Inspect",
            "description": "Inspect the implementation",
            "status": "pending",
            "blocks": [],
            "blockedBy": [],
        }
    }


@pytest.mark.asyncio
async def test_missing_task_results_are_benign(runtime: SessionRuntime) -> None:
    got = await TaskGetTool().run({"taskId": "404"}, context(runtime))
    updated = await TaskUpdateTool().run({"taskId": "404"}, context(runtime))

    assert got.success
    assert got.data == {"task": None}
    assert updated.success
    assert updated.data == {
        "success": False,
        "taskId": "404",
        "updatedFields": [],
        "error": "Task not found",
    }


@pytest.mark.asyncio
async def test_task_list_is_numeric_filters_internal_and_resolved_blockers(
    runtime: SessionRuntime,
) -> None:
    task_ids = [await create_task(runtime, f"Task {index}") for index in range(1, 12)]
    hidden = await create_task(runtime, "Internal", metadata={"_internal": True})
    target = task_ids[-1]

    blocked = await TaskUpdateTool().run(
        {"taskId": task_ids[0], "addBlocks": [target]}, context(runtime)
    )
    assert blocked.success

    before = await TaskListTool().run({}, context(runtime))
    assert [task["id"] for task in before.data["tasks"]] == task_ids
    target_wire = next(task for task in before.data["tasks"] if task["id"] == target)
    assert target_wire["blockedBy"] == [task_ids[0]]
    assert before.data["tasks"][0]["owner"] is None
    assert hidden not in [task["id"] for task in before.data["tasks"]]

    completed = await TaskUpdateTool().run(
        {"taskId": task_ids[0], "status": "completed"}, context(runtime)
    )
    assert completed.success

    after = await TaskListTool().run({}, context(runtime))
    target_wire = next(task for task in after.data["tasks"] if task["id"] == target)
    assert target_wire["blockedBy"] == []


@pytest.mark.asyncio
async def test_task_update_merges_metadata_and_null_deletes(runtime: SessionRuntime) -> None:
    task_id = await create_task(
        runtime,
        "Metadata",
        metadata={"keep": "old", "remove": True},
    )

    result = await TaskUpdateTool().run(
        {
            "taskId": task_id,
            "metadata": {"keep": "new", "remove": None, "nested": {"ok": True}},
        },
        context(runtime),
    )

    assert result.success
    assert result.data == {
        "success": True,
        "taskId": task_id,
        "updatedFields": ["metadata"],
        "statusChange": None,
    }
    task = runtime.get_task(task_id)
    assert task is not None
    assert task.metadata == {"keep": "new", "nested": {"ok": True}}


@pytest.mark.asyncio
async def test_task_update_adds_reciprocal_dependencies_once(runtime: SessionRuntime) -> None:
    first = await create_task(runtime, "First")
    second = await create_task(runtime, "Second")
    third = await create_task(runtime, "Third")

    result = await TaskUpdateTool().run(
        {"taskId": first, "addBlocks": [second], "addBlockedBy": [third]},
        context(runtime),
    )

    assert result.success
    assert result.data["updatedFields"] == ["blocks", "blockedBy"]
    first_task = runtime.get_task(first)
    second_task = runtime.get_task(second)
    third_task = runtime.get_task(third)
    assert first_task is not None
    assert second_task is not None
    assert third_task is not None
    assert first_task.blocks == [second]
    assert second_task.blocked_by == [first]
    assert first_task.blocked_by == [third]
    assert third_task.blocks == [first]

    duplicate = await TaskUpdateTool().run(
        {"taskId": first, "addBlocks": [second], "addBlockedBy": [third]},
        context(runtime),
    )
    assert duplicate.success
    assert duplicate.data["updatedFields"] == []


@pytest.mark.asyncio
async def test_deleted_is_an_action_and_cleans_dependencies(runtime: SessionRuntime) -> None:
    blocker = await create_task(runtime, "Blocker")
    target = await create_task(runtime, "Target")
    await TaskUpdateTool().run({"taskId": blocker, "addBlocks": [target]}, context(runtime))

    result = await TaskUpdateTool().run({"taskId": blocker, "status": "deleted"}, context(runtime))

    assert result.success
    assert result.data == {
        "success": True,
        "taskId": blocker,
        "updatedFields": ["deleted"],
        "statusChange": {"from": "pending", "to": "deleted"},
    }
    assert runtime.get_task(blocker) is None
    target_task = runtime.get_task(target)
    assert target_task is not None
    assert target_task.blocked_by == []


@pytest.mark.asyncio
async def test_claim_conflict_keeps_current_owner(
    session_factory: sessionmaker[Session],
) -> None:
    creator = SessionRuntime("claim-session", SQLAlchemyStateStore(session_factory))
    task_id = await create_task(creator, "Claim me")
    first_runtime = SessionRuntime("claim-session", SQLAlchemyStateStore(session_factory))
    second_runtime = SessionRuntime("claim-session", SQLAlchemyStateStore(session_factory))
    barrier = Barrier(2)

    def claim(runtime: SessionRuntime, owner: str):
        barrier.wait()
        return asyncio.run(
            TaskUpdateTool().run(
                {"taskId": task_id, "status": "in_progress", "owner": owner},
                context(runtime, agent_id=owner),
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(claim, first_runtime, "agent-a")
        second_future = pool.submit(claim, second_runtime, "agent-b")
        results = [first_future.result(), second_future.result()]

    winner = next(result for result in results if result.data["success"])
    loser = next(result for result in results if not result.data["success"])
    assert winner.success
    assert winner.data["updatedFields"] == ["owner", "status"]
    assert winner.data["statusChange"] == {"from": "pending", "to": "in_progress"}
    assert loser.success
    task = creator.get_task(task_id)
    assert task is not None
    assert task.owner in {"agent-a", "agent-b"}
    assert task.owner in loser.data["error"]
    assert task.status.value == "in_progress"


@pytest.mark.asyncio
async def test_owner_can_be_reassigned_outside_atomic_claim(runtime: SessionRuntime) -> None:
    task_id = await create_task(runtime, "Reassign me")
    first = await TaskUpdateTool().run({"taskId": task_id, "owner": "agent-a"}, context(runtime))
    second = await TaskUpdateTool().run({"taskId": task_id, "owner": "agent-b"}, context(runtime))

    assert first.data["success"] is True
    assert second.data["success"] is True
    task = runtime.get_task(task_id)
    assert task is not None
    assert task.owner == "agent-b"


def test_task_tools_are_enabled_only_for_task_v2(runtime: SessionRuntime) -> None:
    tool = TaskCreateTool()
    assert tool.is_enabled(context(runtime))

    runtime.enable_todo_v1()

    assert runtime.task_mode is TaskMode.TODO_V1
    assert not tool.is_enabled(context(runtime))


def test_task_tool_specs_use_node_names(runtime: SessionRuntime) -> None:
    create_spec = ToolRegistry.get_spec("TaskCreate")
    update_spec = ToolRegistry.get_spec("task_update")

    assert create_spec is not None
    assert create_spec.name == "task_create"
    assert set(create_spec.parameters["properties"]) == {
        "subject",
        "description",
        "activeForm",
        "metadata",
    }
    assert update_spec is not None
    assert {
        "taskId",
        "subject",
        "description",
        "activeForm",
        "status",
        "owner",
        "addBlocks",
        "addBlockedBy",
        "metadata",
    } == set(update_spec.parameters["properties"])
