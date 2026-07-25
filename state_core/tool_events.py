"""Canonical payload adapters for durable tool transcript events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def _first_present(value: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return None


def _object_input(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        if isinstance(decoded, Mapping):
            return dict(decoded)
    return {}


def normalize_tool_call(value: Mapping[str, Any]) -> dict[str, Any]:
    """Convert flattened or OpenAI function calls to the durable wire shape."""

    function = value.get("function")
    function = function if isinstance(function, Mapping) else {}
    raw_input = _first_present(value, "input", "arguments")
    if raw_input is None:
        raw_input = function.get("arguments")
    return {
        "toolCallId": str(
            _first_present(value, "toolCallId", "tool_call_id", "id") or ""
        ),
        "name": str(value.get("name") or function.get("name") or ""),
        "input": _object_input(raw_input),
    }


def normalize_tool_result(
    value: Mapping[str, Any],
    *,
    call_name: str = "",
) -> dict[str, Any]:
    """Convert legacy result variants to the durable observation wire shape."""

    tool_call_id = str(
        _first_present(
            value,
            "toolCallId",
            "tool_call_id",
            "tool_use_id",
            "toolUseId",
            "id",
        )
        or ""
    )
    explicit_success = value.get("success")
    is_error = value.get("is_error")
    if isinstance(explicit_success, bool):
        success = explicit_success
    elif isinstance(is_error, bool):
        success = not is_error
    else:
        success = not ("error" in value and value.get("error") is not None)
    if not success and "error" in value:
        result = value.get("error")
    else:
        result = _first_present(value, "result", "output", "content", "error")
    return {
        "toolCallId": tool_call_id,
        "name": str(value.get("name") or call_name),
        "success": success,
        "result": result,
    }


__all__ = ["normalize_tool_call", "normalize_tool_result"]
