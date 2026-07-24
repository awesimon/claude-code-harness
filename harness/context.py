from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional


class PermissionMode(str, Enum):
    DEFAULT = "default"
    PLAN = "plan"
    BYPASS = "bypass"
    AUTO = "auto"


class CancellationToken:
    """Cooperative cancellation shared across parent, agent, and tool tasks."""

    def __init__(self, parent: Optional["CancellationToken"] = None):
        self._event = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._next_callback_id = 0
        self._parent = parent
        self._remove_parent_callback: Callable[[], None] | None = None
        if parent is not None:
            self._remove_parent_callback = parent.add_callback(self.cancel, weak=True)

    @property
    def parent(self) -> Optional["CancellationToken"]:
        """The token whose cancellation propagates to this token."""
        return self._parent

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        if self._event.is_set():
            return
        self._event.set()
        self.dispose()
        for callback in tuple(self._callbacks.values()):
            try:
                callback()
            except Exception:
                continue
        self._callbacks.clear()
        for task in tuple(self._tasks):
            if not task.done():
                task.cancel()

    def dispose(self) -> None:
        """Detach this token from its parent without cancelling either token."""
        if self._remove_parent_callback is not None:
            self._remove_parent_callback()
            self._remove_parent_callback = None

    def add_callback(
        self, callback: Callable[[], None], *, weak: bool = False
    ) -> Callable[[], None]:
        if self.cancelled:
            try:
                callback()
            except Exception:
                pass
            return lambda: None

        self._next_callback_id += 1
        callback_id = self._next_callback_id
        stored_callback = callback
        if weak:
            try:
                reference = weakref.WeakMethod(callback)  # type: ignore[arg-type]
            except TypeError:
                reference = None
            if reference is not None:
                def stored_callback() -> None:
                    target = reference()
                    if target is not None:
                        target()

        self._callbacks[callback_id] = stored_callback

        def remove() -> None:
            self._callbacks.pop(callback_id, None)

        return remove

    def track(self, task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        if self.cancelled:
            task.cancel()
        else:
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return task

    async def wait(self) -> None:
        await self._event.wait()


ApprovalCallback = Callable[[Any], Awaitable[bool] | bool]


@dataclass
class RuntimeContext:
    session_id: Optional[str] = None
    workspace_root: Optional[Path] = None
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    approval_callback: Optional[ApprovalCallback] = None
    cancellation: CancellationToken = field(default_factory=CancellationToken)
    tool_timeout: Optional[float] = None
    tool_timeout_disabled: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def child(self, **overrides: Any) -> "RuntimeContext":
        metadata_overrides = overrides.pop("metadata", {})
        if not isinstance(metadata_overrides, Mapping):
            raise TypeError("RuntimeContext.child metadata must be a mapping")
        if "cancellation" in overrides:
            raise TypeError("RuntimeContext.child always derives its cancellation token")
        values = {
            "session_id": self.session_id,
            "workspace_root": self.workspace_root,
            "permission_mode": self.permission_mode,
            "approval_callback": self.approval_callback,
            "tool_timeout": self.tool_timeout,
            "tool_timeout_disabled": self.tool_timeout_disabled,
            "metadata": {**self.metadata, **dict(metadata_overrides)},
        }
        values.update(overrides)
        values["cancellation"] = CancellationToken(parent=self.cancellation)
        return RuntimeContext(**values)
