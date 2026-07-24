import json
from datetime import datetime, timezone

import pytest

from state_core import (
    ClaimResult,
    EventType,
    InvalidTransition,
    NewTask,
    Plan,
    PlanState,
    SessionEvent,
    SessionHealth,
    SessionSnapshot,
    SessionState,
    TaskItem,
    TaskMode,
    TaskMutation,
    TaskStatus,
)


def test_task_round_trip_preserves_node_fields():
    task = TaskItem(
        id="7",
        subject="Implement store",
        description="Persist runtime state",
        active_form="Implementing store",
        owner="agent-a",
        status=TaskStatus.IN_PROGRESS,
        blocks=["8"],
        blocked_by=["6"],
        metadata={"source": "plan"},
    )

    wire = task.to_dict()

    assert wire == {
        "id": "7",
        "subject": "Implement store",
        "description": "Persist runtime state",
        "activeForm": "Implementing store",
        "owner": "agent-a",
        "status": "in_progress",
        "blocks": ["8"],
        "blockedBy": ["6"],
        "metadata": {"source": "plan"},
    }
    assert TaskItem.from_dict(wire) == task


def test_session_state_round_trip_preserves_plan_and_todos():
    state = SessionState.new("session-1")
    state.permission_mode = "plan"
    state.pre_plan_permission_mode = "default"
    state.plan.state = PlanState.PLANNING
    state.plan.slug = "durable-session"
    state.plan.allowed_prompts = [{"tool": "Bash", "prompt": "run tests"}]
    state.task_mode = TaskMode.TODO_V1
    state.todos["agent-a"] = [
        {"content": "Inspect code", "status": "in_progress", "activeForm": "Inspecting code"}
    ]

    wire = state.to_dict()

    assert wire["sessionId"] == "session-1"
    assert wire["prePlanPermissionMode"] == "default"
    assert wire["taskMode"] == "todo_v1"
    assert wire["plan"]["allowedPrompts"] == [{"tool": "Bash", "prompt": "run tests"}]
    assert SessionState.from_dict(wire) == state


def test_nested_domain_values_serialize_without_framework_objects():
    created_at = datetime(2026, 7, 24, 10, 30, tzinfo=timezone.utc)
    state = SessionState.new("session-2", now=created_at)
    event = SessionEvent(
        id=4,
        session_id=state.session_id,
        event_type=EventType.TASK_MUTATION,
        payload={"status": "completed", "at": created_at.isoformat()},
        created_at=created_at,
        parent_event_id=3,
    )
    snapshot = SessionSnapshot(
        session_id=state.session_id,
        last_event_id=event.id,
        state=state,
        created_at=created_at,
    )

    event_wire = event.to_dict()
    snapshot_wire = snapshot.to_dict()

    assert json.loads(json.dumps(event_wire)) == event_wire
    assert json.loads(json.dumps(snapshot_wire)) == snapshot_wire
    assert SessionEvent.from_dict(event_wire) == event
    assert SessionSnapshot.from_dict(snapshot_wire) == snapshot


def test_task_command_values_round_trip_explicitly():
    new_task = NewTask(
        subject="Write store",
        description="Persist state",
        active_form="Writing store",
        metadata={"source": "plan"},
    )
    mutation = TaskMutation(
        status=TaskStatus.IN_PROGRESS,
        owner="agent-a",
        add_blocks=["2"],
        remove_blocked_by=["3"],
        metadata={"attempt": 1},
    )
    result = ClaimResult(success=False, reason="already_claimed", current_owner="agent-b")

    assert NewTask.from_dict(new_task.to_dict()) == new_task
    assert TaskMutation.from_dict(mutation.to_dict()) == mutation
    assert ClaimResult.from_dict(result.to_dict()) == result


def test_invalid_enum_value_is_rejected():
    wire = TaskItem("1", "Subject", "Description").to_dict()
    wire["status"] = "deleted"

    with pytest.raises(ValueError, match="deleted"):
        TaskItem.from_dict(wire)


def test_invalid_plan_transition_raises_typed_error():
    plan = Plan()

    with pytest.raises(InvalidTransition) as exc_info:
        plan.transition_to(PlanState.APPROVED)

    assert exc_info.value.current is PlanState.IDLE
    assert exc_info.value.target is PlanState.APPROVED


def test_session_health_rejects_unknown_wire_value():
    wire = SessionState.new("session-3").to_dict()
    wire["health"] = "unknown"

    with pytest.raises(ValueError, match="unknown"):
        SessionState.from_dict(wire)

    assert SessionHealth.RECOVERY_REQUIRED.value == "recovery_required"
