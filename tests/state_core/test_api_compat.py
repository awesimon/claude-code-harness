from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app_context
import main
import services.task_service as task_service_module
from harness import SessionHarnessFactory
from models import Base, Conversation, Message, Plan, Task, TaskStatus, get_db
from query_engine import QueryEngine
from services.llm_service import ChatCompletionResponse
from state_core import (
    AgentRecord,
    AgentStatus,
    AgentTerminationReason,
    EventType,
    NewTask,
    SessionRuntimeFactory,
    SQLAlchemyStateStore,
    TaskMutation,
)


def _create_agent_record(
    runtime_factory: SessionRuntimeFactory,
    workspace: Path,
    agent_id: str,
    *,
    status: AgentStatus = AgentStatus.PENDING,
    surface: str | None = "/agents",
) -> AgentRecord:
    api_compat = {"name": agent_id, "type": "worker"}
    if surface is not None:
        api_compat["surface"] = surface
    record = AgentRecord(
        agent_id=agent_id,
        root_session_id=f"root-{agent_id}",
        agent_type="Explore",
        prompt="inspect",
        description=agent_id,
        is_background=True,
        effective_cwd=str(workspace),
        definition_snapshot={"metadata": {"api_compat": api_compat}},
    )
    repository = runtime_factory.store.agents
    record = repository.create(record)
    if status is AgentStatus.PENDING:
        return record
    if status is AgentStatus.RUNNING:
        return repository.transition(agent_id, status, record.revision)
    record = repository.transition(agent_id, AgentStatus.RUNNING, record.revision)
    return repository.transition(
        agent_id,
        status,
        record.revision,
        termination_reason=AgentTerminationReason(status.value),
    )


@pytest.fixture
def api_runtime(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    runtime_factory = SessionRuntimeFactory(SQLAlchemyStateStore(session_factory))
    query_engine = QueryEngine(
        llm_service=AsyncMock(),
        session_runtime_factory=runtime_factory,
        workspace_root=tmp_path,
    )

    def override_get_db():
        with session_factory() as db:
            yield db

    monkeypatch.setattr(main, "query_engine", query_engine)
    monkeypatch.setattr(app_context, "query_engine", query_engine)
    main.app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(main.app), runtime_factory, session_factory, tmp_path
    finally:
        main.app.dependency_overrides.clear()


def test_agent_api_background_lifecycle_survives_new_factory(api_runtime) -> None:
    client, runtime_factory, _, workspace = api_runtime

    response = client.post(
        "/agents",
        json={
            "description": "Inspect",
            "prompt": "inspect",
            "subagent_type": "Explore",
            "run_in_background": True,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    recovered = SessionHarnessFactory(
        SessionRuntimeFactory(runtime_factory.store),
        workspace_root=workspace,
    ).resume(data["session_id"])
    assert recovered.agent_scheduler.status(data["agent_id"]) is not None


def test_plan_api_write_is_visible_from_new_runtime(api_runtime) -> None:
    client, runtime_factory, _, _ = api_runtime
    runtime_factory.create("s1")

    response = client.post(
        "/api/v1/plans",
        json={"conversation_id": "s1", "content": "# Plan"},
    )

    assert response.status_code == 200
    recovered = SessionRuntimeFactory(runtime_factory.store).resume("s1")
    plan_events = [
        event
        for event in recovered.events()
        if event.event_type is EventType.PLAN_TRANSITION
        and event.payload.get("action") == "save_draft"
    ]
    assert plan_events[-1].payload["content"] == "# Plan"


def test_message_api_appends_one_authoritative_event(api_runtime) -> None:
    client, runtime_factory, _, _ = api_runtime
    created = client.post("/api/v1/conversations", json={"title": "Messages"})
    conversation_id = created.json()["id"]

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "hello"},
    )

    assert response.status_code == 200
    events = SessionRuntimeFactory(runtime_factory.store).resume(conversation_id).events()
    user_events = [event for event in events if event.event_type is EventType.USER_MESSAGE]
    assert len(user_events) == 1
    assert user_events[0].payload["content"] == "hello"


def test_message_api_persists_normalized_tool_events_and_fresh_engine_recovers(
    api_runtime,
) -> None:
    client, runtime_factory, _, workspace = api_runtime
    conversation_id = client.post(
        "/api/v1/conversations", json={"title": "Tools"}
    ).json()["id"]

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={
            "role": "assistant",
            "content": "checking",
            "tool_calls": [
                {
                    "id": "call-flat",
                    "name": "read_file",
                    "input": {"file_path": "a.py"},
                },
                {
                    "id": "call-openai",
                    "type": "function",
                    "function": {
                        "name": "grep",
                        "arguments": '{"pattern": "needle"}',
                    },
                },
            ],
            "tool_results": [
                {
                    "tool_call_id": "call-flat",
                    "name": "read_file",
                    "content": {"text": "contents"},
                },
                {"toolCallId": "call-openai", "output": ["match"]},
            ],
        },
    )

    assert response.status_code == 200
    events = SessionRuntimeFactory(runtime_factory.store).resume(conversation_id).events()
    assistant_events = [
        event for event in events if event.event_type is EventType.ASSISTANT_MESSAGE
    ]
    call_events = [event for event in events if event.event_type is EventType.TOOL_CALL]
    result_events = [
        event for event in events if event.event_type is EventType.TOOL_RESULT
    ]
    assert len(assistant_events) == 1
    assert [
        (event.payload["toolCallId"], event.payload["name"], event.payload["input"])
        for event in call_events
    ] == [
        ("call-flat", "read_file", {"file_path": "a.py"}),
        ("call-openai", "grep", {"pattern": "needle"}),
    ]
    assert [
        (
            event.payload["toolCallId"],
            event.payload["name"],
            event.payload["success"],
            event.payload["result"],
        )
        for event in result_events
    ] == [
        ("call-flat", "read_file", True, {"text": "contents"}),
        ("call-openai", "grep", True, ["match"]),
    ]
    assert [event.parent_event_id for event in result_events] == [
        event.id for event in call_events
    ]

    fresh = QueryEngine(
        llm_service=AsyncMock(),
        session_runtime_factory=SessionRuntimeFactory(runtime_factory.store),
        workspace_root=workspace,
    )
    fresh.resume_conversation(conversation_id)
    turns = fresh.get_conversation(conversation_id).messages
    assistant = next(turn for turn in turns if turn.role == "assistant")
    tool = next(turn for turn in turns if turn.role == "tool")
    assert [(call.id, call.name, call.arguments) for call in assistant.tool_calls] == [
        ("call-flat", "read_file", {"file_path": "a.py"}),
        ("call-openai", "grep", {"pattern": "needle"}),
    ]
    assert [
        (item.tool_call_id, item.name, item.result.success, item.result.data)
        for item in tool.tool_observations
    ] == [
        ("call-flat", "read_file", True, {"text": "contents"}),
        ("call-openai", "grep", True, ["match"]),
    ]


@pytest.mark.parametrize(
    ("durable_status", "legacy_status"),
    [
        (AgentStatus.PENDING, "idle"),
        (AgentStatus.RUNNING, "busy"),
        (AgentStatus.COMPLETED, "completed"),
        (AgentStatus.FAILED, "error"),
        (AgentStatus.TIMED_OUT, "error"),
        (AgentStatus.INTERRUPTED, "error"),
        (AgentStatus.ORPHANED, "error"),
        (AgentStatus.BUDGET_EXHAUSTED, "error"),
        (AgentStatus.CANCELLED, "error"),
    ],
)
def test_legacy_agent_status_maps_from_durable_status(
    api_runtime, durable_status: AgentStatus, legacy_status: str
) -> None:
    client, runtime_factory, _, workspace = api_runtime
    record = _create_agent_record(
        runtime_factory,
        workspace,
        f"status-{durable_status.value}",
        status=durable_status,
    )

    response = client.get(f"/agents/{record.agent_id}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == legacy_status
    assert data["agent_status"] == durable_status.value
    assert data["statistics"]["status"] == legacy_status


def test_legacy_agent_list_filters_status_and_preserves_unknown_empty_contract(
    api_runtime,
) -> None:
    client, runtime_factory, _, workspace = api_runtime
    _create_agent_record(runtime_factory, workspace, "pending", status=AgentStatus.PENDING)
    _create_agent_record(runtime_factory, workspace, "running", status=AgentStatus.RUNNING)
    _create_agent_record(runtime_factory, workspace, "failed", status=AgentStatus.FAILED)
    _create_agent_record(
        runtime_factory, workspace, "cancelled", status=AgentStatus.CANCELLED
    )

    busy = client.get("/agents", params={"status": "busy"})
    errors = client.get("/agents", params={"status": "error"})
    unknown = client.get("/agents", params={"status": "running"})
    empty = client.get("/agents", params={"status": ""})
    unfiltered = client.get("/agents")

    assert [item["id"] for item in busy.json()["data"]] == ["running"]
    assert {item["id"] for item in errors.json()["data"]} == {"failed", "cancelled"}
    assert unknown.status_code == 200
    assert unknown.json()["data"] == []
    assert empty.status_code == 200
    assert empty.json()["data"] == unfiltered.json()["data"]


def test_agent_http_surfaces_hide_internal_agents_and_preserve_records(
    api_runtime,
) -> None:
    client, runtime_factory, _, workspace = api_runtime
    internal = AgentRecord(
        agent_id="internal",
        root_session_id="internal-root",
        agent_type="Explore",
        prompt="internal",
        description="internal",
        is_background=True,
        effective_cwd=str(workspace),
        definition_snapshot={},
    )
    runtime_factory.store.agents.create(internal)

    assert client.get("/agents").json()["data"] == []
    assert client.get("/agents/internal").status_code == 404
    assert client.delete("/agents/internal").status_code == 400
    assert runtime_factory.store.agents.get("internal") is not None


def test_legacy_agent_delete_unknown_returns_bad_request(api_runtime) -> None:
    client, _, _, _ = api_runtime

    response = client.delete("/agents/does-not-exist")

    assert response.status_code == 400


def test_legacy_agent_accepts_historical_api_compat_without_surface(
    api_runtime,
) -> None:
    client, runtime_factory, _, workspace = api_runtime
    record = _create_agent_record(
        runtime_factory,
        workspace,
        "historical-legacy",
        surface=None,
    )

    listed = client.get("/agents")
    fetched = client.get(f"/agents/{record.agent_id}")
    deleted = client.delete(f"/agents/{record.agent_id}")

    assert record.agent_id in {item["id"] for item in listed.json()["data"]}
    assert fetched.status_code == 200
    assert deleted.status_code == 200
    durable = runtime_factory.store.agents.get(record.agent_id)
    assert durable is not None
    assert durable.status is AgentStatus.INTERRUPTED


@pytest.mark.asyncio
async def test_query_engine_public_durable_agent_list_get_stop_from_fresh_cache(
    api_runtime,
) -> None:
    _, runtime_factory, _, workspace = api_runtime

    class BlockingLLM:
        def __init__(self) -> None:
            self.release = asyncio.Event()

        async def chat_completion(self, request):
            await self.release.wait()
            return ChatCompletionResponse(
                id="blocked", model="test", content="done", finish_reason="stop"
            )

    llm = BlockingLLM()
    first = QueryEngine(
        llm_service=llm,
        session_runtime_factory=runtime_factory,
        workspace_root=workspace,
    )
    first.create_conversation("public-agent-root")
    scheduler = first._session_harness("public-agent-root").agent_scheduler
    record = await first.spawn_durable_agent(
        "public-agent-root",
        "Explore",
        "inspect",
        background=True,
        api_surface="/agents",
    )
    fresh = QueryEngine(
        llm_service=AsyncMock(),
        session_runtime_factory=SessionRuntimeFactory(runtime_factory.store),
        workspace_root=workspace,
    )
    assert fresh._session_harnesses == {}

    listed = fresh.list_durable_agents(api_surface="/agents")
    recovered = fresh.get_durable_agent(record.agent_id, api_surface="/agents")
    stopped = await fresh.stop_durable_agent(record.agent_id, api_surface="/agents")

    assert [item.agent_id for item in listed] == [record.agent_id]
    assert recovered is not None
    assert stopped is not None
    assert stopped.status is AgentStatus.CANCELLED, stopped.error
    llm.release.set()
    await scheduler.shutdown()


def test_agent_routes_depend_only_on_query_engine_public_durable_boundary(
    api_runtime, monkeypatch
) -> None:
    client, runtime_factory, _, workspace = api_runtime
    record = _create_agent_record(runtime_factory, workspace, "public-only")

    class PublicOnlyEngine:
        workspace_root = workspace

        def list_durable_agents(self, *, api_surface):
            assert api_surface == "legacy_agents"
            return [record]

        def get_durable_agent(self, agent_id, *, api_surface):
            assert api_surface == "legacy_agents"
            return record if agent_id == record.agent_id else None

        async def stop_durable_agent(self, agent_id, *, api_surface):
            assert api_surface == "legacy_agents"
            return record if agent_id == record.agent_id else None

    public_engine = PublicOnlyEngine()
    monkeypatch.setattr(main, "query_engine", public_engine)
    monkeypatch.setattr(app_context, "query_engine", public_engine)

    assert client.get("/agents").status_code == 200
    assert client.get("/agents/public-only").status_code == 200
    assert client.delete("/agents/public-only").status_code == 200


def test_agent_http_surfaces_are_isolated_and_api_spawn_is_owned(api_runtime) -> None:
    client, runtime_factory, _, workspace = api_runtime
    legacy = _create_agent_record(
        runtime_factory, workspace, "legacy-owned", surface="legacy_agents"
    )
    modern = _create_agent_record(
        runtime_factory, workspace, "api-owned", surface="api_agents"
    )

    assert client.get(f"/agents/{modern.agent_id}").status_code == 404
    assert client.delete(f"/agents/{modern.agent_id}").status_code == 400
    assert client.get(f"/api/agents/{legacy.agent_id}/status").status_code == 404
    assert client.get(f"/api/agents/{modern.agent_id}/status").status_code == 200
    assert runtime_factory.store.agents.get(modern.agent_id).status is AgentStatus.PENDING

    spawned = client.post(
        "/api/agents/spawn",
        json={"agent_type": "Explore", "prompt": "inspect", "is_async": True},
    )
    assert spawned.status_code == 200
    record = runtime_factory.store.agents.get(spawned.json()["agent_id"])
    assert record is not None
    assert record.definition_snapshot["metadata"]["api_compat"]["surface"] == (
        "api_agents"
    )


def test_legacy_conversation_messages_and_plan_are_migrated_on_read(api_runtime) -> None:
    client, runtime_factory, session_factory, _ = api_runtime
    created_at = datetime(2024, 1, 2, 3, 4, 5)
    updated_at = datetime(2024, 2, 3, 4, 5, 6)
    with session_factory() as db, db.begin():
        db.add(
            Conversation(
                id="legacy",
                title="Legacy title",
                created_at=created_at,
                updated_at=updated_at,
            )
        )
        db.add(
            Message(
                id="legacy-message",
                conversation_id="legacy",
                role="user",
                content="old hello",
                timestamp=created_at,
            )
        )
        db.add(
            Plan(
                id="legacy-plan",
                conversation_id="legacy",
                content="# Old plan",
                version=7,
                created_at=created_at,
                updated_at=updated_at,
            )
        )
    assert runtime_factory.store.states.load_session("legacy") is None

    conversation = client.get("/api/v1/conversations/legacy")
    listing = client.get("/api/v1/conversations")
    messages = client.get("/api/v1/conversations/legacy/messages")
    plan = client.get("/api/v1/plans/conversation/legacy")

    assert conversation.status_code == 200
    assert conversation.json()["title"] == "Legacy title"
    assert any(item["id"] == "legacy" for item in listing.json())
    assert messages.status_code == 200
    assert messages.json()[0]["id"] == "legacy-message"
    assert plan.status_code == 200
    assert plan.json()["id"] == "legacy-plan"
    assert plan.json()["content"] == "# Old plan"
    metadata = runtime_factory.store.metadata.get("legacy", "api.plan")
    assert metadata is not None
    assert metadata.snapshot["version"] == 7
    assert metadata.snapshot["created_at"] == created_at.isoformat()
    assert metadata.snapshot["updated_at"] == updated_at.isoformat()

    deleted = client.delete("/api/v1/conversations/legacy/messages/legacy-message")
    assert deleted.status_code == 200
    assert client.get("/api/v1/conversations/legacy/messages").json() == []


def test_agent_api_preserves_worker_config_and_response_fields(api_runtime) -> None:
    client, runtime_factory, _, _ = api_runtime

    response = client.post(
        "/agents",
        json={
            "name": "reader",
            "description": "Inspect safely",
            "capabilities": ["read_file", "grep"],
            "max_concurrent_tasks": 3,
            "run_in_background": True,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "reader"
    assert data["type"] == "worker"
    assert data["config"] == {
        "description": "Inspect safely",
        "tools": ["read_file", "grep"],
        "max_concurrent_tasks": 3,
    }
    assert data["statistics"]["id"] == data["id"]
    assert data["agent_id"] == data["id"]
    assert data["session_id"]

    record = runtime_factory.store.agents.get(data["agent_id"])
    assert record is not None
    assert record.definition_snapshot["metadata"]["api_compat"] == {
        "name": "reader",
        "type": "worker",
        "description": "Inspect safely",
        "capabilities": ["read_file", "grep"],
        "max_concurrent_tasks": 3,
        "surface": "legacy_agents",
    }
    recovered = client.get(f"/agents/{data['agent_id']}")
    assert recovered.status_code == 200
    assert recovered.json()["data"]["config"] == data["config"]


def test_plan_api_migrates_legacy_conversation_before_write(api_runtime) -> None:
    client, runtime_factory, session_factory, _ = api_runtime
    with session_factory() as db, db.begin():
        db.add(Conversation(id="legacy-plan-write", title="Legacy"))

    response = client.post(
        "/api/v1/plans",
        json={"conversation_id": "legacy-plan-write", "content": "# New plan"},
    )

    assert response.status_code == 200
    recovered = SessionRuntimeFactory(runtime_factory.store).resume(
        "legacy-plan-write"
    )
    assert any(
        event.event_type is EventType.PLAN_TRANSITION
        and event.payload.get("action") == "save_draft"
        and event.payload.get("content") == "# New plan"
        for event in recovered.events()
    )


def test_migrated_task_keeps_legacy_id_across_http_mutations(api_runtime) -> None:
    client, _, session_factory, _ = api_runtime
    with session_factory() as db, db.begin():
        db.add(Conversation(id="legacy-task-session", title="Legacy"))
        db.add(
            Task(
                id="old-task",
                conversation_id="legacy-task-session",
                subject="Old subject",
                description="Old description",
                status=TaskStatus.PENDING,
            )
        )

    listed = client.get(
        "/api/v1/tasks", params={"conversation_id": "legacy-task-session"}
    )
    fetched = client.get("/api/v1/tasks/old-task")
    updated = client.patch(
        "/api/v1/tasks/old-task", json={"subject": "Updated subject"}
    )
    deleted = client.delete("/api/v1/tasks/old-task")

    assert listed.status_code == 200
    assert [task["id"] for task in listed.json()] == ["old-task"]
    assert fetched.status_code == 200
    assert fetched.json()["id"] == "old-task"
    assert updated.status_code == 200
    assert updated.json()["id"] == "old-task"
    assert updated.json()["subject"] == "Updated subject"
    assert deleted.status_code == 200
    assert client.get("/api/v1/tasks/old-task").status_code == 404


def test_task_public_ids_are_unique_and_route_every_mutation_to_its_scope(
    api_runtime,
) -> None:
    client, runtime_factory, _, _ = api_runtime
    first_runtime = runtime_factory.create("first-session")
    second_runtime = runtime_factory.create("second-session")
    first = first_runtime.create_task(NewTask(subject="First", description="First"))
    second = second_runtime.create_task(NewTask(subject="Second", description="Second"))
    assert first.id == second.id == "1"

    listed = client.get("/api/v1/tasks").json()
    ids = {task["subject"]: task["id"] for task in listed}

    assert ids == {
        "First": "first-session:1",
        "Second": "second-session:1",
    }
    assert client.get(f"/api/v1/tasks/{ids['First']}").json()["subject"] == "First"
    updated = client.patch(
        f"/api/v1/tasks/{ids['Second']}", json={"subject": "Second updated"}
    )
    assert updated.status_code == 200
    assert first_runtime.get_task("1").subject == "First"
    assert second_runtime.get_task("1").subject == "Second updated"

    claimed = client.post(
        f"/api/v1/tasks/{ids['Second']}/claim", json={"agent_id": "worker"}
    )
    assert claimed.status_code == 200
    assert claimed.json()["task"]["id"] == "second-session:1"
    unassigned = client.post(f"/api/v1/tasks/{ids['Second']}/unassign")
    assert unassigned.status_code == 200
    assert unassigned.json()["task"]["id"] == "second-session:1"

    deleted = client.delete(f"/api/v1/tasks/{ids['First']}")
    assert deleted.status_code == 200
    assert first_runtime.get_task("1") is None
    assert second_runtime.get_task("1") is not None


def test_fallback_public_id_uses_namespace_when_legacy_id_claims_base(
    api_runtime,
) -> None:
    client, runtime_factory, _, _ = api_runtime
    legacy_runtime = runtime_factory.create("a-session")
    legacy = legacy_runtime.create_task(
        NewTask(
            subject="Legacy claimant",
            description="Claims another scope fallback",
            metadata={"legacyId": "z-session:1"},
        )
    )
    fallback_runtime = runtime_factory.create("z-session")
    fallback = fallback_runtime.create_task(
        NewTask(subject="Runtime fallback", description="Needs alternate")
    )

    listed = client.get("/api/v1/tasks").json()
    by_subject = {task["subject"]: task["id"] for task in listed}
    fallback_public_id = by_subject["Runtime fallback"]

    assert legacy.id == fallback.id == "1"
    assert by_subject["Legacy claimant"] == "z-session:1"
    assert fallback_public_id != "z-session:1"
    assert fallback_public_id.startswith("runtime:")
    assert len(set(by_subject.values())) == 2
    assert client.get("/api/v1/tasks/z-session:1").json()["subject"] == "Legacy claimant"
    assert client.get(f"/api/v1/tasks/{fallback_public_id}").json()["subject"] == (
        "Runtime fallback"
    )

    assert client.patch(
        f"/api/v1/tasks/{fallback_public_id}", json={"subject": "Runtime updated"}
    ).status_code == 200
    assert fallback_runtime.get_task(fallback.id).subject == "Runtime updated"
    assert legacy_runtime.get_task(legacy.id).subject == "Legacy claimant"

    assert client.delete("/api/v1/tasks/z-session:1").status_code == 200
    after_delete = client.get("/api/v1/tasks").json()
    runtime_task = next(
        task for task in after_delete if task["subject"] == "Runtime updated"
    )
    assert runtime_task["id"] == fallback_public_id
    assert client.get(f"/api/v1/tasks/{fallback_public_id}").status_code == 200

    metadata_patch = client.patch(
        f"/api/v1/tasks/{fallback_public_id}",
        json={"meta": {"apiPublicId": "tampered"}},
    )
    assert metadata_patch.status_code == 200
    assert metadata_patch.json()["id"] == fallback_public_id
    assert metadata_patch.json()["meta"]["apiPublicId"] == fallback_public_id


def test_api_task_uuid_retries_when_generated_public_id_is_taken(
    api_runtime,
    monkeypatch,
) -> None:
    client, runtime_factory, _, _ = api_runtime
    runtime = runtime_factory.create("uuid-session")
    runtime.create_task(
        NewTask(
            subject="Existing",
            description="Existing",
            metadata={"legacyId": "taken-id"},
        )
    )
    generated = iter(["taken-id", "fresh-id"])
    monkeypatch.setattr(task_service_module, "uuid4", lambda: next(generated))

    created = client.post(
        "/api/v1/tasks",
        json={
            "conversation_id": "uuid-session",
            "subject": "Created",
            "description": "Created",
        },
    )

    assert created.status_code == 200
    assert created.json()["id"] == "fresh-id"
    assert created.json()["meta"]["apiTaskId"] == "fresh-id"


def test_api_task_ids_and_dependencies_are_durable_public_ids(api_runtime) -> None:
    client, runtime_factory, _, _ = api_runtime
    runtime_factory.create("api-task-session")

    prerequisite = client.post(
        "/api/v1/tasks",
        json={
            "conversation_id": "api-task-session",
            "subject": "Prerequisite",
            "description": "Prerequisite",
            "meta": {"source": "api"},
        },
    ).json()
    dependent = client.post(
        "/api/v1/tasks",
        json={
            "conversation_id": "api-task-session",
            "subject": "Dependent",
            "description": "Dependent",
            "blocked_by": [prerequisite["id"]],
        },
    ).json()

    assert prerequisite["id"] != "1"
    assert prerequisite["id"] != dependent["id"]
    assert prerequisite["meta"]["apiTaskId"] == prerequisite["id"]
    assert prerequisite["meta"]["source"] == "api"
    assert dependent["blocked_by"] == [prerequisite["id"]]
    assert client.get(f"/api/v1/tasks/{prerequisite['id']}").status_code == 200

    runtime = SessionRuntimeFactory(runtime_factory.store).resume("api-task-session")
    durable = {task.subject: task for task in runtime.list_tasks()}
    assert durable["Prerequisite"].metadata["apiTaskId"] == prerequisite["id"]
    assert durable["Dependent"].blocked_by == [durable["Prerequisite"].id]

    blocked = client.post(
        f"/api/v1/tasks/{prerequisite['id']}/block/{dependent['id']}"
    )
    assert blocked.status_code == 200
    assert client.get(f"/api/v1/tasks/{prerequisite['id']}").json()["blocks"] == [
        dependent["id"]
    ]


def test_subject_only_patch_does_not_remap_internal_dependency_ids(api_runtime) -> None:
    client, runtime_factory, _, _ = api_runtime
    runtime = runtime_factory.create("collision-session")
    decoy = runtime.create_task(
        NewTask(
            subject="Decoy",
            description="Public ID collides with internal ID",
            metadata={"legacyId": "2"},
        )
    )
    target = runtime.create_task(
        NewTask(
            subject="Target",
            description="Dependency target",
            metadata={"apiTaskId": "target-public"},
        )
    )
    source = runtime.create_task(NewTask(subject="Source", description="Source"))
    runtime.update_task(source.id, TaskMutation(add_blocks=[target.id]))
    assert decoy.id == "1"
    assert target.id == "2"

    response = client.patch(
        "/api/v1/tasks/collision-session:3",
        json={"subject": "Source updated"},
    )

    assert response.status_code == 200
    assert response.json()["blocks"] == ["target-public"]
    recovered = SessionRuntimeFactory(runtime_factory.store).resume("collision-session")
    assert recovered.get_task(source.id).blocks == [target.id]
    assert recovered.get_task(decoy.id).blocked_by == []
    assert recovered.get_task(target.id).blocked_by == [source.id]


def test_runtime_only_sessions_are_not_conversations_or_deletable(api_runtime) -> None:
    client, runtime_factory, _, _ = api_runtime
    runtime = runtime_factory.create("agent-session-internal")
    task = runtime.create_task(NewTask(subject="Internal", description="Keep me"))
    created = client.post("/api/v1/conversations", json={"title": "Visible"}).json()

    listed = client.get("/api/v1/conversations")
    fetched = client.get("/api/v1/conversations/agent-session-internal")
    deleted = client.delete("/api/v1/conversations/agent-session-internal")

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [created["id"]]
    assert fetched.status_code == 404
    assert deleted.status_code == 404
    recovered = SessionRuntimeFactory(runtime_factory.store).resume(
        "agent-session-internal"
    )
    assert recovered.get_task(task.id) is not None


def test_deleted_legacy_conversation_tasks_cannot_be_remigrated(api_runtime) -> None:
    client, runtime_factory, session_factory, _ = api_runtime
    with session_factory() as db, db.begin():
        db.add(Conversation(id="deleted-legacy", title="Delete me"))
        db.add(
            Task(
                id="deleted-legacy-task",
                conversation_id="deleted-legacy",
                subject="Must stay deleted",
                description="Must stay deleted",
            )
        )

    assert client.get("/api/v1/tasks/deleted-legacy-task").status_code == 200
    assert client.delete("/api/v1/conversations/deleted-legacy").status_code == 200

    listed = client.get(
        "/api/v1/tasks", params={"conversation_id": "deleted-legacy"}
    )
    fetched = client.get("/api/v1/tasks/deleted-legacy-task")
    updated = client.patch(
        "/api/v1/tasks/deleted-legacy-task", json={"subject": "Resurrected"}
    )
    deleted = client.delete("/api/v1/tasks/deleted-legacy-task")

    assert listed.status_code == 200
    assert listed.json() == []
    assert fetched.status_code == 404
    assert updated.status_code == 404
    assert deleted.status_code == 404
    assert runtime_factory.store.states.load_session("deleted-legacy") is None


def test_message_delete_is_scoped_to_route_conversation(api_runtime) -> None:
    client, _, _, _ = api_runtime
    first = client.post("/api/v1/conversations", json={"title": "First"}).json()
    second = client.post("/api/v1/conversations", json={"title": "Second"}).json()
    first_message = client.post(
        f"/api/v1/conversations/{first['id']}/messages",
        json={"role": "user", "content": "first"},
    ).json()
    second_message = client.post(
        f"/api/v1/conversations/{second['id']}/messages",
        json={"role": "user", "content": "second"},
    ).json()

    wrong_parent = client.delete(
        f"/api/v1/conversations/{first['id']}/messages/{second_message['id']}"
    )

    assert wrong_parent.status_code == 404
    assert client.get(
        f"/api/v1/conversations/{first['id']}/messages"
    ).json() == [first_message]
    assert client.get(
        f"/api/v1/conversations/{second['id']}/messages"
    ).json() == [second_message]


def test_legacy_global_tasks_migrate_with_ids_dependencies_and_metadata(
    api_runtime,
) -> None:
    client, runtime_factory, session_factory, _ = api_runtime
    with session_factory() as db, db.begin():
        db.add_all(
            [
                Task(
                    id="global-first",
                    conversation_id=None,
                    subject="Global first",
                    description="First",
                    blocks=["global-second"],
                    meta={"scope": "global"},
                ),
                Task(
                    id="global-second",
                    conversation_id=None,
                    subject="Global second",
                    description="Second",
                    blocked_by=["global-first"],
                    status=TaskStatus.IN_PROGRESS,
                    owner="worker",
                ),
            ]
        )

    listed = client.get("/api/v1/tasks")
    fetched = client.get("/api/v1/tasks/global-first")
    updated = client.patch(
        "/api/v1/tasks/global-second", json={"subject": "Global updated"}
    )

    assert listed.status_code == 200
    by_id = {task["id"]: task for task in listed.json()}
    assert set(by_id) == {"global-first", "global-second"}
    assert by_id["global-first"]["conversation_id"] is None
    assert by_id["global-first"]["blocks"] == ["global-second"]
    assert by_id["global-first"]["meta"] == {
        "scope": "global",
        "legacyId": "global-first",
    }
    assert fetched.status_code == 200
    assert fetched.json()["blocked_by"] == []
    assert updated.status_code == 200
    assert updated.json()["id"] == "global-second"
    assert updated.json()["subject"] == "Global updated"

    runtime = SessionRuntimeFactory(runtime_factory.store).resume("global")
    durable = {task.metadata["legacyId"]: task for task in runtime.list_tasks()}
    assert durable["global-first"].blocks == [durable["global-second"].id]
    assert durable["global-second"].blocked_by == [durable["global-first"].id]

    deleted = client.delete("/api/v1/tasks/global-first")
    assert deleted.status_code == 200
    assert client.get("/api/v1/tasks/global-first").status_code == 404
