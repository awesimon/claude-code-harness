from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from tools.base import (
    ToolExecutionError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolResult,
    ToolTimeoutError,
)

from .context import RuntimeContext
from .permissions import PermissionPolicy


class TerminationReason(str, Enum):
    COMPLETED = "completed"
    PERMISSION_DENIED = "permission_denied"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    FAILED = "failed"


@dataclass
class ToolExecution:
    tool_name: str
    result: ToolResult
    termination_reason: TerminationReason


class ToolRuntime:
    def __init__(
        self,
        registry: Any,
        permission_policy: Optional[PermissionPolicy] = None,
        default_timeout: Optional[float] = 60.0,
    ):
        self.registry = registry
        self.permission_policy = permission_policy or PermissionPolicy()
        self.default_timeout = default_timeout

    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        context: RuntimeContext,
        *,
        timeout: Optional[float] = None,
    ) -> ToolExecution:
        canonical = self.registry.resolve_name(tool_name) or tool_name
        input_data = self._prepare_input(canonical, input_data, context)
        tool = self.registry.get(canonical)
        if tool is None:
            error = ToolNotFoundError(tool_name)
            return ToolExecution(tool_name, ToolResult.fail(error), TerminationReason.FAILED)

        allowed, reason = await self.permission_policy.authorize(
            tool, canonical, input_data, context
        )
        if not allowed:
            error = ToolPermissionError(f"Permission denied: {reason}")
            return ToolExecution(
                canonical, ToolResult.fail(error), TerminationReason.PERMISSION_DENIED
            )

        if context.cancellation.cancelled:
            return ToolExecution(
                canonical,
                ToolResult.fail(ToolExecutionError("Operation cancelled")),
                TerminationReason.CANCELLED,
            )

        tool_context = self.tool_context(context)
        task = asyncio.create_task(tool.run(input_data, tool_context))
        context.cancellation.track(task)
        if timeout is not None:
            effective_timeout = timeout
        elif context.tool_timeout_disabled:
            effective_timeout = None
        else:
            effective_timeout = context.tool_timeout
        if effective_timeout is None and not context.tool_timeout_disabled:
            effective_timeout = self.default_timeout

        try:
            if effective_timeout is None:
                result = await task
            else:
                result = await asyncio.wait_for(task, effective_timeout)
            return ToolExecution(
                canonical,
                result,
                TerminationReason.COMPLETED if result.success else TerminationReason.FAILED,
            )
        except asyncio.TimeoutError:
            task.cancel()
            error = ToolTimeoutError(float(effective_timeout))
            return ToolExecution(canonical, ToolResult.fail(error), TerminationReason.TIMEOUT)
        except asyncio.CancelledError:
            task.cancel()
            error = ToolExecutionError("Operation cancelled")
            return ToolExecution(canonical, ToolResult.fail(error), TerminationReason.CANCELLED)
        except Exception as exc:
            error = exc if isinstance(exc, ToolExecutionError) else ToolExecutionError(str(exc))
            return ToolExecution(canonical, ToolResult.fail(error), TerminationReason.FAILED)

    @staticmethod
    def tool_context(context: RuntimeContext) -> dict[str, Any]:
        """Build the authoritative legacy context passed to tools and predicates."""

        tool_context = dict(context.metadata)
        tool_context.update(
            {
                "session_runtime": context.metadata.get("session_runtime"),
                "agent_id": context.metadata.get("agent_id"),
                "session_harness": context.metadata.get("session_harness"),
                "session_id": context.session_id,
                "current_mode": context.permission_mode.value,
                "workspace_root": str(context.workspace_root)
                if context.workspace_root
                else None,
                "runtime_context": context,
                "effective_cwd": str(context.workspace_root.resolve())
                if context.workspace_root
                else None,
                "cancellation": context.cancellation,
                "permission_mode": context.permission_mode,
                "approval_callback": context.approval_callback,
                "tool_timeout": context.tool_timeout,
                "tool_timeout_disabled": context.tool_timeout_disabled,
            }
        )
        return tool_context

    @staticmethod
    def _prepare_input(
        tool_name: str,
        input_data: dict[str, Any],
        context: RuntimeContext,
    ) -> dict[str, Any]:
        prepared = dict(input_data)
        root = context.workspace_root
        if root is None:
            return prepared

        path_keys = {
            "path",
            "file_path",
            "notebook_path",
            "working_dir",
            "working_directory",
            "cwd",
            "directory",
            "root_dir",
        }
        for key, value in tuple(prepared.items()):
            normalized_key = key.lower()
            is_path = (
                normalized_key in path_keys
                or normalized_key.endswith("_path")
                or normalized_key.endswith("_dir")
            )
            if not is_path or not isinstance(value, str) or not value:
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            prepared[key] = str(candidate.resolve())

        if tool_name == "bash" and not prepared.get("working_dir"):
            prepared["working_dir"] = str(root.resolve())
        return prepared
