"""Session-scoped runtime composition for root and child agent work."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from state_core import SessionRuntime, SessionRuntimeFactory

from .context import ApprovalCallback, PermissionMode, RuntimeContext
from .permissions import PermissionPolicy
from .runtime import ToolRuntime

_UNSET = object()
_CAPABILITY_TOKEN = object()
_RESERVED_METADATA_KEYS = frozenset(
    {
        "agent_id",
        "allowed_workspaces",
        "approval_callback",
        "cancellation",
        "current_mode",
        "effective_cwd",
        "parent_agent_id",
        "permission_mode",
        "runtime_context",
        "session_harness",
        "session_id",
        "session_runtime",
        "store",
        "tool_runtime",
        "tool_timeout",
        "tool_timeout_disabled",
        "workspace_root",
    }
)


class HarnessScopeError(ValueError):
    """Raised when a scope attempts to exceed harness-owned boundaries."""


def _canonical_path(value: Path | str) -> Path:
    if not isinstance(value, (Path, str)):
        raise HarnessScopeError("workspace paths must be strings or Path instances")
    return Path(value).expanduser().resolve()


def _copy_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise HarnessScopeError("metadata must be a mapping")
    reserved = _RESERVED_METADATA_KEYS.intersection(metadata)
    if reserved:
        names = ", ".join(sorted(reserved))
        raise HarnessScopeError(f"reserved metadata keys are not allowed: {names}")
    try:
        return copy.deepcopy(dict(metadata))
    except Exception as exc:
        raise HarnessScopeError("metadata copy failed safely") from exc


def _canonicalize_allowed_workspaces(
    values: Iterable[Path | str] | object,
    root_workspace: Path,
) -> tuple[Path, ...]:
    if isinstance(values, (str, bytes)):
        raise HarnessScopeError("allowed_workspaces must be an iterable of paths")
    try:
        candidates = list(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise HarnessScopeError("allowed_workspaces must be an iterable of paths") from exc
    roots = [root_workspace, *(_canonical_path(value) for value in candidates)]
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return tuple(unique)


def _canonicalize_paths(values: Iterable[Path | str] | object) -> tuple[Path, ...]:
    if isinstance(values, (str, bytes)):
        raise HarnessScopeError("allowed_workspaces must be an iterable of paths")
    try:
        return tuple(_canonical_path(value) for value in values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise HarnessScopeError("allowed_workspaces must be an iterable of paths") from exc


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_permission_mode(value: object) -> PermissionMode:
    if not isinstance(value, PermissionMode):
        raise HarnessScopeError("permission_mode must be a PermissionMode")
    return value


def _validate_approval_callback(value: object) -> ApprovalCallback | None:
    if value is not None and not callable(value):
        raise HarnessScopeError("approval_callback must be callable or None")
    return value


def _validate_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HarnessScopeError("tool_timeout must be a non-negative finite number or None")
    timeout = float(value)
    if timeout < 0 or not math.isfinite(timeout):
        raise HarnessScopeError("tool_timeout must be a non-negative finite number or None")
    return timeout


@dataclass(frozen=True)
class _HarnessConfig:
    workspace_root: Path
    permission_mode: PermissionMode
    approval_callback: ApprovalCallback | None
    tool_timeout: float | None
    tool_timeout_disabled: bool
    metadata: dict[str, Any]
    allowed_workspaces: tuple[Path, ...]
    agent_id: str | None
    parent_agent_id: str | None


@dataclass(frozen=True)
class SessionHarness:
    """The authoritative runtime and scoped execution context for one agent."""

    session_runtime: SessionRuntime
    tool_runtime: ToolRuntime
    runtime_context: RuntimeContext
    agent_id: str | None = None
    parent_agent_id: str | None = None
    allowed_workspaces: tuple[Path, ...] = ()
    _root_workspace: Path | str | None = field(default=None, repr=False)
    _is_child: bool = field(default=False, repr=False)
    _capability_token: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        input_context = self.runtime_context
        if input_context.session_id != self.session_runtime.session_id:
            raise HarnessScopeError(
                "runtime_context.session_id must match session_runtime.session_id"
            )
        if input_context.workspace_root is None:
            raise HarnessScopeError("workspace_root is required")
        if self.allowed_workspaces and self._capability_token is not _CAPABILITY_TOKEN:
            raise HarnessScopeError(
                "allowed_workspaces capabilities may only be created by the factory"
            )
        workspace_root = _canonical_path(input_context.workspace_root)
        root_workspace = _canonical_path(self._root_workspace or workspace_root)
        allowed_workspaces = _canonicalize_allowed_workspaces(
            self.allowed_workspaces, root_workspace
        )
        if not any(_is_within(workspace_root, root) for root in allowed_workspaces):
            raise HarnessScopeError("workspace_root is outside the allowed workspace policy")
        if self._is_child:
            user_metadata = {
                key: value
                for key, value in input_context.metadata.items()
                if key not in _RESERVED_METADATA_KEYS
            }
        else:
            user_metadata = input_context.metadata
        metadata = _copy_metadata(user_metadata)
        metadata.update(
            {
                "session_runtime": self.session_runtime,
                "agent_id": self.agent_id,
                "session_harness": self,
            }
        )
        object.__setattr__(self, "_root_workspace", root_workspace)
        object.__setattr__(self, "allowed_workspaces", allowed_workspaces)
        object.__setattr__(
            self,
            "runtime_context",
            RuntimeContext(
                session_id=self.session_runtime.session_id,
                workspace_root=workspace_root,
                permission_mode=input_context.permission_mode,
                approval_callback=input_context.approval_callback,
                cancellation=input_context.cancellation,
                tool_timeout=input_context.tool_timeout,
                tool_timeout_disabled=input_context.tool_timeout_disabled,
                metadata=metadata,
            ),
        )

    @property
    def session_id(self) -> str:
        return self.session_runtime.session_id

    @property
    def root_session_id(self) -> str:
        return self.session_runtime.session_id

    @property
    def store(self):
        return self.session_runtime.store

    @property
    def agent_scheduler(self):
        from .agents import AgentScheduler

        return AgentScheduler.for_harness(self)

    @property
    def hooks(self):
        from .hooks import HookRuntime

        return HookRuntime(
            None,
            metadata_repository=self.store.metadata,
            root_session_id=self.root_session_id,
        )

    @property
    def effective_cwd(self) -> Path:
        assert self.runtime_context.workspace_root is not None
        return _canonical_path(self.runtime_context.workspace_root)

    def child(
        self,
        agent_id: str,
        *,
        parent_agent_id: str | None = None,
        cwd: Path | str | None = None,
        metadata: Mapping[str, Any] | None = None,
        permission_mode: PermissionMode | object = _UNSET,
        approval_callback: ApprovalCallback | None | object = _UNSET,
        tool_timeout: float | None | object = _UNSET,
    ) -> "SessionHarness":
        if not isinstance(agent_id, str) or not agent_id:
            raise HarnessScopeError("agent_id must be a non-empty string")
        safe_metadata = _copy_metadata({} if metadata is None else metadata)
        inherited_metadata = {
            key: value
            for key, value in self.runtime_context.metadata.items()
            if key not in _RESERVED_METADATA_KEYS
        }
        inherited_metadata = _copy_metadata(inherited_metadata)
        inherited_metadata.update(safe_metadata)
        child_cwd = self.effective_cwd if cwd is None else _canonical_path(cwd)
        if not any(_is_within(child_cwd, root) for root in self.allowed_workspaces):
            raise HarnessScopeError("Child cwd is outside the allowed workspace policy")
        context_overrides: dict[str, Any] = {
            "workspace_root": child_cwd,
            "metadata": inherited_metadata,
        }
        if permission_mode is not _UNSET:
            context_overrides["permission_mode"] = _validate_permission_mode(permission_mode)
        if approval_callback is not _UNSET:
            context_overrides["approval_callback"] = _validate_approval_callback(approval_callback)
        if tool_timeout is not _UNSET:
            if tool_timeout is None:
                context_overrides["tool_timeout"] = None
                context_overrides["tool_timeout_disabled"] = True
            else:
                context_overrides["tool_timeout"] = _validate_timeout(tool_timeout)
                context_overrides["tool_timeout_disabled"] = False

        return SessionHarness(
            session_runtime=self.session_runtime,
            tool_runtime=self.tool_runtime,
            runtime_context=self.runtime_context.child(**context_overrides),
            agent_id=agent_id,
            parent_agent_id=self.agent_id if parent_agent_id is None else parent_agent_id,
            allowed_workspaces=self.allowed_workspaces,
            _root_workspace=self._root_workspace,
            _is_child=True,
            _capability_token=_CAPABILITY_TOKEN,
        )


class SessionHarnessFactory:
    """Builds fresh harness handles over one durable session runtime factory."""

    def __init__(
        self,
        session_runtime_factory: SessionRuntimeFactory,
        tool_registry: Any = None,
        permission_policy: PermissionPolicy | None = None,
        workspace_root: Path | str | None = None,
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
        approval_callback: ApprovalCallback | None = None,
        tool_timeout: float | None = None,
        allowed_workspaces: Iterable[Path | str] = (),
    ) -> None:
        if tool_registry is None:
            from tools import ToolRegistry

            tool_registry = ToolRegistry
        self.session_runtime_factory = session_runtime_factory
        self.tool_registry = tool_registry
        self.permission_policy = permission_policy
        self.workspace_root = (
            _canonical_path(workspace_root) if workspace_root is not None else None
        )
        self.permission_mode = _validate_permission_mode(permission_mode)
        self.approval_callback = _validate_approval_callback(approval_callback)
        self.tool_timeout = None if tool_timeout is None else _validate_timeout(tool_timeout)
        self.allowed_workspaces = _canonicalize_paths(allowed_workspaces)
        self._agent_schedulers: dict[str, Any] = {}

    def create(
        self,
        session_id: str,
        *,
        workspace_root: Path | str | None | object = _UNSET,
        permission_mode: PermissionMode | object = _UNSET,
        approval_callback: ApprovalCallback | None | object = _UNSET,
        tool_timeout: float | None | object = _UNSET,
        metadata: Mapping[str, Any] | object = _UNSET,
        agent_id: str | None | object = _UNSET,
        parent_agent_id: str | None | object = _UNSET,
        allowed_workspaces: Iterable[Path | str] | object = _UNSET,
    ) -> SessionHarness:
        config = self._build_config(
            workspace_root, permission_mode, approval_callback, tool_timeout, metadata,
            agent_id, parent_agent_id, allowed_workspaces,
        )
        return self._compose(self.session_runtime_factory.create(session_id), config)

    def resume(
        self,
        session_id: str,
        *,
        workspace_root: Path | str | None | object = _UNSET,
        permission_mode: PermissionMode | object = _UNSET,
        approval_callback: ApprovalCallback | None | object = _UNSET,
        tool_timeout: float | None | object = _UNSET,
        metadata: Mapping[str, Any] | object = _UNSET,
        agent_id: str | None | object = _UNSET,
        parent_agent_id: str | None | object = _UNSET,
        allowed_workspaces: Iterable[Path | str] | object = _UNSET,
    ) -> SessionHarness:
        config = self._build_config(
            workspace_root, permission_mode, approval_callback, tool_timeout, metadata,
            agent_id, parent_agent_id, allowed_workspaces,
        )
        resumed = self._compose(
            self.session_runtime_factory.resume(session_id), config
        )
        from .agents import AgentScheduler

        scheduler = AgentScheduler.for_harness(resumed)
        self._agent_schedulers[session_id] = scheduler
        scheduler.reconcile()
        return resumed

    def _build_config(
        self,
        workspace_root: object,
        permission_mode: object,
        approval_callback: object,
        tool_timeout: object,
        metadata: object,
        agent_id: object,
        parent_agent_id: object,
        allowed_workspaces: object,
    ) -> _HarnessConfig:
        resolved_workspace = self.workspace_root if workspace_root is _UNSET else workspace_root
        if resolved_workspace is None or resolved_workspace is _UNSET:
            raise HarnessScopeError(
                "workspace_root is required when no factory default workspace is configured"
            )
        root = _canonical_path(resolved_workspace)
        mode = (
            self.permission_mode
            if permission_mode is _UNSET
            else _validate_permission_mode(permission_mode)
        )
        approval = (
            self.approval_callback
            if approval_callback is _UNSET
            else _validate_approval_callback(approval_callback)
        )
        if tool_timeout is _UNSET:
            timeout = self.tool_timeout
            timeout_disabled = False
        elif tool_timeout is None:
            timeout = None
            timeout_disabled = True
        else:
            timeout = _validate_timeout(tool_timeout)
            timeout_disabled = False
        raw_metadata = {} if metadata is _UNSET else metadata
        if not isinstance(raw_metadata, Mapping):
            raise HarnessScopeError("metadata must be a mapping")
        raw_metadata = dict(raw_metadata)
        metadata_allowed = raw_metadata.pop("allowed_workspaces", ())
        safe_metadata = _copy_metadata(raw_metadata)
        configured_allowed = (
            self.allowed_workspaces
            if allowed_workspaces is _UNSET
            else _canonicalize_paths(allowed_workspaces)
        )
        metadata_allowed = _canonicalize_paths(metadata_allowed)
        roots = _canonicalize_allowed_workspaces(
            [*configured_allowed, *metadata_allowed], root
        )
        resolved_agent_id = None if agent_id is _UNSET else agent_id
        resolved_parent_agent_id = None if parent_agent_id is _UNSET else parent_agent_id
        if resolved_agent_id is not None and not isinstance(resolved_agent_id, str):
            raise HarnessScopeError("agent_id must be a string or None")
        if resolved_parent_agent_id is not None and not isinstance(resolved_parent_agent_id, str):
            raise HarnessScopeError("parent_agent_id must be a string or None")
        return _HarnessConfig(
            root,
            mode,
            approval,
            timeout,
            timeout_disabled,
            safe_metadata,
            roots,
            resolved_agent_id,
            resolved_parent_agent_id,
        )

    def _compose(self, runtime: SessionRuntime, config: _HarnessConfig) -> SessionHarness:
        context = RuntimeContext(
            session_id=runtime.session_id,
            workspace_root=config.workspace_root,
            permission_mode=config.permission_mode,
            approval_callback=config.approval_callback,
            tool_timeout=config.tool_timeout,
            tool_timeout_disabled=config.tool_timeout_disabled,
            metadata=config.metadata,
        )
        return SessionHarness(
            session_runtime=runtime,
            tool_runtime=ToolRuntime(
                self.tool_registry,
                permission_policy=self.permission_policy,
                default_timeout=self.tool_timeout if self.tool_timeout is not None else 60.0,
            ),
            runtime_context=context,
            agent_id=config.agent_id,
            parent_agent_id=config.parent_agent_id,
            allowed_workspaces=config.allowed_workspaces,
            _root_workspace=config.workspace_root,
            _capability_token=_CAPABILITY_TOKEN,
        )
