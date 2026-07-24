"""Session-scoped command hook execution and durable hook snapshots."""

from __future__ import annotations

import asyncio
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
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Mapping

from state_core import RuntimeMetadataRepository, RuntimeRecordRevisionConflict

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


async def _read_limited(
    stream: asyncio.StreamReader, limit: int, consumed: list[int]
) -> bytes:
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

        existing = next(
            (item for item in self._definitions if item.hook_id == hook.hook_id), None
        )
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
        return tuple(
            HookDefinition.from_json(item) for item in hooks if isinstance(item, Mapping)
        )

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
