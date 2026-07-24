"""Compatibility adapter over the canonical plan file store."""

from __future__ import annotations

from pathlib import Path

from state_core.plan_files import PlanFileStore
from state_core.runtime import plan_slug

from .types import PlanContext


class PlanStorage:
    def __init__(self, plans_directory: str | None = None) -> None:
        root = Path(plans_directory).resolve().parent if plans_directory else Path.cwd()
        self._store = PlanFileStore(root)

    def get_plan_file_path(self, session_id: str, agent_id: str | None = None) -> str:
        slug = plan_slug(session_id)
        if agent_id:
            slug = f"{slug}-agent-{plan_slug(agent_id)}"
        return str(self._store.path_for(slug))

    async def save_plan(self, session_id: str, content: str, agent_id: str | None = None) -> str:
        slug = plan_slug(session_id)
        if agent_id:
            slug = f"{slug}-agent-{plan_slug(agent_id)}"
        return self._store.save(slug, content)

    async def load_plan(self, session_id: str, agent_id: str | None = None) -> str | None:
        slug = plan_slug(session_id)
        if agent_id:
            slug = f"{slug}-agent-{plan_slug(agent_id)}"
        return self._store.load(slug)

    async def update_plan(self, session_id: str, content: str, agent_id: str | None = None) -> str:
        return await self.save_plan(session_id, content, agent_id)

    def plan_exists(self, session_id: str, agent_id: str | None = None) -> bool:
        return Path(self.get_plan_file_path(session_id, agent_id)).exists()

    def get_plan_context(self, session_id: str, agent_id: str | None = None) -> PlanContext | None:
        path = Path(self.get_plan_file_path(session_id, agent_id))
        return PlanContext(plan_file_path=str(path)) if path.exists() else None

    def clear_session(self, session_id: str) -> None:
        return None

    def list_all_plans(self) -> list[dict[str, object]]:
        directory = self._store.root / "plans"
        if not directory.exists():
            return []
        return [
            {"filename": path.name, "path": str(path), "size": path.stat().st_size}
            for path in sorted(directory.glob("*.md"))
        ]


def get_plan_storage(plans_directory: str | None = None) -> PlanStorage:
    return PlanStorage(plans_directory)


def reset_plan_storage() -> None:
    return None
