"""Compatibility exports for session-scoped Agent Skill tools."""

from __future__ import annotations

from dataclasses import dataclass

from .base import Tool, ToolResult
from .skill_tool_v2 import (
    SkillInstallInput,
    SkillInstallToolV2,
    SkillListInput,
    SkillListToolV2,
    SkillUninstallInput,
    SkillUninstallToolV2,
)

SkillInstallTool = SkillInstallToolV2
SkillListTool = SkillListToolV2
SkillUninstallTool = SkillUninstallToolV2


@dataclass
class SkillEnableInput:
    name: str


@dataclass
class SkillDisableInput:
    name: str


class _ImmutableSkillStateTool(Tool):
    async def execute(self, input_data) -> ToolResult:
        return ToolResult.fail(
            "Agent Skill snapshots are immutable; install or uninstall the skill "
            "for a new session scope"
        )


class SkillEnableTool(_ImmutableSkillStateTool):
    name = "skill_enable"
    description = "Compatibility adapter for immutable Agent Skill snapshots"
    input_type = SkillEnableInput


class SkillDisableTool(_ImmutableSkillStateTool):
    name = "skill_disable"
    description = "Compatibility adapter for immutable Agent Skill snapshots"
    input_type = SkillDisableInput


__all__ = [
    "SkillInstallInput",
    "SkillInstallTool",
    "SkillListInput",
    "SkillListTool",
    "SkillUninstallInput",
    "SkillUninstallTool",
    "SkillEnableInput",
    "SkillEnableTool",
    "SkillDisableInput",
    "SkillDisableTool",
]
