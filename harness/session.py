"""Session-scoped runtime composition for root and child agent work."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from state_core import SessionRuntime, SessionRuntimeFactory

from .context import ApprovalCallback, PermissionMode, RuntimeContext
from .permissions import PermissionPolicy
from .runtime import ToolRuntime


_UNSET = object()
_ROOT_WORKSPACE_KEY = "_harness_root_workspace"


class HarnessScopeError(ValueError):
    """Raised when a child scope requests a workspace outside its policy."""


def _canonical_path(value: Path | str) -> Path:
    return Path(value).expanduser().resolve()


@dataclass
class SessionHarness:
    """The authoritative runtime and scoped execution context for one agent."""

    session_runtime: SessionRuntime
    tool_runtime: ToolRuntime
    runtime_context: RuntimeContext
    agent_id: str | None = None
    parent_agent_id: str | None = None
    _root_workspace: Path | str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.runtime_context.workspace_root is None:
            raise ValueError("SessionHarness requires a workspace_root")
        workspace_root = _canonical_path(self.runtime_context.workspace_root)
        self.runtime_context.workspace_root = workspace_root
        metadata = dict(self.runtime_context.metadata)
        self._root_workspace = _canonical_path(self._root_workspace or workspace_root)
        metadata[_ROOT_WORKSPACE_KEY] = str(self._root_workspace)
        metadata["session_runtime"] = self.session_runtime
        metadata["agent_id"] = self.agent_id
        metadata["session_harness"] = self
        self.runtime_context.metadata = metadata

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
    def effective_cwd(self) -> Path:
        """Canonical scope-local working directory without mutating process cwd."""
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
        child_cwd = self.effective_cwd if cwd is None else self._validate_child_cwd(cwd)
        context_overrides: dict[str, Any] = {
            "workspace_root": child_cwd,
            "metadata": dict(metadata or {}),
        }
        if permission_mode is not _UNSET:
            context_overrides["permission_mode"] = permission_mode
        if approval_callback is not _UNSET:
            context_overrides["approval_callback"] = approval_callback
        if tool_timeout is not _UNSET:
            context_overrides["tool_timeout"] = tool_timeout

        return SessionHarness(
            session_runtime=self.session_runtime,
            tool_runtime=self.tool_runtime,
            runtime_context=self.runtime_context.child(**context_overrides),
            agent_id=agent_id,
            parent_agent_id=self.agent_id if parent_agent_id is None else parent_agent_id,
            _root_workspace=self._root_workspace,
        )

    def _validate_child_cwd(self, cwd: Path | str) -> Path:
        candidate = _canonical_path(cwd)
        roots = [
            self._root_workspace,
            *(
                _canonical_path(value)
                for value in self.runtime_context.metadata.get("allowed_workspaces", [])
            ),
        ]
        if any(_is_within(candidate, root) for root in roots):
            return candidate
        raise HarnessScopeError(f"Child cwd is outside the allowed workspace policy: {candidate}")


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


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
    ) -> None:
        if tool_registry is None:
            # Late import keeps the harness primitives independent of tool registration imports.
            from tools import ToolRegistry

            tool_registry = ToolRegistry
        self.session_runtime_factory = session_runtime_factory
        self.tool_registry = tool_registry
        self.permission_policy = permission_policy
        self.workspace_root = _canonical_path(workspace_root) if workspace_root is not None else None
        self.permission_mode = permission_mode
        self.approval_callback = approval_callback
        self.tool_timeout = tool_timeout

    def create(self, session_id: str, **overrides: Any) -> SessionHarness:
        self._resolve_workspace_root(overrides)
        return self._compose(self.session_runtime_factory.create(session_id), **overrides)

    def resume(self, session_id: str, **overrides: Any) -> SessionHarness:
        self._resolve_workspace_root(overrides)
        return self._compose(self.session_runtime_factory.resume(session_id), **overrides)

    def _compose(self, runtime: SessionRuntime, **overrides: Any) -> SessionHarness:
        workspace_root = self._resolve_workspace_root(overrides)
        overrides.pop("workspace_root", None)
        permission_mode = overrides.pop("permission_mode", self.permission_mode)
        approval_callback = overrides.pop("approval_callback", self.approval_callback)
        tool_timeout = overrides.pop("tool_timeout", self.tool_timeout)
        agent_id = overrides.pop("agent_id", None)
        parent_agent_id = overrides.pop("parent_agent_id", None)
        metadata = overrides.pop("metadata", {})
        if overrides:
            unexpected = ", ".join(sorted(overrides))
            raise TypeError(f"Unexpected SessionHarnessFactory override(s): {unexpected}")
        if not isinstance(metadata, Mapping):
            raise TypeError("SessionHarnessFactory metadata must be a mapping")
        context = RuntimeContext(
            session_id=runtime.session_id,
            workspace_root=workspace_root,
            permission_mode=permission_mode,
            approval_callback=approval_callback,
            tool_timeout=tool_timeout,
            metadata=dict(metadata),
        )
        return SessionHarness(
            session_runtime=runtime,
            tool_runtime=ToolRuntime(
                self.tool_registry,
                permission_policy=self.permission_policy,
                default_timeout=tool_timeout,
            ),
            runtime_context=context,
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )

    def _resolve_workspace_root(self, overrides: Mapping[str, Any]) -> Path:
        workspace_root = overrides.get("workspace_root", self.workspace_root)
        if workspace_root is None:
            raise HarnessScopeError(
                "workspace_root is required when no factory default workspace is configured"
            )
        return _canonical_path(workspace_root)
