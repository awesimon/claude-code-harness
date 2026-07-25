"""Legacy /chat/stream：SSE 落库与 query_engine 对话循环。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import HTTPException

from models import Conversation, SessionLocal
from query_engine import QueryEngine
from services.conversation_title import (
    log_conversation_title_task_done,
    maybe_update_conversation_title_async,
)
from services.llm_service import LLMService
from state_core import migrate_legacy_session

logger = logging.getLogger(__name__)


async def hydrate_query_engine_conversation(
    query_engine: QueryEngine,
    conversation_id: str,
) -> None:
    """Resume durable state, migrating a legacy conversation once."""
    if query_engine.get_conversation(conversation_id):
        return
    if query_engine.has_durable_conversation(conversation_id):
        query_engine.resume_conversation(conversation_id)
        return
    db = SessionLocal()
    try:
        if db.get(Conversation, conversation_id) is None:
            raise HTTPException(
                status_code=404, detail=f"对话 {conversation_id} 不存在"
            )
    finally:
        db.close()
    migrate_legacy_session(conversation_id, SessionLocal)
    query_engine.resume_conversation(conversation_id)


async def iter_chat_sse(
    query_engine: QueryEngine,
    llm_service: LLMService,
    conversation_id: str,
    user_message: str,
) -> AsyncIterator[str]:
    """产出 SSE 行（含末尾 data: [DONE]）。"""
    _title_task = asyncio.create_task(
        maybe_update_conversation_title_async(
            conversation_id,
            user_message,
            llm_service=llm_service,
            provider=query_engine.provider,
        )
    )
    _title_task.add_done_callback(log_conversation_title_task_done)

    async for event in query_engine.chat_stream(conversation_id, user_message):
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    yield "data: [DONE]\n\n"
