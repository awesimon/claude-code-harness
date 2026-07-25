"""Progressive Agent Skill discovery with durable per-agent snapshots."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from state_core import RuntimeMetadataRepository, RuntimeRecordRevisionConflict
from utils.frontmatter_parser import extract_frontmatter_field, parse_frontmatter
from utils.skill_paths import is_valid_skill_name

from .hooks import HookDefinition, HookEvent, HookRuntime

_SNAPSHOT_NAMESPACE = "skills.snapshots"
_RESOURCE_DIRECTORIES = ("assets", "references", "scripts")
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


@dataclass(frozen=True)
class SkillIndexEntry:
    name: str
    description: str
    location: str
    digest: str
    metadata: Mapping[str, Any]
    content: None = None


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


class SkillResolver:
    """Index metadata eagerly and resolve immutable skill bodies on selection."""

    def __init__(
        self,
        skills_dir: Path | str,
        *,
        metadata_repository: RuntimeMetadataRepository | None = None,
        root_session_id: str | None = None,
        agent_id: str | None = None,
        hook_runtime: HookRuntime | None = None,
    ) -> None:
        if (metadata_repository is None) != (root_session_id is None):
            raise ValueError("metadata_repository and root_session_id must be provided together")
        self.skills_dir = Path(skills_dir).expanduser().resolve()
        self._metadata = metadata_repository
        self._root_session_id = root_session_id
        self.agent_id = agent_id
        self._scope = agent_id or "root"
        self._hooks = hook_runtime

    def index(self) -> tuple[SkillIndexEntry, ...]:
        if not self.skills_dir.is_dir():
            return ()
        entries: list[SkillIndexEntry] = []
        for child in sorted(self.skills_dir.iterdir(), key=lambda item: item.name):
            try:
                canonical = child.resolve(strict=True)
                if not child.is_dir() or not _within(canonical, self.skills_dir):
                    continue
                raw, frontmatter, _ = self._read_skill(canonical)
                name, description = self._identity(canonical, frontmatter)
                metadata = frontmatter.get("metadata")
                entries.append(
                    SkillIndexEntry(
                        name,
                        description,
                        str(canonical),
                        _sha256(raw),
                        dict(metadata) if isinstance(metadata, Mapping) else {},
                    )
                )
            except (OSError, SkillError, ValueError):
                continue
        return tuple(entries)

    def resolve(self, name: str) -> SkillSnapshot:
        if not is_valid_skill_name(name):
            raise SkillNotFound(name)
        stored = self._stored_snapshot(name)
        if stored is not None:
            self._register_hooks(stored)
            return stored
        skill_dir = (self.skills_dir / name).resolve(strict=True)
        if not skill_dir.is_dir() or not _within(skill_dir, self.skills_dir):
            raise SkillPathError("skill directory escapes the configured skills root")
        raw, frontmatter, content = self._read_skill(skill_dir)
        resolved_name, description = self._identity(skill_dir, frontmatter)
        if resolved_name != name:
            raise SkillError("skill directory and frontmatter names must match")
        allowed_tools = tuple(
            _canonical_tool_name(item)
            for item in _string_tuple(
                extract_frontmatter_field(frontmatter, "allowed_tools"), "allowed-tools"
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
        snapshot = SkillSnapshot(
            resolved_name,
            description,
            str(skill_dir),
            _sha256(raw),
            content,
            allowed_tools,
            dict(hooks) if isinstance(hooks, Mapping) else None,
            required_mcp,
            self._resource_manifest(skill_dir),
            dict(metadata) if isinstance(metadata, Mapping) else None,
        )
        self._store_snapshot(snapshot)
        self._register_hooks(snapshot)
        return snapshot

    def read_resource(self, skill_name: str, resource: str) -> str:
        snapshot = self.resolve(skill_name)
        if not isinstance(resource, str) or not resource or Path(resource).is_absolute():
            raise SkillPathError("resource path must be relative to the skill directory")
        parts = Path(resource).parts
        if ".." in parts:
            raise SkillPathError("resource path cannot contain parent traversal")
        base = Path(snapshot.base_dir).resolve(strict=True)
        candidate = (base / resource).resolve(strict=True)
        if not _within(candidate, base) or not candidate.is_file():
            raise SkillPathError("resource escapes the skill directory")
        manifest = {item.path: item for item in snapshot.resources}
        expected = manifest.get(candidate.relative_to(base).as_posix())
        if expected is None:
            raise SkillPathError("resource was not part of the selected skill snapshot")
        raw = candidate.read_bytes()
        if len(raw) != expected.size or _sha256(raw) != expected.digest:
            raise SkillChangedError("resource changed after the skill snapshot was selected")
        return raw.decode("utf-8")

    def _read_skill(self, skill_dir: Path) -> tuple[bytes, dict[str, Any], str]:
        skill_file = (skill_dir / "SKILL.md").resolve(strict=True)
        if not _within(skill_file, skill_dir) or not skill_file.is_file():
            raise SkillPathError("SKILL.md escapes the skill directory")
        raw = skill_file.read_bytes()
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
        resources: list[SkillResource] = []
        for directory_name in _RESOURCE_DIRECTORIES:
            directory = skill_dir / directory_name
            if not directory.exists():
                continue
            canonical_directory = directory.resolve(strict=True)
            if not canonical_directory.is_dir() or not _within(canonical_directory, skill_dir):
                raise SkillPathError(f"{directory_name} escapes the skill directory")
            for candidate in sorted(directory.rglob("*")):
                canonical = candidate.resolve(strict=True)
                if not _within(canonical, skill_dir):
                    raise SkillPathError("skill resource escapes the skill directory")
                if canonical.is_dir():
                    continue
                if not canonical.is_file():
                    raise SkillPathError("skill resource is not a regular file")
                raw = canonical.read_bytes()
                if len(raw) > 8 * 1024 * 1024:
                    raise SkillError("skill resource exceeds the 8 MiB limit")
                resources.append(
                    SkillResource(
                        candidate.relative_to(skill_dir).as_posix(),
                        _sha256(raw),
                        len(raw),
                    )
                )
        return tuple(resources)

    def _stored_snapshot(self, name: str) -> SkillSnapshot | None:
        snapshot = self._metadata_snapshot()
        scopes = snapshot.get("scopes", {})
        scope = scopes.get(self._scope, {}) if isinstance(scopes, Mapping) else {}
        value = scope.get(name) if isinstance(scope, Mapping) else None
        return SkillSnapshot.from_json(value) if isinstance(value, Mapping) else None

    def _store_snapshot(self, selected: SkillSnapshot) -> None:
        if self._metadata is None:
            return

        def mutation(snapshot: dict[str, Any]) -> dict[str, Any]:
            scopes = {
                key: dict(value)
                for key, value in snapshot.get("scopes", {}).items()
                if isinstance(key, str) and isinstance(value, Mapping)
            }
            scope = dict(scopes.get(self._scope, {}))
            scope.setdefault(selected.name, selected.to_json())
            scopes[self._scope] = scope
            return {"scopes": scopes}

        self._mutate_metadata(mutation)

    def _register_hooks(self, snapshot: SkillSnapshot) -> None:
        if self._hooks is None or not snapshot.hooks:
            return
        for index, definition in enumerate(self._hook_definitions(snapshot)):
            self._hooks.register(definition)

    @staticmethod
    def _hook_definitions(snapshot: SkillSnapshot) -> Iterable[HookDefinition]:
        assert snapshot.hooks is not None
        for event_name, configured in snapshot.hooks.items():
            try:
                event = HookEvent(event_name)
            except ValueError as exc:
                raise SkillError(f"unsupported skill hook event: {event_name}") from exc
            items = configured if isinstance(configured, list) else [configured]
            for outer_index, item in enumerate(items):
                if not isinstance(item, Mapping):
                    raise SkillError("skill hook entries must be mappings")
                commands = item.get("hooks")
                commands = commands if isinstance(commands, list) else [item]
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
