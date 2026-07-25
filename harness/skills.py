"""Progressive Agent Skill discovery with durable per-agent snapshots."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable, Iterator, Mapping

from state_core import (
    EventType,
    RevisionConflict,
    RuntimeMetadataRepository,
    RuntimeRecordRevisionConflict,
)
from utils.frontmatter_parser import extract_frontmatter_field, parse_frontmatter
from utils.skill_paths import is_valid_skill_name

from .hooks import HookDefinition, HookEvent, HookRuntime

_SNAPSHOT_NAMESPACE = "skills.snapshots"
_ANNOUNCEMENT_NAMESPACE = "skills.announcements"
_ACTIVATION_NAMESPACE = "skills.activations"
_RESOURCE_DIRECTORIES = ("assets", "references", "scripts")
_DEFAULT_ANNOUNCEMENT_CHAR_BUDGET = 8_000
_CHARS_PER_TOKEN = 4
_ANNOUNCEMENT_CONTEXT_PERCENT = 0.01
_MAX_ANNOUNCEMENT_DESCRIPTION_CHARS = 250
_MAX_FRONTMATTER_BYTES = 256 * 1024
_DISCOVERY_CHUNK_BYTES = 64 * 1024
_MAX_SKILL_DISCOVERY_BYTES = 8 * 1024 * 1024
_MAX_SKILL_RESOURCE_FILES = 256
_MAX_SKILL_RESOURCE_BYTES = 8 * 1024 * 1024
_MAX_SKILL_RESOURCE_TOTAL_BYTES = 32 * 1024 * 1024
_UNSET = object()
_TOOL_ALIASES = {
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "edit_file",
    "Glob": "glob",
    "Grep": "grep",
    "Bash": "bash",
    "Agent": "agent",
}


class SkillError(ValueError):
    pass


class SkillNotFound(SkillError):
    pass


class SkillPathError(SkillError):
    pass


class SkillChangedError(SkillError):
    pass


class SkillRequirementError(SkillError):
    pass


def _within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _canonical_tool_name(name: str) -> str:
    if name in _TOOL_ALIASES:
        return _TOOL_ALIASES[name]
    value = name.replace("-", "_").replace(" ", "_")
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"_+", "_", value).strip("_").lower()


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    values = value.split() if isinstance(value, str) else value
    if not isinstance(values, list) or any(
        not isinstance(item, str) or not item.strip() for item in values
    ):
        raise SkillError(f"{field} must be a string or list of strings")
    return tuple(item.strip() for item in values)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _directory_identity(value: os.stat_result) -> tuple[int, int, int]:
    return (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode))


@contextmanager
def _open_stable_directory_at(
    parent_fd: int, name: str, display_path: Path
) -> Iterator[int]:
    if not name or name in {".", ".."} or "/" in name:
        raise SkillPathError(f"invalid skill directory component: {name}")
    try:
        path_before = display_path.lstat()
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise SkillPathError(f"skill directory is unavailable: {display_path}") from exc
    if stat.S_ISLNK(path_before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise SkillPathError(f"skill directory cannot be symbolic: {display_path}")
    if not stat.S_ISDIR(path_before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise SkillPathError(f"skill directory is not a directory: {display_path}")
    if _directory_identity(path_before) != _directory_identity(before):
        raise SkillPathError(f"skill directory changed before open: {display_path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise SkillPathError(
            f"skill directory changed before open: {display_path}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if _directory_identity(opened) != _directory_identity(before):
            raise SkillPathError(
                f"skill directory changed before open: {display_path}"
            )
        yield descriptor
        closed = os.fstat(descriptor)
        if _directory_identity(closed) != _directory_identity(opened):
            raise SkillPathError(
                f"skill directory changed while reading: {display_path}"
            )
        try:
            after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            path_after = display_path.lstat()
        except OSError as exc:
            raise SkillPathError(
                f"skill directory changed while reading: {display_path}"
            ) from exc
        if (
            stat.S_ISLNK(after.st_mode)
            or stat.S_ISLNK(path_after.st_mode)
            or _directory_identity(after) != _directory_identity(opened)
            or _directory_identity(path_after) != _directory_identity(opened)
        ):
            raise SkillPathError(
                f"skill directory changed while reading: {display_path}"
            )
    finally:
        os.close(descriptor)


@contextmanager
def _open_directory_path(path: Path) -> Iterator[int]:
    if not path.is_absolute():
        raise SkillPathError(f"skill directory must be absolute: {path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(path.anchor, flags)
    except OSError as exc:
        raise SkillPathError(f"skill directory is unavailable: {path}") from exc
    with ExitStack() as stack:
        stack.callback(os.close, root_fd)
        descriptor = root_fd
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            descriptor = stack.enter_context(
                _open_stable_directory_at(descriptor, part, current)
            )
        yield descriptor


@contextmanager
def _open_directory_beneath(
    boundary: Path, relative_parts: Iterable[str]
) -> Iterator[int]:
    parts = tuple(relative_parts)
    if any(not part or part in {".", ".."} or "/" in part for part in parts):
        raise SkillPathError("skill directory contains invalid path components")
    with ExitStack() as stack:
        descriptor = stack.enter_context(_open_directory_path(boundary))
        current = boundary
        for part in parts:
            current /= part
            descriptor = stack.enter_context(
                _open_stable_directory_at(descriptor, part, current)
            )
        yield descriptor


@contextmanager
def _open_stable_file_at(
    directory_fd: int, name: str, display_path: Path
) -> Iterator[BinaryIO]:
    if not name or name in {".", ".."} or "/" in name:
        raise SkillPathError(f"invalid skill file name: {name}")
    try:
        path_before = display_path.lstat()
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise SkillPathError(f"skill file is unavailable: {display_path}") from exc
    if stat.S_ISLNK(path_before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise SkillPathError(f"skill file cannot be symbolic: {display_path}")
    if not stat.S_ISREG(path_before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise SkillPathError(f"skill file is not regular: {display_path}")
    if _file_identity(path_before) != _file_identity(before):
        raise SkillPathError(f"skill file changed before open: {display_path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise SkillPathError(f"skill file changed before open: {display_path}") from exc
    try:
        opened = os.fstat(descriptor)
        if _file_identity(opened) != _file_identity(before):
            raise SkillPathError(f"skill file changed before open: {display_path}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            yield stream
        closed = os.fstat(descriptor)
        if _file_identity(closed) != _file_identity(opened):
            raise SkillPathError(f"skill file changed while reading: {display_path}")
        try:
            after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            path_after = display_path.lstat()
        except OSError as exc:
            raise SkillPathError(
                f"skill file changed while reading: {display_path}"
            ) from exc
        if (
            stat.S_ISLNK(after.st_mode)
            or stat.S_ISLNK(path_after.st_mode)
            or _file_identity(after) != _file_identity(opened)
            or _file_identity(path_after) != _file_identity(opened)
        ):
            raise SkillPathError(
                f"skill file changed while reading: {display_path}"
            )
    finally:
        os.close(descriptor)


@contextmanager
def _open_stable_file(path: Path) -> Iterator[BinaryIO]:
    with _open_directory_path(path.parent) as directory_fd:
        with _open_stable_file_at(directory_fd, path.name, path) as stream:
            yield stream


@dataclass(frozen=True)
class SkillIndexEntry:
    name: str
    description: str
    location: str
    digest: str
    metadata: Mapping[str, Any]
    boundary: str = ""
    relative_parts: tuple[str, ...] = ()
    content: None = None


@dataclass(frozen=True)
class SkillAnnouncement:
    content: str
    entries: tuple[SkillIndexEntry, ...]
    is_initial: bool


@dataclass(frozen=True)
class SkillResource:
    path: str
    digest: str
    size: int

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "digest": self.digest, "size": self.size}

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "SkillResource":
        return cls(str(value["path"]), str(value["digest"]), int(value["size"]))


@dataclass(frozen=True)
class SkillSnapshot:
    name: str
    description: str
    base_dir: str
    digest: str
    content: str
    allowed_tools: tuple[str, ...] = ()
    hooks: Mapping[str, Any] | None = None
    required_mcp_servers: tuple[str, ...] = ()
    resources: tuple[SkillResource, ...] = ()
    metadata: Mapping[str, Any] | None = None

    @property
    def scripts(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.resources if item.path.startswith("scripts/"))

    def prompt(self, session_id: str) -> str:
        skill_dir = self.base_dir.replace("\\", "/")
        content = self.content.replace("${CLAUDE_SKILL_DIR}", skill_dir)
        content = content.replace("${CLAUDE_SESSION_ID}", session_id)
        return f"Base directory for this skill: {skill_dir}\n\n{content}"

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "base_dir": self.base_dir,
            "digest": self.digest,
            "content": self.content,
            "allowed_tools": list(self.allowed_tools),
            "hooks": self.hooks,
            "required_mcp_servers": list(self.required_mcp_servers),
            "resources": [item.to_json() for item in self.resources],
            "metadata": self.metadata,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "SkillSnapshot":
        hooks = value.get("hooks")
        metadata = value.get("metadata")
        return cls(
            name=str(value["name"]),
            description=str(value["description"]),
            base_dir=str(value["base_dir"]),
            digest=str(value["digest"]),
            content=str(value["content"]),
            allowed_tools=tuple(value.get("allowed_tools", ())),
            hooks=dict(hooks) if isinstance(hooks, Mapping) else None,
            required_mcp_servers=tuple(value.get("required_mcp_servers", ())),
            resources=tuple(
                SkillResource.from_json(item) for item in value.get("resources", ())
            ),
            metadata=dict(metadata) if isinstance(metadata, Mapping) else None,
        )


@dataclass(frozen=True)
class SkillActivation:
    snapshot: SkillSnapshot
    newly_activated: bool
    registered_hook_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DurableSkillDeliveryIndex:
    harness: Any
    consumed_digests: set[str]
    attempt_counts: dict[str, int]
    acknowledged_attempt_ids: set[str]

    @classmethod
    def load(cls, harness: Any) -> "DurableSkillDeliveryIndex":
        index = cls(harness, set(), {}, set())
        for event in harness.session_runtime.events():
            if event.payload.get("agentId") != harness.agent_id:
                continue
            digest = event.payload.get("digest")
            if event.event_type is EventType.SKILL_DELIVERY_ATTEMPT:
                if isinstance(digest, str):
                    index.consumed_digests.add(digest)
                    attempt = event.payload.get("attempt")
                    if isinstance(attempt, int) and not isinstance(attempt, bool):
                        index.attempt_counts[digest] = max(
                            index.attempt_counts.get(digest, 0), attempt
                        )
                continue
            if event.event_type is EventType.SKILL_DELIVERY:
                if isinstance(digest, str):
                    index.consumed_digests.add(digest)
                attempt_id = event.payload.get("attemptId")
                if isinstance(attempt_id, str):
                    index.acknowledged_attempt_ids.add(attempt_id)
                continue
            if event.event_type is not EventType.TOOL_RESULT:
                continue
            if (
                _canonical_tool_name(str(event.payload.get("name") or ""))
                != "skill_execute"
            ):
                continue
            result = event.payload.get("result")
            if isinstance(result, Mapping) and result.get("content") is not None:
                result_digest = result.get("digest")
                if isinstance(result_digest, str):
                    index.consumed_digests.add(result_digest)
        return index

    def record_attempt(self, digest: str, attempt: int) -> None:
        self.consumed_digests.add(digest)
        self.attempt_counts[digest] = max(self.attempt_counts.get(digest, 0), attempt)

    def record_acknowledgement(self, attempt_id: str) -> None:
        self.acknowledged_attempt_ids.add(attempt_id)


@dataclass(frozen=True)
class SkillDeliveryAttempt:
    snapshot: SkillSnapshot
    attempt_id: str
    idempotency_key: str
    delivery_index: DurableSkillDeliveryIndex


def _reload_runtime_after_conflict(harness: Any) -> None:
    runtime = harness.session_runtime
    state = runtime.store.states.load_session(runtime.session_id)
    if state is None:
        raise KeyError(runtime.session_id)
    runtime.state = state


def claim_skill_delivery_attempts(
    harness: Any,
    snapshots: Iterable[SkillSnapshot],
    *,
    source: str,
    retry: bool = False,
    delivery_index: DurableSkillDeliveryIndex | None = None,
) -> tuple[SkillDeliveryAttempt, ...]:
    index = delivery_index or DurableSkillDeliveryIndex.load(harness)
    claimed: list[SkillDeliveryAttempt] = []
    for snapshot in snapshots:
        for _ in range(16):
            if snapshot.digest in index.consumed_digests and not retry:
                break
            attempt_number = index.attempt_counts.get(snapshot.digest, 0) + 1
            scope = harness.agent_id or "root"
            idempotency_key = (
                f"skill-delivery:{harness.root_session_id}:{scope}:"
                f"{snapshot.digest}:{attempt_number}"
            )
            attempt_id = _sha256(idempotency_key.encode("utf-8"))
            try:
                harness.session_runtime.append_event(
                    EventType.SKILL_DELIVERY_ATTEMPT,
                    {
                        "agentId": harness.agent_id,
                        "skill": snapshot.name,
                        "digest": snapshot.digest,
                        "attempt": attempt_number,
                        "attemptId": attempt_id,
                        "idempotencyKey": idempotency_key,
                        "source": source,
                        "status": "dispatch_claimed",
                    },
                )
            except RevisionConflict:
                _reload_runtime_after_conflict(harness)
                index = DurableSkillDeliveryIndex.load(harness)
                continue
            index.record_attempt(snapshot.digest, attempt_number)
            claimed.append(
                SkillDeliveryAttempt(snapshot, attempt_id, idempotency_key, index)
            )
            break
        else:
            raise SkillError("skill delivery claim conflicted repeatedly")
    return tuple(claimed)


def acknowledge_skill_delivery(
    harness: Any,
    attempt: SkillDeliveryAttempt,
    *,
    source: str,
) -> None:
    index = attempt.delivery_index
    for _ in range(16):
        if attempt.attempt_id in index.acknowledged_attempt_ids:
            return
        try:
            harness.session_runtime.append_event(
                EventType.SKILL_DELIVERY,
                {
                    "agentId": harness.agent_id,
                    "skill": attempt.snapshot.name,
                    "digest": attempt.snapshot.digest,
                    "attemptId": attempt.attempt_id,
                    "idempotencyKey": attempt.idempotency_key,
                    "source": source,
                    "status": "acknowledged",
                },
            )
        except RevisionConflict:
            _reload_runtime_after_conflict(harness)
            index = DurableSkillDeliveryIndex.load(harness)
            continue
        index.record_acknowledgement(attempt.attempt_id)
        return
    raise SkillError("skill delivery acknowledgement conflicted repeatedly")


class SkillCatalog:
    """Discover skill metadata from configured and path-relevant roots."""

    def __init__(
        self,
        roots: Iterable[Path | str],
        *,
        cwd: Path | str | None = None,
        allowed_roots: Iterable[Path | str] = (),
    ) -> None:
        configured_roots = tuple(
            Path(root).expanduser() for root in roots
        )
        self._configured_roots = configured_roots
        self.roots = tuple(
            root.resolve(strict=False)
            for root in configured_roots
            if not root.is_symlink()
        )
        self.cwd = (
            Path(cwd).expanduser().resolve(strict=False) if cwd is not None else None
        )
        boundaries = [
            *(Path(root).expanduser().resolve(strict=False) for root in allowed_roots),
            *self.roots,
        ]
        if self.cwd is not None:
            boundaries.append(self.cwd)
        self.allowed_roots = tuple(dict.fromkeys(boundaries))

    def discover(
        self, *, accessed_paths: Iterable[Path | str] = ()
    ) -> tuple[SkillIndexEntry, ...]:
        selected: dict[str, tuple[tuple[int, int, int], SkillIndexEntry]] = {}
        for root, precedence in self._candidate_roots(accessed_paths):
            if not root.is_dir():
                continue
            canonical_root = root.resolve(strict=True)
            for child in sorted(root.iterdir(), key=lambda item: item.name):
                try:
                    if child.is_symlink() or not child.is_dir():
                        continue
                    canonical = child.resolve(strict=True)
                    if not _within(canonical, canonical_root):
                        continue
                    digest, frontmatter = self._read_frontmatter(canonical)
                    name, description = self._identity(canonical, frontmatter)
                    metadata = frontmatter.get("metadata")
                    entry = SkillIndexEntry(
                        name=name,
                        description=description,
                        location=str(canonical),
                        digest=digest,
                        metadata=(
                            dict(metadata) if isinstance(metadata, Mapping) else {}
                        ),
                        boundary=str(canonical_root),
                        relative_parts=(child.name,),
                    )
                    current = selected.get(name)
                    if current is None or precedence >= current[0]:
                        selected[name] = (precedence, entry)
                except (OSError, SkillError, ValueError):
                    continue
        return tuple(selected[name][1] for name in sorted(selected))

    def _candidate_roots(
        self, accessed_paths: Iterable[Path | str]
    ) -> tuple[tuple[Path, tuple[int, int, int]], ...]:
        candidates: dict[Path, tuple[int, int, int]] = {}
        for index, configured in enumerate(self._configured_roots):
            if configured.is_symlink():
                continue
            root = configured.resolve(strict=False)
            candidates[root] = (0, index, len(root.parts))

        relevant = ([self.cwd] if self.cwd is not None else []) + [
            Path(path).expanduser().resolve(strict=False) for path in accessed_paths
        ]
        for path_index, path in enumerate(relevant):
            cursor = path if path.is_dir() else path.parent
            if not any(_within(cursor, boundary) for boundary in self.allowed_roots):
                continue
            for ancestor in (cursor, *cursor.parents):
                if not any(
                    _within(ancestor, boundary) for boundary in self.allowed_roots
                ):
                    continue
                nested = ancestor / ".claude" / "skills"
                if nested.is_symlink() or not nested.is_dir():
                    continue
                canonical = nested.resolve(strict=True)
                if not any(
                    _within(canonical, boundary) for boundary in self.allowed_roots
                ):
                    continue
                precedence = (1, len(ancestor.parts), path_index)
                previous = candidates.get(canonical)
                if previous is None or precedence > previous:
                    candidates[canonical] = precedence
        return tuple(
            sorted(candidates.items(), key=lambda item: (item[1], str(item[0])))
        )

    @staticmethod
    def _read_frontmatter(skill_dir: Path) -> tuple[str, dict[str, Any]]:
        skill_file = skill_dir / "SKILL.md"

        digest = hashlib.sha256()
        frontmatter_lines: list[bytes] = []
        frontmatter_size = 0
        total_size = 0
        with _open_stable_file(skill_file) as stream:
            first_line = stream.readline(_MAX_FRONTMATTER_BYTES + 1)
            digest.update(first_line)
            total_size += len(first_line)
            if total_size > _MAX_SKILL_DISCOVERY_BYTES:
                raise SkillError("SKILL.md exceeds the discovery byte limit")
            if first_line.rstrip(b"\r\n").strip() != b"---":
                raise SkillError("skill frontmatter is required")
            frontmatter_lines.append(first_line)
            frontmatter_size += len(first_line)

            while True:
                remaining = _MAX_FRONTMATTER_BYTES - frontmatter_size
                if remaining <= 0:
                    raise SkillError("skill frontmatter exceeds the 256 KiB limit")
                line = stream.readline(remaining + 1)
                if not line:
                    raise SkillError("skill frontmatter is not terminated")
                digest.update(line)
                total_size += len(line)
                if total_size > _MAX_SKILL_DISCOVERY_BYTES:
                    raise SkillError("SKILL.md exceeds the discovery byte limit")
                frontmatter_lines.append(line)
                frontmatter_size += len(line)
                if frontmatter_size > _MAX_FRONTMATTER_BYTES:
                    raise SkillError("skill frontmatter exceeds the 256 KiB limit")
                if line.rstrip(b"\r\n").strip() == b"---":
                    break

            while chunk := stream.read(_DISCOVERY_CHUNK_BYTES):
                total_size += len(chunk)
                if total_size > _MAX_SKILL_DISCOVERY_BYTES:
                    raise SkillError("SKILL.md exceeds the discovery byte limit")
                digest.update(chunk)

        try:
            frontmatter, _content = parse_frontmatter(
                b"".join(frontmatter_lines).decode("utf-8")
            )
        except UnicodeDecodeError as exc:
            raise SkillError("skill frontmatter must be UTF-8") from exc
        if not isinstance(frontmatter, dict):
            raise SkillError("skill frontmatter must be a mapping")
        return digest.hexdigest(), frontmatter

    @staticmethod
    def _identity(skill_dir: Path, frontmatter: Mapping[str, Any]) -> tuple[str, str]:
        name = frontmatter.get("name")
        description = frontmatter.get("description")
        if not is_valid_skill_name(name) or name != skill_dir.name:
            raise SkillError("skill name is invalid or does not match its directory")
        if not isinstance(description, str) or not description.strip():
            raise SkillError("skill description is required")
        return name, description.strip()


class SkillResolver:
    """Index metadata eagerly and resolve immutable skill bodies on selection."""

    def __init__(
        self,
        skills_dir: Path | str,
        *,
        skill_roots: Iterable[Path | str] = (),
        cwd: Path | str | None = None,
        allowed_roots: Iterable[Path | str] = (),
        metadata_repository: RuntimeMetadataRepository | None = None,
        root_session_id: str | None = None,
        agent_id: str | None = None,
        hook_runtime: HookRuntime | None = None,
        activation_repository: Any | None = None,
    ) -> None:
        if (metadata_repository is None) != (root_session_id is None):
            raise ValueError("metadata_repository and root_session_id must be provided together")
        configured_skills_dir = Path(skills_dir).expanduser()
        self.skills_dir = configured_skills_dir.resolve()
        roots = (configured_skills_dir, *(Path(root) for root in skill_roots))
        self.catalog = SkillCatalog(roots, cwd=cwd, allowed_roots=allowed_roots)
        self._metadata = metadata_repository
        self._root_session_id = root_session_id
        self.agent_id = agent_id
        self._scope = agent_id or "root"
        self._hooks = hook_runtime
        self._activation_repository = activation_repository
        self._accessed_paths: list[Path] = []
        self._ephemeral_activations: dict[str, SkillSnapshot] = {}
        self._ephemeral_announced: dict[str, str] = {}
        self._ephemeral_announcement_history: list[str] = []

    def index(
        self, *, accessed_paths: Iterable[Path | str] = ()
    ) -> tuple[SkillIndexEntry, ...]:
        for path in accessed_paths:
            canonical = Path(path).expanduser().resolve(strict=False)
            if canonical not in self._accessed_paths:
                self._accessed_paths.append(canonical)
        return self.catalog.discover(accessed_paths=self._accessed_paths)

    def announcement_delta(
        self,
        *,
        context_window_tokens: int | None = None,
        accessed_paths: Iterable[Path | str] = (),
    ) -> SkillAnnouncement | None:
        indexed = self.index(accessed_paths=accessed_paths)
        if not indexed:
            return None
        if self._metadata is None or self._root_session_id is None:
            pending = tuple(
                entry
                for entry in indexed
                if self._ephemeral_announced.get(entry.name) != entry.digest
            )
            if not pending:
                return None
            content, included = self._format_announcement(
                pending, context_window_tokens=context_window_tokens
            )
            if not included:
                return None
            is_initial = not self._ephemeral_announced
            for entry in included:
                self._ephemeral_announced[entry.name] = entry.digest
            self._ephemeral_announcement_history.append(content)
            return SkillAnnouncement(content, included, is_initial)

        for _ in range(16):
            current = self._metadata.get(
                self._root_session_id, _ANNOUNCEMENT_NAMESPACE
            )
            state = dict(current.snapshot) if current is not None else {}
            scopes = {
                key: dict(value)
                for key, value in state.get("scopes", {}).items()
                if isinstance(key, str) and isinstance(value, Mapping)
            }
            histories = {
                key: list(value)
                for key, value in state.get("history", {}).items()
                if isinstance(key, str) and isinstance(value, (list, tuple))
                and all(isinstance(item, str) for item in value)
            }
            announced = {
                str(name): str(digest)
                for name, digest in scopes.get(self._scope, {}).items()
                if isinstance(name, str) and isinstance(digest, str)
            }
            pending = tuple(
                entry
                for entry in indexed
                if announced.get(entry.name) != entry.digest
            )
            if not pending:
                return None
            content, included = self._format_announcement(
                pending, context_window_tokens=context_window_tokens
            )
            if not included:
                return None
            is_initial = not announced
            for entry in included:
                announced[entry.name] = entry.digest
            scopes[self._scope] = announced
            histories.setdefault(self._scope, []).append(content)
            try:
                self._metadata.put(
                    self._root_session_id,
                    _ANNOUNCEMENT_NAMESPACE,
                    {"scopes": scopes, "history": histories},
                    current.revision if current is not None else None,
                )
                return SkillAnnouncement(content, included, is_initial)
            except RuntimeRecordRevisionConflict:
                continue
        raise SkillError("skill announcement update conflicted repeatedly")

    def announcement_history(self) -> tuple[str, ...]:
        if self._metadata is None or self._root_session_id is None:
            return tuple(self._ephemeral_announcement_history)
        record = self._metadata.get(self._root_session_id, _ANNOUNCEMENT_NAMESPACE)
        state = record.snapshot if record is not None else {}
        histories = state.get("history", {})
        values = histories.get(self._scope, ()) if isinstance(histories, Mapping) else ()
        return tuple(item for item in values if isinstance(item, str))

    @staticmethod
    def _format_announcement(
        entries: tuple[SkillIndexEntry, ...], *, context_window_tokens: int | None
    ) -> tuple[str, tuple[SkillIndexEntry, ...]]:
        if context_window_tokens is not None:
            if isinstance(context_window_tokens, bool) or context_window_tokens <= 0:
                raise ValueError("context_window_tokens must be positive")
            budget = min(
                int(
                    context_window_tokens
                    * _CHARS_PER_TOKEN
                    * _ANNOUNCEMENT_CONTEXT_PERCENT
                ),
                _DEFAULT_ANNOUNCEMENT_CHAR_BUDGET,
            )
        else:
            budget = _DEFAULT_ANNOUNCEMENT_CHAR_BUDGET
        if budget <= 0:
            return "", ()

        def full_line(entry: SkillIndexEntry) -> str:
            description = entry.description
            if len(description) > _MAX_ANNOUNCEMENT_DESCRIPTION_CHARS:
                description = description[: _MAX_ANNOUNCEMENT_DESCRIPTION_CHARS - 3] + "..."
            return f"- {entry.name}: {description}"

        full = "\n".join(full_line(entry) for entry in entries)
        if len(full) <= budget:
            return full, entries

        lines: list[str] = []
        included: list[SkillIndexEntry] = []
        for entry in entries:
            line = f"- {entry.name}"
            proposed = line if not lines else "\n".join((*lines, line))
            if len(proposed) > budget:
                break
            lines.append(line)
            included.append(entry)
        return "\n".join(lines), tuple(included)

    def resolve(self, name: str) -> SkillSnapshot:
        if not is_valid_skill_name(name):
            raise SkillNotFound(name)
        stored = self._stored_snapshot(name)
        if stored is not None:
            return stored
        snapshot = self._load_snapshot(name)
        self._store_snapshot(snapshot)
        return snapshot

    def activate(
        self,
        name: str,
        *,
        available_mcp_servers: Iterable[str] = (),
        available_tools: Iterable[str] | None = None,
        activation_repository: Any | None = None,
    ) -> SkillActivation:
        if not is_valid_skill_name(name):
            raise SkillNotFound(name)
        repository = (
            activation_repository
            if activation_repository is not None
            else self._activation_repository
        )
        metadata_entry = self._stored_activation_entry(name)
        existing = self._snapshot_from_activation_entry(metadata_entry)
        legacy_metadata_activation = (
            repository is not None
            and existing is not None
            and not bool(metadata_entry.get("repository_prepared"))
            and metadata_entry.get("status") != "preparing"
        )
        shared_record = self._stored_shared_record(repository, name)
        candidate = (
            SkillSnapshot.from_json(shared_record.snapshot)
            if shared_record is not None
            else existing or self._stored_snapshot(name) or self._load_snapshot(name)
        )
        if repository is None:
            self._validate_requirements(
                candidate,
                available_mcp_servers=available_mcp_servers,
                available_tools=available_tools,
            )
            hook_definitions = (
                tuple(self._hook_definitions(candidate)) if candidate.hooks else ()
            )
            hook_ids = tuple(definition.hook_id for definition in hook_definitions)
            self._store_snapshot(candidate)
            self._register_hook_definitions(hook_definitions)
            selected, newly_activated = self._claim_metadata_activation(
                candidate, hook_ids
            )
        else:
            if shared_record is None:
                self._validate_requirements(
                    candidate,
                    available_mcp_servers=available_mcp_servers,
                    available_tools=available_tools,
                )
            candidate_definitions = (
                tuple(self._hook_definitions(candidate)) if candidate.hooks else ()
            )
            candidate_hook_ids = tuple(
                definition.hook_id for definition in candidate_definitions
            )
            claimed, _created = self._claim_shared_activation(
                repository, candidate, candidate_hook_ids
            )
            selected = SkillSnapshot.from_json(claimed.snapshot)
            self._validate_requirements(
                selected,
                available_mcp_servers=available_mcp_servers,
                available_tools=available_tools,
            )
            hook_definitions = (
                tuple(self._hook_definitions(selected)) if selected.hooks else ()
            )
            hook_ids = tuple(definition.hook_id for definition in hook_definitions)
            self._store_snapshot(selected, replace=True)
            self._mirror_activation_metadata(
                selected,
                hook_ids,
                status=getattr(claimed.status, "value", claimed.status),
            )
            self._register_hook_definitions(hook_definitions)
            _active, first_activated = repository.finalize_active(
                claimed.activation_id, claimed.revision
            )
            newly_activated = first_activated and not legacy_metadata_activation
        return SkillActivation(
            selected,
            newly_activated,
            hook_ids,
        )

    def _stored_shared_activation(
        self, repository: Any | None, name: str
    ) -> SkillSnapshot | None:
        record = self._stored_shared_record(repository, name)
        if record is None or getattr(record.status, "value", record.status) != "active":
            return None
        return SkillSnapshot.from_json(record.snapshot)

    def _stored_shared_record(self, repository: Any | None, name: str) -> Any | None:
        if repository is None or self._root_session_id is None:
            return None
        get_by_name = getattr(repository, "get_by_name", None)
        record = (
            get_by_name(self._root_session_id, self._scope, name)
            if callable(get_by_name)
            else next(
                (
                    item
                    for item in repository.list(
                        self._root_session_id, agent_id=self._scope
                    )
                    if item.skill_name == name
                ),
                None,
            )
        )
        return record

    def is_active(
        self,
        name: str,
        *,
        activation_repository: Any = _UNSET,
    ) -> bool:
        repository = (
            self._activation_repository
            if activation_repository is _UNSET
            else activation_repository
        )
        if repository is not None:
            return self._stored_shared_activation(repository, name) is not None
        return (
            self._stored_activation(name) is not None
            or name in self._ephemeral_activations
        )

    def active_snapshots(self) -> tuple[SkillSnapshot, ...]:
        repository = self._activation_repository
        if repository is None or self._root_session_id is None:
            values: list[SkillSnapshot] = []
            names = {*self._ephemeral_activations, *self._stored_activation_names()}
            for name in sorted(names):
                snapshot = self._stored_activation(name)
                if snapshot is None:
                    snapshot = self._ephemeral_activations.get(name)
                if snapshot is not None:
                    values.append(snapshot)
            return tuple(values)
        records = repository.list(self._root_session_id, agent_id=self._scope)
        active = [
            record
            for record in records
            if getattr(record.status, "value", record.status) == "active"
        ]
        active.sort(key=lambda record: (record.created_at, record.activation_id))
        return tuple(SkillSnapshot.from_json(record.snapshot) for record in active)

    def _stored_activation_names(self) -> tuple[str, ...]:
        if self._metadata is None or self._root_session_id is None:
            return ()
        record = self._metadata.get(self._root_session_id, _ACTIVATION_NAMESPACE)
        state = record.snapshot if record is not None else {}
        scopes = state.get("scopes", {})
        scope = scopes.get(self._scope, {}) if isinstance(scopes, Mapping) else {}
        return tuple(str(name) for name in scope) if isinstance(scope, Mapping) else ()

    def _load_snapshot(self, name: str) -> SkillSnapshot:
        indexed = {entry.name: entry for entry in self.index()}
        entry = indexed.get(name)
        if entry is None:
            raise SkillNotFound(name)
        skill_dir = Path(entry.location)
        boundary = Path(entry.boundary) if entry.boundary else skill_dir.parent
        relative_parts = entry.relative_parts or (skill_dir.name,)
        with _open_directory_beneath(boundary, relative_parts) as skill_fd:
            raw, frontmatter, content = self._read_skill_at(skill_fd, skill_dir)
            if _sha256(raw) != entry.digest:
                raise SkillChangedError(
                    "SKILL.md digest changed after catalog selection"
                )
            resolved_name, description = self._identity(skill_dir, frontmatter)
            if resolved_name != name:
                raise SkillError("skill directory and frontmatter names must match")
            allowed_tools = tuple(
                _canonical_tool_name(item)
                for item in _string_tuple(
                    extract_frontmatter_field(frontmatter, "allowed_tools"),
                    "allowed-tools",
                )
            )
            required_mcp = _string_tuple(
                extract_frontmatter_field(frontmatter, "required_mcp_servers"),
                "required-mcp-servers",
            )
            hooks = frontmatter.get("hooks")
            if hooks is not None and not isinstance(hooks, Mapping):
                raise SkillError("hooks must be a mapping")
            metadata = frontmatter.get("metadata")
            if metadata is not None and not isinstance(metadata, Mapping):
                raise SkillError("metadata must be a mapping")
            return SkillSnapshot(
                resolved_name,
                description,
                str(skill_dir),
                entry.digest,
                content,
                allowed_tools,
                dict(hooks) if isinstance(hooks, Mapping) else None,
                required_mcp,
                self._resource_manifest_at(skill_fd, skill_dir),
                dict(metadata) if isinstance(metadata, Mapping) else None,
            )

    @staticmethod
    def _validate_requirements(
        snapshot: SkillSnapshot,
        *,
        available_mcp_servers: Iterable[str],
        available_tools: Iterable[str] | None,
    ) -> None:
        connected = {str(name) for name in available_mcp_servers}
        missing_mcp = sorted(set(snapshot.required_mcp_servers) - connected)
        if missing_mcp:
            raise SkillRequirementError(
                f"Required MCP servers are not connected: {', '.join(missing_mcp)}"
            )
        if available_tools is None:
            return
        visible = {_canonical_tool_name(str(name)) for name in available_tools}
        missing_tools = sorted(set(snapshot.allowed_tools) - visible)
        if missing_tools:
            raise SkillRequirementError(
                f"Required tools are not available: {', '.join(missing_tools)}"
            )

    def _stored_activation(self, name: str) -> SkillSnapshot | None:
        entry = self._stored_activation_entry(name)
        if entry.get("repository_prepared") or entry.get("status") == "preparing":
            return None
        return self._snapshot_from_activation_entry(entry)

    def _stored_activation_entry(self, name: str) -> Mapping[str, Any]:
        if self._metadata is None or self._root_session_id is None:
            return {}
        record = self._metadata.get(self._root_session_id, _ACTIVATION_NAMESPACE)
        state = dict(record.snapshot) if record is not None else {}
        scopes = state.get("scopes", {})
        scope = scopes.get(self._scope, {}) if isinstance(scopes, Mapping) else {}
        activation = scope.get(name) if isinstance(scope, Mapping) else None
        return activation if isinstance(activation, Mapping) else {}

    @staticmethod
    def _snapshot_from_activation_entry(
        activation: Mapping[str, Any],
    ) -> SkillSnapshot | None:
        snapshot = activation.get("snapshot")
        return SkillSnapshot.from_json(snapshot) if isinstance(snapshot, Mapping) else None

    def _claim_metadata_activation(
        self, candidate: SkillSnapshot, hook_ids: tuple[str, ...]
    ) -> tuple[SkillSnapshot, bool]:
        if self._metadata is None or self._root_session_id is None:
            existing = self._ephemeral_activations.setdefault(candidate.name, candidate)
            if existing is not candidate:
                return existing, False
            return candidate, True
        for _ in range(16):
            current = self._metadata.get(self._root_session_id, _ACTIVATION_NAMESPACE)
            state = dict(current.snapshot) if current is not None else {}
            scopes = {
                key: dict(value)
                for key, value in state.get("scopes", {}).items()
                if isinstance(key, str) and isinstance(value, Mapping)
            }
            scope = dict(scopes.get(self._scope, {}))
            existing = scope.get(candidate.name)
            if isinstance(existing, Mapping) and isinstance(
                existing.get("snapshot"), Mapping
            ):
                return SkillSnapshot.from_json(existing["snapshot"]), False
            scope[candidate.name] = {
                "digest": candidate.digest,
                "snapshot": candidate.to_json(),
                "registered_hook_ids": list(hook_ids),
            }
            scopes[self._scope] = scope
            try:
                self._metadata.put(
                    self._root_session_id,
                    _ACTIVATION_NAMESPACE,
                    {"scopes": scopes},
                    current.revision if current is not None else None,
                )
                self._ephemeral_activations.setdefault(candidate.name, candidate)
                return candidate, True
            except RuntimeRecordRevisionConflict:
                continue
        raise SkillError("skill activation update conflicted repeatedly")

    def _claim_shared_activation(
        self,
        repository: Any,
        snapshot: SkillSnapshot,
        hook_ids: tuple[str, ...],
    ) -> tuple[Any, bool]:
        if self._root_session_id is None:
            raise SkillError("root_session_id is required for durable activation")
        from state_core.runtime_primitives import SkillActivationRecord

        activation_id = _sha256(
            f"{self._root_session_id}\0{self._scope}\0{snapshot.name}\0{snapshot.digest}".encode(
                "utf-8"
            )
        )
        record = SkillActivationRecord(
            activation_id=activation_id,
            root_session_id=self._root_session_id,
            agent_id=self._scope,
            skill_name=snapshot.name,
            skill_digest=snapshot.digest,
            snapshot=snapshot.to_json(),
            registered_hook_ids=hook_ids,
            allowed_tools=snapshot.allowed_tools,
        )
        return repository.claim_by_name(record)

    def _mirror_activation_metadata(
        self,
        snapshot: SkillSnapshot,
        hook_ids: tuple[str, ...],
        *,
        status: str,
    ) -> None:
        if self._metadata is None or self._root_session_id is None:
            return
        for _ in range(16):
            current = self._metadata.get(self._root_session_id, _ACTIVATION_NAMESPACE)
            state = dict(current.snapshot) if current is not None else {}
            scopes = {
                key: dict(value)
                for key, value in state.get("scopes", {}).items()
                if isinstance(key, str) and isinstance(value, Mapping)
            }
            scope = dict(scopes.get(self._scope, {}))
            existing = scope.get(snapshot.name)
            if (
                isinstance(existing, Mapping)
                and "status" not in existing
                and existing.get("digest") == snapshot.digest
                and self._snapshot_from_activation_entry(existing) == snapshot
            ):
                return
            mirrored = {
                "digest": snapshot.digest,
                "snapshot": snapshot.to_json(),
                "registered_hook_ids": list(hook_ids),
                "status": status,
            }
            if existing == mirrored:
                return
            scope[snapshot.name] = mirrored
            scopes[self._scope] = scope
            try:
                self._metadata.put(
                    self._root_session_id,
                    _ACTIVATION_NAMESPACE,
                    {"scopes": scopes},
                    current.revision if current is not None else None,
                )
                return
            except RuntimeRecordRevisionConflict:
                continue
        raise SkillError("skill activation metadata mirror conflicted repeatedly")

    def read_resource(
        self,
        skill_name: str,
        resource: str,
        *,
        activation_repository: Any = _UNSET,
    ) -> str:
        repository = (
            self._activation_repository
            if activation_repository is _UNSET
            else activation_repository
        )
        if repository is not None:
            snapshot = self._stored_shared_activation(repository, skill_name)
        else:
            snapshot = self._stored_activation(skill_name)
            if snapshot is None:
                snapshot = self._ephemeral_activations.get(skill_name)
        if snapshot is None:
            raise SkillError(f"skill must be activated before reading resources: {skill_name}")
        if not isinstance(resource, str) or not resource or Path(resource).is_absolute():
            raise SkillPathError("resource path must be relative to the skill directory")
        parts = Path(resource).parts
        if ".." in parts:
            raise SkillPathError("resource path cannot contain parent traversal")
        base = Path(snapshot.base_dir)
        manifest = {item.path: item for item in snapshot.resources}
        normalized = Path(*parts).as_posix()
        expected = manifest.get(normalized)
        if expected is None:
            raise SkillPathError("resource was not part of the selected skill snapshot")
        with ExitStack() as stack:
            directory_fd = stack.enter_context(_open_directory_path(base))
            current = base
            for part in parts[:-1]:
                current /= part
                directory_fd = stack.enter_context(
                    _open_stable_directory_at(directory_fd, part, current)
                )
            candidate = current / parts[-1]
            with _open_stable_file_at(
                directory_fd, parts[-1], candidate
            ) as stream:
                raw = stream.read(expected.size + 1)
        if len(raw) != expected.size or _sha256(raw) != expected.digest:
            raise SkillChangedError("resource changed after the skill snapshot was selected")
        return raw.decode("utf-8")

    def _read_skill(self, skill_dir: Path) -> tuple[bytes, dict[str, Any], str]:
        with _open_directory_path(skill_dir) as skill_fd:
            return self._read_skill_at(skill_fd, skill_dir)

    @staticmethod
    def _read_skill_at(
        skill_fd: int, skill_dir: Path
    ) -> tuple[bytes, dict[str, Any], str]:
        skill_file = skill_dir / "SKILL.md"
        with _open_stable_file_at(skill_fd, "SKILL.md", skill_file) as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise SkillError("SKILL.md exceeds the 2 MiB limit")
        try:
            frontmatter, content = parse_frontmatter(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise SkillError("SKILL.md must be UTF-8") from exc
        if not isinstance(frontmatter, dict):
            raise SkillError("skill frontmatter must be a mapping")
        return raw, frontmatter, content

    @staticmethod
    def _identity(skill_dir: Path, frontmatter: Mapping[str, Any]) -> tuple[str, str]:
        name = frontmatter.get("name")
        description = frontmatter.get("description")
        if (
            not is_valid_skill_name(name)
            or name != skill_dir.name
        ):
            raise SkillError("skill name is invalid or does not match its directory")
        if not isinstance(description, str) or not description.strip():
            raise SkillError("skill description is required")
        return name, description.strip()

    @staticmethod
    def _resource_manifest(skill_dir: Path) -> tuple[SkillResource, ...]:
        with _open_directory_path(skill_dir) as skill_fd:
            return SkillResolver._resource_manifest_at(skill_fd, skill_dir)

    @staticmethod
    def _resource_manifest_at(
        skill_fd: int, skill_dir: Path
    ) -> tuple[SkillResource, ...]:
        resources: list[SkillResource] = []
        total_bytes = 0

        def walk(directory_fd: int, relative: tuple[str, ...]) -> None:
            nonlocal total_bytes
            directory_path = skill_dir.joinpath(*relative)
            try:
                names = sorted(os.listdir(directory_fd))
            except OSError as exc:
                raise SkillPathError(
                    "skill resource changed while being selected"
                ) from exc
            for name in names:
                candidate_path = directory_path / name
                try:
                    candidate_type = os.stat(
                        name, dir_fd=directory_fd, follow_symlinks=False
                    )
                except OSError as exc:
                    raise SkillPathError(
                        "skill resource changed while being selected"
                    ) from exc
                if stat.S_ISLNK(candidate_type.st_mode):
                    raise SkillPathError("skill resource cannot be symbolic")
                if stat.S_ISDIR(candidate_type.st_mode):
                    with _open_stable_directory_at(
                        directory_fd, name, candidate_path
                    ) as child_fd:
                        walk(child_fd, (*relative, name))
                    continue
                if not stat.S_ISREG(candidate_type.st_mode):
                    raise SkillPathError("skill resource is not a regular file")
                if len(resources) >= _MAX_SKILL_RESOURCE_FILES:
                    raise SkillError("skill resource count limit exceeded")
                with _open_stable_file_at(
                    directory_fd, name, candidate_path
                ) as stream:
                    size = os.fstat(stream.fileno()).st_size
                    if size > _MAX_SKILL_RESOURCE_BYTES:
                        raise SkillError("skill resource exceeds the 8 MiB limit")
                    total_bytes += size
                    if total_bytes > _MAX_SKILL_RESOURCE_TOTAL_BYTES:
                        raise SkillError("skill resource total byte limit exceeded")
                    raw = stream.read(_MAX_SKILL_RESOURCE_BYTES + 1)
                    if len(raw) != size:
                        raise SkillChangedError(
                            "skill resource changed while being selected"
                        )
                resources.append(
                    SkillResource(
                        Path(*relative, name).as_posix(),
                        _sha256(raw),
                        size,
                    )
                )

        for directory_name in _RESOURCE_DIRECTORIES:
            directory = skill_dir / directory_name
            try:
                directory_type = os.stat(
                    directory_name, dir_fd=skill_fd, follow_symlinks=False
                )
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise SkillPathError(
                    "skill resource changed while being selected"
                ) from exc
            if stat.S_ISLNK(directory_type.st_mode):
                raise SkillPathError(f"{directory_name} cannot be symbolic")
            if not stat.S_ISDIR(directory_type.st_mode):
                raise SkillPathError(f"{directory_name} is not a directory")
            with _open_stable_directory_at(
                skill_fd, directory_name, directory
            ) as directory_fd:
                walk(directory_fd, (directory_name,))
        return tuple(resources)

    def _stored_snapshot(self, name: str) -> SkillSnapshot | None:
        snapshot = self._metadata_snapshot()
        scopes = snapshot.get("scopes", {})
        scope = scopes.get(self._scope, {}) if isinstance(scopes, Mapping) else {}
        value = scope.get(name) if isinstance(scope, Mapping) else None
        return SkillSnapshot.from_json(value) if isinstance(value, Mapping) else None

    def _store_snapshot(
        self, selected: SkillSnapshot, *, replace: bool = False
    ) -> None:
        if self._metadata is None:
            return

        def mutation(snapshot: dict[str, Any]) -> dict[str, Any]:
            scopes = {
                key: dict(value)
                for key, value in snapshot.get("scopes", {}).items()
                if isinstance(key, str) and isinstance(value, Mapping)
            }
            scope = dict(scopes.get(self._scope, {}))
            if replace:
                scope[selected.name] = selected.to_json()
            else:
                scope.setdefault(selected.name, selected.to_json())
            scopes[self._scope] = scope
            return {"scopes": scopes}

        self._mutate_metadata(mutation)

    def _register_hooks(self, snapshot: SkillSnapshot) -> None:
        definitions = tuple(self._hook_definitions(snapshot)) if snapshot.hooks else ()
        self._register_hook_definitions(definitions)

    def _register_hook_definitions(
        self, definitions: Iterable[HookDefinition]
    ) -> None:
        if self._hooks is None:
            return
        for definition in definitions:
            self._hooks.register(definition)

    @staticmethod
    def _hook_definitions(snapshot: SkillSnapshot) -> Iterable[HookDefinition]:
        assert snapshot.hooks is not None
        for event_name, configured in snapshot.hooks.items():
            try:
                event = HookEvent(event_name)
            except ValueError as exc:
                raise SkillError(f"unsupported skill hook event: {event_name}") from exc
            items = configured if isinstance(configured, (list, tuple)) else [configured]
            for outer_index, item in enumerate(items):
                if not isinstance(item, Mapping):
                    raise SkillError("skill hook entries must be mappings")
                commands = item.get("hooks")
                commands = commands if isinstance(commands, (list, tuple)) else [item]
                for inner_index, command in enumerate(commands):
                    if not isinstance(command, Mapping) or not isinstance(
                        command.get("command"), str
                    ):
                        raise SkillError("skill command hooks require a command")
                    hook_id = (
                        f"skill:{snapshot.digest}:{event.value}:"
                        f"{outer_index}:{inner_index}"
                    )
                    yield HookDefinition(
                        hook_id=hook_id,
                        event=event,
                        command=command["command"],
                        matcher=item.get("matcher") or command.get("matcher"),
                        timeout=command.get("timeout", 600.0),
                        output_limit=command.get("output_limit", 64 * 1024),
                        fail_closed=command.get("fail_closed"),
                    )

    def _metadata_snapshot(self) -> dict[str, Any]:
        if self._metadata is None or self._root_session_id is None:
            return {}
        record = self._metadata.get(self._root_session_id, _SNAPSHOT_NAMESPACE)
        return dict(record.snapshot) if record is not None else {}

    def _mutate_metadata(
        self, mutation: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        assert self._metadata is not None
        assert self._root_session_id is not None
        for _ in range(16):
            current = self._metadata.get(self._root_session_id, _SNAPSHOT_NAMESPACE)
            snapshot = dict(current.snapshot) if current is not None else {}
            expected = current.revision if current is not None else None
            try:
                self._metadata.put(
                    self._root_session_id,
                    _SNAPSHOT_NAMESPACE,
                    mutation(snapshot),
                    expected,
                )
                return
            except RuntimeRecordRevisionConflict:
                continue
        raise SkillError("skill snapshot update conflicted repeatedly")
