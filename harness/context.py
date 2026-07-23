from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional


class PermissionMode(str, Enum):
    DEFAULT = "default"
    PLAN = "plan"
    BYPASS = "bypass"


class CancellationToken:
    """Cooperative cancellation shared across parent, agent, and tool tasks."""

    def __init__(self, parent: Optional["CancellationToken"] = None):
        self._event = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._callbacks: set[Callable[[], None]] = set()
        if parent is not None:
            parent.add_callback(self.cancel)

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        if self._event.is_set():
            return
        self._event.set()
        for callback in tuple(self._callbacks):
            callback()
        self._callbacks.clear()
        for task in tuple(self._tasks):
            if not task.done():
                task.cancel()

    def add_callback(self, callback: Callable[[], None]) -> None:
        if self.cancelled:
            callback()
            return
        self._callbacks.add(callback)

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
    metadata: dict[str, Any] = field(default_factory=dict)

    def child(self, **overrides: Any) -> "RuntimeContext":
        values = {
            "session_id": self.session_id,
            "workspace_root": self.workspace_root,
            "permission_mode": self.permission_mode,
            "approval_callback": self.approval_callback,
            "tool_timeout": self.tool_timeout,
            "metadata": dict(self.metadata),
        }
        values.update(overrides)
        values["cancellation"] = CancellationToken(parent=self.cancellation)
        return RuntimeContext(**values)
