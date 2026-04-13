"""Legacy /chat/stream：SSE 落库与 query_engine 对话循环。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import HTTPException

from models import SessionLocal, Conversation
from query_engine import (
    ConversationTurn,
    QueryEngine,
    ToolCall,
    ToolObservation,
)
from schemas import MessageCreate
from services.conversation_service import ConversationService
from services.conversation_title import (
    log_conversation_title_task_done,
    maybe_update_conversation_title_async,
)
from services.llm_service import LLMService
from tools.base import ToolResult

logger = logging.getLogger(__name__)


async def hydrate_query_engine_conversation(
    query_engine: QueryEngine,
    conversation_id: str,
) -> None:
    """内存中无会话时从 DB 加载并灌入 query_engine。"""
    if query_engine.get_conversation(conversation_id):
        return
    db = SessionLocal()
    try:
        service = ConversationService(db)
        conversation = service.get_conversation(conversation_id)
        if conversation:
            query_engine.create_conversation(conversation_id)
            messages = service.get_messages(conversation_id)
            context = query_engine.get_conversation(conversation_id)
            if context:
                for msg in messages:
                    tool_calls = None
                    if msg.tool_calls:
                        tool_calls = [
                            ToolCall(
                                id=tc.get("id", ""),
                                name=tc.get("name", ""),
                                arguments=tc.get("arguments", {}),
                            )
                            for tc in msg.tool_calls
                        ]

                    tool_observations = None
                    if msg.tool_results:
                        tool_observations = []
                        for tr in msg.tool_results:
                            result_data = tr.get("result", {})
                            result = ToolResult(
                                success=tr.get("success", False),
                                data=result_data,
                                message="",
                                error=None,
                            )
                            tool_observations.append(
                                ToolObservation(
                                    tool_call_id=tr.get("tool_call_id", ""),
                                    name=tr.get("name", ""),
                                    result=result,
                                    execution_time=tr.get("execution_time", 0),
                                )
                            )

                    context.messages.append(
                        ConversationTurn(
                            role=msg.role,
                            content=msg.content,
                            tool_calls=tool_calls,
                            tool_observations=tool_observations,
                        )
                    )
        else:
            raise HTTPException(
                status_code=404, detail=f"对话 {conversation_id} 不存在"
            )
    finally:
        db.close()


async def iter_chat_sse(
    query_engine: QueryEngine,
    llm_service: LLMService,
    conversation_id: str,
    user_message: str,
) -> AsyncIterator[str]:
    """产出 SSE 行（含末尾 data: [DONE]）。"""
    assistant_content = ""
    assistant_thinking = ""
    current_tool_calls: list = []
    current_tool_results: list = []
    assistant_message_saved = False

    db_user = SessionLocal()
    try:
        svc_user = ConversationService(db_user)
        conv_row = svc_user.get_conversation(conversation_id)
        if not conv_row:
            db_user.add(Conversation(id=conversation_id))
            db_user.commit()
        svc_user.add_message(
            conversation_id,
            MessageCreate(role="user", content=user_message),
        )
    except Exception as e:
        logger.error("Failed to save user message: %s", e)
    finally:
        db_user.close()

    _title_task = asyncio.create_task(
        maybe_update_conversation_title_async(
            conversation_id,
            user_message,
            llm_service=llm_service,
            provider=query_engine.provider,
        )
    )
    _title_task.add_done_callback(log_conversation_title_task_done)

    def flush_pre_tool_assistant() -> None:
        nonlocal assistant_content, assistant_thinking, current_tool_calls, current_tool_results
        has_body = bool((assistant_content or "").strip())
        has_tools = bool(current_tool_calls) or bool(current_tool_results)
        if not has_body and not has_tools:
            return
        db = SessionLocal()
        try:
            service = ConversationService(db)
            service.add_message(
                conversation_id,
                MessageCreate(
                    role="assistant",
                    content=assistant_content or "",
                    thinking=assistant_thinking if assistant_thinking else None,
                    tool_calls=current_tool_calls if current_tool_calls else None,
                    tool_results=current_tool_results if current_tool_results else None,
                ),
            )
            logger.info(
                "Saved pre-tool assistant: content_len=%s tools=%s results=%s",
                len(assistant_content or ""),
                len(current_tool_calls or []),
                len(current_tool_results or []),
            )
        except Exception as e:
            logger.error("Failed to save pre-tool assistant: %s", e)
        finally:
            db.close()
        assistant_content = ""
        assistant_thinking = ""
        current_tool_calls = []
        current_tool_results = []

    async for event in query_engine.chat_stream(conversation_id, user_message):
        event_type = event.get("type")

        if event_type == "thinking":
            thinking_content = event.get("content", "")
            if thinking_content:
                assistant_thinking += thinking_content

        elif event_type == "assistant_message":
            content = event.get("content", "")
            if content:
                assistant_content += content

            finish_reason = event.get("finish_reason")
            if finish_reason and not assistant_message_saved:
                db = SessionLocal()
                try:
                    service = ConversationService(db)
                    service.add_message(
                        conversation_id,
                        MessageCreate(
                            role="assistant",
                            content=assistant_content,
                            thinking=assistant_thinking if assistant_thinking else None,
                            tool_calls=None,
                            tool_results=None,
                        ),
                    )
                    assistant_message_saved = True
                    logger.info(
                        "Saved assistant message with content length: %s",
                        len(assistant_content),
                    )
                except Exception as e:
                    logger.error("Failed to save assistant message: %s", e)
                finally:
                    db.close()

        elif event_type == "tool_call":
            tool_calls = event.get("tool_calls", [])
            if tool_calls:
                current_tool_calls = [
                    {
                        "id": tc.get("id", ""),
                        "name": tc.get("name", ""),
                        "arguments": tc.get("arguments", {}),
                    }
                    for tc in tool_calls
                ]

        elif event_type == "tool_result":
            current_tool_results.append(
                {
                    "tool_call_id": event.get("tool_call_id", ""),
                    "name": event.get("name", ""),
                    "success": event.get("success", False),
                    "result": event.get("result"),
                    "execution_time": event.get("execution_time", 0),
                }
            )

        elif event_type == "state_change" and event.get("state") == "observing":
            flush_pre_tool_assistant()

        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    if current_tool_calls or current_tool_results:
        flush_pre_tool_assistant()

    if not assistant_message_saved and assistant_content:
        db = SessionLocal()
        try:
            service = ConversationService(db)
            service.add_message(
                conversation_id,
                MessageCreate(
                    role="assistant",
                    content=assistant_content,
                    thinking=assistant_thinking if assistant_thinking else None,
                    tool_calls=None,
                    tool_results=None,
                ),
            )
            logger.info(
                "Saved assistant message at end with content length: %s",
                len(assistant_content),
            )
        except Exception as e:
            logger.error("Failed to save assistant message at end: %s", e)
        finally:
            db.close()

    yield "data: [DONE]\n\n"
