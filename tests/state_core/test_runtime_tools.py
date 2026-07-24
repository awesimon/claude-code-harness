from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from harness.context import PermissionMode, RuntimeContext
from harness.runtime import ToolRuntime
from models import Base
from state_core import SessionRuntime, SQLAlchemyStateStore, TaskMode
from tools import ToolRegistry


@pytest.fixture
def runtime(tmp_path: Path) -> SessionRuntime:
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return SessionRuntime("session-tools", SQLAlchemyStateStore(factory))


async def execute(
    name: str,
    payload: dict,
    runtime: SessionRuntime,
    tmp_path: Path,
    *,
    agent_id: str | None = None,
    approval_callback=None,
    permission_mode: PermissionMode = PermissionMode.DEFAULT,
):
    context = RuntimeContext(
        session_id=runtime.session_id,
        workspace_root=tmp_path,
        permission_mode=permission_mode,
        approval_callback=approval_callback,
        metadata={"session_runtime": runtime, "agent_id": agent_id},
    )
    return await ToolRuntime(ToolRegistry).execute(name, payload, context)


@pytest.mark.asyncio
async def test_task_v2_tools_use_runtime_and_node_wire_names(
    runtime: SessionRuntime, tmp_path: Path
) -> None:
    created = await execute(
        "task_create",
        {
            "subject": "Implement",
            "description": "Build it",
            "active_form": "Building",
            "metadata": {"keep": 1, "remove": 2},
        },
        runtime,
        tmp_path,
    )
    assert created.result.success
    task_id = created.result.data["task"]["id"]

    updated = await execute(
        "task_update",
        {
            "task_id": task_id,
            "status": "in_progress",
            "metadata": {"remove": None, "added": True},
        },
        runtime,
        tmp_path,
    )
    assert updated.result.data["updatedFields"] == ["status", "metadata"]
    assert runtime.get_task(task_id).metadata == {"keep": 1, "added": True}

    listed = await execute("task_list", {}, runtime, tmp_path)
    assert listed.result.data["tasks"] == [
        {
            "id": task_id,
            "subject": "Implement",
            "status": "in_progress",
            "owner": None,
            "blockedBy": [],
        }
    ]

    missing = await execute("task_update", {"task_id": "999"}, runtime, tmp_path)
    assert missing.result.success
    assert missing.result.data == {
        "success": False,
        "taskId": "999",
        "updatedFields": [],
        "error": "Task not found",
    }


@pytest.mark.asyncio
async def test_todo_compatibility_is_exclusive_scoped_and_clears_storage(
    runtime: SessionRuntime, tmp_path: Path
) -> None:
    runtime.enable_todo_v1()
    assert runtime.task_mode is TaskMode.TODO_V1

    submitted = [
        {"content": "one", "status": "pending", "activeForm": "Doing one"},
        {"content": "two", "status": "completed", "activeForm": "Doing two"},
    ]
    first = await execute("todo_write", {"todos": submitted}, runtime, tmp_path, agent_id="agent-1")
    assert first.result.success
    assert first.result.data == {"oldTodos": [], "newTodos": submitted}
    assert runtime.state.todos["agent-1"] == submitted

    completed = [{"content": "one", "status": "completed", "activeForm": "Doing one"}]
    second = await execute(
        "todo_write", {"todos": completed}, runtime, tmp_path, agent_id="agent-1"
    )
    assert second.result.data["newTodos"] == completed
    assert runtime.state.todos["agent-1"] == []

    task = await execute(
        "task_create", {"subject": "no", "description": "disabled"}, runtime, tmp_path
    )
    assert not task.result.success


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", [PermissionMode.DEFAULT, PermissionMode.AUTO, PermissionMode.BYPASS]
)
async def test_plan_requires_approval_and_restores_pre_plan_permission(
    runtime: SessionRuntime,
    tmp_path: Path,
    mode: PermissionMode,
) -> None:
    entered = await execute("enter_plan_mode", {}, runtime, tmp_path, permission_mode=mode)
    assert entered.result.data["state"] == "planning"

    rejected = await execute(
        "exit_plan_mode",
        {"plan": "# Plan\n\nDo the work.", "allowed_prompts": []},
        runtime,
        tmp_path,
        approval_callback=lambda _: False,
        permission_mode=PermissionMode.PLAN,
    )
    assert rejected.result.data["state"] == "planning"
    assert runtime.state.permission_mode == "plan"

    approved = await execute(
        "exit_plan_mode",
        {"plan": "# Plan\n\nDo the work.", "allowed_prompts": []},
        runtime,
        tmp_path,
        approval_callback=lambda _: True,
        permission_mode=PermissionMode.PLAN,
    )
    assert approved.result.data["approved"] is True
    assert runtime.state.permission_mode == mode.value
    assert Path(approved.result.data["filePath"]).read_text() == "# Plan\n\nDo the work."


@pytest.mark.asyncio
async def test_plan_without_approval_stays_pending(runtime: SessionRuntime, tmp_path: Path) -> None:
    await execute("enter_plan_mode", {}, runtime, tmp_path)
    pending = await execute(
        "exit_plan_mode", {"plan": "# Pending", "allowed_prompts": []}, runtime, tmp_path
    )
    assert pending.result.data["awaitingApproval"] is True
    assert runtime.state.plan.state.value == "pending_approval"
    with pytest.raises(RuntimeError, match="approved"):
        runtime.exit_plan()
