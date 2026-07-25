"""Compatibility boundary for the removed process-global skill manager.

Agent Skills are resolved by ``SessionHarness.skills`` from declarative
``SKILL.md`` files. This module intentionally performs no installation,
dependency loading, Python imports, or global tool registration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from tools.base import ToolResult


@dataclass(frozen=True)
class SkillInfo:
    """Legacy metadata shape retained for import compatibility."""

    name: str
    version: str
    description: str
    author: str
    source_url: str
    install_path: str
    installed_at: str
    tools: List[str]
    enabled: bool = True


class SkillManager:
    """Fail-closed adapter directing callers to session-scoped skill tools."""

    _MESSAGE = (
        "Process-global skill management was removed; use the session-scoped "
        "skill_install, skill_list, and skill_uninstall tools"
    )

    def install_from_git(self, git_url: str, name: str | None = None) -> ToolResult:
        return ToolResult.fail(self._MESSAGE)

    def install_from_local(self, local_path: str, name: str | None = None) -> ToolResult:
        return ToolResult.fail(self._MESSAGE)

    def uninstall(self, name: str) -> ToolResult:
        return ToolResult.fail(self._MESSAGE)

    def list_skills(self) -> ToolResult:
        return ToolResult.fail(self._MESSAGE)

    def enable_skill(self, name: str) -> ToolResult:
        return ToolResult.fail(self._MESSAGE)

    def disable_skill(self, name: str) -> ToolResult:
        return ToolResult.fail(self._MESSAGE)

    def load_all_skills(self) -> int:
        """Retained as a no-op for callers migrating off the legacy manager."""

        return 0


skill_manager = SkillManager()


__all__ = ["SkillInfo", "SkillManager", "skill_manager"]
