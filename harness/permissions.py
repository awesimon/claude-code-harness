from __future__ import annotations

import inspect
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from tools.base import Tool, tool_flag
from .context import PermissionMode, RuntimeContext


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class PermissionRequest:
    tool_name: str
    input_data: dict[str, Any]
    reason: str
    session_id: Optional[str] = None


class PermissionPolicy:
    PLAN_ALLOWED_TOOLS = {
        "read_file",
        "glob",
        "grep",
        "bash",
        "enter_plan_mode",
        "exit_plan_mode",
        "ask_user_question",
    }
    PLAN_BASH_WRITE_MARKERS = (
        "touch ",
        "mkdir ",
        "rm ",
        "cp ",
        "mv ",
        ">",
        "git add",
        "git commit",
        "git push",
    )

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root.resolve() if workspace_root else None

    def check(
        self,
        tool: Tool,
        tool_name: str,
        input_data: dict[str, Any],
        context: RuntimeContext,
    ) -> tuple[PermissionDecision, str]:
        boundary_error = self._check_workspace_boundary(
            tool_name,
            input_data,
            context.workspace_root,
        )
        if boundary_error:
            return PermissionDecision.DENY, boundary_error

        if context.permission_mode == PermissionMode.PLAN:
            if tool_name not in self.PLAN_ALLOWED_TOOLS:
                return PermissionDecision.DENY, "Only planning tools are allowed in plan mode"
            if tool_name == "bash":
                command = str(input_data.get("command", "")).lower()
                if any(marker in command for marker in self.PLAN_BASH_WRITE_MARKERS):
                    return PermissionDecision.DENY, "Bash command may mutate state in plan mode"

        if context.permission_mode == PermissionMode.BYPASS:
            return PermissionDecision.ALLOW, "bypass mode"

        if tool_flag(tool, "requires_confirmation") or tool_flag(tool, "is_destructive"):
            return PermissionDecision.ASK, "tool is destructive or requires confirmation"
        return PermissionDecision.ALLOW, "read-only tool"

    @staticmethod
    def _check_workspace_boundary(
        tool_name: str,
        input_data: dict[str, Any],
        workspace_root: Optional[Path],
    ) -> Optional[str]:
        if workspace_root is None:
            return None
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
        for key, value in input_data.items():
            normalized_key = key.lower()
            if not (
                normalized_key in path_keys
                or normalized_key.endswith("_path")
                or normalized_key.endswith("_dir")
            ) or not isinstance(value, str):
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = workspace_root / candidate
            candidate = candidate.resolve()
            try:
                candidate.relative_to(workspace_root.resolve())
            except ValueError:
                return f"Path for '{key}' is outside the workspace boundary"
        if tool_name == "bash":
            command = input_data.get("command")
            if isinstance(command, str):
                try:
                    tokens = shlex.split(command)
                except ValueError:
                    return "Bash command could not be safely parsed"
                for token in tokens:
                    candidate_text = token.lstrip("<>")
                    if not candidate_text.startswith(("/", "~/", "../", "./")):
                        continue
                    candidate = Path(candidate_text).expanduser()
                    if not candidate.is_absolute():
                        candidate = workspace_root / candidate
                    try:
                        candidate.resolve().relative_to(workspace_root.resolve())
                    except ValueError:
                        return "Bash command references a path outside the workspace boundary"
        return None

    async def authorize(
        self,
        tool: Tool,
        tool_name: str,
        input_data: dict[str, Any],
        context: RuntimeContext,
    ) -> tuple[bool, str]:
        decision, reason = self.check(tool, tool_name, input_data, context)
        if decision == PermissionDecision.ALLOW:
            return True, reason
        if decision == PermissionDecision.DENY:
            return False, reason
        if context.approval_callback is None:
            return False, "Approval is required but no approval callback is configured"
        request = PermissionRequest(tool_name, input_data, reason, context.session_id)
        result = context.approval_callback(request)
        approved = await result if inspect.isawaitable(result) else result
        return bool(approved), "approved" if approved else "approval denied"
