"""Ordered, session-aware tool execution pipeline."""

from __future__ import annotations

import asyncio
import inspect
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from state_core import EventType, SessionRuntime
from tools.base import (
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolResult,
    ToolTimeoutError,
    to_json_value,
)

from .context import RuntimeContext
from .hooks import HookContext, HookDecision
from .permissions import PermissionPolicy


class TerminationReason(str, Enum):
    COMPLETED = "completed"
    PERMISSION_DENIED = "permission_denied"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    TIMEOUT = "timed_out"  # compatibility alias
    BUDGET_EXHAUSTED = "budget_exhausted"
    HOOK_BLOCKED = "hook_blocked"
    MCP_UNAVAILABLE = "mcp_unavailable"
    INTERRUPTED = "interrupted"
    ORPHANED = "orphaned"
    FAILED = "failed"


@dataclass
class ToolExecution:
    tool_name: str
    result: ToolResult
    termination_reason: TerminationReason
    tool_call_id: str | None = None


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class _StateCoreToolPersister:
    def persist(
        self,
        *,
        context: RuntimeContext,
        tool_call_id: str,
        tool_name: str,
        input_data: Mapping[str, Any],
        result: ToolResult,
        termination_reason: TerminationReason,
    ) -> None:
        runtime = context.metadata.get("session_runtime")
        if not isinstance(runtime, SessionRuntime):
            return
        runtime.append_event(
            EventType.TOOL_CALL,
            {
                "toolCallId": tool_call_id,
                "name": tool_name,
                "input": to_json_value(dict(input_data), "tool input"),
            },
        )
        parent_event_id = runtime.state.last_event_id
        runtime.append_event(
            EventType.TOOL_RESULT,
            {
                "toolCallId": tool_call_id,
                "name": tool_name,
                "success": result.success,
                "result": result.data if result.success else str(result.error),
                "terminationReason": termination_reason.value,
            },
            parent_event_id=parent_event_id,
        )


class ToolRuntime:
    """Execute every tool through one deterministic, observable pipeline."""

    def __init__(
        self,
        registry: Any,
        permission_policy: Optional[PermissionPolicy] = None,
        default_timeout: Optional[float] = 60.0,
        *,
        deferred_registry: Any = None,
        hook_runtime: Any = None,
        budget_controller: Any = None,
        result_normalizer: Callable[[Any], Any] = to_json_value,
        persister: Any = None,
    ) -> None:
        self.registry = registry
        self.permission_policy = permission_policy or PermissionPolicy()
        self.default_timeout = default_timeout
        self.deferred_registry = deferred_registry
        self.hook_runtime = hook_runtime
        self.budget_controller = budget_controller
        self.result_normalizer = result_normalizer
        self.persister = persister or _StateCoreToolPersister()

    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        context: RuntimeContext,
        *,
        timeout: Optional[float] = None,
        tool_call_id: str | None = None,
    ) -> ToolExecution:
        call_id = tool_call_id or f"tool_{uuid.uuid4().hex}"
        canonical = self.registry.resolve_name(tool_name) or tool_name
        prepared_input = self._prepare_input(canonical, input_data, context)
        tool = self.registry.get(canonical)
        if tool is None:
            return await self._finish(
                canonical,
                call_id,
                prepared_input,
                context,
                ToolResult.fail(ToolNotFoundError(tool_name)),
                TerminationReason.FAILED,
            )

        deferred = self._service(context, "deferred_tools", self.deferred_registry)
        if deferred is not None:
            try:
                await _maybe_await(
                    deferred.require_active(
                        canonical, agent_id=context.metadata.get("agent_id")
                    )
                )
            except Exception as exc:
                category = getattr(exc, "category", "failed")
                reason = (
                    TerminationReason.MCP_UNAVAILABLE
                    if category == "mcp_unavailable"
                    else TerminationReason.FAILED
                )
                return await self._finish(
                    canonical,
                    call_id,
                    prepared_input,
                    context,
                    ToolResult.fail(ToolExecutionError(str(exc))),
                    reason,
                )

        typed_input, validation_error = await tool.prepare_input(prepared_input)
        if validation_error is not None:
            return await self._finish(
                canonical,
                call_id,
                prepared_input,
                context,
                ToolResult.fail(validation_error),
                TerminationReason.FAILED,
            )

        hooks = self._service(context, "hooks", self.hook_runtime)
        hook_context = self._hook_context(context)
        if hooks is not None:
            try:
                pre = await hooks.run_pre_tool(
                    canonical, prepared_input, hook_context
                )
            except Exception as exc:
                return await self._finish(
                    canonical,
                    call_id,
                    prepared_input,
                    context,
                    ToolResult.fail(
                        ToolExecutionError(f"PreToolUse hook failed: {exc}")
                    ),
                    TerminationReason.HOOK_BLOCKED,
                )
            prepared_input = dict(pre.input)
            if pre.decision is HookDecision.BLOCK:
                return await self._finish(
                    canonical,
                    call_id,
                    prepared_input,
                    context,
                    ToolResult.fail(
                        ToolExecutionError(pre.reason or "tool call blocked by hook")
                    ),
                    TerminationReason.HOOK_BLOCKED,
                )
            typed_input, validation_error = tool.coerce_input(prepared_input)
            if validation_error is not None:
                return await self._finish(
                    canonical,
                    call_id,
                    prepared_input,
                    context,
                    ToolResult.fail(validation_error),
                    TerminationReason.FAILED,
                )

        try:
            allowed, permission_reason = await self.permission_policy.authorize(
                tool, canonical, prepared_input, context
            )
        except Exception as exc:
            return await self._finish(
                canonical,
                call_id,
                prepared_input,
                context,
                ToolResult.fail(
                    ToolExecutionError(f"Permission evaluation failed: {exc}")
                ),
                TerminationReason.FAILED,
            )
        if not allowed:
            return await self._finish(
                canonical,
                call_id,
                prepared_input,
                context,
                ToolResult.fail(
                    ToolPermissionError(f"Permission denied: {permission_reason}")
                ),
                TerminationReason.PERMISSION_DENIED,
            )

        budget = self._service(context, "budget", self.budget_controller)
        reservation = None
        if budget is not None:
            try:
                reservation = await _maybe_await(
                    budget.reserve_tool_call(agent_id=context.metadata.get("agent_id"))
                )
            except Exception as exc:
                return await self._finish(
                    canonical,
                    call_id,
                    prepared_input,
                    context,
                    ToolResult.fail(ToolExecutionError(str(exc))),
                    TerminationReason.BUDGET_EXHAUSTED,
                )

        if context.cancellation.cancelled:
            return await self._finish(
                canonical,
                call_id,
                prepared_input,
                context,
                ToolResult.fail(ToolExecutionError("Operation cancelled")),
                TerminationReason.CANCELLED,
                reservation=reservation,
                executed=False,
            )

        effective_timeout = self._effective_timeout(context, timeout)
        tool_context = self.tool_context(context)
        task = asyncio.create_task(tool.invoke_prepared(typed_input, tool_context))
        context.cancellation.track(task)
        try:
            result = (
                await task
                if effective_timeout is None
                else await asyncio.wait_for(task, effective_timeout)
            )
            reason = (
                TerminationReason.COMPLETED
                if result.success
                else TerminationReason.FAILED
            )
        except asyncio.TimeoutError:
            task.cancel()
            result = ToolResult.fail(ToolTimeoutError(float(effective_timeout)))
            reason = TerminationReason.TIMED_OUT
        except asyncio.CancelledError:
            task.cancel()
            result = ToolResult.fail(ToolExecutionError("Operation cancelled"))
            reason = TerminationReason.CANCELLED
        except Exception as exc:
            error = exc if isinstance(exc, ToolError) else ToolExecutionError(str(exc))
            result = ToolResult.fail(error)
            reason = TerminationReason.FAILED

        result, reason = self._normalize_result(result, reason)
        if hooks is not None:
            envelope = {
                "success": result.success,
                "data": result.data,
                "message": result.message,
                "error": None if result.success else str(result.error),
            }
            try:
                post = await hooks.run_post_tool(
                    canonical,
                    prepared_input,
                    envelope,
                    hook_context,
                    failed=not result.success,
                )
            except Exception:
                post = None
            if post is not None:
                result = self._apply_post_result(result, post.result, post.metadata)

        return await self._finish(
            canonical,
            call_id,
            prepared_input,
            context,
            result,
            reason,
            reservation=reservation,
            executed=True,
        )

    def _normalize_result(
        self, result: ToolResult, reason: TerminationReason
    ) -> tuple[ToolResult, TerminationReason]:
        try:
            if result.success:
                data = self.result_normalizer(result.data)
                metadata = (
                    self.result_normalizer(result.metadata)
                    if result.metadata is not None
                    else None
                )
                return ToolResult.ok(data, result.message, metadata), reason
            self.result_normalizer({"error": str(result.error)})
            return result, reason
        except (TypeError, ValueError) as exc:
            return (
                ToolResult.fail(
                    ToolExecutionError(f"Tool result serialization failed: {exc}")
                ),
                TerminationReason.FAILED,
            )

    @staticmethod
    def _apply_post_result(
        original: ToolResult,
        envelope: Mapping[str, Any],
        hook_metadata: Mapping[str, Any],
    ) -> ToolResult:
        metadata = dict(original.metadata or {})
        metadata.update(hook_metadata)
        if envelope.get("success", original.success):
            return ToolResult.ok(
                envelope.get("data", original.data),
                str(envelope.get("message", original.message)),
                metadata or None,
            )
        return ToolResult.fail(str(envelope.get("error") or original.error))

    async def _finish(
        self,
        tool_name: str,
        tool_call_id: str,
        input_data: Mapping[str, Any],
        context: RuntimeContext,
        result: ToolResult,
        reason: TerminationReason,
        *,
        reservation: Any = None,
        executed: bool = False,
    ) -> ToolExecution:
        if reservation is not None:
            operation = reservation.consume if executed else reservation.release
            try:
                await _maybe_await(operation())
            except Exception:
                pass
        try:
            await _maybe_await(
                self.persister.persist(
                    context=context,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    input_data=dict(input_data),
                    result=result,
                    termination_reason=reason,
                )
            )
        except Exception:
            pass
        return ToolExecution(tool_name, result, reason, tool_call_id)

    @staticmethod
    def _service(context: RuntimeContext, attribute: str, configured: Any) -> Any:
        if configured is not None:
            return configured
        harness = context.metadata.get("session_harness")
        return getattr(harness, attribute, None) if harness is not None else None

    @staticmethod
    def _hook_context(context: RuntimeContext) -> HookContext:
        cwd = context.workspace_root or Path.cwd()
        return HookContext(
            session_id=context.session_id or "",
            cwd=cwd,
            cancellation=context.cancellation,
            agent_id=context.metadata.get("agent_id"),
        )

    def _effective_timeout(
        self, context: RuntimeContext, timeout: float | None
    ) -> float | None:
        if timeout is not None:
            return timeout
        if context.tool_timeout_disabled:
            return None
        if context.tool_timeout is not None:
            return context.tool_timeout
        return self.default_timeout

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
