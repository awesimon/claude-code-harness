"""Import-compatible plan adapter; SessionRuntime owns every mutation."""

from __future__ import annotations

from typing import Any, Callable

from state_core import PlanState, SessionRuntime

from .storage import PlanStorage
from .types import AlreadyInPlanModeError, PlanApprovalRequiredError


class PlanModeManager:
    def __init__(
        self,
        plans_directory: str | None = None,
        runtime_provider: Callable[[str], SessionRuntime] | None = None,
    ) -> None:
        self._runtime_provider = runtime_provider
        self._storage = PlanStorage(plans_directory)

    def _runtime(self, session_id: str) -> SessionRuntime:
        if self._runtime_provider is None:
            raise RuntimeError("PlanModeManager requires a SessionRuntime provider")
        return self._runtime_provider(session_id)

    def is_in_plan_mode(self, session_id: str) -> bool:
        return self._runtime(session_id).state.plan.state is not PlanState.IDLE

    def get_state(self, session_id: str) -> PlanState:
        return self._runtime(session_id).state.plan.state

    async def enter_plan_mode(
        self, session_id: str, previous_mode: str | None = None
    ) -> dict[str, Any]:
        runtime = self._runtime(session_id)
        if runtime.state.plan.state is not PlanState.IDLE:
            raise AlreadyInPlanModeError(session_id)
        runtime.enter_plan(previous_mode or runtime.state.permission_mode)
        return {"success": True, "state": "planning", "message": "Entered plan mode"}

    async def save_plan(
        self, session_id: str, content: str, is_edited: bool = False
    ) -> dict[str, Any]:
        path = await self._storage.save_plan(session_id, content)
        self._runtime(session_id).save_plan_draft(content, path)
        return {"success": True, "file_path": path, "content_length": len(content)}

    async def submit_plan_for_approval(
        self, session_id: str, allowed_prompts: list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        runtime = self._runtime(session_id)
        content = await self._storage.load_plan(session_id) or ""
        runtime.submit_plan(
            content,
            allowed_prompts or [],
            file_path=self._storage.get_plan_file_path(session_id),
        )
        return {"success": True, "state": "pending_approval"}

    async def approve_plan(
        self, session_id: str, edited_content: str | None = None
    ) -> dict[str, Any]:
        if edited_content is not None:
            await self.save_plan(session_id, edited_content, is_edited=True)
        runtime = self._runtime(session_id)
        runtime.approve_plan()
        return {"success": True, "state": "approved"}

    async def reject_plan(self, session_id: str, reason: str | None = None) -> dict[str, Any]:
        runtime = self._runtime(session_id)
        runtime.reject_plan()
        return {"success": True, "state": "planning", "reason": reason}

    async def exit_plan_mode(self, session_id: str) -> dict[str, Any]:
        runtime = self._runtime(session_id)
        if runtime.state.plan.state is not PlanState.APPROVED:
            raise PlanApprovalRequiredError(session_id)
        runtime.exit_plan()
        return {"success": True, "state": "idle", "restored_mode": runtime.state.permission_mode}

    def get_session(self, session_id: str) -> SessionRuntime:
        return self._runtime(session_id)

    def clear_session(self, session_id: str) -> None:
        return None


def get_plan_mode_manager(
    plans_directory: str | None = None,
    runtime_provider: Callable[[str], SessionRuntime] | None = None,
) -> PlanModeManager:
    return PlanModeManager(plans_directory, runtime_provider)


def reset_plan_mode_manager() -> None:
    return None
