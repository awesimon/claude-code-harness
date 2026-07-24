"""Compatibility tools over the session-owned worktree manager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harness.worktrees import WorktreeNotClean

from .base import (
    Tool,
    ToolResult,
    ToolValidationError,
    get_active_tool_context,
    register_tool,
)


def _manager():
    harness = get_active_tool_context().get("session_harness")
    if harness is None:
        raise ToolValidationError("worktree tools require an active session harness")
    return harness.worktrees


@dataclass
class EnterWorktreeInput:
    name: str | None = None
    base_branch: str | None = None


@dataclass
class ExitWorktreeInput:
    keep: bool = False
    action: str | None = None
    discard_changes: bool = False
    worktree_id: str | None = None


@register_tool
class EnterWorktreeTool(Tool[EnterWorktreeInput, dict[str, Any]]):
    name = "enter_worktree"
    description = "创建隔离的 Git worktree，并将当前会话切换到该工作目录"
    input_type = EnterWorktreeInput
    should_defer = True
    search_hint = "create an isolated git worktree and switch into it"

    async def execute(self, input_data: EnterWorktreeInput) -> ToolResult:
        record = _manager().create(input_data.name, base_branch=input_data.base_branch)
        slug = str(record.details.get("slug", input_data.name or ""))
        data = {
            "worktree_id": record.worktree_id,
            "worktree_name": slug,
            "worktree_path": record.canonical_path,
            "worktreePath": record.canonical_path,
            "branch": record.branch,
            "worktreeBranch": record.branch,
            "base_branch": record.details.get("base_ref"),
            "base_commit": record.base_commit,
            "original_path": record.repository_root,
        }
        return ToolResult.ok(
            data,
            f"Created worktree at {record.canonical_path} on branch {record.branch}",
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "base_branch": {"type": "string"},
                },
            },
        }


@register_tool
class ExitWorktreeTool(Tool[ExitWorktreeInput, dict[str, Any]]):
    name = "exit_worktree"
    description = "退出当前会话 worktree，可选择保留或安全删除"
    input_type = ExitWorktreeInput
    should_defer = True
    search_hint = "leave, keep, or safely remove the active git worktree"

    async def validate(self, input_data: ExitWorktreeInput):
        if input_data.action not in {None, "keep", "remove"}:
            return ToolValidationError("action must be 'keep' or 'remove'")
        if input_data.action == "remove" and input_data.keep:
            return ToolValidationError("keep conflicts with action='remove'")
        return None

    async def execute(self, input_data: ExitWorktreeInput) -> ToolResult:
        action = input_data.action or ("keep" if input_data.keep else "remove")
        manager = _manager()
        try:
            record = (
                manager.keep(input_data.worktree_id)
                if action == "keep"
                else manager.remove(
                    input_data.worktree_id,
                    discard_changes=input_data.discard_changes,
                )
            )
        except WorktreeNotClean as exc:
            return ToolResult.fail(
                ToolValidationError(
                    str(exc),
                    details={
                        "changed_files": exc.changed_files,
                        "commits": exc.commits,
                        "discard_changes_required": True,
                    },
                )
            )
        data = {
            "action": action,
            "worktree_id": record.worktree_id,
            "worktree_path": record.canonical_path,
            "worktreePath": record.canonical_path,
            "worktree_branch": record.branch,
            "worktreeBranch": record.branch,
            "original_path": record.repository_root,
            "originalCwd": record.repository_root,
            "kept": action == "keep",
            "discardedFiles": record.details.get("discarded_files", 0),
            "discardedCommits": record.details.get("discarded_commits", 0),
        }
        verb = "preserved" if action == "keep" else "removed"
        return ToolResult.ok(data, f"Exited worktree; {record.canonical_path} was {verb}")

    def is_destructive(self) -> bool:
        return True

    def get_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "keep": {"type": "boolean", "default": False},
                    "action": {"type": "string", "enum": ["keep", "remove"]},
                    "discard_changes": {"type": "boolean", "default": False},
                    "worktree_id": {"type": "string"},
                },
            },
        }
