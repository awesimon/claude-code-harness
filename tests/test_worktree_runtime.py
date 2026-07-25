from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from harness import (
    PermissionMode,
    SessionHarnessFactory,
    WorktreeNotClean,
    WorktreeOwnershipError,
    WorktreePathError,
    WorktreeStatus,
)
from models import Base
from state_core import EventType, SessionRuntimeFactory, SQLAlchemyStateStore


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Harness Tests")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    return repo.resolve()


@pytest.fixture
def harness_factory(tmp_path: Path, git_repo: Path) -> SessionHarnessFactory:
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime.db'}")
    Base.metadata.create_all(engine)
    store = SQLAlchemyStateStore(sessionmaker(bind=engine))
    return SessionHarnessFactory(
        SessionRuntimeFactory(store),
        workspace_root=git_repo,
        permission_mode=PermissionMode.BYPASS,
    )


def test_create_changes_only_harness_effective_cwd(
    git_repo: Path, harness_factory: SessionHarnessFactory
) -> None:
    process_cwd = Path.cwd()
    harness = harness_factory.create("root")

    record = harness.worktrees.create("feature")

    assert record.status is WorktreeStatus.READY
    assert harness.effective_cwd == Path(record.canonical_path)
    assert harness.runtime_context.workspace_root == git_repo
    assert Path.cwd() == process_cwd
    assert record.branch == "worktree-feature"
    assert record.base_commit == _git(git_repo, "rev-parse", "HEAD")
    assert record.details["attached"] is True


def test_remove_fails_closed_for_uncommitted_changes(
    harness_factory: SessionHarnessFactory,
) -> None:
    harness = harness_factory.create("dirty")
    record = harness.worktrees.create("dirty")
    worktree = Path(record.canonical_path)
    (worktree / "dirty.txt").write_text("dirty", encoding="utf-8")

    with pytest.raises(WorktreeNotClean) as error:
        harness.worktrees.remove(record.worktree_id)

    assert error.value.changed_files == 1
    assert worktree.exists()
    assert harness.store.worktrees.get(record.worktree_id).status is WorktreeStatus.READY


@pytest.mark.parametrize(
    "slug",
    ["../escape", "/absolute", "a//b", "a/./b", "a/../b", "bad:name", "x" * 65],
)
def test_create_rejects_unsafe_names_before_writes(
    harness_factory: SessionHarnessFactory, slug: str
) -> None:
    harness = harness_factory.create(f"unsafe-{len(slug)}-{slug[-1:]}")

    with pytest.raises(WorktreePathError):
        harness.worktrees.create(slug)

    assert harness.store.worktrees.list(harness.root_session_id) == []


def test_wrong_agent_cannot_keep_or_remove_worktree(
    harness_factory: SessionHarnessFactory,
) -> None:
    root = harness_factory.create("owners")
    owner = root.child("owner")
    other = root.child("other")
    record = owner.worktrees.create("owned")

    with pytest.raises(WorktreeOwnershipError):
        other.worktrees.keep(record.worktree_id)
    with pytest.raises(WorktreeOwnershipError):
        other.worktrees.remove(record.worktree_id, discard_changes=True)

    assert Path(record.canonical_path).exists()


def test_existing_branch_is_reset_to_requested_base(
    git_repo: Path, harness_factory: SessionHarnessFactory
) -> None:
    initial = _git(git_repo, "rev-parse", "HEAD")
    _git(git_repo, "branch", "worktree-existing", initial)
    harness = harness_factory.create("existing")

    record = harness.worktrees.create("existing", base_branch="main")

    assert record.base_commit == initial
    assert _git(Path(record.canonical_path), "rev-parse", "HEAD") == initial
    assert _git(Path(record.canonical_path), "branch", "--show-current") == record.branch


def test_keep_preserves_worktree_and_detaches_harness(
    git_repo: Path, harness_factory: SessionHarnessFactory
) -> None:
    harness = harness_factory.create("keep")
    record = harness.worktrees.create("keep")

    kept = harness.worktrees.keep(record.worktree_id)

    assert kept.status is WorktreeStatus.READY
    assert kept.details["attached"] is False
    assert Path(record.canonical_path).exists()
    assert harness.effective_cwd == git_repo
    resumed = harness_factory.resume("keep")
    assert resumed.effective_cwd == git_repo


def test_remove_requires_explicit_discard_for_commits_and_dirty_files(
    harness_factory: SessionHarnessFactory,
) -> None:
    harness = harness_factory.create("discard")
    record = harness.worktrees.create("discard")
    worktree = Path(record.canonical_path)
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    _git(worktree, "add", "tracked.txt")
    _git(worktree, "commit", "-m", "work")
    (worktree / "dirty.txt").write_text("dirty", encoding="utf-8")

    with pytest.raises(WorktreeNotClean) as error:
        harness.worktrees.remove(record.worktree_id)
    assert (error.value.changed_files, error.value.commits) == (1, 1)

    removed = harness.worktrees.remove(record.worktree_id, discard_changes=True)

    assert removed.status is WorktreeStatus.REMOVED
    assert not worktree.exists()
    assert harness.effective_cwd == Path(record.repository_root)


def test_resume_restores_only_a_valid_owned_worktree(
    harness_factory: SessionHarnessFactory,
) -> None:
    first = harness_factory.create("resume")
    record = first.worktrees.create("resume")

    resumed = harness_factory.resume("resume")

    assert resumed.effective_cwd == Path(record.canonical_path)
    assert resumed.worktrees.active().worktree_id == record.worktree_id
    restored = [
        event
        for event in resumed.session_runtime.events()
        if event.event_type is EventType.WORKTREE_RESTORED
    ]
    assert len(restored) == 1


def test_resume_marks_invalid_git_metadata_orphaned_without_deleting(
    git_repo: Path, harness_factory: SessionHarnessFactory
) -> None:
    first = harness_factory.create("orphan")
    record = first.worktrees.create("orphan")
    worktree = Path(record.canonical_path)
    _git(worktree, "checkout", "--detach")

    resumed = harness_factory.resume("orphan")

    persisted = resumed.store.worktrees.get(record.worktree_id)
    assert persisted.status is WorktreeStatus.ORPHANED
    assert resumed.effective_cwd == git_repo
    assert worktree.exists()
    assert any(
        event.event_type is EventType.WORKTREE_ORPHANED
        for event in resumed.session_runtime.events()
    )


@pytest.mark.asyncio
async def test_path_aware_tools_use_effective_cwd(
    harness_factory: SessionHarnessFactory,
) -> None:
    harness = harness_factory.create("tools")
    record = harness.worktrees.create("tools")
    worktree = Path(record.canonical_path)

    written = await harness.tool_runtime.execute(
        "write_file",
        {"file_path": "scoped.txt", "content": "needle\n", "overwrite": False},
        harness.runtime_context,
    )
    shell = await harness.tool_runtime.execute(
        "bash", {"command": "pwd"}, harness.runtime_context
    )
    globbed = await harness.tool_runtime.execute(
        "glob", {"pattern": "scoped.txt"}, harness.runtime_context
    )
    grepped = await harness.tool_runtime.execute(
        "grep", {"pattern": "needle", "path": "."}, harness.runtime_context
    )

    assert written.result.success
    assert (worktree / "scoped.txt").read_text(encoding="utf-8") == "needle\n"
    assert shell.result.data["stdout"].strip() == str(worktree)
    assert globbed.result.data == [str(worktree / "scoped.txt")]
    assert grepped.result.success
    assert grepped.result.metadata["search_path"] == str(worktree)
