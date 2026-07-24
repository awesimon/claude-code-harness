from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.engine import AgentExecutor
from agents.types import AgentSource, CustomAgentDefinition
from harness import SessionHarnessFactory
from harness.hooks import HookRuntime
from models import Base
from services.llm_service import ChatCompletionResponse
from state_core import AgentRecord, SessionRuntimeFactory, SQLAlchemyStateStore


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
    assert len(hooks.list()) == 1
    assert hooks.list()[0].matcher == "bash"


class _RecordingLLM:
    def __init__(self) -> None:
        self.requests = []

    async def chat_completion(self, request):
        self.requests.append(request)
        return ChatCompletionResponse(id="one", model="test", content="done")


@pytest.mark.asyncio
async def test_agent_skill_prompt_and_allowed_tools_are_isolated(tmp_path: Path) -> None:
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

    request = llm.requests[0]
    assert "Base prompt." in request.messages[0].content
    assert "Review carefully." in request.messages[0].content
    assert [tool["function"]["name"] for tool in request.tools] == ["read_file"]


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
