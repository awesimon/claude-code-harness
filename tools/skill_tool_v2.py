"""Session-scoped Agent Skill tools using progressive resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harness.skills import SkillError, SkillResolver
from services.skill_loader import SkillLoader
from utils.skill_paths import validate_skill_name

from .base import (
    Tool,
    ToolExecutionError,
    ToolResult,
    ToolValidationError,
    get_active_tool_context,
    register_tool,
)


def _resolver() -> SkillResolver | None:
    harness = get_active_tool_context().get("session_harness")
    resolver = getattr(harness, "skills", None)
    return resolver if isinstance(resolver, SkillResolver) else None


@dataclass
class SkillExecuteInput:
    skill: str
    args: str | None = None


@dataclass
class SkillListInput:
    include_details: bool = False


@dataclass
class SkillInstallInput:
    source: str
    name: str | None = None


@dataclass
class SkillUninstallInput:
    skill: str


@register_tool
class SkillExecuteToolV2(Tool[SkillExecuteInput, dict[str, Any]]):
    name = "skill_execute"
    description = "Resolve an installed Agent Skill for the current session"
    version = "2.0"
    input_type = SkillExecuteInput

    async def validate(self, input_data: SkillExecuteInput):
        if not input_data.skill or not input_data.skill.strip():
            return ToolValidationError("skill name is required")
        return None

    async def execute(self, input_data: SkillExecuteInput) -> ToolResult:
        resolver = _resolver()
        if resolver is None:
            return ToolResult.fail("session_harness is required for skill execution")
        try:
            snapshot = resolver.resolve(input_data.skill.strip())
        except (OSError, SkillError) as exc:
            return ToolResult.fail(ToolExecutionError(str(exc)))
        return ToolResult.ok(
            {
                "skill": snapshot.name,
                "description": snapshot.description,
                "content": snapshot.content,
                "base_dir": snapshot.base_dir,
                "digest": snapshot.digest,
                "allowed_tools": list(snapshot.allowed_tools),
                "required_mcp_servers": list(snapshot.required_mcp_servers),
                "resources": [item.path for item in snapshot.resources],
                "scripts": list(snapshot.scripts),
                "args": input_data.args,
            },
            f"Skill '{snapshot.name}' resolved for this session",
            metadata={"skill": snapshot.name, "digest": snapshot.digest},
        )


@register_tool
class SkillListToolV2(Tool[SkillListInput, list[dict[str, Any]]]):
    name = "skill_list"
    description = "List indexed Agent Skills without loading their bodies"
    version = "2.0"
    input_type = SkillListInput

    async def execute(self, input_data: SkillListInput) -> ToolResult:
        resolver = _resolver()
        if resolver is None:
            return ToolResult.fail("session_harness is required for skill discovery")
        indexed = resolver.index()
        values = []
        for skill in indexed:
            item: dict[str, Any] = {
                "name": skill.name,
                "description": skill.description,
            }
            if input_data.include_details:
                item.update(
                    {
                        "location": skill.location,
                        "digest": skill.digest,
                        "metadata": dict(skill.metadata),
                    }
                )
            values.append(item)
        return ToolResult.ok(
            values,
            f"Found {len(values)} installed skills",
            metadata={"count": len(values)},
        )

    def is_read_only(self) -> bool:
        return True


@register_tool
class SkillInstallToolV2(Tool[SkillInstallInput, dict[str, str]]):
    name = "skill_install"
    description = "Install an Agent Skill into the current harness skill directory"
    version = "2.0"
    input_type = SkillInstallInput

    async def validate(self, input_data: SkillInstallInput):
        if not input_data.source or not input_data.source.strip():
            return ToolValidationError("source path is required")
        if input_data.name is not None:
            try:
                validate_skill_name(input_data.name.strip())
            except ValueError as exc:
                return ToolValidationError(str(exc))
        return None

    async def execute(self, input_data: SkillInstallInput) -> ToolResult:
        resolver = _resolver()
        if resolver is None:
            return ToolResult.fail("session_harness is required for skill installation")
        loader = SkillLoader(str(resolver.skills_dir))
        installed_name: str | None = None
        try:
            installed_name = await loader.install_skill(
                input_data.source.strip(), input_data.name.strip() if input_data.name else None
            )
            resolver.resolve(installed_name)
        except Exception as exc:
            if installed_name is not None:
                try:
                    await loader.uninstall_skill(installed_name)
                except Exception as rollback_exc:
                    return ToolResult.fail(
                        ToolExecutionError(f"{exc}; rollback failed: {rollback_exc}")
                    )
            return ToolResult.fail(ToolExecutionError(str(exc)))
        return ToolResult.ok(
            {"skill": installed_name},
            f"Successfully installed skill: {installed_name}",
        )

    def is_destructive(self) -> bool:
        return True


@register_tool
class SkillUninstallToolV2(Tool[SkillUninstallInput, bool]):
    name = "skill_uninstall"
    description = "Uninstall an Agent Skill from the current harness skill directory"
    version = "2.0"
    input_type = SkillUninstallInput

    async def validate(self, input_data: SkillUninstallInput):
        if not input_data.skill or not input_data.skill.strip():
            return ToolValidationError("skill name is required")
        try:
            validate_skill_name(input_data.skill.strip())
        except ValueError as exc:
            return ToolValidationError(str(exc))
        return None

    async def execute(self, input_data: SkillUninstallInput) -> ToolResult:
        resolver = _resolver()
        if resolver is None:
            return ToolResult.fail("session_harness is required for skill removal")
        loader = SkillLoader(str(resolver.skills_dir))
        try:
            removed = await loader.uninstall_skill(input_data.skill.strip())
        except Exception as exc:
            return ToolResult.fail(ToolExecutionError(str(exc)))
        if not removed:
            return ToolResult.fail(f"Skill not found: {input_data.skill.strip()}")
        return ToolResult.ok(True, f"Successfully uninstalled skill: {input_data.skill.strip()}")

    def is_destructive(self) -> bool:
        return True

    def requires_confirmation(self) -> bool:
        return True
