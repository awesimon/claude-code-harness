"""Durable, session-scoped Git worktree ownership and recovery."""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from state_core import EventType, WorktreeRecord, WorktreeStatus

_VALID_SLUG_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_SLUG_LENGTH = 64
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}


class WorktreeError(RuntimeError):
    """Base class for controlled worktree failures."""


class WorktreePathError(WorktreeError):
    """Raised when a worktree name or path is unsafe."""


class WorktreeOwnershipError(WorktreeError):
    """Raised when a scope does not own a durable worktree record."""


class WorktreeStateError(WorktreeError):
    """Raised when durable and Git worktree state cannot be reconciled safely."""


class WorktreeNotClean(WorktreeError):
    """Raised before destructive cleanup when work would be discarded."""

    def __init__(self, changed_files: int, commits: int) -> None:
        self.changed_files = changed_files
        self.commits = commits
        super().__init__(
            "worktree removal requires discard_changes=True "
            f"({changed_files} changed files, {commits} commits)"
        )


@dataclass(frozen=True)
class _GitWorktree:
    path: Path
    head: str
    branch_ref: str | None


class WorktreeManager:
    """Own Git worktree mutations for exactly one harness scope."""

    def __init__(self, harness: Any) -> None:
        self.harness = harness
        self.repository = harness.store.worktrees

    @property
    def owner_agent_id(self) -> str | None:
        return self.harness.agent_id

    def create(self, slug: str | None = None, *, base_branch: str | None = None) -> WorktreeRecord:
        if self.active() is not None:
            raise WorktreeStateError("this harness scope is already attached to a worktree")
        slug = slug or f"session-{uuid.uuid4().hex[:8]}"
        self._validate_slug(slug)
        repository_root, common_dir = self._repository_identity(self.harness.effective_cwd)
        flattened = slug.replace("/", "+")
        worktree_root = (repository_root / ".claude" / "worktrees").resolve()
        canonical_path = (worktree_root / flattened).resolve()
        self._require_contained(canonical_path, worktree_root)
        branch = f"worktree-{flattened}"
        base_ref = base_branch or "HEAD"
        base_commit = self._git(repository_root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
        if not base_commit:
            raise WorktreeStateError(f"cannot resolve base revision {base_ref!r}")

        record = self.repository.create(
            WorktreeRecord(
                worktree_id=f"wt-{uuid.uuid4().hex}",
                root_session_id=self.harness.root_session_id,
                agent_id=self.owner_agent_id,
                repository_root=str(repository_root),
                canonical_path=str(canonical_path),
                branch=branch,
                base_commit=base_commit,
                details={
                    "slug": slug,
                    "base_ref": base_ref,
                    "common_git_dir": str(common_dir),
                    "creation_mode": "git",
                    "attached": True,
                },
            )
        )
        record = self.repository.update(
            record.worktree_id, record.revision, status=WorktreeStatus.CREATING
        )
        try:
            worktree_root.mkdir(parents=True, exist_ok=True)
            self._git(
                repository_root,
                "worktree",
                "add",
                "-B",
                branch,
                str(canonical_path),
                base_commit,
            )
            self._validate_record(record, require_ready=False)
            record = self.repository.update(
                record.worktree_id, record.revision, status=WorktreeStatus.READY
            )
        except Exception as exc:
            failed = self.repository.get(record.worktree_id)
            if failed is not None and failed.status in {
                WorktreeStatus.PENDING,
                WorktreeStatus.CREATING,
            }:
                self.repository.update(
                    failed.worktree_id,
                    failed.revision,
                    status=WorktreeStatus.FAILED,
                    details={**failed.details, "error": str(exc), "attached": False},
                )
            self._event(
                EventType.WORKTREE_FAILED,
                record,
                action="create",
                error=str(exc),
            )
            raise

        self.harness._set_effective_cwd(canonical_path)
        self._event(EventType.WORKTREE_CREATED, record, action="create")
        return record

    def active(self) -> WorktreeRecord | None:
        records = [
            record
            for record in self.repository.list(self.harness.root_session_id)
            if record.agent_id == self.owner_agent_id
            and record.status is WorktreeStatus.READY
            and record.details.get("attached") is True
        ]
        if len(records) > 1:
            raise WorktreeStateError("multiple worktrees are attached to one harness scope")
        return records[0] if records else None

    def restore_active(self) -> WorktreeRecord | None:
        try:
            record = self.active()
        except WorktreeStateError:
            candidates = [
                item
                for item in self.repository.list(self.harness.root_session_id)
                if item.agent_id == self.owner_agent_id
                and item.status is WorktreeStatus.READY
                and item.details.get("attached") is True
            ]
            for candidate in candidates:
                self._mark_orphaned(candidate, "multiple attached worktree records")
            return None
        if record is None:
            return None
        try:
            self._validate_record(record)
        except Exception as exc:
            self._mark_orphaned(record, str(exc))
            return None
        self.harness._set_effective_cwd(Path(record.canonical_path))
        self._event(EventType.WORKTREE_RESTORED, record, action="restore")
        return record

    def keep(self, worktree_id: str | None = None) -> WorktreeRecord:
        record = self._owned_record(worktree_id)
        self._validate_record(record)
        kept = self.repository.update(
            record.worktree_id,
            record.revision,
            details={**record.details, "attached": False, "kept": True},
        )
        self.harness._set_effective_cwd(Path(record.repository_root))
        self._event(EventType.WORKTREE_KEPT, kept, action="keep")
        return kept

    def remove(
        self,
        worktree_id: str | None = None,
        *,
        discard_changes: bool = False,
    ) -> WorktreeRecord:
        record = self._owned_record(worktree_id)
        self._validate_record(record)
        changed_files, commits = self._change_summary(record)
        if (changed_files or commits) and not discard_changes:
            raise WorktreeNotClean(changed_files, commits)

        removing = self.repository.update(
            record.worktree_id,
            record.revision,
            status=WorktreeStatus.REMOVING,
            details={
                **record.details,
                "attached": False,
                "discarded_files": changed_files if discard_changes else 0,
                "discarded_commits": commits if discard_changes else 0,
            },
        )
        try:
            arguments = ["worktree", "remove"]
            if discard_changes:
                arguments.append("--force")
            arguments.append(record.canonical_path)
            self._git(Path(record.repository_root), *arguments)
            self._git(Path(record.repository_root), "branch", "-D", record.branch)
            removed = self.repository.update(
                removing.worktree_id,
                removing.revision,
                status=WorktreeStatus.REMOVED,
            )
        except Exception as exc:
            current = self.repository.get(removing.worktree_id)
            if current is not None and current.status is WorktreeStatus.REMOVING:
                self.repository.update(
                    current.worktree_id,
                    current.revision,
                    status=WorktreeStatus.FAILED,
                    details={**current.details, "error": str(exc)},
                )
            self._event(
                EventType.WORKTREE_FAILED,
                removing,
                action="remove",
                error=str(exc),
            )
            raise
        self.harness._set_effective_cwd(Path(record.repository_root))
        self._event(
            EventType.WORKTREE_REMOVED,
            removed,
            action="remove",
            discardedFiles=changed_files if discard_changes else 0,
            discardedCommits=commits if discard_changes else 0,
        )
        return removed

    def _owned_record(self, worktree_id: str | None) -> WorktreeRecord:
        record = self.active() if worktree_id is None else self.repository.get(worktree_id)
        if record is None:
            raise WorktreeStateError("worktree record was not found")
        if (
            record.root_session_id != self.harness.root_session_id
            or record.agent_id != self.owner_agent_id
        ):
            raise WorktreeOwnershipError("worktree is owned by another harness scope")
        if record.status is not WorktreeStatus.READY:
            raise WorktreeStateError(
                f"worktree is not ready (status={record.status.value})"
            )
        return record

    def _validate_record(self, record: WorktreeRecord, *, require_ready: bool = True) -> None:
        if require_ready and record.status is not WorktreeStatus.READY:
            raise WorktreeStateError("worktree record is not ready")
        repository_root = Path(record.repository_root).resolve()
        canonical_path = Path(record.canonical_path).resolve()
        expected_root, expected_common = self._repository_identity(repository_root)
        if expected_root != repository_root:
            raise WorktreeStateError("repository root identity changed")
        if str(expected_common) != record.details.get("common_git_dir"):
            raise WorktreeStateError("repository common Git directory changed")
        worktree_root = (repository_root / ".claude" / "worktrees").resolve()
        self._require_contained(canonical_path, worktree_root)
        if not canonical_path.is_dir():
            raise WorktreeStateError("worktree directory is missing")
        _, actual_common = self._repository_identity(canonical_path)
        if actual_common != expected_common:
            raise WorktreeStateError("worktree belongs to a different repository")
        entries = self._registered_worktrees(repository_root)
        entry = entries.get(canonical_path)
        if entry is None:
            raise WorktreeStateError("path is not registered as a Git worktree")
        if entry.branch_ref != f"refs/heads/{record.branch}":
            raise WorktreeStateError("worktree branch does not match its durable record")
        if not self._git_ok(
            canonical_path,
            "merge-base",
            "--is-ancestor",
            record.base_commit,
            "HEAD",
        ):
            raise WorktreeStateError("worktree base commit is not an ancestor of HEAD")

    def _change_summary(self, record: WorktreeRecord) -> tuple[int, int]:
        path = Path(record.canonical_path)
        status = self._git(path, "status", "--porcelain")
        changed_files = sum(1 for line in status.splitlines() if line.strip())
        count = self._git(path, "rev-list", "--count", f"{record.base_commit}..HEAD")
        try:
            commits = int(count)
        except ValueError as exc:
            raise WorktreeStateError("Git returned an invalid commit count") from exc
        return changed_files, commits

    def _mark_orphaned(self, record: WorktreeRecord, reason: str) -> WorktreeRecord:
        orphaned = self.repository.update(
            record.worktree_id,
            record.revision,
            status=WorktreeStatus.ORPHANED,
            details={**record.details, "attached": False, "orphan_reason": reason},
        )
        self.harness._set_effective_cwd(Path(record.repository_root))
        self._event(
            EventType.WORKTREE_ORPHANED,
            orphaned,
            action="restore",
            reason=reason,
        )
        return orphaned

    def _event(self, event_type: EventType, record: WorktreeRecord, **details: Any) -> None:
        self.harness.session_runtime.append_event(
            event_type,
            {
                "worktreeId": record.worktree_id,
                "agentId": record.agent_id,
                "path": record.canonical_path,
                "branch": record.branch,
                "status": record.status.value,
                **details,
            },
        )

    @staticmethod
    def _validate_slug(slug: str) -> None:
        if not isinstance(slug, str) or not slug or len(slug) > _MAX_SLUG_LENGTH:
            raise WorktreePathError("worktree name must contain 1 to 64 characters")
        for segment in slug.split("/"):
            if segment in {".", ".."} or not _VALID_SLUG_SEGMENT.fullmatch(segment):
                raise WorktreePathError(
                    "worktree name segments may contain only letters, digits, dots, underscores, and dashes"
                )

    @staticmethod
    def _require_contained(candidate: Path, root: Path) -> None:
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise WorktreePathError("worktree path escapes the configured root") from exc

    @classmethod
    def _repository_identity(cls, cwd: Path) -> tuple[Path, Path]:
        entries = cls._registered_worktrees(cwd)
        if not entries:
            raise WorktreeStateError(f"not a Git repository: {cwd}")
        repository_root = next(iter(entries)).resolve()
        common = Path(
            cls._git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
        ).resolve()
        return repository_root, common

    @classmethod
    def _registered_worktrees(cls, cwd: Path) -> dict[Path, _GitWorktree]:
        output = cls._git(cwd, "worktree", "list", "--porcelain")
        records: dict[Path, _GitWorktree] = {}
        current: dict[str, str] = {}
        for line in [*output.splitlines(), ""]:
            if not line:
                path = current.get("worktree")
                if path:
                    record = _GitWorktree(
                        Path(path).resolve(),
                        current.get("HEAD", ""),
                        current.get("branch"),
                    )
                    records[record.path] = record
                current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        return records

    @staticmethod
    def _git(cwd: Path, *arguments: str) -> str:
        environment = {**os.environ, **_GIT_ENV}
        result = subprocess.run(
            ["git", "-C", str(cwd), *arguments],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=environment,
            timeout=30,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "Git command failed"
            raise WorktreeStateError(message)
        return result.stdout.strip()

    @staticmethod
    def _git_ok(cwd: Path, *arguments: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(cwd), *arguments],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env={**os.environ, **_GIT_ENV},
            timeout=30,
        )
        return result.returncode == 0
