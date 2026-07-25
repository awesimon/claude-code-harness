from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, local

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.engine import AgentExecutor
from agents.types import AgentSource, CustomAgentDefinition
from harness import SessionHarnessFactory
from harness.context_control import CompactionSummary, ContextControlConfig
from harness.hooks import HookRuntime
from models import Base
from query_engine import QueryEngine
from services.llm_service import ChatCompletionResponse
from state_core import AgentRecord, EventType, SessionRuntimeFactory, SQLAlchemyStateStore


def _store(tmp_path: Path) -> SQLAlchemyStateStore:
    engine = create_engine(f"sqlite:///{tmp_path / 'skills.db'}")
    Base.metadata.create_all(engine)
    return SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))


def _write_skill(
    root: Path,
    name: str = "reviewing",
    *,
    body: str = "Review carefully.",
    extra: str = "",
) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"""---
name: {name}
description: Review code safely
{extra}---

{body}
""",
        encoding="utf-8",
    )
    return directory


def _resolver(skills_dir: Path, store: SQLAlchemyStateStore, *, agent_id: str | None = None):
    from harness.skills import SkillResolver

    return SkillResolver(
        skills_dir,
        metadata_repository=store.metadata,
        root_session_id="root",
        agent_id=agent_id,
    )


def test_skill_body_is_loaded_only_when_selected_and_snapshot_is_durable(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = _write_skill(skills_dir)
    store = _store(tmp_path)
    resolver = _resolver(skills_dir, store)

    indexed = resolver.index()
    selected = resolver.resolve("reviewing")
    original_digest = selected.digest
    (skill_dir / "SKILL.md").write_text(
        "---\nname: reviewing\ndescription: changed\n---\n\nChanged body.",
        encoding="utf-8",
    )
    resumed = _resolver(skills_dir, store).resolve("reviewing")

    assert indexed[0].content is None
    assert indexed[0].description == "Review code safely"
    assert selected.content == "Review carefully."
    assert resumed.content == "Review carefully."
    assert resumed.digest == original_digest


def test_agent_scopes_snapshot_skill_versions_independently(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = _write_skill(skills_dir, body="Version one.")
    store = _store(tmp_path)

    first = _resolver(skills_dir, store, agent_id="a1").resolve("reviewing")
    (skill_dir / "SKILL.md").write_text(
        "---\nname: reviewing\ndescription: Review code safely\n---\n\nVersion two.",
        encoding="utf-8",
    )
    same_scope = _resolver(skills_dir, store, agent_id="a1").resolve("reviewing")
    other_scope = _resolver(skills_dir, store, agent_id="a2").resolve("reviewing")

    assert first.content == same_scope.content == "Version one."
    assert other_scope.content == "Version two."


def test_session_harness_caches_fully_scoped_skill_resolver(tmp_path: Path) -> None:
    primary = tmp_path / "primary-skills"
    extra = tmp_path / "extra-skills"
    _write_skill(primary, "primary")
    _write_skill(extra, "extra")
    store = _store(tmp_path)
    harness = SessionHarnessFactory(
        SessionRuntimeFactory(store), workspace_root=tmp_path
    ).create(
        "resolver-cache",
        metadata={
            "skills_dir": str(primary),
            "skill_roots": [str(extra)],
        },
    )

    resolver = harness.skills
    child = harness.child("child-1")

    assert resolver is harness.skills
    assert child.skills is child.skills
    assert child.skills is not resolver
    assert resolver.skills_dir == primary.resolve()
    assert [entry.name for entry in resolver.index()] == ["extra", "primary"]
    assert resolver.catalog.cwd == harness.effective_cwd
    assert set(resolver.catalog.allowed_roots).issuperset(harness.allowed_workspaces)
    assert resolver._activation_repository is store.skill_activations
    assert child.skills.agent_id == "child-1"


def test_skill_resources_are_manifested_and_cannot_escape_base(tmp_path: Path) -> None:
    from harness.skills import SkillPathError

    skills_dir = tmp_path / "skills"
    skill_dir = _write_skill(skills_dir)
    references = skill_dir / "references"
    references.mkdir()
    (references / "guide.md").write_text("guide", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (references / "escape.txt").symlink_to(outside)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (references / "escape-dir").symlink_to(outside_dir, target_is_directory=True)
    resolver = _resolver(skills_dir, _store(tmp_path))

    with pytest.raises(SkillPathError):
        resolver.resolve("reviewing")

    (references / "escape.txt").unlink()
    (references / "escape-dir").unlink()
    snapshot = resolver.resolve("reviewing")
    assert snapshot.resources[0].path == "references/guide.md"
    resolver.activate("reviewing")
    assert resolver.read_resource("reviewing", "references/guide.md") == "guide"
    with pytest.raises(SkillPathError):
        resolver.read_resource("reviewing", "../secret.txt")


def test_skill_metadata_and_hooks_are_snapshotted_and_registered_once(tmp_path: Path) -> None:
    from harness.skills import SkillResolver

    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir,
        extra="""allowed-tools: Read Bash
required-mcp-servers: [docs]
hooks:
  PreToolUse:
    - matcher: bash
      command: echo '{"decision":"allow"}'
""",
    )
    store = _store(tmp_path)
    hooks = HookRuntime(
        [], metadata_repository=store.metadata, root_session_id="root"
    )
    resolver = SkillResolver(
        skills_dir,
        metadata_repository=store.metadata,
        root_session_id="root",
        hook_runtime=hooks,
    )

    snapshot = resolver.resolve("reviewing")
    resolver.resolve("reviewing")

    assert snapshot.allowed_tools == ("read_file", "bash")
    assert snapshot.required_mcp_servers == ("docs",)
    assert hooks.list() == ()

    resolver.activate(
        "reviewing",
        available_mcp_servers=("docs",),
        available_tools=("read_file", "bash"),
    )
    resolver.activate(
        "reviewing",
        available_mcp_servers=("docs",),
        available_tools=("read_file", "bash"),
    )

    assert len(hooks.list()) == 1
    assert hooks.list()[0].matcher == "bash"


class _RecordingLLM:
    def __init__(self) -> None:
        self.requests = []

    async def chat_completion(self, request):
        self.requests.append(request)
        return ChatCompletionResponse(id="one", model="test", content="done")


class _RecordingFailingLLM(_RecordingLLM):
    async def chat_completion(self, request):
        self.requests.append(request)
        raise RuntimeError("provider outcome unknown")


@pytest.mark.asyncio
async def test_root_skill_announcements_survive_delta_compaction_and_resume(
    tmp_path: Path,
) -> None:
    skills = tmp_path / ".claude" / "skills"
    _write_skill(skills, "alpha")
    store = _store(tmp_path)
    llm = _RecordingLLM()

    async def summarize(_messages):
        return CompactionSummary("durable summary")

    options = {
        "llm_service": llm,
        "enable_error_recovery": False,
        "workspace_root": tmp_path,
        "session_runtime_factory": SessionRuntimeFactory(store),
        "context_control_config": ContextControlConfig(
            micro_threshold_tokens=1,
            hard_threshold_tokens=1,
            target_tokens=1,
        ),
        "context_summary_callback": summarize,
    }
    engine = QueryEngine(**options)
    conversation_id = engine.create_conversation("skill-announcements")

    _ = [event async for event in engine.chat(conversation_id, "first")]
    _write_skill(skills, "beta")
    _ = [event async for event in engine.chat(conversation_id, "second")]

    first_content = "\n".join(message.content for message in llm.requests[0].messages)
    second_content = "\n".join(message.content for message in llm.requests[1].messages)
    assert "- alpha:" in first_content
    assert "- alpha:" in second_content
    assert "- beta:" in second_content

    resumed_llm = _RecordingLLM()
    resumed = QueryEngine(**{**options, "llm_service": resumed_llm})
    resumed.resume_conversation(conversation_id)
    _ = [event async for event in resumed.chat(conversation_id, "third")]

    resumed_content = "\n".join(
        message.content for message in resumed_llm.requests[0].messages
    )
    assert resumed_content.count("- alpha:") == 1
    assert resumed_content.count("- beta:") == 1


@pytest.mark.asyncio
async def test_active_skill_body_is_recovered_then_durably_acknowledged(
    tmp_path: Path,
) -> None:
    skills = tmp_path / ".claude" / "skills"
    _write_skill(skills, "research", body="Recovery-only body.")
    store = _store(tmp_path)
    llm = _RecordingLLM()
    engine = QueryEngine(
        llm_service=llm,
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=SessionRuntimeFactory(store),
    )
    conversation_id = engine.create_conversation("skill-delivery")
    activation = engine._session_harness(conversation_id).skills.activate("research")
    assert activation.newly_activated is True

    _ = [event async for event in engine.chat(conversation_id, "first")]
    resumed_llm = _RecordingLLM()
    resumed = QueryEngine(
        llm_service=resumed_llm,
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=SessionRuntimeFactory(store),
    )
    resumed.resume_conversation(conversation_id)
    _ = [event async for event in resumed.chat(conversation_id, "second")]

    first_content = "\n".join(message.content for message in llm.requests[0].messages)
    second_content = "\n".join(
        message.content for message in resumed_llm.requests[0].messages
    )
    assert first_content.count("Recovery-only body.") == 1
    assert "Recovery-only body." not in second_content
    runtime_events = engine._session_runtime(conversation_id).events()
    attempts = [
        event
        for event in runtime_events
        if event.event_type.value == "skill_delivery_attempt"
    ]
    deliveries = [
        event
        for event in runtime_events
        if event.event_type.value == "skill_delivery"
    ]
    assert len(attempts) == 1
    assert len(deliveries) == 1
    assert deliveries[0].payload["attemptId"] == attempts[0].payload["attemptId"]
    assert attempts[0].id < deliveries[0].id


@pytest.mark.asyncio
async def test_root_skill_dispatch_claim_prevents_replay_after_pre_provider_crash(
    tmp_path: Path,
) -> None:
    from services import Message

    skills = tmp_path / ".claude" / "skills"
    _write_skill(skills, "research", body="Claimed root body.")
    store = _store(tmp_path)
    engine = QueryEngine(
        llm_service=_RecordingLLM(),
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=SessionRuntimeFactory(store),
    )
    conversation_id = engine.create_conversation("root-dispatch-crash")
    engine._session_harness(conversation_id).skills.activate("research")

    prepared = await engine._prepare_model_messages(
        conversation_id,
        [Message(role="user", content="before crash")],
    )
    assert any("Claimed root body." in message.content for message in prepared)
    attempts = [
        event
        for event in engine._session_runtime(conversation_id).events()
        if event.event_type.value == "skill_delivery_attempt"
    ]
    assert len(attempts) == 1
    assert attempts[0].payload["agentId"] is None
    assert attempts[0].payload["digest"]
    assert attempts[0].payload["attemptId"]
    assert attempts[0].payload["idempotencyKey"]

    resumed_llm = _RecordingLLM()
    resumed = QueryEngine(
        llm_service=resumed_llm,
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=SessionRuntimeFactory(store),
    )
    resumed.resume_conversation(conversation_id)
    _ = [event async for event in resumed.chat(conversation_id, "after crash")]

    assert not any(
        message.role == "system" and "Claimed root body." in message.content
        for message in resumed_llm.requests[0].messages
    )
    assert len(
        [
            event
            for event in resumed._session_runtime(conversation_id).events()
            if event.event_type.value == "skill_delivery_attempt"
        ]
    ) == 1


@pytest.mark.asyncio
async def test_concurrent_root_prepares_keep_attempt_bound_to_body_owner(
    tmp_path: Path,
) -> None:
    from services import Message

    skills = tmp_path / ".claude" / "skills"
    _write_skill(skills, "research", body="Only request A owns this body.")
    store = _store(tmp_path)
    engine = QueryEngine(
        llm_service=_RecordingLLM(),
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=SessionRuntimeFactory(store),
    )
    conversation_id = engine.create_conversation("concurrent-skill-delivery")
    engine._session_harness(conversation_id).skills.activate("research")
    a_prepared = asyncio.Event()
    allow_a_ack = asyncio.Event()

    async def request_a() -> None:
        prepared = await engine._prepare_model_messages(
            conversation_id,
            [Message(role="user", content="A")],
        )
        assert any("Only request A owns this body." in item.content for item in prepared)
        a_prepared.set()
        await allow_a_ack.wait()
        engine._ack_skill_deliveries(conversation_id)

    async def request_b() -> None:
        await a_prepared.wait()
        prepared = await engine._prepare_model_messages(
            conversation_id,
            [Message(role="user", content="B")],
        )
        assert not any("Only request A owns this body." in item.content for item in prepared)
        engine._ack_skill_deliveries(conversation_id)
        assert not any(
            event.event_type is EventType.SKILL_DELIVERY
            for event in engine._session_runtime(conversation_id).events()
        )
        allow_a_ack.set()

    await asyncio.gather(request_a(), request_b())

    acknowledgements = [
        event
        for event in engine._session_runtime(conversation_id).events()
        if event.event_type is EventType.SKILL_DELIVERY
    ]
    assert len(acknowledgements) == 1


def test_skill_dispatch_claim_is_unique_under_session_revision_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import harness.skills as skill_runtime

    skills = tmp_path / ".claude" / "skills"
    _write_skill(skills, "research", body="Claim exactly once.")
    store = _store(tmp_path)
    factory = SessionHarnessFactory(
        SessionRuntimeFactory(store), workspace_root=tmp_path
    )
    first = factory.create("dispatch-cas")
    snapshot = first.skills.activate("research").snapshot
    second = factory.resume("dispatch-cas")
    barrier = Barrier(2)
    thread_state = local()
    original_commit = store.states.commit

    def synchronize_attempt_commits(state, batch, expected_revision):
        is_attempt = any(
            event.event_type is EventType.SKILL_DELIVERY_ATTEMPT
            for event in batch.events
        )
        if is_attempt and not getattr(thread_state, "synchronized", False):
            thread_state.synchronized = True
            barrier.wait()
        return original_commit(state, batch, expected_revision)

    monkeypatch.setattr(store.states, "commit", synchronize_attempt_commits)

    def claim(harness):
        return skill_runtime.claim_skill_delivery_attempts(
            harness,
            (snapshot,),
            source="cas_test",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (first, second)))

    assert sorted(len(result) for result in results) == [0, 1]
    attempts = [
        event
        for event in first.session_runtime.events()
        if event.event_type is EventType.SKILL_DELIVERY_ATTEMPT
    ]
    assert len(attempts) == 1
    assert attempts[0].payload["attempt"] == 1


@pytest.mark.asyncio
async def test_model_boundary_builds_delivery_index_with_one_event_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import Message

    async def preserve_messages(_controller, messages):
        return messages

    skills = tmp_path / ".claude" / "skills"
    _write_skill(skills, "alpha", body="Alpha body.")
    _write_skill(skills, "beta", body="Beta body.")
    store = _store(tmp_path)
    engine = QueryEngine(
        llm_service=_RecordingLLM(),
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=SessionRuntimeFactory(store),
    )
    conversation_id = engine.create_conversation("delivery-index")
    harness = engine._session_harness(conversation_id)
    harness.skills.activate("alpha")
    harness.skills.activate("beta")
    original_events = harness.session_runtime.events
    event_reads = 0

    def counted_events(after_id: int = 0):
        nonlocal event_reads
        event_reads += 1
        return original_events(after_id)

    monkeypatch.setattr(harness.session_runtime, "events", counted_events)
    monkeypatch.setattr(
        "query_engine.ContextController.prepare_messages", preserve_messages
    )

    prepared = await engine._prepare_model_messages(
        conversation_id,
        [Message(role="user", content="dispatch")],
    )
    engine._ack_skill_deliveries(conversation_id)

    assert any("Alpha body." in item.content for item in prepared)
    assert any("Beta body." in item.content for item in prepared)
    assert event_reads == 1


@pytest.mark.asyncio
async def test_successful_skill_tool_result_is_a_durable_delivery_ack(
    tmp_path: Path,
) -> None:
    skills = tmp_path / ".claude" / "skills"
    _write_skill(skills, "research", body="Tool-delivered body.")
    store = _store(tmp_path)
    llm = _RecordingLLM()
    engine = QueryEngine(
        llm_service=llm,
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=SessionRuntimeFactory(store),
    )
    conversation_id = engine.create_conversation("skill-tool-delivery")
    harness = engine._session_harness(conversation_id)

    execution = await harness.tool_runtime.execute(
        "skill_execute",
        {"skill": "research"},
        harness.runtime_context,
        tool_call_id="skill-call",
    )
    assert execution.result.success is True
    assert execution.result.data["content"] == "Tool-delivered body."

    _ = [event async for event in engine.chat(conversation_id, "continue")]

    assert not any(
        message.role == "system" and "Tool-delivered body." in message.content
        for message in llm.requests[0].messages
    )
    assert not any(
        event.event_type.value == "skill_delivery"
        for event in harness.session_runtime.events()
    )


@pytest.mark.asyncio
async def test_skill_delivery_ack_is_scoped_to_the_activating_agent(tmp_path: Path) -> None:
    skills = tmp_path / ".claude" / "skills"
    _write_skill(skills, "research", body="Root-scoped body.")
    store = _store(tmp_path)
    llm = _RecordingLLM()
    engine = QueryEngine(
        llm_service=llm,
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=SessionRuntimeFactory(store),
    )
    conversation_id = engine.create_conversation("skill-agent-scope")
    harness = engine._session_harness(conversation_id)
    harness.skills.activate("research")
    child = harness.child("child-1")
    child_activation = child.skills.activate("research")
    child.session_runtime.append_event(
        EventType.SKILL_DELIVERY,
        {
            "agentId": child.agent_id,
            "skill": "research",
            "digest": child_activation.snapshot.digest,
            "source": "static_agent",
        },
    )

    _ = [event async for event in engine.chat(conversation_id, "continue")]

    request_content = "\n".join(message.content for message in llm.requests[0].messages)
    assert request_content.count("Root-scoped body.") == 1
    delivery_agents = [
        event.payload["agentId"]
        for event in harness.session_runtime.events()
        if event.event_type.value == "skill_delivery"
    ]
    assert delivery_agents == ["child-1", None]


@pytest.mark.asyncio
async def test_agent_skill_allowed_tools_do_not_narrow_agent_tool_visibility(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / ".claude" / "skills"
    _write_skill(skills_dir, extra="allowed-tools: Read\n")
    store = _store(tmp_path)
    child = SessionHarnessFactory(
        SessionRuntimeFactory(store), workspace_root=tmp_path
    ).create("root").child("agent-1")
    definition = CustomAgentDefinition(
        agent_type="reviewer",
        when_to_use="review",
        source=AgentSource.USER_SETTINGS,
        skills=["reviewing"],
        tools=["read_file", "bash"],
        get_system_prompt=lambda: "Base prompt.",
    )
    llm = _RecordingLLM()
    executor = AgentExecutor(definition, llm_service=llm)
    record = AgentRecord(
        "agent-1", "root", "reviewer", "inspect", "inspect", False, str(tmp_path), {}
    )

    await executor.run(record, child)
    await executor.run(record, child)

    first_request, second_request = llm.requests
    first_content = "\n".join(message.content for message in first_request.messages)
    second_content = "\n".join(message.content for message in second_request.messages)
    assert "Base prompt." in first_request.messages[0].content
    assert "Review carefully." in first_content
    assert "- reviewing:" in first_content
    assert "Review carefully." not in second_content
    assert "- reviewing:" in second_content
    assert [tool["function"]["name"] for tool in first_request.tools] == [
        "read_file",
        "bash",
    ]
    activation = store.skill_activations.get_by_name("root", "agent-1", "reviewing")
    assert activation is not None
    assert activation.status.value == "active"


@pytest.mark.asyncio
async def test_child_skill_dispatch_is_not_replayed_after_unknown_provider_outcome(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / ".claude" / "skills"
    _write_skill(skills_dir, body="Claimed child body.")
    store = _store(tmp_path)
    child = SessionHarnessFactory(
        SessionRuntimeFactory(store), workspace_root=tmp_path
    ).create("root").child("agent-1")
    definition = CustomAgentDefinition(
        agent_type="reviewer",
        when_to_use="review",
        source=AgentSource.USER_SETTINGS,
        skills=["reviewing"],
        get_system_prompt=lambda: "Base prompt.",
    )
    record = AgentRecord(
        "agent-1", "root", "reviewer", "inspect", "inspect", False, str(tmp_path), {}
    )
    failing_llm = _RecordingFailingLLM()

    with pytest.raises(RuntimeError, match="provider outcome unknown"):
        await AgentExecutor(definition, llm_service=failing_llm).run(record, child)

    assert any(
        "Claimed child body." in message.content
        for message in failing_llm.requests[0].messages
    )
    attempts = [
        event
        for event in child.session_runtime.events()
        if event.event_type.value == "skill_delivery_attempt"
    ]
    assert len(attempts) == 1
    assert attempts[0].payload["agentId"] == "agent-1"

    resumed_llm = _RecordingLLM()
    await AgentExecutor(definition, llm_service=resumed_llm).run(record, child)

    assert not any(
        "Claimed child body." in message.content
        for message in resumed_llm.requests[0].messages
    )
    assert len(
        [
            event
            for event in child.session_runtime.events()
            if event.event_type.value == "skill_delivery_attempt"
        ]
    ) == 1


@pytest.mark.asyncio
async def test_static_agent_skill_requirements_fail_before_durable_activation(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillRequirementError

    skills_dir = tmp_path / ".claude" / "skills"
    _write_skill(skills_dir, extra="required-mcp-servers: [docs]\n")
    store = _store(tmp_path)
    child = SessionHarnessFactory(
        SessionRuntimeFactory(store), workspace_root=tmp_path
    ).create("root").child("agent-1")
    definition = CustomAgentDefinition(
        agent_type="reviewer",
        when_to_use="review",
        source=AgentSource.USER_SETTINGS,
        skills=["reviewing"],
        get_system_prompt=lambda: "Base prompt.",
    )
    llm = _RecordingLLM()
    executor = AgentExecutor(definition, llm_service=llm)
    record = AgentRecord(
        "agent-1", "root", "reviewer", "inspect", "inspect", False, str(tmp_path), {}
    )

    with pytest.raises(SkillRequirementError, match="MCP.*docs"):
        await executor.run(record, child)

    assert llm.requests == []
    assert store.skill_activations.list("root", agent_id="agent-1") == []


@pytest.mark.asyncio
async def test_static_skill_rejects_tool_disabled_by_agent_definition_before_claim(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillRequirementError

    skills_dir = tmp_path / ".claude" / "skills"
    _write_skill(skills_dir, extra="allowed-tools: Bash\n")
    store = _store(tmp_path)
    child = SessionHarnessFactory(
        SessionRuntimeFactory(store), workspace_root=tmp_path
    ).create("root").child("agent-1")
    definition = CustomAgentDefinition(
        agent_type="reviewer",
        when_to_use="review",
        source=AgentSource.USER_SETTINGS,
        skills=["reviewing"],
        tools=["read_file", "bash"],
        disallowed_tools=["bash"],
        get_system_prompt=lambda: "Base prompt.",
    )
    llm = _RecordingLLM()
    executor = AgentExecutor(definition, llm_service=llm)
    record = AgentRecord(
        "agent-1", "root", "reviewer", "inspect", "inspect", False, str(tmp_path), {}
    )

    with pytest.raises(SkillRequirementError, match="tools.*bash"):
        await executor.run(record, child)

    assert llm.requests == []
    assert store.skill_activations.list("root", agent_id="agent-1") == []


@pytest.mark.asyncio
async def test_skill_tool_does_not_execute_packaged_scripts(tmp_path: Path) -> None:
    from tools.skill_tool_v2 import SkillExecuteToolV2

    skills_dir = tmp_path / ".claude" / "skills"
    skill_dir = _write_skill(skills_dir)
    scripts = skill_dir / "scripts"
    scripts.mkdir()
    marker = tmp_path / "executed"
    (scripts / "run.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    store = _store(tmp_path)
    harness = SessionHarnessFactory(
        SessionRuntimeFactory(store), workspace_root=tmp_path
    ).create("root")

    result = await SkillExecuteToolV2().run(
        {"skill": "reviewing"}, {"session_harness": harness}
    )

    assert result.success is True
    assert result.data["content"] == "Review carefully."
    assert result.data["scripts"] == ["scripts/run.py"]
    assert not marker.exists()


@pytest.mark.asyncio
async def test_compatibility_registry_is_progressive_and_rejects_custom_executors(
    tmp_path: Path,
) -> None:
    from services.skill_registry import SkillRegistry

    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir)
    registry = SkillRegistry()
    registry.initialize(str(skills_dir))

    assert await registry.load_all_skills() == 1
    indexed = registry.get("reviewing")
    assert indexed is not None
    assert indexed.content is None
    with pytest.raises(RuntimeError, match="tool pipeline"):
        registry.register_executor("reviewing", lambda: None)


def test_primary_skill_surface_is_session_scoped_without_dynamic_python() -> None:
    import query_engine
    import services.skill_manager as legacy_skill_manager
    from tools import ToolRegistry
    from tools.skill_tool_v2 import (
        SkillInstallToolV2,
        SkillListToolV2,
        SkillUninstallToolV2,
    )

    assert isinstance(ToolRegistry.get("skill_install"), SkillInstallToolV2)
    assert isinstance(ToolRegistry.get("skill_list"), SkillListToolV2)
    assert isinstance(ToolRegistry.get("skill_uninstall"), SkillUninstallToolV2)

    query_source = inspect.getsource(query_engine)
    legacy_source = inspect.getsource(legacy_skill_manager)
    assert "services.skill_manager" not in query_source
    assert "load_all_skills()" not in query_source
    assert "exec_module" not in legacy_source
    assert '"pip", "install"' not in legacy_source
    assert "ToolRegistry.register" not in legacy_source


@pytest.mark.asyncio
async def test_skill_mutation_tools_reject_path_names_before_execution() -> None:
    from tools.skill_tool_v2 import (
        SkillInstallInput,
        SkillInstallToolV2,
        SkillUninstallInput,
        SkillUninstallToolV2,
    )

    install_error = await SkillInstallToolV2().validate(
        SkillInstallInput(source="/tmp/reviewing", name="../outside")
    )
    uninstall_error = await SkillUninstallToolV2().validate(
        SkillUninstallInput(skill="/tmp/outside")
    )

    assert install_error is not None
    assert install_error.message == "Invalid skill name: ../outside"
    assert uninstall_error is not None
    assert uninstall_error.message == "Invalid skill name: /tmp/outside"


@pytest.mark.asyncio
async def test_skill_install_tool_rolls_back_when_resolver_rejects_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from harness.skills import SkillResolver
    from tools.skill_tool_v2 import SkillInstallToolV2

    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: reviewing\ndescription: valid source\n---\n\nReview",
        encoding="utf-8",
    )
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    resolver = SkillResolver(skills_dir)

    def reject_published_skill(name: str):
        raise ValueError(f"rejected after publish: {name}")

    monkeypatch.setattr(resolver, "resolve", reject_published_skill)
    harness = SimpleNamespace(skills=resolver)
    target = skills_dir / "reviewing"

    result = await SkillInstallToolV2().run(
        {"source": str(source), "name": "reviewing"},
        {"session_harness": harness},
    )

    assert result.success is False
    assert not target.exists()


@pytest.mark.asyncio
async def test_compatibility_registry_does_not_report_invalid_install_as_success(
    tmp_path: Path,
) -> None:
    from services.skill_registry import SkillRegistry

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: wrong-name\ndescription: mismatch\n---\n\nReview",
        encoding="utf-8",
    )
    registry = SkillRegistry()
    registry.initialize(str(skills_dir))

    with pytest.raises(ValueError):
        await registry.install_skill(str(source), "reviewing")

    assert registry.list_skills() == []
    assert not (skills_dir / "reviewing").exists()
