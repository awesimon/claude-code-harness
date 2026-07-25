from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base
from state_core import (
    NewTask,
    SessionRuntimeFactory,
    SQLAlchemyStateStore,
    TaskMutation,
    TaskStatus,
)
from tools.verify_plan_tool import VerifyPlanExecutionTool


@pytest.fixture
def runtime(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'verify.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    runtime = SessionRuntimeFactory(SQLAlchemyStateStore(factory)).create("session-1")
    runtime.store.metadata.put(
        runtime.session_id,
        "api.plan",
        {"id": "plan-1", "version": 1, "deleted": False},
    )
    return runtime


@pytest.mark.asyncio
async def test_verify_plan_uses_durable_task_completion(runtime, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_VERIFY_PLAN", "true")
    task = runtime.create_task(NewTask(subject="Build API", description="Implement it"))
    runtime.update_task(task.id, TaskMutation(status=TaskStatus.COMPLETED))

    result = await VerifyPlanExecutionTool().run(
        {"plan_id": "plan-1", "expected_steps": ["Build API"]},
        {"session_runtime": runtime},
    )

    assert result.success
    assert result.data == {
        "plan_id": "plan-1",
        "verified": True,
        "completed_steps": 1,
        "total_steps": 1,
        "missing_steps": [],
        "unexpected_steps": [],
    }


@pytest.mark.asyncio
async def test_verify_plan_reports_incomplete_and_unexpected_tasks(runtime, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_VERIFY_PLAN", "true")
    runtime.create_task(NewTask(subject="Build API", description="Implement it"))
    runtime.create_task(NewTask(subject="Extra task", description="Not in plan"))

    result = await VerifyPlanExecutionTool().run(
        {
            "plan_id": "plan-1",
            "expected_steps": ["Build API"],
            "strict": True,
        },
        {"session_runtime": runtime},
    )

    assert result.success
    assert not result.data["verified"]
    assert result.data["missing_steps"] == ["Build API"]
    assert result.data["unexpected_steps"] == ["Extra task"]


@pytest.mark.asyncio
async def test_verify_plan_rejects_unknown_plan(runtime, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_VERIFY_PLAN", "true")

    result = await VerifyPlanExecutionTool().run(
        {"plan_id": "missing"},
        {"session_runtime": runtime},
    )

    assert not result.success
    assert "does not match" in result.message
