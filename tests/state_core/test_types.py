import json
from datetime import datetime, timedelta, timezone
from typing import get_type_hints

import pytest

from state_core import (
    ClaimResult,
    CommitResult,
    EventType,
    InvalidTransition,
    NewTask,
    PendingSessionEvent,
    Plan,
    PlanState,
    SessionEvent,
    SessionHealth,
    SessionSnapshot,
    SessionState,
    StateRepository,
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
    state.last_event_id = event.id
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


def test_json_metadata_and_payload_round_trip_as_detached_trees():
    metadata = {
        "nested": {"items": [None, True, 7, 1.25, "value"]},
        "empty": {},
    }
    task = TaskItem("1", "Subject", "Description", metadata=metadata)
    event = SessionEvent(
        id=1,
        session_id="session-1",
        event_type=EventType.TASK_MUTATION,
        payload=metadata,
    )

    task_wire = task.to_dict()
    event_wire = event.to_dict()
    metadata["nested"]["items"].append("mutated")

    assert task_wire["metadata"]["nested"]["items"] == [None, True, 7, 1.25, "value"]
    assert event_wire["payload"]["nested"]["items"] == [None, True, 7, 1.25, "value"]
    assert TaskItem.from_dict(task_wire).metadata == task_wire["metadata"]
    assert SessionEvent.from_dict(event_wire).payload == event_wire["payload"]
    json.dumps(task_wire, allow_nan=False)
    json.dumps(event_wire, allow_nan=False)


@pytest.mark.parametrize(
    "unsupported",
    [
        {"set"},
        b"bytes",
        object(),
        {1: "non-string key"},
        float("nan"),
        float("inf"),
        TaskStatus.PENDING,
        datetime(2026, 7, 24, tzinfo=timezone.utc),
    ],
)
def test_arbitrary_json_fields_reject_non_json_values(unsupported):
    task = TaskItem("1", "Subject", "Description", metadata={"value": unsupported})

    with pytest.raises((TypeError, ValueError)):
        task.to_dict()


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


def test_session_decoder_requires_the_complete_persisted_shape():
    wire = SessionState.new("session-4").to_dict()
    required_fields = {
        "sessionId",
        "revision",
        "permissionMode",
        "prePlanPermissionMode",
        "plan",
        "taskListId",
        "taskMode",
        "todos",
        "transcriptCursor",
        "lastEventId",
        "agents",
        "health",
        "createdAt",
        "updatedAt",
        "interruptedAt",
    }

    assert set(wire) == required_fields
    for field_name in required_fields:
        truncated = dict(wire)
        del truncated[field_name]
        with pytest.raises(KeyError, match=field_name):
            SessionState.from_dict(truncated)


def test_session_decode_is_deterministic_and_validates_cursors():
    wire = SessionState.new(
        "session-5",
        now=datetime(2026, 7, 24, 12, 34, 56, 789000, tzinfo=timezone.utc),
    ).to_dict()

    first = SessionState.from_dict(wire)
    second = SessionState.from_dict(wire)

    assert first == second
    assert first.to_dict() == wire

    wire["lastEventId"] = -1
    with pytest.raises(ValueError, match="lastEventId"):
        SessionState.from_dict(wire)


def test_event_and_snapshot_decoders_require_complete_shapes():
    state = SessionState.new("session-6")
    event_wire = SessionEvent(1, state.session_id, EventType.CHECKPOINT).to_dict()
    snapshot_wire = SessionSnapshot(state.session_id, 0, state).to_dict()

    for field_name in event_wire:
        truncated = dict(event_wire)
        del truncated[field_name]
        with pytest.raises(KeyError, match=field_name):
            SessionEvent.from_dict(truncated)

    for field_name in snapshot_wire:
        truncated = dict(snapshot_wire)
        del truncated[field_name]
        with pytest.raises(KeyError, match=field_name):
            SessionSnapshot.from_dict(truncated)


@pytest.mark.parametrize(
    ("session_id", "last_event_id", "message"),
    [
        ("different-session", 0, "sessionId"),
        ("session-7", 2, "lastEventId"),
    ],
)
def test_snapshot_decoder_validates_nested_state_identity_and_cursor(
    session_id, last_event_id, message
):
    state = SessionState.new("session-7")
    wire = SessionSnapshot(state.session_id, state.last_event_id, state).to_dict()
    wire["sessionId"] = session_id
    wire["lastEventId"] = last_event_id

    with pytest.raises(ValueError, match=message):
        SessionSnapshot.from_dict(wire)


def test_node_timestamp_codec_normalizes_to_canonical_utc():
    wire = SessionState.new("session-8").to_dict()
    wire["createdAt"] = "2026-07-24T12:34:56.789Z"
    wire["updatedAt"] = "2026-07-24T20:34:56.789+08:00"

    decoded = SessionState.from_dict(wire)

    assert decoded.created_at == datetime(2026, 7, 24, 12, 34, 56, 789000, tzinfo=timezone.utc)
    assert decoded.updated_at == decoded.created_at
    assert decoded.to_dict()["createdAt"] == "2026-07-24T12:34:56.789Z"
    assert decoded.to_dict()["updatedAt"] == "2026-07-24T12:34:56.789Z"


def test_timestamp_codec_rejects_naive_values():
    state = SessionState.new("session-9")
    state.updated_at = datetime(2026, 7, 24, 12, 34, 56)

    with pytest.raises(ValueError, match="timezone-aware"):
        state.to_dict()

    wire = SessionState.new("session-9").to_dict()
    wire["updatedAt"] = "2026-07-24T12:34:56.789"
    with pytest.raises(ValueError, match="timezone-aware"):
        SessionState.from_dict(wire)


@pytest.mark.parametrize("success", ["false", "true", 0, 1, None])
def test_claim_result_decoder_requires_boolean_success(success):
    wire = ClaimResult(success=False).to_dict()
    wire["success"] = success

    with pytest.raises(TypeError, match="success"):
        ClaimResult.from_dict(wire)


@pytest.mark.parametrize(
    "allowed_prompts",
    [
        [{"tool": "Bash", "prompt": {"nested": "invalid"}}],
        [{"tool": 3, "prompt": "run tests"}],
        [{1: "Bash", "prompt": "run tests"}],
        ["not-a-mapping"],
    ],
)
def test_plan_decoder_rejects_non_string_allowed_prompt_entries(allowed_prompts):
    wire = Plan().to_dict()
    wire["allowedPrompts"] = allowed_prompts

    with pytest.raises(TypeError, match="allowedPrompts"):
        Plan.from_dict(wire)


def test_plan_allowed_prompts_are_detached_on_decode():
    wire = Plan(allowed_prompts=[{"tool": "Bash", "prompt": "run tests"}]).to_dict()

    plan = Plan.from_dict(wire)
    wire["allowedPrompts"][0]["prompt"] = "changed"

    assert plan.allowed_prompts == [{"tool": "Bash", "prompt": "run tests"}]


def test_timestamp_codec_normalizes_offset_datetime_instances():
    offset = timezone(timedelta(hours=8))
    state = SessionState.new(
        "session-10",
        now=datetime(2026, 7, 24, 20, 34, 56, 789000, tzinfo=offset),
    )

    assert state.to_dict()["createdAt"] == "2026-07-24T12:34:56.789Z"


def test_plan_and_event_timestamps_preserve_microsecond_precision():
    timestamp = datetime(2026, 7, 24, 12, 34, 56, 123456, tzinfo=timezone.utc)
    plan = Plan(submitted_at=timestamp, approved_at=timestamp)
    event = SessionEvent(
        id=1,
        session_id="session-microseconds",
        event_type=EventType.CHECKPOINT,
        created_at=timestamp,
    )

    plan_wire = plan.to_dict()
    event_wire = event.to_dict()

    assert plan_wire["submittedAt"] == "2026-07-24T12:34:56.123456Z"
    assert event_wire["createdAt"] == "2026-07-24T12:34:56.123456Z"
    assert Plan.from_dict(plan_wire) == plan
    assert SessionEvent.from_dict(event_wire) == event


def test_pending_event_can_reference_an_existing_persisted_parent():
    pending = PendingSessionEvent(
        sequence=0,
        session_id="session-events",
        event_type=EventType.TOOL_RESULT,
        payload={"result": "ok"},
        parent_event_id=41,
    )

    wire = pending.to_dict()

    assert "id" not in wire
    assert wire["sequence"] == 0
    assert wire["parentEventId"] == 41
    assert wire["parentSequence"] is None
    assert PendingSessionEvent.from_dict(wire) == pending


def test_pending_batch_links_later_result_to_earlier_tool_call():
    tool_call = PendingSessionEvent(
        sequence=0,
        session_id="session-events",
        event_type=EventType.TOOL_CALL,
        payload={"toolCallId": "call-1"},
    )
    tool_result = PendingSessionEvent(
        sequence=1,
        session_id="session-events",
        event_type=EventType.TOOL_RESULT,
        payload={"toolCallId": "call-1", "content": "ok"},
        parent_sequence=tool_call.sequence,
    )

    assert [event.sequence for event in (tool_call, tool_result)] == [0, 1]
    assert tool_result.to_dict()["parentSequence"] == 0


@pytest.mark.parametrize(
    ("sequence", "parent_sequence"),
    [(0, 0), (1, 1), (1, 2)],
)
def test_pending_event_rejects_self_and_forward_batch_links(sequence, parent_sequence):
    with pytest.raises(ValueError, match="parentSequence"):
        PendingSessionEvent(
            sequence=sequence,
            session_id="session-events",
            event_type=EventType.TOOL_RESULT,
            parent_sequence=parent_sequence,
        )


def test_pending_event_rejects_two_parent_link_types():
    with pytest.raises(ValueError, match="only one parent"):
        PendingSessionEvent(
            sequence=1,
            session_id="session-events",
            event_type=EventType.TOOL_RESULT,
            parent_event_id=9,
            parent_sequence=0,
        )


@pytest.mark.parametrize("event_id", [0, -1])
def test_persisted_event_requires_positive_assigned_id(event_id):
    with pytest.raises(ValueError, match="id"):
        SessionEvent(
            id=event_id,
            session_id="session-events",
            event_type=EventType.CHECKPOINT,
        )


@pytest.mark.parametrize("parent_event_id", [0, 4, 5])
def test_persisted_event_requires_earlier_positive_parent_id(parent_event_id):
    with pytest.raises(ValueError, match="parentEventId"):
        SessionEvent(
            id=4,
            session_id="session-events",
            event_type=EventType.TOOL_RESULT,
            parent_event_id=parent_event_id,
        )


def test_commit_result_round_trip_returns_state_and_assigned_events():
    state = SessionState.new("session-events")
    state.last_event_id = 2
    events = [
        SessionEvent(1, state.session_id, EventType.TOOL_CALL),
        SessionEvent(2, state.session_id, EventType.TOOL_RESULT, parent_event_id=1),
    ]
    result = CommitResult(state=state, events=events)

    assert CommitResult.from_dict(result.to_dict()) == result


def test_state_repository_commit_contract_uses_pending_and_assigned_events():
    hints = get_type_hints(StateRepository.commit)

    assert hints["events"] == list[PendingSessionEvent]
    assert hints["return"] is CommitResult
