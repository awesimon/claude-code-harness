"""Plan-mode compatibility routes backed only by durable state-core."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models import get_db
from schemas import PlanCreate
from services.plan_service import PlanService

router = APIRouter(prefix="/api/plan", tags=["plan"])


class PlanModeStatusResponse(BaseModel):
    session_id: str
    is_in_plan_mode: bool
    state: str
    plan_file_path: Optional[str]
    has_plan_content: bool


class PlanContentResponse(BaseModel):
    session_id: str
    plan_content: Optional[str]
    file_path: Optional[str]
    is_edited: bool
    created_at: Optional[str]
    updated_at: Optional[str]


class ApprovePlanRequest(BaseModel):
    edited_content: Optional[str] = None


class ApprovePlanResponse(BaseModel):
    success: bool
    message: str
    plan_content: Optional[str]
    is_edited: bool


class RejectPlanRequest(BaseModel):
    reason: Optional[str] = None


class RejectPlanResponse(BaseModel):
    success: bool
    message: str
    can_continue_planning: bool


class SavePlanRequest(BaseModel):
    content: str


class SavePlanResponse(BaseModel):
    success: bool
    file_path: str
    content_length: int


@router.get("/{session_id}/status", response_model=PlanModeStatusResponse)
async def get_plan_mode_status(
    session_id: str, db: Session = Depends(get_db)
):
    service = PlanService(db)
    snapshot = service.get_mode_snapshot(session_id)
    plan = service.get_plan_by_conversation(session_id)
    return PlanModeStatusResponse(
        session_id=session_id,
        is_in_plan_mode=service.is_in_plan_mode(session_id),
        state=str(snapshot["state"]),
        plan_file_path=snapshot.get("filePath"),
        has_plan_content=bool(plan and plan.content),
    )


@router.get("/{session_id}/content", response_model=PlanContentResponse)
async def get_plan_content(session_id: str, db: Session = Depends(get_db)):
    plan = PlanService(db).get_plan_by_conversation(session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} has no plan")
    snapshot = PlanService(db).get_mode_snapshot(session_id)
    return PlanContentResponse(
        session_id=session_id,
        plan_content=plan.content,
        file_path=snapshot.get("filePath"),
        is_edited=plan.version > 1,
        created_at=plan.created_at.isoformat(),
        updated_at=plan.updated_at.isoformat(),
    )


@router.post("/{session_id}/save", response_model=SavePlanResponse)
async def save_plan(
    session_id: str, request: SavePlanRequest, db: Session = Depends(get_db)
):
    try:
        service = PlanService(db)
        plan = service.create_or_update_plan(
            PlanCreate(conversation_id=session_id, content=request.content)
        )
        path = service.get_mode_snapshot(session_id).get("filePath")
        assert path is not None
        return SavePlanResponse(
            success=True, file_path=path, content_length=len(plan.content)
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{session_id}/approve", response_model=ApprovePlanResponse)
async def approve_plan(
    session_id: str, request: ApprovePlanRequest, db: Session = Depends(get_db)
):
    try:
        return ApprovePlanResponse(
            **PlanService(db).approve_plan(session_id, request.edited_content)
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{session_id}/reject", response_model=RejectPlanResponse)
async def reject_plan(
    session_id: str, request: RejectPlanRequest, db: Session = Depends(get_db)
):
    try:
        return RejectPlanResponse(
            **PlanService(db).reject_plan(session_id, request.reason)
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{session_id}/info")
async def get_plan_mode_info(session_id: str, db: Session = Depends(get_db)):
    service = PlanService(db)
    snapshot = service.get_mode_snapshot(session_id)
    plan = service.get_plan_by_conversation(session_id)
    return {
        **snapshot,
        "content": plan.content if plan is not None else None,
    }


@router.post("/{session_id}/force-exit")
async def force_exit_plan_mode(session_id: str, db: Session = Depends(get_db)):
    try:
        return PlanService(db).force_exit_plan_mode(session_id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
