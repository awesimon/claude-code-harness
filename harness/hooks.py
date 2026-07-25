"""Session-scoped command hook execution and durable hook snapshots."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
import re
import signal
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable, Mapping

from state_core import (
    HookAsyncMode,
    HookDefinitionRecord,
    HookInvocationRecord,
    HookInvocationStatus,
    RuntimeMetadataRepository,
    RuntimeRecordRevisionConflict,
)

from .context import CancellationToken


class HookEvent(str, Enum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    PERMISSION_DENIED = "PermissionDenied"
    NOTIFICATION = "Notification"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    SESSION_START = "SessionStart"
    STOP = "Stop"
    STOP_FAILURE = "StopFailure"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    SESSION_END = "SessionEnd"
    PERMISSION_REQUEST = "PermissionRequest"
    SETUP = "Setup"
    TEAMMATE_IDLE = "TeammateIdle"
    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"
    ELICITATION = "Elicitation"
    ELICITATION_RESULT = "ElicitationResult"
    CONFIG_CHANGE = "ConfigChange"
    INSTRUCTIONS_LOADED = "InstructionsLoaded"
    WORKTREE_CREATE = "WorktreeCreate"
    WORKTREE_REMOVE = "WorktreeRemove"
    CWD_CHANGED = "CwdChanged"
    FILE_CHANGED = "FileChanged"


class HookDecision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"


_FAIL_CLOSED_EVENTS = frozenset(
    {
        HookEvent.PRE_TOOL_USE,
        HookEvent.PERMISSION_REQUEST,
        HookEvent.PRE_COMPACT,
        HookEvent.WORKTREE_CREATE,
        HookEvent.WORKTREE_REMOVE,
    }
)
_ENV_ALLOWLIST = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "LOGNAME",
        "PATH",
        "SHELL",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USER",
        "WINDIR",
    }
)
_HOOK_DEPTH: ContextVar[int] = ContextVar("hook_execution_depth", default=0)
_ACTIVE_HOOK_EVENTS: ContextVar[frozenset[str]] = ContextVar(
    "active_hook_events", default=frozenset()
)
_CONFIG_NAMESPACE = "hooks.config"
_EVENT_NAMESPACE = "hooks.events"


@dataclass(frozen=True)
class HookDefinition:
    hook_id: str
    event: HookEvent
    command: str
    matcher: str | None = None
    timeout: float = 600.0
    output_limit: int = 64 * 1024
    fail_closed: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event, HookEvent):
            object.__setattr__(self, "event", HookEvent(self.event))
        if not isinstance(self.hook_id, str) or not self.hook_id.strip():
            raise ValueError("hook_id must be a non-empty string")
        if not isinstance(self.command, str) or not self.command.strip():
            raise ValueError("command must be a non-empty string")
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or not math.isfinite(float(self.timeout))
            or float(self.timeout) <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        object.__setattr__(self, "timeout", float(self.timeout))
        if isinstance(self.output_limit, bool) or self.output_limit <= 0:
            raise ValueError("output_limit must be a positive integer")
        if self.matcher is not None:
            if not isinstance(self.matcher, str) or not self.matcher:
                raise ValueError("matcher must be a non-empty regular expression")
            re.compile(self.matcher)
        if self.fail_closed is None:
            object.__setattr__(self, "fail_closed", self.event in _FAIL_CLOSED_EVENTS)

    def matches(self, value: str | None) -> bool:
        return self.matcher is None or (
            value is not None and re.search(self.matcher, value) is not None
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "hook_id": self.hook_id,
            "event": self.event.value,
            "command": self.command,
            "matcher": self.matcher,
            "timeout": self.timeout,
            "output_limit": self.output_limit,
            "fail_closed": self.fail_closed,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "HookDefinition":
        return cls(
            hook_id=value["hook_id"],
            event=HookEvent(value["event"]),
            command=value["command"],
            matcher=value.get("matcher"),
            timeout=value.get("timeout", 600.0),
            output_limit=value.get("output_limit", 64 * 1024),
            fail_closed=value.get("fail_closed"),
        )


@dataclass(frozen=True)
class HookContext:
    session_id: str
    cwd: Path
    cancellation: CancellationToken
    agent_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "cwd", Path(self.cwd).expanduser().resolve())


@dataclass(frozen=True)
class HookFailure:
    hook_id: str
    category: str
    message: str
    exit_code: int | None = None


@dataclass(frozen=True)
class PreHookResult:
    decision: HookDecision
    input: dict[str, Any]
    reason: str | None = None
    failures: tuple[HookFailure, ...] = ()
    executed_hook_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PostHookResult:
    result: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    failures: tuple[HookFailure, ...] = ()
    executed_hook_ids: tuple[str, ...] = ()


class _HookCommandFailure(Exception):
    def __init__(self, category: str, message: str, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.exit_code = exit_code


async def _read_limited(stream: asyncio.StreamReader, limit: int, consumed: list[int]) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await stream.read(min(8192, limit + 1))
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        consumed[0] += len(chunk)
        if size > limit or consumed[0] > limit:
            raise _HookCommandFailure("output_limit", "hook output exceeded configured limit")
        chunks.append(chunk)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), 0.2)
        return
    except asyncio.TimeoutError:
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        return
    await process.wait()


def _parse_single_object(stdout: bytes) -> dict[str, Any]:
    try:
        text = stdout.decode("utf-8")
        stripped = text.lstrip()
        value, offset = json.JSONDecoder().raw_decode(stripped)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _HookCommandFailure(
            "malformed_output", "hook stdout must contain exactly one JSON object"
        ) from exc
    if stripped[offset:].strip() or not isinstance(value, dict):
        raise _HookCommandFailure(
            "malformed_output", "hook stdout must contain exactly one JSON object"
        )
    return value


class HookRuntime:
    """Resolve and execute one durable hook configuration snapshot."""

    def __init__(
        self,
        definitions: Iterable[HookDefinition] | None = None,
        *,
        metadata_repository: RuntimeMetadataRepository | None = None,
        root_session_id: str | None = None,
        max_events: int = 256,
    ) -> None:
        if (metadata_repository is None) != (root_session_id is None):
            raise ValueError("metadata_repository and root_session_id must be provided together")
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self._metadata = metadata_repository
        self._root_session_id = root_session_id
        self._max_events = max_events
        if definitions is None:
            self._definitions = self._load_definitions()
        else:
            self._definitions = tuple(definitions)
            self._persist_definitions(self._definitions)

    @asynccontextmanager
    async def execution_guard(self) -> AsyncIterator[None]:
        token = _HOOK_DEPTH.set(_HOOK_DEPTH.get() + 1)
        try:
            yield
        finally:
            _HOOK_DEPTH.reset(token)

    def list(self) -> tuple[HookDefinition, ...]:
        return self._definitions

    def add(
        self,
        event: HookEvent | str,
        command: str,
        *,
        matcher: str | None = None,
        timeout: float = 600.0,
        output_limit: int = 64 * 1024,
        fail_closed: bool | None = None,
    ) -> HookDefinition:
        hook = HookDefinition(
            hook_id=uuid.uuid4().hex,
            event=HookEvent(event),
            command=command,
            matcher=matcher,
            timeout=timeout,
            output_limit=output_limit,
            fail_closed=fail_closed,
        )
        self._definitions = (*self._definitions, hook)
        self._persist_definitions(self._definitions)
        return hook

    def register(self, hook: HookDefinition) -> HookDefinition:
        """Register a definition once by stable ID and persist the new snapshot."""

        existing = next((item for item in self._definitions if item.hook_id == hook.hook_id), None)
        if existing is not None:
            if existing != hook:
                raise ValueError(f"hook_id {hook.hook_id!r} is already registered")
            return existing
        self._definitions = (*self._definitions, hook)
        self._persist_definitions(self._definitions)
        return hook

    def remove(self, index: int) -> HookDefinition:
        if isinstance(index, bool) or not 0 <= index < len(self._definitions):
            raise IndexError("hook index is out of range")
        removed = self._definitions[index]
        self._definitions = self._definitions[:index] + self._definitions[index + 1 :]
        self._persist_definitions(self._definitions)
        return removed

    def events(self) -> tuple[dict[str, Any], ...]:
        snapshot = self._metadata_snapshot(_EVENT_NAMESPACE)
        events = snapshot.get("events", [])
        return tuple(dict(event) for event in events if isinstance(event, Mapping))

    async def run_pre_tool(
        self,
        tool_name: str,
        tool_input: Mapping[str, Any],
        context: HookContext,
    ) -> PreHookResult:
        current_input = dict(tool_input)
        if _HOOK_DEPTH.get() > 0:
            return PreHookResult(HookDecision.ALLOW, current_input)
        failures: list[HookFailure] = []
        executed: list[str] = []
        for hook in self._matching(HookEvent.PRE_TOOL_USE, tool_name):
            executed.append(hook.hook_id)
            payload = self._payload(
                hook.event,
                context,
                tool_name=tool_name,
                tool_input=current_input,
            )
            output, failure = await self._run_one(hook, payload, context)
            if failure is not None:
                failures.append(failure)
                if hook.fail_closed:
                    return PreHookResult(
                        HookDecision.BLOCK,
                        current_input,
                        reason=failure.message,
                        failures=tuple(failures),
                        executed_hook_ids=tuple(executed),
                    )
                continue
            assert output is not None
            try:
                decision = HookDecision(output.get("decision", "allow"))
                updated = output.get("updated_input")
                if updated is not None:
                    if not isinstance(updated, dict):
                        raise ValueError("updated_input must be a JSON object")
                    current_input = dict(updated)
            except (TypeError, ValueError) as exc:
                failure = HookFailure(hook.hook_id, "malformed_output", str(exc))
                failures.append(failure)
                self._append_event(hook, "failure", failure.category)
                if hook.fail_closed:
                    return PreHookResult(
                        HookDecision.BLOCK,
                        current_input,
                        reason=failure.message,
                        failures=tuple(failures),
                        executed_hook_ids=tuple(executed),
                    )
                continue
            if decision is HookDecision.BLOCK:
                return PreHookResult(
                    decision,
                    current_input,
                    reason=str(output.get("reason") or "blocked by hook"),
                    failures=tuple(failures),
                    executed_hook_ids=tuple(executed),
                )
        return PreHookResult(
            HookDecision.ALLOW,
            current_input,
            failures=tuple(failures),
            executed_hook_ids=tuple(executed),
        )

    async def run_post_tool(
        self,
        tool_name: str,
        tool_input: Mapping[str, Any],
        result: Mapping[str, Any],
        context: HookContext,
        *,
        failed: bool = False,
    ) -> PostHookResult:
        current_result = dict(result)
        if _HOOK_DEPTH.get() > 0:
            return PostHookResult(current_result)
        failures: list[HookFailure] = []
        executed: list[str] = []
        metadata: dict[str, Any] = {}
        event = HookEvent.POST_TOOL_USE_FAILURE if failed else HookEvent.POST_TOOL_USE
        for hook in self._matching(event, tool_name):
            executed.append(hook.hook_id)
            payload = self._payload(
                event,
                context,
                tool_name=tool_name,
                tool_input=dict(tool_input),
                tool_result=current_result,
            )
            output, failure = await self._run_one(hook, payload, context)
            if failure is not None:
                failures.append(failure)
                continue
            assert output is not None
            attached = output.get("metadata")
            if isinstance(attached, dict):
                metadata.update(attached)
            updated = output.get("updated_result")
            if isinstance(updated, dict):
                current_result = dict(updated)
        return PostHookResult(
            current_result,
            metadata,
            tuple(failures),
            tuple(executed),
        )

    async def run_post_tool_failure(
        self,
        tool_name: str,
        tool_input: Mapping[str, Any],
        result: Mapping[str, Any],
        context: HookContext,
    ) -> PostHookResult:
        return await self.run_post_tool(tool_name, tool_input, result, context, failed=True)

    async def run_pre_compact(
        self,
        details: Mapping[str, Any],
        context: HookContext,
    ) -> PreHookResult:
        current = dict(details)
        if _HOOK_DEPTH.get() > 0:
            return PreHookResult(HookDecision.ALLOW, current)
        failures: list[HookFailure] = []
        executed: list[str] = []
        matcher = str(current.get("trigger") or "auto")
        for hook in self._matching(HookEvent.PRE_COMPACT, matcher):
            executed.append(hook.hook_id)
            output, failure = await self._run_one(
                hook, self._payload(hook.event, context, **current), context
            )
            if failure is not None:
                failures.append(failure)
                if hook.fail_closed:
                    return PreHookResult(
                        HookDecision.BLOCK,
                        current,
                        reason=failure.message,
                        failures=tuple(failures),
                        executed_hook_ids=tuple(executed),
                    )
                continue
            assert output is not None
            try:
                decision = HookDecision(output.get("decision", "allow"))
            except (TypeError, ValueError) as exc:
                failure = HookFailure(hook.hook_id, "malformed_output", str(exc))
                failures.append(failure)
                self._append_event(hook, "failure", failure.category)
                if hook.fail_closed:
                    return PreHookResult(
                        HookDecision.BLOCK,
                        current,
                        reason=failure.message,
                        failures=tuple(failures),
                        executed_hook_ids=tuple(executed),
                    )
                continue
            if decision is HookDecision.BLOCK:
                return PreHookResult(
                    decision,
                    current,
                    reason=str(output.get("reason") or "blocked by hook"),
                    failures=tuple(failures),
                    executed_hook_ids=tuple(executed),
                )
        return PreHookResult(
            HookDecision.ALLOW,
            current,
            failures=tuple(failures),
            executed_hook_ids=tuple(executed),
        )

    async def run_post_compact(
        self,
        details: Mapping[str, Any],
        context: HookContext,
    ) -> PostHookResult:
        current = dict(details)
        if _HOOK_DEPTH.get() > 0:
            return PostHookResult(current)
        failures: list[HookFailure] = []
        executed: list[str] = []
        metadata: dict[str, Any] = {}
        matcher = str(current.get("trigger") or "auto")
        for hook in self._matching(HookEvent.POST_COMPACT, matcher):
            executed.append(hook.hook_id)
            output, failure = await self._run_one(
                hook, self._payload(hook.event, context, **current), context
            )
            if failure is not None:
                failures.append(failure)
                continue
            assert output is not None
            attached = output.get("metadata")
            if isinstance(attached, dict):
                metadata.update(attached)
        return PostHookResult(
            current,
            metadata,
            tuple(failures),
            tuple(executed),
        )

    def _matching(self, event: HookEvent, matcher_value: str | None) -> tuple[HookDefinition, ...]:
        return tuple(
            hook
            for hook in self._definitions
            if hook.event is event and hook.matches(matcher_value)
        )

    @staticmethod
    def _payload(
        event: HookEvent,
        context: HookContext,
        **values: Any,
    ) -> dict[str, Any]:
        return {
            "hook_event_name": event.value,
            "session_id": context.session_id,
            "agent_id": context.agent_id,
            "cwd": str(context.cwd),
            **values,
        }

    async def _run_one(
        self,
        hook: HookDefinition,
        payload: Mapping[str, Any],
        context: HookContext,
    ) -> tuple[dict[str, Any] | None, HookFailure | None]:
        started = time.monotonic()
        try:
            async with self.execution_guard():
                output = await self._run_command(hook, payload, context)
        except _HookCommandFailure as exc:
            failure = HookFailure(hook.hook_id, exc.category, str(exc), exc.exit_code)
            self._append_event(
                hook,
                "failure",
                exc.category,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return None, failure
        self._append_event(
            hook,
            "success",
            str(output.get("decision", "observed")),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return output, None

    async def _run_command(
        self,
        hook: HookDefinition,
        payload: Mapping[str, Any],
        context: HookContext,
    ) -> dict[str, Any]:
        environment = {key: value for key, value in os.environ.items() if key in _ENV_ALLOWLIST}
        try:
            process = await asyncio.create_subprocess_shell(
                hook.command,
                cwd=str(context.cwd),
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            raise _HookCommandFailure("spawn_failed", "hook command could not be started") from exc
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        consumed = [0]
        stdout_task = asyncio.create_task(
            _read_limited(process.stdout, hook.output_limit, consumed)
        )
        stderr_task = asyncio.create_task(
            _read_limited(process.stderr, hook.output_limit, consumed)
        )
        process_task = asyncio.create_task(process.wait())
        cancel_task = asyncio.create_task(context.cancellation.wait())
        try:
            process.stdin.write(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            await process.stdin.drain()
            process.stdin.close()
            deadline = asyncio.get_running_loop().time() + hook.timeout
            watched = {stdout_task, stderr_task, process_task, cancel_task}
            while not {stdout_task, stderr_task, process_task}.issubset(
                {task for task in watched if task.done()}
            ):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise _HookCommandFailure("timed_out", "hook command timed out")
                done, _ = await asyncio.wait(
                    watched, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
                if not done:
                    raise _HookCommandFailure("timed_out", "hook command timed out")
                if cancel_task in done:
                    raise _HookCommandFailure("cancelled", "hook command was cancelled")
                for task in (stdout_task, stderr_task):
                    if task in done:
                        failure = task.exception()
                        if isinstance(failure, _HookCommandFailure):
                            raise failure
                        if failure is not None:
                            raise _HookCommandFailure("io_failed", "hook output could not be read")
            stdout = stdout_task.result()
            stderr = stderr_task.result()
            exit_code = process_task.result()
            if exit_code != 0:
                message = stderr.decode("utf-8", errors="replace")[:500].strip()
                raise _HookCommandFailure(
                    "nonzero_exit",
                    message or f"hook command exited with status {exit_code}",
                    exit_code,
                )
            return _parse_single_object(stdout)
        finally:
            if process.returncode is None:
                await _terminate_process(process)
            for task in (stdout_task, stderr_task, process_task, cancel_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                stdout_task,
                stderr_task,
                process_task,
                cancel_task,
                return_exceptions=True,
            )

    def _load_definitions(self) -> tuple[HookDefinition, ...]:
        snapshot = self._metadata_snapshot(_CONFIG_NAMESPACE)
        hooks = snapshot.get("hooks", [])
        return tuple(HookDefinition.from_json(item) for item in hooks if isinstance(item, Mapping))

    def _persist_definitions(self, definitions: Iterable[HookDefinition]) -> None:
        if self._metadata is None:
            return
        value = [hook.to_json() for hook in definitions]
        self._mutate_metadata(_CONFIG_NAMESPACE, lambda _snapshot: {"hooks": value})

    def _append_event(
        self,
        hook: HookDefinition,
        outcome: str,
        detail: str,
        *,
        duration_ms: int = 0,
    ) -> None:
        if self._metadata is None:
            return
        event = {
            "hook_id": hook.hook_id,
            "event": hook.event.value,
            "outcome": outcome,
            "detail": detail[:200],
            "duration_ms": max(0, duration_ms),
            "timestamp_ms": int(time.time() * 1000),
        }

        def append(snapshot: dict[str, Any]) -> dict[str, Any]:
            current = snapshot.get("events", [])
            events = [dict(item) for item in current if isinstance(item, Mapping)]
            events.append(event)
            return {"events": events[-self._max_events :]}

        self._mutate_metadata(_EVENT_NAMESPACE, append)

    def _metadata_snapshot(self, namespace: str) -> dict[str, Any]:
        if self._metadata is None or self._root_session_id is None:
            return {}
        record = self._metadata.get(self._root_session_id, namespace)
        return dict(record.snapshot) if record is not None else {}

    def _mutate_metadata(
        self,
        namespace: str,
        mutation: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        assert self._metadata is not None
        assert self._root_session_id is not None
        for _ in range(16):
            current = self._metadata.get(self._root_session_id, namespace)
            snapshot = dict(current.snapshot) if current is not None else {}
            expected_revision = current.revision if current is not None else None
            try:
                self._metadata.put(
                    self._root_session_id,
                    namespace,
                    mutation(snapshot),
                    expected_revision,
                )
                return
            except RuntimeRecordRevisionConflict:
                continue
        raise RuntimeRecordRevisionConflict(
            "metadata", f"{self._root_session_id}:{namespace}", None, None
        )


@dataclass(frozen=True)
class HookDispatchResult:
    """Stable, runner-neutral effects produced by one lifecycle event."""

    decision: HookDecision = HookDecision.ALLOW
    input_patch: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    permission_decision: str | None = None
    permission_updates: tuple[Mapping[str, Any], ...] = ()
    reason: str | None = None
    failures: tuple[HookFailure, ...] = ()
    executed_hook_ids: tuple[str, ...] = ()
    async_invocation_ids: tuple[str, ...] = ()
    attempt_limit_reached: bool = False

    @property
    def blocked(self) -> bool:
        return self.decision is HookDecision.BLOCK


class _RunnerFailure(Exception):
    def __init__(self, category: str, message: str, *, exit_code: int | None = None):
        super().__init__(message)
        self.category = category
        self.exit_code = exit_code


Runner = Callable[
    [Mapping[str, Any], Mapping[str, Any], CancellationToken],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]


@dataclass(frozen=True)
class RunnerContext:
    """Authority and execution limits supplied to an agent hook by the dispatcher."""

    root_session_id: str
    agent_id: str | None
    cancellation: CancellationToken
    allowed_tools: tuple[str, ...]
    permission_mode: str
    budget: Mapping[str, Any]
    recursion_depth: int
    recursion_limit: int
    active_events: frozenset[str]
    restricted: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools))
        object.__setattr__(self, "budget", MappingProxyType(dict(self.budget)))


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


class HookDispatcher:
    """Execute versioned durable hooks and aggregate effects deterministically."""

    _FEEDBACK_EVENTS = frozenset({"Stop", "TaskCompleted", "TeammateIdle"})

    def __init__(
        self,
        definition_repository: Any,
        invocation_repository: Any,
        *,
        root_session_id: str,
        owner_token: str,
        prompt_runner: Runner | None = None,
        http_runner: Runner | None = None,
        agent_runner: Runner | None = None,
        notification_sink: Callable[[dict[str, Any]], Any] | None = None,
        feedback_attempt_limit: int = 3,
        output_limit: int = 64 * 1024,
        recursion_limit: int = 4,
        budget_consumer: Callable[[HookDefinitionRecord], Any] | None = None,
        agent_tool_allowlist: Iterable[str] = (),
        agent_permission_mode: str = "default",
        agent_budget: Mapping[str, Any] | None = None,
        hook_transaction_service: Any = None,
    ) -> None:
        if feedback_attempt_limit <= 0 or output_limit <= 0 or recursion_limit <= 0:
            raise ValueError("hook limits must be positive")
        self._definitions = definition_repository
        self._invocations = invocation_repository
        self._root_session_id = root_session_id
        self._owner_token = owner_token
        self._prompt_runner = prompt_runner
        self._http_runner = http_runner
        self._agent_runner = agent_runner
        self._notification_sink = notification_sink
        self._feedback_attempt_limit = feedback_attempt_limit
        self._output_limit = output_limit
        self._recursion_limit = recursion_limit
        self._budget_consumer = budget_consumer
        self._agent_tool_allowlist = tuple(dict.fromkeys(agent_tool_allowlist))
        self._agent_permission_mode = agent_permission_mode
        self._agent_budget = MappingProxyType(dict(agent_budget or {}))
        if hook_transaction_service is None and hasattr(invocation_repository, "_session_factory"):
            from state_core.outbox import SQLAlchemyHookTransactionService

            hook_transaction_service = SQLAlchemyHookTransactionService(
                invocation_repository._session_factory
            )
        self._hook_transactions = hook_transaction_service
        self._background: set[asyncio.Task[Any]] = set()

    async def dispatch(
        self,
        envelope: Mapping[str, Any],
        cancellation: CancellationToken | None = None,
    ) -> HookDispatchResult:
        event = str(envelope.get("hook_event_name") or envelope.get("event") or "")
        correlation_id = str(envelope.get("correlation_id") or "")
        if not event or not correlation_id:
            raise ValueError("hook envelopes require hook_event_name and correlation_id")
        if event in _ACTIVE_HOOK_EVENTS.get():
            return HookDispatchResult()
        if str(envelope.get("root_session_id") or self._root_session_id) != self._root_session_id:
            raise ValueError("hook envelope belongs to another root session")
        attempt = int(envelope.get("feedback_attempt", 1))
        if event in self._FEEDBACK_EVENTS and attempt > self._feedback_attempt_limit:
            return HookDispatchResult(
                decision=HookDecision.BLOCK,
                reason="hook feedback attempt limit reached",
                attempt_limit_reached=True,
            )
        token = cancellation or CancellationToken()
        definitions = sorted(
            (
                definition
                for definition in self._definitions.list(self._root_session_id)
                if definition.enabled
                and definition.event == event
                and self._matches(definition.matcher, envelope)
            ),
            key=lambda item: (item.order, item.definition_id),
        )
        scheduled: list[tuple[HookDefinitionRecord, asyncio.Task[Any] | None, str | None]] = []
        for definition in definitions:
            invocation, claimed = self._claim(definition, envelope, attempt)
            if not claimed or invocation is None:
                continue
            if definition.async_mode is not HookAsyncMode.SYNC:
                task = asyncio.create_task(
                    self._complete_async_invocation(
                        definition,
                        invocation,
                        envelope,
                        token,
                        rewake=definition.async_mode is HookAsyncMode.ASYNC_REWAKE,
                    )
                )
                self._background.add(task)
                task.add_done_callback(self._background.discard)
                scheduled.append((definition, None, invocation.invocation_id))
            else:
                scheduled.append(
                    (
                        definition,
                        asyncio.create_task(
                            self._complete_invocation(definition, invocation, envelope, token)
                        ),
                        None,
                    )
                )

        sync_tasks = [item[1] for item in scheduled if item[1] is not None]
        raw = await asyncio.gather(*sync_tasks) if sync_tasks else []
        outcomes = iter(raw)
        ordered: list[tuple[HookDefinitionRecord, Mapping[str, Any] | None, HookFailure | None]] = (
            []
        )
        for definition, task, _ in scheduled:
            if task is None:
                continue
            ordered.append((definition, *next(outcomes)))
        return self._aggregate(
            ordered,
            tuple(item.definition_id for item, _, _ in scheduled),
            tuple(invocation_id for _, _, invocation_id in scheduled if invocation_id),
        )

    async def drain(self) -> None:
        while self._background:
            await asyncio.gather(*tuple(self._background), return_exceptions=True)

    def reconcile(self, *, live_owner_tokens: set[str] | None = None) -> list[Any]:
        return self._invocations.interrupt_open(
            self._root_session_id,
            live_owner_tokens=live_owner_tokens or set(),
            now=datetime.now(timezone.utc),
        )

    @staticmethod
    def _matches(matcher: str | None, envelope: Mapping[str, Any]) -> bool:
        if matcher is None:
            return True
        candidate = (
            envelope.get("tool_name")
            or envelope.get("task_id")
            or envelope.get("mcp_server_id")
            or envelope.get("payload", {}).get("trigger")
            if isinstance(envelope.get("payload", {}), Mapping)
            else None
        )
        return candidate is not None and re.search(matcher, str(candidate)) is not None

    def _claim(
        self,
        definition: HookDefinitionRecord,
        envelope: Mapping[str, Any],
        attempt: int,
    ) -> tuple[HookInvocationRecord | None, bool]:
        correlation = str(envelope["correlation_id"])
        if definition.once:
            key = (
                f"hook-once:{self._root_session_id}:{definition.definition_id}:"
                f"{definition.config_revision}"
            )
        else:
            suffix = f":{attempt}" if definition.event in self._FEEDBACK_EVENTS else ""
            key = (
                f"hook:{self._root_session_id}:{definition.definition_id}:"
                f"{definition.config_revision}:{correlation}{suffix}"
            )
        if self._invocations.get_by_idempotency_key(key) is not None:
            return None, False
        invocation = HookInvocationRecord(
            invocation_id=f"hook_{uuid.uuid4().hex}",
            root_session_id=self._root_session_id,
            definition_id=definition.definition_id,
            definition_revision=definition.config_revision,
            event=definition.event,
            event_envelope=dict(envelope),
            correlation_id=correlation,
            idempotency_key=key,
            agent_id=envelope.get("agent_id"),
            attempt=attempt,
            deadline_at=datetime.now(timezone.utc) + timedelta(milliseconds=definition.timeout_ms),
        )
        try:
            return self._invocations.create(invocation), True
        except RuntimeRecordRevisionConflict:
            return None, False

    async def _complete_invocation(
        self,
        definition: HookDefinitionRecord,
        invocation: HookInvocationRecord,
        envelope: Mapping[str, Any],
        cancellation: CancellationToken,
        *,
        rewake: bool = False,
    ) -> tuple[Mapping[str, Any] | None, HookFailure | None]:
        try:
            running = self._invocations.transition(
                invocation.invocation_id,
                HookInvocationStatus.RUNNING,
                invocation.revision,
                lease_owner=self._owner_token,
                lease_expires_at=invocation.deadline_at,
            )
        except RuntimeRecordRevisionConflict:
            return None, HookFailure(
                definition.definition_id, "interrupted", "invocation claim lost"
            )
        try:
            output = await self._run_with_limits(definition, envelope, cancellation)
            blocked = (
                output.get("decision") in {"block", "deny"}
                or output.get("continue") is False
                or output.get("permission_decision") == "deny"
            )
            terminal = HookInvocationStatus.BLOCKED if blocked else HookInvocationStatus.SUCCEEDED
            self._complete_terminal(
                running,
                terminal,
                rewake=rewake,
                outcome=dict(output),
            )
            return output, None
        except asyncio.CancelledError:
            try:
                self._complete_terminal(
                    running,
                    HookInvocationStatus.CANCELLED,
                    rewake=rewake,
                    error={"category": "cancelled", "message": "hook dispatch cancelled"},
                )
            except RuntimeRecordRevisionConflict:
                pass
            raise
        except _RunnerFailure as exc:
            status = {
                "timed_out": HookInvocationStatus.TIMED_OUT,
                "cancelled": HookInvocationStatus.CANCELLED,
                "blocking": HookInvocationStatus.BLOCKED,
            }.get(exc.category, HookInvocationStatus.FAILED)
            try:
                self._complete_terminal(
                    running,
                    status,
                    rewake=rewake,
                    error={"category": exc.category, "message": str(exc)},
                )
            except RuntimeRecordRevisionConflict:
                pass
            return None, HookFailure(
                definition.definition_id, exc.category, str(exc), exc.exit_code
            )

    async def _complete_async_invocation(
        self,
        definition: HookDefinitionRecord,
        invocation: HookInvocationRecord,
        envelope: Mapping[str, Any],
        cancellation: CancellationToken,
        *,
        rewake: bool,
    ) -> tuple[Mapping[str, Any] | None, HookFailure | None]:
        result = await self._complete_invocation(
            definition,
            invocation,
            envelope,
            cancellation,
            rewake=rewake,
        )
        if rewake:
            await self._notify_rewake(invocation.invocation_id)
        return result

    def _complete_terminal(
        self,
        running: HookInvocationRecord,
        status: HookInvocationStatus,
        *,
        rewake: bool,
        outcome: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> HookInvocationRecord:
        if rewake:
            if self._hook_transactions is None:
                raise RuntimeError("asyncRewake hooks require a durable hook transaction service")
            return self._hook_transactions.complete_with_rewake(
                running.invocation_id,
                status,
                running.revision,
                outcome=outcome,
                error=error,
            )
        return self._invocations.transition(
            running.invocation_id,
            status,
            running.revision,
            lease_owner=None,
            lease_expires_at=None,
            outcome=outcome,
            error=error,
        )

    async def _run_with_limits(
        self,
        definition: HookDefinitionRecord,
        envelope: Mapping[str, Any],
        cancellation: CancellationToken,
    ) -> Mapping[str, Any]:
        if _HOOK_DEPTH.get() >= self._recursion_limit:
            raise _RunnerFailure("recursion_guard", "hook recursion limit reached")
        if self._budget_consumer is not None:
            allowed = self._budget_consumer(definition)
            allowed = await allowed if inspect.isawaitable(allowed) else allowed
            if allowed is False:
                raise _RunnerFailure("budget_exhausted", "hook budget exhausted")
        depth_token = _HOOK_DEPTH.set(_HOOK_DEPTH.get() + 1)
        event = str(envelope.get("hook_event_name") or envelope.get("event"))
        events_token = _ACTIVE_HOOK_EVENTS.set(_ACTIVE_HOOK_EVENTS.get() | {event})
        runner_task = asyncio.create_task(self._run_runner(definition, envelope, cancellation))
        cancel_task = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {runner_task, cancel_task},
                timeout=definition.timeout_ms / 1000,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done:
                runner_task.cancel()
                await asyncio.gather(runner_task, return_exceptions=True)
                raise _RunnerFailure("cancelled", "hook execution cancelled")
            if runner_task not in done:
                runner_task.cancel()
                await asyncio.gather(runner_task, return_exceptions=True)
                raise _RunnerFailure("timed_out", "hook execution timed out")
            try:
                result = await runner_task
            except _RunnerFailure:
                raise
            except asyncio.CancelledError:
                raise _RunnerFailure("cancelled", "hook execution cancelled") from None
            except Exception as exc:
                raise _RunnerFailure("runner_failed", str(exc)) from exc
            if not isinstance(result, Mapping):
                raise _RunnerFailure("malformed_output", "hook runner must return a JSON object")
            result = self._normalize_output(_plain_json(result))
            encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
            limit = int(definition.runner_config.get("output_limit", self._output_limit))
            if len(encoded) > limit:
                raise _RunnerFailure("output_limit", "hook output exceeded configured limit")
            return result
        except asyncio.CancelledError:
            runner_task.cancel()
            await asyncio.gather(runner_task, return_exceptions=True)
            raise
        finally:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
            _ACTIVE_HOOK_EVENTS.reset(events_token)
            _HOOK_DEPTH.reset(depth_token)

    async def _run_runner(
        self,
        definition: HookDefinitionRecord,
        envelope: Mapping[str, Any],
        cancellation: CancellationToken,
    ) -> Mapping[str, Any]:
        kind = definition.runner_kind
        config = definition.runner_config
        if kind == "command":
            return await self._run_command(envelope, config)
        runner = {
            "prompt": self._prompt_runner,
            "http": self._http_runner,
            "agent": self._agent_runner,
        }.get(kind)
        if runner is None:
            raise _RunnerFailure("runner_unavailable", f"{kind} hook runner is not configured")
        runner_scope: CancellationToken | RunnerContext = cancellation
        if kind == "agent":
            runner_scope = RunnerContext(
                root_session_id=self._root_session_id,
                agent_id=envelope.get("agent_id"),
                cancellation=cancellation,
                allowed_tools=self._agent_tool_allowlist,
                permission_mode=self._agent_permission_mode,
                budget=self._agent_budget,
                recursion_depth=_HOOK_DEPTH.get(),
                recursion_limit=self._recursion_limit,
                active_events=_ACTIVE_HOOK_EVENTS.get(),
            )
        value = runner(envelope, config, runner_scope)
        return await value if inspect.isawaitable(value) else value

    async def _run_command(
        self, envelope: Mapping[str, Any], config: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        command = config.get("command")
        if not isinstance(command, str) or not command.strip():
            raise _RunnerFailure("invalid_config", "command hook requires a command")
        env = {key: value for key, value in os.environ.items() if key in _ENV_ALLOWLIST}
        cwd = envelope.get("cwd")
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        limit = int(config.get("output_limit", self._output_limit))
        consumed = [0]
        stdout_task = asyncio.create_task(_read_limited(process.stdout, limit, consumed))
        stderr_task = asyncio.create_task(_read_limited(process.stderr, limit, consumed))
        process_task = asyncio.create_task(process.wait())
        try:
            process.stdin.write(json.dumps(dict(envelope), ensure_ascii=False).encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()
            pending = {stdout_task, stderr_task, process_task}
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in (stdout_task, stderr_task):
                    if task in done and task.done():
                        failure = task.exception()
                        if isinstance(failure, _HookCommandFailure):
                            raise _RunnerFailure(
                                failure.category, str(failure), exit_code=failure.exit_code
                            ) from failure
                        if failure is not None:
                            raise _RunnerFailure("io_failed", "hook output could not be read")
            stdout = stdout_task.result()
            stderr = stderr_task.result()
            exit_code = process_task.result()
        except asyncio.CancelledError:
            await _terminate_process(process)
            raise
        finally:
            if process.returncode is None:
                await _terminate_process(process)
            for task in (stdout_task, stderr_task, process_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, process_task, return_exceptions=True)
        if exit_code != 0:
            category = "blocking" if exit_code == 2 else "nonzero_exit"
            raise _RunnerFailure(
                category,
                stderr.decode("utf-8", "replace").strip() or f"hook exited {exit_code}",
                exit_code=exit_code,
            )
        if not stdout.strip():
            return {}
        try:
            return _parse_single_object(stdout)
        except _HookCommandFailure as exc:
            raise _RunnerFailure(exc.category, str(exc), exit_code=exc.exit_code) from exc

    @staticmethod
    def _normalize_output(output: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(output)
        specific = normalized.get("hookSpecificOutput")
        if not isinstance(specific, Mapping):
            return normalized
        event = specific.get("hookEventName")
        if event == "PreToolUse":
            normalized["permission_decision"] = specific.get("permissionDecision")
            if isinstance(specific.get("updatedInput"), Mapping):
                normalized["updated_input"] = specific["updatedInput"]
        elif event == "PermissionRequest" and isinstance(specific.get("decision"), Mapping):
            decision = specific["decision"]
            normalized["permission_decision"] = decision.get("behavior")
            if isinstance(decision.get("updatedInput"), Mapping):
                normalized["updated_input"] = decision["updatedInput"]
            if isinstance(decision.get("updatedPermissions"), list):
                normalized["permission_updates"] = decision["updatedPermissions"]
            if decision.get("message") is not None:
                normalized["reason"] = decision["message"]
        if specific.get("additionalContext") is not None:
            normalized.setdefault("metadata", {})["additional_context"] = specific[
                "additionalContext"
            ]
        return normalized

    @staticmethod
    def _aggregate(
        outcomes: Iterable[
            tuple[HookDefinitionRecord, Mapping[str, Any] | None, HookFailure | None]
        ],
        executed: tuple[str, ...],
        async_ids: tuple[str, ...],
    ) -> HookDispatchResult:
        patch: dict[str, Any] = {}
        metadata: dict[str, Any] = {}
        failures: list[HookFailure] = []
        decision = HookDecision.ALLOW
        permission: str | None = None
        updates: list[Mapping[str, Any]] = []
        reason: str | None = None
        rank = {None: 0, "allow": 1, "ask": 2, "deny": 3}
        fail_closed = {event.value for event in _FAIL_CLOSED_EVENTS}
        for definition, output, failure in outcomes:
            if failure is not None:
                failures.append(failure)
                if failure.category == "blocking" or definition.event in fail_closed:
                    decision = HookDecision.BLOCK
                    reason = failure.message
                continue
            assert output is not None
            current_patch = output.get("updated_input", output.get("input_patch"))
            if isinstance(current_patch, Mapping):
                patch.update(current_patch)
            attached = output.get("metadata")
            if isinstance(attached, Mapping):
                metadata.update(attached)
            candidate = output.get("permission_decision")
            if candidate in rank and rank[candidate] > rank[permission]:
                permission = candidate
            candidate_updates = output.get("permission_updates", ())
            if isinstance(candidate_updates, (list, tuple)):
                updates.extend(item for item in candidate_updates if isinstance(item, Mapping))
            if (
                output.get("decision") in {"block", "deny"}
                or output.get("continue") is False
                or candidate == "deny"
            ):
                decision = HookDecision.BLOCK
                reason = str(output.get("reason") or output.get("stop_reason") or "blocked by hook")
        return HookDispatchResult(
            decision=decision,
            input_patch=patch,
            metadata=metadata,
            permission_decision=permission,
            permission_updates=tuple(updates),
            reason=reason,
            failures=tuple(failures),
            executed_hook_ids=executed,
            async_invocation_ids=async_ids,
        )

    async def _notify_rewake(self, invocation_id: str) -> None:
        if self._notification_sink is None:
            return
        notification = {
            "type": "hook_async_rewake",
            "root_session_id": self._root_session_id,
            "invocation_id": invocation_id,
        }
        value = self._notification_sink(notification)
        if inspect.isawaitable(value):
            await value
