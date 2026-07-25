"""Stateless REST compatibility adapter for durable plan state."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from harness import SessionHarnessFactory
from models import Conversation as LegacyConversation
from models import Plan as LegacyPlan
from schemas import PlanCreate, PlanUpdate
from state_core import (
    PlanState,
    SessionRuntimeFactory,
    SQLAlchemyStateStore,
    migrate_legacy_session,
)
from state_core.plan_files import PlanFileStore
from state_core.runtime import plan_slug

_PLAN_NAMESPACE = "api.plan"


def _parse_time(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


@dataclass
class PlanView:
    id: str
    conversation_id: str
    content: str
    version: int
    created_at: datetime
    updated_at: datetime


class PlanService:
    def __init__(self, db: Session):
        self.db = db
        session_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
        self._session_factory = session_factory
        self._store = SQLAlchemyStateStore(session_factory)
        self._harnesses = SessionHarnessFactory(
            SessionRuntimeFactory(self._store), workspace_root=Path.cwd()
        )
        self._files = PlanFileStore(Path.cwd())

    def _resume_runtime(self, conversation_id: str):
        return self._harnesses.resume(conversation_id).session_runtime

    def _metadata(self, conversation_id: str):
        return self._store.metadata.get(conversation_id, _PLAN_NAMESPACE)

    def _ensure_runtime(self, conversation_id: str):
        state = self._store.states.load_session(conversation_id)
        if state is None:
            if self.db.get(LegacyConversation, conversation_id) is None:
                return None
            return migrate_legacy_session(
                conversation_id,
                self._session_factory,
                plan_root=Path.cwd(),
            )
        if self._metadata(conversation_id) is None:
            return migrate_legacy_session(
                conversation_id,
                self._session_factory,
                plan_root=Path.cwd(),
            )
        return self._resume_runtime(conversation_id)

    def _runtime(self, conversation_id: str):
        runtime = self._ensure_runtime(conversation_id)
        if runtime is None:
            raise ValueError(f"Conversation {conversation_id} not found")
        return runtime

    def _content(self, conversation_id: str) -> str | None:
        runtime = self._runtime(conversation_id)
        path = runtime.state.plan.file_path
        if path is None:
            return None
        plan_path = Path(path)
        return plan_path.read_text(encoding="utf-8") if plan_path.exists() else None

    def _view(self, conversation_id: str) -> PlanView | None:
        runtime = self._ensure_runtime(conversation_id)
        if runtime is None:
            return None
        metadata = self._metadata(conversation_id)
        if metadata is None or metadata.snapshot.get("deleted"):
            return None
        content = self._content(conversation_id)
        if content is None:
            return None
        return PlanView(
            id=str(metadata.snapshot["id"]),
            conversation_id=conversation_id,
            content=content,
            version=int(metadata.snapshot.get("version", 1)),
            created_at=_parse_time(
                metadata.snapshot.get("created_at"), metadata.created_at
            ),
            updated_at=_parse_time(
                metadata.snapshot.get("updated_at"), metadata.updated_at
            ),
        )

    def create_or_update_plan(self, data: PlanCreate) -> PlanView:
        runtime = self._runtime(data.conversation_id)
        slug = runtime.state.plan.slug or plan_slug(data.conversation_id)
        path = self._files.save(slug, data.content)
        runtime.save_plan_draft(data.content, path)

        current = self._metadata(data.conversation_id)
        snapshot = dict(current.snapshot) if current is not None else {}
        snapshot.update(
            {
                "id": snapshot.get("id", str(uuid.uuid4())),
                "version": int(snapshot.get("version", 0)) + 1,
                "deleted": False,
            }
        )
        self._store.metadata.put(
            data.conversation_id,
            _PLAN_NAMESPACE,
            snapshot,
            expected_revision=current.revision if current is not None else None,
        )
        plan = self._view(data.conversation_id)
        assert plan is not None
        return plan

    def get_plan(self, plan_id: str) -> PlanView | None:
        plan = next(
            (
                plan
                for state in self._store.states.list_sessions()
                if (plan := self._view(state.session_id)) is not None
                and plan.id == plan_id
            ),
            None,
        )
        if plan is not None:
            return plan
        legacy = self.db.get(LegacyPlan, plan_id)
        if legacy is None:
            return None
        migrate_legacy_session(
            legacy.conversation_id,
            self._session_factory,
            plan_root=Path.cwd(),
        )
        return self._view(legacy.conversation_id)

    def get_plan_by_conversation(self, conversation_id: str) -> PlanView | None:
        if self._ensure_runtime(conversation_id) is None:
            return None
        return self._view(conversation_id)

    def update_plan(self, plan_id: str, updates: PlanUpdate) -> PlanView | None:
        plan = self.get_plan(plan_id)
        if plan is None or updates.content is None:
            return plan
        return self.create_or_update_plan(
            PlanCreate(conversation_id=plan.conversation_id, content=updates.content)
        )

    def delete_plan(self, plan_id: str) -> bool:
        plan = self.get_plan(plan_id)
        if plan is None:
            return False
        runtime = self._runtime(plan.conversation_id)
        if runtime.state.plan.file_path:
            Path(runtime.state.plan.file_path).unlink(missing_ok=True)
        current = self._metadata(plan.conversation_id)
        assert current is not None
        self._store.metadata.put(
            plan.conversation_id,
            _PLAN_NAMESPACE,
            {**dict(current.snapshot), "deleted": True},
            expected_revision=current.revision,
        )
        return True

    def enter_plan_mode(self, conversation_id: str) -> dict[str, Any]:
        runtime = self._runtime(conversation_id)
        if runtime.state.plan.state is PlanState.IDLE:
            runtime.enter_plan(runtime.state.permission_mode)
        return {
            "success": True,
            "message": "Entered plan mode",
            "state": runtime.state.plan.state.value,
            "conversation_id": conversation_id,
        }

    def exit_plan_mode(
        self,
        conversation_id: str,
        plan_content: str | None = None,
        allowed_prompts: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        runtime = self._runtime(conversation_id)
        content = plan_content
        if content is None:
            content = self._content(conversation_id) or ""
        slug = runtime.state.plan.slug or plan_slug(conversation_id)
        path = self._files.save(slug, content)
        runtime.submit_plan(content, allowed_prompts or [], file_path=path)
        return {
            "success": True,
            "message": "Plan submitted for approval",
            "state": runtime.state.plan.state.value,
            "conversation_id": conversation_id,
            "plan": content,
            "file_path": path,
            "allowed_prompts": allowed_prompts or [],
        }

    def approve_plan(
        self, conversation_id: str, edited_content: str | None = None
    ) -> dict[str, Any]:
        runtime = self._runtime(conversation_id)
        if edited_content is not None:
            slug = runtime.state.plan.slug or plan_slug(conversation_id)
            path = self._files.save(slug, edited_content)
            runtime.save_plan_draft(edited_content, path)
        runtime.approve_plan()
        runtime.exit_plan()
        return {
            "success": True,
            "message": "Plan approved",
            "plan_content": edited_content or self._content(conversation_id),
            "is_edited": edited_content is not None,
        }

    def reject_plan(self, conversation_id: str, reason: str | None = None) -> dict[str, Any]:
        runtime = self._runtime(conversation_id)
        runtime.reject_plan()
        return {
            "success": True,
            "message": reason or "Plan rejected",
            "can_continue_planning": True,
        }

    def force_exit_plan_mode(self, conversation_id: str) -> dict[str, Any]:
        runtime = self._runtime(conversation_id)
        previous = runtime.state.plan.state.value
        if runtime.state.plan.state is PlanState.PLANNING:
            self.exit_plan_mode(conversation_id)
            runtime = self._runtime(conversation_id)
        if runtime.state.plan.state is PlanState.PENDING_APPROVAL:
            runtime.approve_plan()
        if runtime.state.plan.state is PlanState.APPROVED:
            runtime.exit_plan()
        return {
            "success": True,
            "message": "Plan mode exited",
            "previous_mode": previous,
        }

    def get_plan_mode_state(self, conversation_id: str) -> str:
        return self._runtime(conversation_id).state.plan.state.value

    def get_mode_snapshot(self, conversation_id: str) -> dict[str, Any]:
        """Return the route-facing durable plan state without exposing its runtime."""
        return self._runtime(conversation_id).state.plan.to_dict()

    def is_in_plan_mode(self, conversation_id: str) -> bool:
        return self._runtime(conversation_id).state.plan.state is not PlanState.IDLE

    def get_plan_versions(self, conversation_id: str) -> list[dict[str, Any]]:
        plan = self.get_plan_by_conversation(conversation_id)
        if plan is None:
            return []
        return [
            {
                "version": plan.version,
                "created_at": plan.created_at.isoformat(),
                "updated_at": plan.updated_at.isoformat(),
                "content_preview": plan.content[:200],
            }
        ]

    def recover_plan(self, conversation_id: str, version: int) -> PlanView | None:
        plan = self.get_plan_by_conversation(conversation_id)
        return plan if plan is not None and plan.version == version else None
