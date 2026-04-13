"""
Legacy /chat/* HTTP 路由（前端 SSE、历史等）。
依赖 app_context 中的 query_engine / llm_service。
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

import app_context
from schemas import APIResponse, LegacyChatRequest
from services.chat_stream import hydrate_query_engine_conversation, iter_chat_sse

router = APIRouter(tags=["chat-legacy"])


def _qe():
    qe = app_context.query_engine
    if qe is None:
        raise RuntimeError("app_context.query_engine is not bound")
    return qe


def _llm():
    llm = app_context.llm_service
    if llm is None:
        raise RuntimeError("app_context.llm_service is not bound")
    return llm


@router.post("/chat/create")
async def create_conversation():
    conversation_id = _qe().create_conversation()
    return APIResponse(
        success=True,
        data={"conversation_id": conversation_id},
        message="对话创建成功",
    )


@router.post("/chat")
async def chat(request: LegacyChatRequest):
    qe = _qe()
    conversation_id = request.conversation_id
    if not conversation_id:
        conversation_id = qe.create_conversation()
    if not qe.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail=f"对话 {conversation_id} 不存在")

    events = []
    async for event in qe.chat(conversation_id, request.message):
        events.append(event)

    return APIResponse(
        success=True,
        data={"conversation_id": conversation_id, "events": events},
        message="对话完成",
    )


@router.post("/chat/stream")
async def chat_stream(request: LegacyChatRequest):
    qe = _qe()
    llm = _llm()
    conversation_id = request.conversation_id
    if not conversation_id:
        conversation_id = qe.create_conversation()

    await hydrate_query_engine_conversation(qe, conversation_id)

    return StreamingResponse(
        iter_chat_sse(qe, llm, conversation_id, request.message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.get("/chat/{conversation_id}/history")
async def get_conversation_history(conversation_id: str):
    history = _qe().get_conversation_history(conversation_id)
    if history is None:
        raise HTTPException(status_code=404, detail=f"对话 {conversation_id} 不存在")
    return APIResponse(
        success=True,
        data={"history": history},
        message="获取对话历史成功",
    )


@router.delete("/chat/{conversation_id}")
async def clear_conversation(conversation_id: str):
    _qe().clear_conversation(conversation_id)
    return APIResponse(
        success=True,
        message=f"对话 {conversation_id} 已清空",
    )


@router.get("/chat/{conversation_id}/status")
async def get_conversation_status(conversation_id: str):
    context = _qe().get_conversation(conversation_id)
    if not context:
        raise HTTPException(status_code=404, detail=f"对话 {conversation_id} 不存在")
    return APIResponse(
        success=True,
        data={
            "conversation_id": conversation_id,
            "state": context.state.value,
            "message_count": len(context.messages),
        },
        message="获取对话状态成功",
    )
