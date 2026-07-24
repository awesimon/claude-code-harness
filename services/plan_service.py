"""Legacy plan API adapter over durable plan state and markdown files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from models import Conversation, Plan
from schemas import PlanCreate, PlanUpdate
from state_core import PlanState, SessionRuntime, SQLAlchemyStateStore
from state_core.plan_files import PlanFileStore
from state_core.runtime import plan_slug


class PlanService:
    def __init__(self, db: Session):
        self.db = db
        factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
        self._store = SQLAlchemyStateStore(factory)

    def _runtime(self, conversation_id: str) -> SessionRuntime:
        return SessionRuntime(conversation_id, self._store)

    @staticmethod
    def _files() -> PlanFileStore:
        return PlanFileStore(Path.cwd())

    def create_or_update_plan(self, data: PlanCreate) -> Plan:
        conversation = self.db.get(Conversation, data.conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation {data.conversation_id} not found")
        plan = self.db.query(Plan).filter(Plan.conversation_id == data.conversation_id).first()
        if plan is None:
            plan = Plan(conversation_id=data.conversation_id, content=data.content, version=1)
            self.db.add(plan)
        else:
            plan.content = data.content
            plan.version += 1
        self.db.commit()
        self.db.refresh(plan)

        runtime = self._runtime(data.conversation_id)
        slug = runtime.state.plan.slug or plan_slug(data.conversation_id)
        path = self._files().save(slug, data.content)
        runtime.save_plan_draft(data.content, path)
        return plan

    def get_plan(self, plan_id: str) -> Plan | None:
        return self.db.get(Plan, plan_id)

    def get_plan_by_conversation(self, conversation_id: str) -> Plan | None:
        return self.db.query(Plan).filter(Plan.conversation_id == conversation_id).first()

    def update_plan(self, plan_id: str, updates: PlanUpdate) -> Plan | None:
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
        self.db.delete(plan)
        self.db.commit()
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
        content = plan_content or ""
        slug = runtime.state.plan.slug or plan_slug(conversation_id)
        path = self._files().save(slug, content)
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

    def get_plan_mode_state(self, conversation_id: str) -> str:
        return self._runtime(conversation_id).state.plan.state.value

    def is_in_plan_mode(self, conversation_id: str) -> bool:
        return self._runtime(conversation_id).state.plan.state is not PlanState.IDLE

    def get_plan_versions(self, conversation_id: str) -> list[dict[str, Any]]:
        plan = self.get_plan_by_conversation(conversation_id)
        if plan is None:
            return []
        return [
            {
                "version": plan.version,
                "created_at": plan.created_at.isoformat() if plan.created_at else None,
                "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
                "content_preview": plan.content[:200],
            }
        ]

    def recover_plan(self, conversation_id: str, version: int) -> Plan | None:
        plan = self.get_plan_by_conversation(conversation_id)
        return plan if plan is not None and plan.version == version else None
