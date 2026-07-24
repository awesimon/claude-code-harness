"""Idempotent migration from legacy conversation tables into state-core."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from models import Message
from models import Plan as LegacyPlan
from models import Task as LegacyTask

from .plan_files import PlanFileStore
from .runtime import SessionRuntime, plan_slug
from .sqlalchemy_store import RuntimeSession, SQLAlchemyStateStore
from .types import EventType, NewTask, PlanState, TaskMutation, TaskStatus


def migrate_legacy_session(
    session_id: str,
    session_factory: Any,
    *,
    plan_root: Path | None = None,
) -> SessionRuntime:
    """Import a legacy conversation exactly once and return its runtime."""

    store = SQLAlchemyStateStore(session_factory)
    runtime = SessionRuntime(session_id, store)
    with session_factory() as db:
        marker = db.get(RuntimeSession, session_id)
        if marker is not None and marker.migrated_at is not None:
            return SessionRuntime.recover(session_id, store)

        messages = db.scalars(
            select(Message)
            .where(Message.conversation_id == session_id)
            .order_by(Message.timestamp, Message.id)
        ).all()
        tasks = db.scalars(
            select(LegacyTask)
            .where(LegacyTask.conversation_id == session_id)
            .order_by(LegacyTask.created_at, LegacyTask.id)
        ).all()
        legacy_plan = db.scalar(
            select(LegacyPlan).where(LegacyPlan.conversation_id == session_id).limit(1)
        )

    for message in messages:
        event_type = (
            EventType.USER_MESSAGE if message.role == "user" else EventType.ASSISTANT_MESSAGE
        )
        runtime.append_event(
            event_type,
            {
                "legacyMessageId": message.id,
                "role": message.role,
                "content": message.content,
                "thinking": message.thinking,
            },
        )
        for call in message.tool_calls or []:
            runtime.append_event(
                EventType.TOOL_CALL,
                {
                    "legacyMessageId": message.id,
                    "toolCallId": call.get("id") or call.get("tool_call_id"),
                    "name": call.get("name") or call.get("function", {}).get("name"),
                    "input": call.get("input") or call.get("function", {}).get("arguments"),
                },
            )
        for result in message.tool_results or []:
            runtime.append_event(
                EventType.TOOL_RESULT,
                {
                    "legacyMessageId": message.id,
                    "toolCallId": result.get("tool_call_id") or result.get("toolCallId"),
                    "content": result.get("content") or result.get("output"),
                },
            )

    existing_legacy_ids = {
        task.metadata.get("legacyId") for task in runtime.list_tasks() if task.metadata
    }
    migrated_ids: dict[str, str] = {}
    for legacy in tasks:
        if legacy.id in existing_legacy_ids:
            continue
        created = runtime.create_task(
            NewTask(
                subject=legacy.subject,
                description=legacy.description,
                active_form=legacy.active_form,
                metadata={**(legacy.meta or {}), "legacyId": legacy.id},
            )
        )
        migrated_ids[legacy.id] = created.id
        status = getattr(legacy.status, "value", legacy.status)
        mutation = TaskMutation(
            status=TaskStatus(status) if status != TaskStatus.PENDING.value else None,
            owner=legacy.owner,
        )
        runtime.update_task(created.id, mutation)

    for legacy in tasks:
        current_id = migrated_ids.get(legacy.id)
        if current_id is None:
            continue
        blocks = [migrated_ids[item] for item in legacy.blocks or [] if item in migrated_ids]
        blocked_by = [
            migrated_ids[item] for item in legacy.blocked_by or [] if item in migrated_ids
        ]
        if blocks or blocked_by:
            runtime.update_task(
                current_id,
                TaskMutation(add_blocks=blocks, add_blocked_by=blocked_by),
            )

    if legacy_plan is not None and runtime.state.plan.state is PlanState.IDLE:
        runtime.enter_plan(runtime.state.permission_mode)
        root = (plan_root or Path.cwd()).resolve()
        slug = plan_slug(session_id)
        path = PlanFileStore(root).save(slug, legacy_plan.content)
        runtime.state.plan.slug = slug
        runtime.state.plan.file_path = path
        runtime._persist(
            EventType.PLAN_TRANSITION,
            {"action": "migrate", "legacyPlanId": legacy_plan.id},
        )

    with session_factory() as db, db.begin():
        marker = db.get(RuntimeSession, session_id)
        if marker is not None:
            marker.migrated_at = datetime.now(timezone.utc)

    return runtime
