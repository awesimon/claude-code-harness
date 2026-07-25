"""Compatibility exports for the canonical session-scoped Skill tools."""

from .skill_tool_v2 import (
    SkillExecuteInput,
    SkillExecuteToolV2,
    SkillListInput,
    SkillListToolV2,
)

SkillExecuteTool = SkillExecuteToolV2
SkillListTool = SkillListToolV2

__all__ = [
    "SkillExecuteInput",
    "SkillExecuteTool",
    "SkillListInput",
    "SkillListTool",
]
