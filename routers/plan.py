"""
Plan Mode 路由
提供计划模式相关的API端点
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from query_engine import query_engine
from plan import PlanModeState, get_plan_mode_manager

router = APIRouter(prefix="/api/plan", tags=["plan"])


class PlanModeStatusResponse(BaseModel):
    """计划模式状态响应"""
    session_id: str
    is_in_plan_mode: bool
    state: str
    plan_file_path: Optional[str]
    has_plan_content: bool


class PlanContentResponse(BaseModel):
    """计划内容响应"""
    session_id: str
    plan_content: Optional[str]
    file_path: Optional[str]
    is_edited: bool
    created_at: Optional[str]
    updated_at: Optional[str]


class ApprovePlanRequest(BaseModel):
    """批准计划请求"""
    edited_content: Optional[str] = None


class ApprovePlanResponse(BaseModel):
    """批准计划响应"""
    success: bool
    message: str
    plan_content: Optional[str]
    is_edited: bool


class RejectPlanRequest(BaseModel):
    """拒绝计划请求"""
    reason: Optional[str] = None


class RejectPlanResponse(BaseModel):
    """拒绝计划响应"""
    success: bool
    message: str
    can_continue_planning: bool


class SavePlanRequest(BaseModel):
    """保存计划请求"""
    content: str


class SavePlanResponse(BaseModel):
    """保存计划响应"""
    success: bool
    file_path: str
    content_length: int


@router.get("/{session_id}/status", response_model=PlanModeStatusResponse)
async def get_plan_mode_status(session_id: str):
    """
    获取计划模式状态
    """
    state = query_engine.get_plan_mode_state(session_id)
    plan_context = query_engine.get_plan_mode_info(session_id)

    return PlanModeStatusResponse(
        session_id=session_id,
        is_in_plan_mode=state == PlanModeState.PLANNING,
        state=state.value,
        plan_file_path=plan_context.get("plan_context", {}).get("plan_file_path") if plan_context else None,
        has_plan_content=bool(plan_context.get("plan_context", {}).get("plan_content")) if plan_context else False,
    )


@router.get("/{session_id}/content", response_model=PlanContentResponse)
async def get_plan_content(session_id: str):
    """
    获取计划内容
    """
    plan_context = query_engine.get_plan_mode_info(session_id)

    if not plan_context:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    ctx = plan_context.get("plan_context", {})

    return PlanContentResponse(
        session_id=session_id,
        plan_content=ctx.get("plan_content"),
        file_path=ctx.get("plan_file_path"),
        is_edited=ctx.get("is_edited", False),
        created_at=ctx.get("created_at"),
        updated_at=ctx.get("updated_at"),
    )


@router.post("/{session_id}/save", response_model=SavePlanResponse)
async def save_plan(session_id: str, request: SavePlanRequest):
    """
    保存计划内容
    """
    manager = get_plan_mode_manager()

    try:
        result = await manager.save_plan(session_id, request.content)
        return SavePlanResponse(
            success=result["success"],
            file_path=result["file_path"],
            content_length=result["content_length"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{session_id}/approve", response_model=ApprovePlanResponse)
async def approve_plan(session_id: str, request: ApprovePlanRequest):
    """
    批准计划

    用户在查看计划后调用此端点批准计划，允许LLM开始实施。
    可选地提供编辑后的计划内容。
    """
    try:
        result = await query_engine.approve_plan(session_id, request.edited_content)
        return ApprovePlanResponse(
            success=result["success"],
            message=result["message"],
            plan_content=result.get("plan_content"),
            is_edited=result.get("is_edited", False),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{session_id}/reject", response_model=RejectPlanResponse)
async def reject_plan(session_id: str, request: RejectPlanRequest):
    """
    拒绝计划

    用户调用此端点拒绝计划，LLM将返回计划模式继续编辑。
    """
    try:
        result = await query_engine.reject_plan(session_id, request.reason)
        return RejectPlanResponse(
            success=result["success"],
            message=result["message"],
            can_continue_planning=result.get("can_continue_planning", True),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{session_id}/info")
async def get_plan_mode_info(session_id: str):
    """
    获取完整的计划模式信息
    """
    info = query_engine.get_plan_mode_info(session_id)

    if not info:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    return info


@router.post("/{session_id}/force-exit")
async def force_exit_plan_mode(session_id: str):
    """
    强制退出计划模式（管理员功能）

    用于紧急情况或测试，直接退出计划模式而不需要审批。
    """
    manager = get_plan_mode_manager()

    try:
        result = await manager.exit_plan_mode(session_id, skip_approval=True)
        return {
            "success": result["success"],
            "message": result["message"],
            "previous_mode": result.get("previous_mode"),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
