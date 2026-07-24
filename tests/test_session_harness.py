from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import harness
from harness import PermissionMode
from models import Base
from state_core import EventType, SQLAlchemyStateStore, SessionRuntimeFactory


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def factory(tmp_path: Path, workspace: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'harness.db'}")
    Base.metadata.create_all(engine)
    runtime_factory = SessionRuntimeFactory(SQLAlchemyStateStore(sessionmaker(bind=engine)))
    return harness.SessionHarnessFactory(runtime_factory, workspace_root=workspace)


def test_root_composes_authoritative_runtime_context_and_metadata(
    factory, workspace: Path
) -> None:
    harness = factory.create("root", metadata={"request_id": "request-1"})

    assert harness.session_id == harness.root_session_id == "root"
    assert harness.store is harness.session_runtime.store
    assert harness.runtime_context.session_id == "root"
    assert harness.effective_cwd == workspace.resolve()
    assert harness.runtime_context.metadata["session_runtime"] is harness.session_runtime
    assert harness.runtime_context.metadata["agent_id"] is None
    assert harness.runtime_context.metadata["session_harness"] is harness
    assert harness.runtime_context.metadata["request_id"] == "request-1"


def test_child_scopes_share_runtime_pipeline_and_store_but_not_context(
    factory,
) -> None:
    root = factory.create("children")
    child = root.child("worker")

    assert child.session_runtime is root.session_runtime
    assert child.tool_runtime is root.tool_runtime
    assert child.store is root.store
    assert child.runtime_context is not root.runtime_context
    assert child.runtime_context.cancellation is not root.runtime_context.cancellation
    assert child.runtime_context.cancellation.parent is root.runtime_context.cancellation
    assert child.agent_id == "worker"
    assert child.parent_agent_id is None


def test_cancellation_flows_downward_without_sibling_or_parent_leaks(
    factory,
) -> None:
    root = factory.create("cancellation")
    left = root.child("left")
    grandchild = left.child("grandchild")
    right = root.child("right")

    left.runtime_context.cancellation.cancel()
    assert left.runtime_context.cancellation.cancelled
    assert grandchild.runtime_context.cancellation.cancelled
    assert not root.runtime_context.cancellation.cancelled
    assert not right.runtime_context.cancellation.cancelled

    root.runtime_context.cancellation.cancel()
    assert right.runtime_context.cancellation.cancelled


def test_child_metadata_merges_and_harness_values_cannot_be_overridden(
    factory,
) -> None:
    root = factory.create("metadata", metadata={"trace": "parent", "shared": {"key": "value"}})
    child = root.child(
        "worker",
        parent_agent_id="supervisor",
        metadata={"trace": "child", "extra": True, "agent_id": "wrong"},
    )

    assert child.parent_agent_id == "supervisor"
    assert child.runtime_context.metadata["shared"] == {"key": "value"}
    assert child.runtime_context.metadata["trace"] == "child"
    assert child.runtime_context.metadata["extra"] is True
    assert child.runtime_context.metadata["agent_id"] == "worker"
    assert child.runtime_context.metadata["session_harness"] is child


def test_child_cwd_enforces_workspace_policy_without_changing_process_cwd(
    factory, workspace: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    original_cwd = Path.cwd()
    root = factory.create("cwd", metadata={"allowed_workspaces": [outside]})

    inside = root.child("inside", cwd=workspace / "nested" / "..")
    allowed = root.child("allowed", cwd=outside)

    assert inside.effective_cwd == workspace.resolve()
    assert allowed.effective_cwd == outside.resolve()
    assert Path.cwd() == original_cwd
    with pytest.raises(harness.HarnessScopeError, match="outside"):
        root.child("rejected", cwd=tmp_path / "escape")
    assert os.getcwd() == str(original_cwd)


def test_root_metadata_cannot_expand_child_cwd_policy(
    factory, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = factory.create("protected", metadata={"_harness_root_workspace": outside})

    with pytest.raises(harness.HarnessScopeError, match="outside"):
        root.child("rejected", cwd=outside)


@pytest.mark.asyncio
async def test_root_and_child_todo_scopes_remain_isolated(factory) -> None:
    root = factory.create("todos")
    root.session_runtime.enable_todo_v1()
    left = root.child("left")
    right = root.child("right")
    todo = lambda text: [{"content": text, "status": "pending", "activeForm": text}]

    root_result = await root.tool_runtime.execute(
        "TodoWrite", {"todos": todo("root")}, root.runtime_context
    )
    left_result = await left.tool_runtime.execute(
        "TodoWrite", {"todos": todo("left")}, left.runtime_context
    )
    right_result = await right.tool_runtime.execute(
        "TodoWrite", {"todos": todo("right")}, right.runtime_context
    )

    assert root_result.result.success and left_result.result.success and right_result.result.success
    assert root.session_runtime.state.todos == {
        "todos": todo("root"),
        "left": todo("left"),
        "right": todo("right"),
    }


def test_resume_is_fresh_and_observes_durable_checkpoint_without_replaying_work(
    factory,
) -> None:
    first = factory.create("durable")
    first.session_runtime.append_event(EventType.USER_MESSAGE, {"content": "persisted"})
    first.session_runtime.checkpoint()

    resumed = factory.resume("durable")
    resumed_again = factory.resume("durable")

    assert resumed is not first
    assert resumed_again is not resumed
    assert resumed.store is first.store
    assert resumed.session_runtime.state.transcript_cursor == 1
    assert [event.event_type for event in resumed.session_runtime.events()] == [EventType.USER_MESSAGE]


def test_resume_reconciles_running_agents_through_runtime_recovery(
    factory,
) -> None:
    first = factory.create("interrupted")
    first.session_runtime.update_agent_lifecycle("worker", "running")

    resumed = factory.resume("interrupted")

    assert resumed.session_runtime.state.agents["worker"]["status"] == "interrupted"
    assert [event.event_type for event in resumed.session_runtime.events()] == [
        EventType.AGENT_LIFECYCLE,
        EventType.EXECUTION_INTERRUPTED,
    ]


def test_factory_inherits_and_allows_scope_overrides(
    tmp_path: Path, workspace: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'overrides.db'}")
    Base.metadata.create_all(engine)
    callback = lambda request: True
    factory = harness.SessionHarnessFactory(
        SessionRuntimeFactory(SQLAlchemyStateStore(sessionmaker(bind=engine))),
        workspace_root=workspace,
        permission_mode=PermissionMode.AUTO,
        approval_callback=callback,
        tool_timeout=12.0,
    )

    root = factory.create("overrides")
    child = root.child(
        "worker",
        permission_mode=PermissionMode.BYPASS,
        approval_callback=None,
        tool_timeout=3.0,
    )

    assert root.runtime_context.permission_mode is PermissionMode.AUTO
    assert root.runtime_context.approval_callback is callback
    assert root.runtime_context.tool_timeout == 12.0
    assert child.runtime_context.permission_mode is PermissionMode.BYPASS
    assert child.runtime_context.approval_callback is None
    assert child.runtime_context.tool_timeout == 3.0
