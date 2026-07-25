"""API-neutral lifecycle envelopes for all hook-producing subsystems."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from state_core.runtime_primitives import MAX_HOOK_EVENT_ENVELOPE_BYTES

from .context import CancellationToken
from .hooks import HookEvent

_SENSITIVE_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "authtoken",
        "bearertoken",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "idtoken",
        "password",
        "privatekey",
        "refreshtoken",
        "secret",
        "sessiontoken",
        "signingkey",
        "stderr",
        "stdin",
        "stdout",
        "token",
        "xapikey",
    }
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:\b(?:bearer|basic)\s+\S+|\b(?:api[_-]?key|password|secret|token)\s*[:=]\s*\S+)"
)
_OPTIONAL_IDENTIFIERS = frozenset(
    {
        "tool_call_id",
        "tool_name",
        "task_id",
        "team_id",
        "team_name",
        "worktree_id",
        "worktree_path",
        "mcp_server_id",
        "mcp_request_id",
        "config_id",
    }
)


def _safe_json(value: Any, path: str = "payload") -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if (
                normalized in _SENSITIVE_KEYS
                or normalized.endswith(("password", "secret", "privatekey", "signingkey"))
                or normalized.startswith(("stdin", "stdout", "stderr"))
            ):
                raise ValueError(f"{path}.{key} contains a sensitive field")
            result[key] = _safe_json(item, f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_json(item, f"{path}[]") for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if _SENSITIVE_TEXT.search(value):
            raise ValueError(f"{path} contains sensitive authorization text")
        return value
    raise TypeError(f"{path} contains a non-JSON value")


class LifecycleDispatcher:
    """Normalize producer events before delegating runner selection to hooks."""

    def __init__(
        self,
        hook_dispatcher: Any,
        *,
        root_session_id: str,
        cwd: Path | str,
        agent_id: str | None = None,
        agent_type: str | None = None,
        max_envelope_bytes: int = MAX_HOOK_EVENT_ENVELOPE_BYTES,
    ) -> None:
        self._hooks = hook_dispatcher
        self._root_session_id = root_session_id
        self._cwd = Path(cwd).expanduser().resolve()
        self._agent_id = agent_id
        self._agent_type = agent_type
        self._max_envelope_bytes = max_envelope_bytes

    async def emit(
        self,
        event: HookEvent | str,
        payload: Mapping[str, Any] | None = None,
        *,
        correlation_id: str | None = None,
        cwd: Path | str | None = None,
        agent_id: str | None = None,
        agent_type: str | None = None,
        permission_mode: str | None = None,
        transcript_position: int | str | None = None,
        cancellation: CancellationToken | None = None,
        feedback_attempt: int | None = None,
        **identifiers: Any,
    ) -> Any:
        hook_event = event if isinstance(event, HookEvent) else HookEvent(event)
        unsupported = set(identifiers) - _OPTIONAL_IDENTIFIERS
        if unsupported:
            raise ValueError(f"unsupported lifecycle identifiers: {sorted(unsupported)!r}")
        canonical_cwd = Path(cwd).expanduser().resolve() if cwd is not None else self._cwd
        envelope: dict[str, Any] = {
            "schema_version": 1,
            "hook_event_name": hook_event.value,
            "event": hook_event.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "correlation_id": correlation_id or f"lifecycle_{uuid.uuid4().hex}",
            "root_session_id": self._root_session_id,
            "agent_id": agent_id if agent_id is not None else self._agent_id,
            "agent_type": agent_type if agent_type is not None else self._agent_type,
            "cwd": str(canonical_cwd),
            "permission_mode": permission_mode,
            "transcript_position": transcript_position,
            "payload": _safe_json(payload or {}),
        }
        if feedback_attempt is not None:
            if feedback_attempt <= 0:
                raise ValueError("feedback_attempt must be positive")
            envelope["feedback_attempt"] = feedback_attempt
        envelope.update({key: value for key, value in identifiers.items() if value is not None})
        envelope = {key: value for key, value in envelope.items() if value is not None}
        encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > self._max_envelope_bytes:
            raise ValueError(f"lifecycle event envelope exceeds {self._max_envelope_bytes} bytes")
        return await self._hooks.dispatch(envelope, cancellation=cancellation)
