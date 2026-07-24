"""Compatibility facade over the progressive session skill resolver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from harness.skills import SkillIndexEntry, SkillResolver
from models.skill import SkillExecutionResult

from .skill_loader import SkillLoader


@dataclass
class SkillExecutionContext:
    session_id: Optional[str] = None
    agent_context: Optional[Any] = None
    tool_context: Optional[dict[str, Any]] = None


class SkillRegistry:
    """Legacy API without process-global skill bodies or executable callbacks."""

    def __init__(self) -> None:
        self._resolver: SkillResolver | None = None
        self._index: dict[str, SkillIndexEntry] = {}

    def initialize(self, skills_dir: Optional[str] = None) -> None:
        self._resolver = SkillResolver(skills_dir or "~/.claude/skills")
        self._index = {}

    def _require_resolver(self) -> SkillResolver:
        if self._resolver is None:
            raise RuntimeError("SkillRegistry not initialized. Call initialize() first.")
        return self._resolver

    async def load_all_skills(self) -> int:
        self._index = {
            entry.name: entry for entry in self._require_resolver().index()
        }
        return len(self._index)

    def get(self, name: str) -> SkillIndexEntry | None:
        return self._index.get(name)

    def list_skills(self) -> list[str]:
        return sorted(self._index)

    def get_all_skills(self) -> dict[str, SkillIndexEntry]:
        return dict(self._index)

    def register_executor(self, skill_name: str, executor: Any) -> None:
        raise RuntimeError(
            "Custom skill executors are disabled; scripts must cross the tool pipeline"
        )

    async def execute_skill(
        self,
        skill_name: str,
        args: Optional[str] = None,
        context: Optional[SkillExecutionContext] = None,
    ) -> SkillExecutionResult:
        try:
            snapshot = self._require_resolver().resolve(skill_name)
        except Exception as exc:
            return SkillExecutionResult(success=False, error=str(exc))
        return SkillExecutionResult(
            success=True,
            data={
                "skill": snapshot.name,
                "description": snapshot.description,
                "content": snapshot.content,
                "args": args,
                "scripts": list(snapshot.scripts),
            },
            message=(
                f"Skill '{snapshot.name}' resolved. Packaged scripts must be run "
                "through an approved subprocess tool."
            ),
        )

    async def install_skill(
        self, source_path: str, skill_name: Optional[str] = None
    ) -> str:
        resolver = self._require_resolver()
        installed = await SkillLoader(str(resolver.skills_dir)).install_skill(
            source_path, skill_name
        )
        await self.load_all_skills()
        return installed

    async def uninstall_skill(self, skill_name: str) -> bool:
        resolver = self._require_resolver()
        removed = await SkillLoader(str(resolver.skills_dir)).uninstall_skill(skill_name)
        await self.load_all_skills()
        return removed

    def clear(self) -> None:
        self._index.clear()


def get_skill_registry() -> SkillRegistry:
    """Return an unconfigured compatibility facade, never a global owner."""

    return SkillRegistry()
