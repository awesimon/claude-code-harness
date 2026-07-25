"""Stateless REST compatibility adapter for durable conversations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from harness import SessionHarnessFactory
from models import Conversation as LegacyConversation
from schemas import ConversationCreate, ConversationUpdate, MessageCreate
from state_core import (
    EventType,
    SessionRuntimeFactory,
    SQLAlchemyStateStore,
    migrate_legacy_session,
)
from state_core.tool_events import normalize_tool_call, normalize_tool_result

_CONVERSATION_NAMESPACE = "api.conversation"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


@dataclass
class ConversationView:
    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


@dataclass
class MessageView:
    id: str
    conversation_id: str
    role: str
    content: str
    thinking: str | None
    tool_calls: list[dict[str, Any]] | None
    tool_results: list[dict[str, Any]] | None
    timestamp: datetime


class ConversationService:
    def __init__(self, db: Session):
        self.db = db
        self._session_factory = sessionmaker(
            bind=db.get_bind(), expire_on_commit=False
        )
        self._store = SQLAlchemyStateStore(self._session_factory)
        self._harnesses = SessionHarnessFactory(
            SessionRuntimeFactory(self._store), workspace_root=Path.cwd()
        )

    def _metadata(self, conversation_id: str):
        return self._store.metadata.get(conversation_id, _CONVERSATION_NAMESPACE)

    def _ensure_state(self, conversation_id: str):
        record = self._metadata(conversation_id)
        if record is not None and record.snapshot.get("deleted"):
            return None
        state = self._store.states.load_session(conversation_id)
        if state is not None and record is not None:
            return state
        legacy = self.db.get(LegacyConversation, conversation_id)
        if legacy is not None:
            migrate_legacy_session(
                conversation_id, self._session_factory, plan_root=Path.cwd()
            )
            return self._store.states.load_session(conversation_id)
        return None

    def _view(self, state) -> ConversationView | None:
        record = self._metadata(state.session_id)
        if record is None:
            return None
        snapshot = dict(record.snapshot)
        if snapshot.get("deleted"):
            return None
        messages = self.get_messages(state.session_id)
        return ConversationView(
            id=state.session_id,
            title=snapshot.get("title"),
            created_at=_parse_time(snapshot.get("created_at"), state.created_at),
            updated_at=_parse_time(snapshot.get("updated_at"), state.updated_at),
            message_count=len(messages),
        )

    def _put_metadata(self, conversation_id: str, updates: dict[str, Any]) -> None:
        current = self._metadata(conversation_id)
        snapshot = dict(current.snapshot) if current is not None else {}
        snapshot.update(updates)
        snapshot["updated_at"] = _utc_now().isoformat()
        self._store.metadata.put(
            conversation_id,
            _CONVERSATION_NAMESPACE,
            snapshot,
            expected_revision=current.revision if current is not None else None,
        )

    def create_conversation(self, data: ConversationCreate) -> ConversationView:
        conversation_id = str(uuid.uuid4())
        self._harnesses.create(conversation_id)
        now = _utc_now()
        self._store.metadata.put(
            conversation_id,
            _CONVERSATION_NAMESPACE,
            {
                "title": data.title,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
        )
        view = self.get_conversation(conversation_id)
        assert view is not None
        return view

    def get_conversation(self, conversation_id: str) -> ConversationView | None:
        state = self._ensure_state(conversation_id)
        return self._view(state) if state is not None else None

    def list_conversations(self, limit: int = 50) -> list[ConversationView]:
        legacy_ids = self.db.scalars(
            select(LegacyConversation.id).order_by(LegacyConversation.updated_at.desc())
        ).all()
        for conversation_id in legacy_ids:
            self._ensure_state(conversation_id)
        views = [
            view
            for state in self._store.states.list_sessions()
            if (view := self._view(state)) is not None
        ]
        return sorted(views, key=lambda view: view.updated_at, reverse=True)[:limit]

    def update_conversation(
        self, conversation_id: str, updates: ConversationUpdate
    ) -> ConversationView | None:
        if self.get_conversation(conversation_id) is None:
            return None
        data = updates.model_dump(exclude_unset=True)
        if "title" in data:
            self._put_metadata(conversation_id, {"title": data["title"]})
        return self.get_conversation(conversation_id)

    def delete_conversation(self, conversation_id: str) -> bool:
        if self.get_conversation(conversation_id) is None:
            return False
        self._put_metadata(conversation_id, {"deleted": True})
        self._store.tasks.delete_list(conversation_id)
        return self._store.states.delete_session(conversation_id)

    def add_message(
        self,
        conversation_id: str,
        data: MessageCreate,
        *,
        project_only: bool = False,
    ) -> MessageView:
        del project_only
        if self.get_conversation(conversation_id) is None:
            raise ValueError(f"Conversation {conversation_id} not found")
        runtime = self._harnesses.resume(conversation_id).session_runtime
        event_type = (
            EventType.ASSISTANT_MESSAGE
            if data.role == "assistant"
            else EventType.USER_MESSAGE
        )
        runtime.append_event(
            event_type,
            {
                "role": data.role,
                "content": data.content,
                "thinking": data.thinking,
                "toolCalls": data.tool_calls,
                "toolResults": data.tool_results,
            },
        )
        event = runtime.events()[-1]
        calls_by_id: dict[str, tuple[int, str]] = {}
        for raw_call in data.tool_calls or []:
            call = normalize_tool_call(raw_call)
            runtime.append_event(EventType.TOOL_CALL, call)
            calls_by_id[call["toolCallId"]] = (
                runtime.state.last_event_id,
                call["name"],
            )
        for raw_result in data.tool_results or []:
            result = normalize_tool_result(raw_result)
            call_event = calls_by_id.get(result["toolCallId"])
            if call_event is not None and not result["name"]:
                result = {**result, "name": call_event[1]}
            runtime.append_event(
                EventType.TOOL_RESULT,
                result,
                parent_event_id=call_event[0] if call_event is not None else None,
            )
        self._put_metadata(conversation_id, {})
        return self._message_view(event)

    @staticmethod
    def _message_view(event) -> MessageView:
        payload = event.payload
        default_role = (
            "assistant"
            if event.event_type is EventType.ASSISTANT_MESSAGE
            else "user"
        )
        return MessageView(
            id=str(payload.get("legacyMessageId") or event.id),
            conversation_id=event.session_id,
            role=str(payload.get("role") or default_role),
            content=str(payload.get("content") or ""),
            thinking=payload.get("thinking"),
            tool_calls=payload.get("toolCalls"),
            tool_results=payload.get("toolResults"),
            timestamp=event.created_at,
        )

    def get_messages(self, conversation_id: str, limit: int = 100) -> list[MessageView]:
        if self._ensure_state(conversation_id) is None:
            return []
        metadata = self._metadata(conversation_id)
        snapshot = dict(metadata.snapshot) if metadata is not None else {}
        deleted = {str(item) for item in snapshot.get("deleted_message_ids", [])}
        cleared_through = int(snapshot.get("cleared_through_event_id", 0))
        events = [
            event
            for event in self._store.states.list_events(conversation_id)
            if event.event_type
            in {EventType.USER_MESSAGE, EventType.ASSISTANT_MESSAGE}
            and event.id > cleared_through
            and str(event.payload.get("legacyMessageId") or event.id) not in deleted
        ]
        return [self._message_view(event) for event in events[:limit]]

    def get_message(
        self,
        conversation_id: str,
        message_id: str,
    ) -> MessageView | None:
        return next(
            (
                message
                for message in self.get_messages(conversation_id)
                if message.id == message_id
            ),
            None,
        )

    def delete_message(self, conversation_id: str, message_id: str) -> bool:
        message = self.get_message(conversation_id, message_id)
        if message is None:
            return False
        metadata = self._metadata(conversation_id)
        snapshot = dict(metadata.snapshot) if metadata is not None else {}
        deleted = [str(item) for item in snapshot.get("deleted_message_ids", [])]
        if message_id not in deleted:
            deleted.append(message_id)
        self._put_metadata(conversation_id, {"deleted_message_ids": deleted})
        return True

    def clear_messages(self, conversation_id: str) -> bool:
        if self.get_conversation(conversation_id) is None:
            return False
        events = self._store.states.list_events(conversation_id)
        through = events[-1].id if events else 0
        self._put_metadata(conversation_id, {"cleared_through_event_id": through})
        return True
