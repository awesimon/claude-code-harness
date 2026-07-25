from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base
from state_core import SessionRuntimeFactory, SQLAlchemyStateStore


def _write_skill(
    skills_root: Path,
    name: str,
    *,
    description: str,
    body: str = "Follow the skill.",
    extra: str = "",
) -> Path:
    skill_dir = skills_root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n\n{body}",
        encoding="utf-8",
    )
    return skill_dir


def _store(tmp_path: Path) -> SQLAlchemyStateStore:
    engine = create_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    Base.metadata.create_all(engine)
    return SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))


def test_catalog_discovers_relevant_nested_roots_with_deep_precedence_and_containment(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillCatalog

    user_skills = tmp_path / "user-skills"
    project = tmp_path / "project"
    package = project / "packages" / "api"
    outside = tmp_path / "outside"
    _write_skill(user_skills, "review", description="user review")
    _write_skill(project / ".claude" / "skills", "review", description="project review")
    nested = _write_skill(
        package / ".claude" / "skills", "review", description="package review"
    )
    _write_skill(outside, "escaped", description="must not be discovered")
    (package / ".claude" / "skills" / "escaped").symlink_to(
        outside / "escaped", target_is_directory=True
    )

    catalog = SkillCatalog((user_skills,), cwd=project)
    entries = catalog.discover(accessed_paths=(package / "src" / "app.py",))

    assert [(entry.name, entry.description) for entry in entries] == [
        ("review", "package review")
    ]
    assert entries[0].location == str(nested.resolve())


def test_catalog_rejects_accessed_paths_outside_cwd_and_allowed_roots(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillCatalog

    workspace = tmp_path / "workspace"
    internal = workspace / "package"
    external = tmp_path / "external"
    _write_skill(
        internal / ".claude" / "skills", "inside", description="allowed"
    )
    _write_skill(
        external / ".claude" / "skills", "outside", description="forbidden"
    )

    entries = SkillCatalog((), cwd=workspace).discover(
        accessed_paths=(
            internal / "src" / "app.py",
            external / "secret" / "data.txt",
        )
    )

    assert [entry.name for entry in entries] == ["inside"]


def test_catalog_rejects_symlinked_nested_skill_root(tmp_path: Path) -> None:
    from harness.skills import SkillCatalog

    workspace = tmp_path / "workspace"
    actual = workspace / "actual-skills"
    _write_skill(actual, "escaped", description="must not be discovered")
    nested = workspace / ".claude" / "skills"
    nested.parent.mkdir(parents=True)
    nested.symlink_to(actual, target_is_directory=True)

    entries = SkillCatalog((), cwd=workspace).discover()

    assert entries == ()


def test_catalog_rejects_symlinked_configured_root_and_keeps_regular_root(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillCatalog

    external = tmp_path / "external-skills"
    regular = tmp_path / "regular-skills"
    _write_skill(external, "escaped", description="must not be discovered")
    _write_skill(regular, "allowed", description="regular configured root")
    linked = tmp_path / "linked-skills"
    linked.symlink_to(external, target_is_directory=True)

    entries = SkillCatalog((linked, regular)).discover()

    assert [(entry.name, entry.description) for entry in entries] == [
        ("allowed", "regular configured root")
    ]


def test_resolver_preserves_primary_root_symlink_identity_for_catalog(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillResolver

    external = tmp_path / "external-skills"
    _write_skill(external, "escaped", description="must not be discovered")
    linked = tmp_path / "linked-skills"
    linked.symlink_to(external, target_is_directory=True)

    resolver = SkillResolver(linked)

    assert resolver.index() == ()
    assert resolver.skills_dir == external.resolve()


def test_catalog_rechecks_configured_root_symlink_on_every_discovery(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillCatalog

    configured = tmp_path / "configured-skills"
    external = tmp_path / "external-skills"
    _write_skill(external, "escaped", description="must not be discovered")
    catalog = SkillCatalog((configured,))
    configured.symlink_to(external, target_is_directory=True)

    assert catalog.discover() == ()


def test_catalog_rejects_nested_root_whose_canonical_path_escapes_boundary(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillCatalog

    workspace = tmp_path / "workspace"
    external_claude = tmp_path / "external" / ".claude"
    _write_skill(
        external_claude / "skills", "escaped", description="must not be discovered"
    )
    workspace.mkdir()
    (workspace / ".claude").symlink_to(external_claude, target_is_directory=True)

    entries = SkillCatalog((), cwd=workspace).discover()

    assert entries == ()


def test_catalog_streams_large_skill_body_without_path_read_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness.skills import SkillCatalog

    root = tmp_path / "skills"
    body = "x" * (3 * 1024 * 1024)
    skill_dir = _write_skill(root, "large", description="Large skill", body=body)
    expected_digest = hashlib.sha256(
        f"---\nname: large\ndescription: Large skill\n---\n\n{body}".encode()
    ).hexdigest()
    original_read_bytes = Path.read_bytes

    def reject_full_read(path: Path) -> bytes:
        if path.name == "SKILL.md":
            raise AssertionError("catalog discovery must not call Path.read_bytes")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_full_read)

    entries = SkillCatalog((root,)).discover()

    assert len(entries) == 1
    assert entries[0].content is None
    assert entries[0].digest == expected_digest
    assert entries[0].location == str(skill_dir.resolve())


def test_catalog_enforces_total_skill_file_byte_limit_at_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import harness.skills as skill_runtime

    root = tmp_path / "skills"
    skill_dir = _write_skill(root, "bounded", description="Bounded", body="body")
    size = (skill_dir / "SKILL.md").stat().st_size
    monkeypatch.setattr(skill_runtime, "_MAX_SKILL_DISCOVERY_BYTES", size)
    catalog = skill_runtime.SkillCatalog((root,))

    assert [entry.name for entry in catalog.discover()] == ["bounded"]

    monkeypatch.setattr(skill_runtime, "_MAX_SKILL_DISCOVERY_BYTES", size - 1)
    assert catalog.discover() == ()


def test_resource_manifest_enforces_file_count_and_total_byte_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import harness.skills as skill_runtime

    skills = tmp_path / "skills"
    skill_dir = _write_skill(skills, "bounded", description="Bounded")
    references = skill_dir / "references"
    references.mkdir()
    (references / "one.txt").write_text("ab", encoding="utf-8")
    (references / "two.txt").write_text("cde", encoding="utf-8")
    monkeypatch.setattr(skill_runtime, "_MAX_SKILL_RESOURCE_FILES", 2)
    monkeypatch.setattr(skill_runtime, "_MAX_SKILL_RESOURCE_TOTAL_BYTES", 5)

    snapshot = skill_runtime.SkillResolver(skills).resolve("bounded")
    assert sorted(resource.path for resource in snapshot.resources) == [
        "references/one.txt",
        "references/two.txt",
    ]

    (references / "three.txt").write_text("", encoding="utf-8")
    with pytest.raises(skill_runtime.SkillError, match="count limit"):
        skill_runtime.SkillResolver(skills).resolve("bounded")

    (references / "three.txt").unlink()
    (references / "two.txt").write_text("cdef", encoding="utf-8")
    with pytest.raises(skill_runtime.SkillError, match="total byte limit"):
        skill_runtime.SkillResolver(skills).resolve("bounded")


def test_skill_file_replacement_race_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness.skills import SkillError, SkillResolver

    skills = tmp_path / "skills"
    skill_dir = _write_skill(skills, "raced", description="Raced", body="safe")
    skill_file = skill_dir / "SKILL.md"
    outside = tmp_path / "outside.md"
    outside.write_text(
        "---\nname: raced\ndescription: Outside\n---\n\nsecret",
        encoding="utf-8",
    )
    original_lstat = Path.lstat
    replaced = False

    def replace_after_identity(path: Path):
        nonlocal replaced
        identity = original_lstat(path)
        if path == skill_file and not replaced:
            replaced = True
            path.unlink()
            path.symlink_to(outside)
        return identity

    monkeypatch.setattr(Path, "lstat", replace_after_identity)

    with pytest.raises(SkillError):
        SkillResolver(skills).resolve("raced")


def test_skill_directory_symlink_swap_after_discovery_fails_closed(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillError, SkillResolver

    skills = tmp_path / "skills"
    skill_dir = _write_skill(skills, "raced", description="Raced", body="safe")
    outside_root = tmp_path / "outside"
    outside_skill = _write_skill(
        outside_root,
        "raced",
        description="Raced",
        body="outside secret",
    )
    resolver = SkillResolver(skills)
    selected = resolver.index()[0]
    assert selected.location == str(skill_dir.resolve())

    def swap_after_catalog_selection():
        skill_dir.rename(tmp_path / "original-raced")
        skill_dir.symlink_to(outside_skill, target_is_directory=True)
        return (selected,)

    resolver.index = swap_after_catalog_selection  # type: ignore[method-assign]

    with pytest.raises(SkillError, match="changed|symbolic|digest"):
        resolver.resolve("raced")


def test_resource_replacement_race_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness.skills import SkillPathError, SkillResolver

    skills = tmp_path / "skills"
    skill_dir = _write_skill(skills, "raced", description="Raced")
    references = skill_dir / "references"
    references.mkdir()
    resource = references / "guide.md"
    resource.write_text("safe", encoding="utf-8")
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    original_lstat = Path.lstat
    replaced = False

    def replace_after_identity(path: Path):
        nonlocal replaced
        identity = original_lstat(path)
        if path == resource and not replaced:
            replaced = True
            path.unlink()
            path.symlink_to(outside)
        return identity

    monkeypatch.setattr(Path, "lstat", replace_after_identity)

    with pytest.raises(SkillPathError, match="changed|symbolic"):
        SkillResolver(skills).resolve("raced")


@pytest.mark.parametrize("filename", ("SKILL.md", "references/guide.md"))
def test_stable_open_detects_same_inode_equal_length_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    import harness.skills as skill_runtime

    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"original")
    original_lstat = Path.lstat
    calls = 0

    def overwrite_before_final_identity(candidate: Path):
        nonlocal calls
        if candidate == path:
            calls += 1
            if calls == 2:
                candidate.write_bytes(b"modified")
        return original_lstat(candidate)

    monkeypatch.setattr(Path, "lstat", overwrite_before_final_identity)

    with pytest.raises(skill_runtime.SkillPathError, match="changed while reading"):
        with skill_runtime._open_stable_file(path) as stream:
            assert stream.read() == b"original"


def test_resource_manifest_stops_during_iteration_at_count_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import harness.skills as skill_runtime

    skills = tmp_path / "skills"
    skill_dir = _write_skill(skills, "bounded", description="Bounded")
    references = skill_dir / "references"
    references.mkdir()
    first = references / "one.txt"
    second = references / "two.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    original_rglob = Path.rglob

    def guarded_rglob(path: Path, pattern: str):
        if path != references:
            return original_rglob(path, pattern)

        def values():
            yield first
            yield second
            raise AssertionError("resource traversal was materialized past the limit")

        return values()

    monkeypatch.setattr(skill_runtime, "_MAX_SKILL_RESOURCE_FILES", 1)
    monkeypatch.setattr(Path, "rglob", guarded_rglob)

    with pytest.raises(skill_runtime.SkillError, match="count limit"):
        skill_runtime.SkillResolver(skills).resolve("bounded")


def test_announcement_delta_is_durable_agent_scoped_digest_aware_and_bounded(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillResolver

    primary = tmp_path / "primary"
    project_skills = tmp_path / "project" / ".claude" / "skills"
    _write_skill(
        primary,
        "alpha",
        description="A" * 200,
    )
    overridden = _write_skill(
        project_skills,
        "alpha",
        description="project alpha",
    )
    _write_skill(project_skills, "beta", description="B" * 200)
    store = _store(tmp_path)

    root = SkillResolver(
        primary,
        skill_roots=(project_skills,),
        metadata_repository=store.metadata,
        root_session_id="root",
    )
    initial = root.announcement_delta(context_window_tokens=2_500)
    resumed = SkillResolver(
        primary,
        skill_roots=(project_skills,),
        metadata_repository=store.metadata,
        root_session_id="root",
    )

    assert initial is not None
    assert initial.is_initial is True
    assert [entry.name for entry in initial.entries] == ["alpha", "beta"]
    assert initial.entries[0].location == str(overridden.resolve())
    assert len(initial.content) <= 100
    assert resumed.announcement_delta(context_window_tokens=2_500) is None

    _write_skill(project_skills, "gamma", description="new skill")
    delta = resumed.announcement_delta(context_window_tokens=2_500)
    child = SkillResolver(
        primary,
        skill_roots=(project_skills,),
        metadata_repository=store.metadata,
        root_session_id="root",
        agent_id="child",
    ).announcement_delta(context_window_tokens=2_500)

    assert delta is not None
    assert delta.is_initial is False
    assert [entry.name for entry in delta.entries] == ["gamma"]
    assert child is not None
    assert child.is_initial is True
    assert [entry.name for entry in child.entries] == ["alpha", "beta", "gamma"]


def test_accessed_path_discovery_is_reused_by_later_activation(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillResolver

    primary = tmp_path / "user-skills"
    workspace = tmp_path / "workspace"
    package = workspace / "packages" / "api"
    _write_skill(primary, "review", description="user", body="User body.")
    _write_skill(
        package / ".claude" / "skills",
        "review",
        description="package",
        body="Package body.",
    )
    resolver = SkillResolver(primary, cwd=workspace)

    resolver.announcement_delta(accessed_paths=(package / "src" / "app.py",))
    activation = resolver.activate("review")

    assert activation.snapshot.description == "package"
    assert activation.snapshot.content == "Package body."


def test_announcement_budget_never_exceeds_8000_chars_for_large_contexts() -> None:
    from harness.skills import SkillIndexEntry, SkillResolver

    entries = tuple(
        SkillIndexEntry(
            name=f"skill-{index:03d}",
            description="x" * 250,
            location=f"/skills/skill-{index:03d}",
            digest=str(index),
            metadata={},
        )
        for index in range(40)
    )

    content, included = SkillResolver._format_announcement(
        entries, context_window_tokens=1_000_000
    )

    assert included
    assert len(content) <= 8_000


def test_activation_validates_requirements_before_durable_effects_and_is_idempotent(
    tmp_path: Path,
) -> None:
    from harness.hooks import HookRuntime
    from harness.skills import SkillRequirementError, SkillResolver

    skills = tmp_path / "skills"
    _write_skill(
        skills,
        "research",
        description="Research with docs",
        body="Use the connected documentation server.",
        extra="""allowed-tools: Read
required-mcp-servers: [docs]
hooks:
  PreToolUse:
    - matcher: read_file
      command: echo '{"decision":"allow"}'
""",
    )
    store = _store(tmp_path)
    hooks = HookRuntime(
        [], metadata_repository=store.metadata, root_session_id="activation-root"
    )
    resolver = SkillResolver(
        skills,
        metadata_repository=store.metadata,
        root_session_id="activation-root",
        agent_id="agent-1",
        hook_runtime=hooks,
    )

    with pytest.raises(SkillRequirementError, match="MCP.*docs"):
        resolver.activate(
            "research", available_mcp_servers=(), available_tools=("read_file",)
        )
    with pytest.raises(SkillRequirementError, match="tools.*read_file"):
        resolver.activate(
            "research", available_mcp_servers=("docs",), available_tools=()
        )

    assert hooks.list() == ()
    assert store.metadata.get("activation-root", "skills.activations") is None
    assert store.metadata.get("activation-root", "skills.snapshots") is None

    activated = resolver.activate(
        "research",
        available_mcp_servers=("docs",),
        available_tools=("read_file",),
    )
    resumed_hooks = HookRuntime(
        None, metadata_repository=store.metadata, root_session_id="activation-root"
    )
    resumed = SkillResolver(
        skills,
        metadata_repository=store.metadata,
        root_session_id="activation-root",
        agent_id="agent-1",
        hook_runtime=resumed_hooks,
    ).activate(
        "research",
        available_mcp_servers=("docs",),
        available_tools=("read_file",),
    )

    assert activated.newly_activated is True
    assert activated.snapshot.content == "Use the connected documentation server."
    assert resumed.newly_activated is False
    assert resumed.snapshot == activated.snapshot
    assert len(hooks.list()) == len(resumed_hooks.list()) == 1


def test_shared_activation_requirements_fail_before_claiming_preparing_record(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillRequirementError, SkillResolver

    skills = tmp_path / "skills"
    _write_skill(
        skills,
        "research",
        description="Research",
        extra="allowed-tools: Read\nrequired-mcp-servers: [docs]\n",
    )
    store = _store(tmp_path)
    resolver = SkillResolver(
        skills,
        metadata_repository=store.metadata,
        root_session_id="requirements-root",
        agent_id="agent-1",
        activation_repository=store.skill_activations,
    )

    with pytest.raises(SkillRequirementError, match="MCP.*docs"):
        resolver.activate(
            "research", available_mcp_servers=(), available_tools=("read_file",)
        )

    assert store.skill_activations.list("requirements-root", agent_id="agent-1") == []
    assert store.metadata.get("requirements-root", "skills.activations") is None
    assert store.metadata.get("requirements-root", "skills.snapshots") is None


def test_resolve_has_no_activation_or_hook_registration_side_effects(
    tmp_path: Path,
) -> None:
    from harness.hooks import HookRuntime
    from harness.skills import SkillResolver

    skills = tmp_path / "skills"
    _write_skill(
        skills,
        "research",
        description="Research",
        extra="""hooks:
  PreToolUse:
    - matcher: read_file
      command: exit 0
""",
    )
    store = _store(tmp_path)
    hooks = HookRuntime(
        [], metadata_repository=store.metadata, root_session_id="pure-resolve"
    )
    resolver = SkillResolver(
        skills,
        metadata_repository=store.metadata,
        root_session_id="pure-resolve",
        hook_runtime=hooks,
    )

    snapshot = resolver.resolve("research")

    assert snapshot.name == "research"
    assert hooks.list() == ()
    assert store.metadata.get("pure-resolve", "skills.activations") is None


def test_read_resource_requires_prior_activation(tmp_path: Path) -> None:
    from harness.skills import SkillError, SkillResolver

    skills = tmp_path / "skills"
    skill_dir = _write_skill(skills, "research", description="Research")
    references = skill_dir / "references"
    references.mkdir()
    (references / "guide.md").write_text("guide", encoding="utf-8")
    resolver = SkillResolver(skills)

    resolver.resolve("research")
    with pytest.raises(SkillError, match="activat"):
        resolver.read_resource("research", "references/guide.md")

    resolver.activate("research")
    assert resolver.read_resource("research", "references/guide.md") == "guide"


def test_concurrent_shared_activation_returns_body_exactly_once(tmp_path: Path) -> None:
    from harness.skills import SkillResolver

    skills = tmp_path / "skills"
    _write_skill(
        skills,
        "research",
        description="Research",
        body="Inject this body once.",
    )
    store = _store(tmp_path)
    barrier = Barrier(2)

    def activate() -> tuple[bool, str]:
        resolver = SkillResolver(
            skills,
            metadata_repository=store.metadata,
            root_session_id="concurrent-root",
            agent_id="agent-1",
            activation_repository=store.skill_activations,
        )
        barrier.wait()
        result = resolver.activate("research")
        return result.newly_activated, result.snapshot.content

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: activate(), range(2)))

    assert sorted(newly_activated for newly_activated, _body in results) == [False, True]
    assert [body for newly_activated, body in results if newly_activated] == [
        "Inject this body once."
    ]
    persisted = store.skill_activations.get_by_name(
        "concurrent-root", "agent-1", "research"
    )
    assert persisted is not None
    assert persisted.status.value == "active"


def test_partial_hook_failure_leaves_preparing_and_retry_finalizes_once(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillResolver

    class PartialHookRuntime:
        def __init__(self) -> None:
            self.definitions = {}
            self.fail_once = True

        def register(self, definition) -> None:
            if definition.matcher == "second" and self.fail_once:
                self.fail_once = False
                raise RuntimeError("second hook persistence failed")
            self.definitions.setdefault(definition.hook_id, definition)

    skills = tmp_path / "skills"
    _write_skill(
        skills,
        "research",
        description="Research",
        extra="""hooks:
  PreToolUse:
    - matcher: first
      command: echo '{"decision":"allow"}'
    - matcher: second
      command: echo '{"decision":"allow"}'
""",
    )
    store = _store(tmp_path)
    hooks = PartialHookRuntime()
    resolver = SkillResolver(
        skills,
        metadata_repository=store.metadata,
        root_session_id="prepare-failure",
        agent_id="agent-1",
        hook_runtime=hooks,
        activation_repository=store.skill_activations,
    )

    with pytest.raises(RuntimeError, match="second hook persistence failed"):
        resolver.activate("research")

    preparing = store.skill_activations.get_by_name(
        "prepare-failure", "agent-1", "research"
    )
    assert preparing is not None
    assert preparing.status.value == "preparing"
    assert len(hooks.definitions) == 1
    activation_metadata = store.metadata.get("prepare-failure", "skills.activations")
    assert activation_metadata is not None
    mirrored = activation_metadata.snapshot["scopes"]["agent-1"]["research"]
    assert mirrored["status"] == "preparing"
    assert "repository_prepared" not in mirrored

    retried = resolver.activate("research")
    repeated = resolver.activate("research")

    assert retried.newly_activated is True
    assert retried.snapshot.content == "Follow the skill."
    assert repeated.newly_activated is False
    assert len(hooks.definitions) == 2
    active = store.skill_activations.get_by_name(
        "prepare-failure", "agent-1", "research"
    )
    assert active is not None
    assert active.status.value == "active"


def test_concurrent_different_digests_use_one_winner_snapshot_and_body(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillResolver

    first_skills = tmp_path / "first-skills"
    second_skills = tmp_path / "second-skills"
    _write_skill(first_skills, "research", description="First", body="First body.")
    _write_skill(second_skills, "research", description="Second", body="Second body.")
    store = _store(tmp_path)
    barrier = Barrier(2)

    class BarrierRepository:
        def claim_by_name(self, record):
            barrier.wait()
            return store.skill_activations.claim_by_name(record)

        def __getattr__(self, name):
            return getattr(store.skill_activations, name)

    repository = BarrierRepository()

    def activate(skills: Path):
        return SkillResolver(
            skills,
            metadata_repository=store.metadata,
            root_session_id="digest-race",
            agent_id="agent-1",
            activation_repository=repository,
        ).activate("research")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(activate, (first_skills, second_skills)))

    assert sorted(result.newly_activated for result in results) == [False, True]
    assert len({result.snapshot.digest for result in results}) == 1
    assert [result.snapshot.content for result in results if result.newly_activated] in (
        ["First body."],
        ["Second body."],
    )
    persisted = store.skill_activations.get_by_name(
        "digest-race", "agent-1", "research"
    )
    assert persisted is not None
    assert persisted.status.value == "active"
    assert persisted.skill_digest == results[0].snapshot.digest


def test_shared_activation_repository_repairs_metadata_only_activation(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillResolver

    skills = tmp_path / "skills"
    _write_skill(skills, "research", description="Research")
    store = _store(tmp_path)
    metadata_only = SkillResolver(
        skills,
        metadata_repository=store.metadata,
        root_session_id="shared-root",
        agent_id="agent-1",
    ).activate("research")
    assert metadata_only.newly_activated is True
    assert (
        store.skill_activations.get_for_skill(
            "shared-root", "agent-1", "research", metadata_only.snapshot.digest
        )
        is None
    )

    repaired = SkillResolver(
        skills,
        metadata_repository=store.metadata,
        root_session_id="shared-root",
        agent_id="agent-1",
        activation_repository=store.skill_activations,
    ).activate("research")

    assert repaired.newly_activated is False
    assert store.skill_activations.get_for_skill(
        "shared-root", "agent-1", "research", repaired.snapshot.digest
    ) is not None


def test_explicit_activation_repository_is_authoritative_for_is_active(
    tmp_path: Path,
) -> None:
    from harness.skills import SkillResolver

    skills = tmp_path / "skills"
    _write_skill(skills, "research", description="Research")
    metadata_root = tmp_path / "metadata"
    repository_root = tmp_path / "repository"
    metadata_root.mkdir()
    repository_root.mkdir()
    metadata_store = _store(metadata_root)
    repository_store = _store(repository_root)
    resolver = SkillResolver(
        skills,
        metadata_repository=metadata_store.metadata,
        root_session_id="authority-root",
        agent_id="agent-1",
    )
    resolver.activate("research")

    assert resolver.is_active(
        "research", activation_repository=repository_store.skill_activations
    ) is False


def test_shared_activation_snapshot_recovers_without_metadata_or_source_files(
    tmp_path: Path,
) -> None:
    from shutil import rmtree

    from harness.skills import SkillResolver
    from state_core import SkillActivationRecord

    skills = tmp_path / "skills"
    _write_skill(
        skills,
        "research",
        description="Research",
        body="Durable instructions.",
    )
    snapshot = SkillResolver(skills).resolve("research")
    store = _store(tmp_path)
    store.skill_activations.create(
        SkillActivationRecord(
            activation_id="durable-activation",
            root_session_id="recovery-root",
            agent_id="agent-1",
            skill_name="research",
            skill_digest=snapshot.digest,
            snapshot=snapshot.to_json(),
        )
    )
    rmtree(skills / "research")

    recovered = SkillResolver(
        skills,
        metadata_repository=store.metadata,
        root_session_id="recovery-root",
        agent_id="agent-1",
        activation_repository=store.skill_activations,
    ).activate("research")

    assert recovered.newly_activated is False
    assert recovered.snapshot.content == "Durable instructions."
    assert store.metadata.get("recovery-root", "skills.activations") is not None



@pytest.mark.asyncio
async def test_skill_execute_activates_against_harness_capabilities_and_returns_body_once(
    tmp_path: Path,
) -> None:
    from harness import SessionHarnessFactory
    from tools.skill_tool_v2 import SkillExecuteInput, SkillExecuteToolV2

    _write_skill(
        tmp_path / ".claude" / "skills",
        "research",
        description="Research with docs",
        body="Use docs once.",
        extra="allowed-tools: Read\nrequired-mcp-servers: [docs]\n",
    )
    store = _store(tmp_path)
    harness = SessionHarnessFactory(
        SessionRuntimeFactory(store), workspace_root=tmp_path
    ).create("tool-root")
    harness._services["mcp"] = SimpleNamespace(
        list_servers=lambda: [
            SimpleNamespace(name="docs", status=SimpleNamespace(value="connected"))
        ]
    )
    tool = SkillExecuteToolV2()

    first = await tool.run(
        SkillExecuteInput(skill="research"), {"session_harness": harness}
    )
    second = await tool.run(
        SkillExecuteInput(skill="research"), {"session_harness": harness}
    )

    assert first.success is True
    assert first.data["content"] == "Use docs once."
    assert first.data["already_active"] is False
    assert second.success is True
    assert second.data["content"] is None
    assert second.data["already_active"] is True
