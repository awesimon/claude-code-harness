"""会话侧栏标题：LLM 概括 + DB 更新 + WebSocket 通知。"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from services import LLMService, LLMProvider, Message, ChatCompletionRequest

logger = logging.getLogger(__name__)

TITLE_PLACEHOLDERS = frozenset(
    ("new chat", "untitled", "新会话", "新对话", "聊天")
)


def derive_conversation_title(text: str, max_len: int = 80) -> str:
    """从用户首条内容推导侧栏标题（首行截断）。"""
    line = (text or "").strip().split("\n")[0].strip()
    if not line:
        return ""
    if len(line) > max_len:
        return line[: max_len - 1] + "…"
    return line


async def summarize_conversation_title_with_llm(
    llm_service: LLMService,
    provider: LLMProvider,
    user_text: str,
) -> Optional[str]:
    """
    用大模型根据用户输入生成会话标题概括。
    失败或未启用时返回 None，由调用方回退到首行截断。
    """
    snippet = (user_text or "").strip()[:4000]
    if not snippet:
        return None
    title_model = (os.getenv("CONVERSATION_TITLE_MODEL") or "").strip() or None
    system = (
        "请根据用户消息，用自然语言概括会话主题，作为聊天列表中的标题。"
        "直接输出你的概括即可，无需其它说明。"
        "十个字以内"
    )
    req = ChatCompletionRequest(
        messages=[
            Message(role="system", content=system),
            Message(role="user", content=snippet),
        ],
        model=title_model,
        temperature=0.3,
        max_tokens=2048,
        stream=False,
        tools=None,
        tool_choice=None,
        provider=provider,
    )
    try:
        logger.info(
            "Conversation title: calling LLM provider=%s model=%s",
            provider.value,
            title_model or "(default)",
        )
        resp = await llm_service.chat_completion(req)
        raw = (resp.content or "").strip()
        line = " ".join(raw.split())
        if line:
            logger.info("Conversation title: LLM returned %r", line)
        else:
            logger.info("Conversation title: LLM returned empty after sanitize")
        return line if line else None
    except Exception as e:
        logger.warning("Conversation title: LLM failed, will fall back to truncate: %s", e)
        return None


def log_conversation_title_task_done(task: asyncio.Task) -> None:
    """create_task 完成/失败时打日志，避免静默吞掉异常。"""
    if task.cancelled():
        logger.info("Conversation title: background task cancelled")
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Conversation title: background task raised",
            exc_info=exc,
        )


async def maybe_update_conversation_title_async(
    conversation_id: str,
    user_message: str,
    *,
    llm_service: LLMService,
    provider: LLMProvider,
) -> None:
    """发送用户消息后异步更新会话标题，不阻塞 SSE；完成后通过 WebSocket 推送。"""
    logger.info(
        "Conversation title: task started conversation_id=%s user_snippet=%r",
        conversation_id,
        (user_message or "")[:80] + ("…" if len(user_message or "") > 80 else ""),
    )
    try:
        from models import SessionLocal
        from services.conversation_service import ConversationService
        from schemas import ConversationUpdate
        from websocket.manager import manager, WSEventType

        mode = (os.getenv("CONVERSATION_TITLE_UPDATE_MODE") or "first").strip().lower()
        llm_title = await summarize_conversation_title_with_llm(
            llm_service, provider, user_message
        )
        new_title = llm_title or derive_conversation_title(user_message)
        if not new_title:
            logger.info("Conversation title: no title derived, skip")
            return

        db = SessionLocal()
        try:
            service = ConversationService(db)
            conv = service.get_conversation(conversation_id)
            if not conv:
                logger.info(
                    "Conversation title: no DB row for conversation_id=%s, skip",
                    conversation_id,
                )
                return
            should_update = False
            cur = (conv.title or "").strip()
            if mode == "every":
                should_update = True
            else:
                if not cur or cur.lower() in TITLE_PLACEHOLDERS:
                    should_update = True
                elif llm_title and llm_title.strip() != cur:
                    should_update = True
            if not should_update:
                logger.info(
                    "Conversation title: skip update mode=%s cur=%r new=%r source=%s",
                    mode,
                    cur,
                    new_title,
                    "llm" if llm_title else "truncate",
                )
                return
            updated = service.update_conversation(
                conversation_id,
                ConversationUpdate(title=new_title),
            )
            if not updated:
                logger.warning("Conversation title: update_conversation returned None")
                return
            updated_at = (
                updated.updated_at.isoformat() if updated.updated_at else None
            )
            n_ws = len(manager.connections.get(conversation_id, set()))
            await manager.broadcast_to_conversation(
                conversation_id,
                {
                    "type": WSEventType.CONVERSATION_UPDATED,
                    "data": {
                        "id": conversation_id,
                        "title": new_title,
                        "updated_at": updated_at,
                    },
                },
            )
            logger.info(
                "Conversation title: saved title=%r conversation_id=%s ws_subscribers=%s",
                new_title,
                conversation_id,
                n_ws,
            )
        finally:
            db.close()
    except Exception as e:
        logger.warning("Failed to update conversation title (async): %s", e)
