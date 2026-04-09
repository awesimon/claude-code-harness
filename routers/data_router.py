"""
FastAPI routers for the new API endpoints
"""
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from typing import List, Optional

from models import get_db, init_db
from schemas import (
    TaskCreate, TaskUpdate, TaskResponse, TaskClaimRequest, TaskClaimResponse,
    ConversationCreate, ConversationUpdate, ConversationResponse, ConversationDetailResponse,
    MessageCreate, MessageResponse,
    PlanCreate, PlanUpdate, PlanResponse,
)
from services.task_service import TaskService
from services.conversation_service import ConversationService
from services.plan_service import PlanService
from websocket.manager import manager, WSEventType

# Initialize database on module load
init_db()

# Create routers
conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])
tasks_router = APIRouter(prefix="/tasks", tags=["tasks"])
plans_router = APIRouter(prefix="/plans", tags=["plans"])
ws_router = APIRouter(tags=["websocket"])


# ==================== Conversation Routes ====================

@conversations_router.post("", response_model=ConversationResponse)
async def create_conversation(
    data: ConversationCreate,
    db: Session = Depends(get_db)
):
    """Create a new conversation"""
    service = ConversationService(db)
    conversation = service.create_conversation(data)
    return conversation


@conversations_router.get("", response_model=List[ConversationResponse])
async def list_conversations(
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """List recent conversations"""
    service = ConversationService(db)
    conversations = service.list_conversations(limit)
    return conversations


@conversations_router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db)
):
    """Get conversation details with messages and tasks"""
    service = ConversationService(db)
    conversation = service.get_conversation(conversation_id)

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get messages and tasks
    messages = service.get_messages(conversation_id)
    task_service = TaskService(db)
    tasks = task_service.list_tasks(conversation_id=conversation_id)

    return ConversationDetailResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=len(messages),
        messages=messages,
        tasks=tasks
    )


@conversations_router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    data: ConversationUpdate,
    db: Session = Depends(get_db)
):
    """Update a conversation"""
    service = ConversationService(db)
    conversation = service.update_conversation(conversation_id, data)

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Broadcast update
    await manager.broadcast_to_conversation(
        conversation_id,
        {
            "type": WSEventType.CONVERSATION_UPDATED,
            "data": {"id": conversation_id, "title": conversation.title}
        }
    )

    return conversation


@conversations_router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db)
):
    """Delete a conversation"""
    service = ConversationService(db)
    success = service.delete_conversation(conversation_id)

    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"success": True, "message": "Conversation deleted"}


@conversations_router.post("/{conversation_id}/messages", response_model=MessageResponse)
async def add_message(
    conversation_id: str,
    data: MessageCreate,
    db: Session = Depends(get_db)
):
    """Add a message to a conversation"""
    service = ConversationService(db)

    try:
        message = service.add_message(conversation_id, data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Broadcast to conversation
    await manager.broadcast_to_conversation(
        conversation_id,
        {
            "type": WSEventType.MESSAGE_CREATED,
            "data": {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "timestamp": message.timestamp.isoformat()
            }
        }
    )

    return message


@conversations_router.get("/{conversation_id}/messages", response_model=List[MessageResponse])
async def get_messages(
    conversation_id: str,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get messages for a conversation"""
    service = ConversationService(db)
    messages = service.get_messages(conversation_id, limit)
    return messages


# ==================== Task Routes ====================

@tasks_router.post("", response_model=TaskResponse)
async def create_task(
    data: TaskCreate,
    db: Session = Depends(get_db)
):
    """Create a new task"""
    service = TaskService(db)
    task = service.create_task(data)

    # Broadcast to conversation if associated
    if task.conversation_id:
        await manager.broadcast_to_conversation(
            task.conversation_id,
            {
                "type": WSEventType.TASK_CREATED,
                "data": TaskResponse.model_validate(task).model_dump()
            }
        )

    return task


@tasks_router.get("", response_model=List[TaskResponse])
async def list_tasks(
    conversation_id: Optional[str] = None,
    status: Optional[str] = None,
    owner: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """List tasks with optional filters"""
    service = TaskService(db)
    tasks = service.list_tasks(conversation_id, status, owner)
    return tasks


@tasks_router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    db: Session = Depends(get_db)
):
    """Get a task by ID"""
    service = TaskService(db)
    task = service.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return task


@tasks_router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: str,
    data: TaskUpdate,
    db: Session = Depends(get_db)
):
    """Update a task"""
    service = TaskService(db)
    task = service.update_task(task_id, data)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Broadcast to conversation if associated
    if task.conversation_id:
        await manager.broadcast_to_conversation(
            task.conversation_id,
            {
                "type": WSEventType.TASK_UPDATED,
                "data": TaskResponse.model_validate(task).model_dump()
            }
        )

    return task


@tasks_router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    db: Session = Depends(get_db)
):
    """Delete a task"""
    service = TaskService(db)
    task = service.get_task(task_id)
    conversation_id = task.conversation_id if task else None

    success = service.delete_task(task_id)

    if not success:
        raise HTTPException(status_code=404, detail="Task not found")

    # Broadcast to conversation if associated
    if conversation_id:
        await manager.broadcast_to_conversation(
            conversation_id,
            {
                "type": WSEventType.TASK_DELETED,
                "data": {"id": task_id}
            }
        )

    return {"success": True, "message": "Task deleted"}


@tasks_router.post("/{task_id}/claim", response_model=TaskClaimResponse)
async def claim_task(
    task_id: str,
    data: TaskClaimRequest,
    db: Session = Depends(get_db)
):
    """Claim a task for an agent"""
    service = TaskService(db)
    result = service.claim_task(task_id, data.agent_id, data.check_agent_busy)

    # Broadcast if successful
    if result.success and result.task and result.task.conversation_id:
        await manager.broadcast_to_conversation(
            result.task.conversation_id,
            {
                "type": WSEventType.TASK_CLAIMED,
                "data": {
                    "task": result.task.model_dump(),
                    "agent_id": data.agent_id
                }
            }
        )

    return result


@tasks_router.post("/{task_id}/unassign")
async def unassign_task(
    task_id: str,
    db: Session = Depends(get_db)
):
    """Unassign a task from its owner"""
    service = TaskService(db)
    task = service.unassign_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Broadcast to conversation if associated
    if task.conversation_id:
        await manager.broadcast_to_conversation(
            task.conversation_id,
            {
                "type": WSEventType.TASK_UPDATED,
                "data": TaskResponse.model_validate(task).model_dump()
            }
        )

    return {"success": True, "task": TaskResponse.model_validate(task)}


@tasks_router.post("/{from_task_id}/block/{to_task_id}")
async def block_task(
    from_task_id: str,
    to_task_id: str,
    db: Session = Depends(get_db)
):
    """Make from_task block to_task"""
    service = TaskService(db)
    success = service.block_task(from_task_id, to_task_id)

    if not success:
        raise HTTPException(status_code=404, detail="One or both tasks not found")

    return {"success": True, "message": f"Task {from_task_id} now blocks {to_task_id}"}


@tasks_router.get("/agents/status")
async def get_agent_statuses(
    db: Session = Depends(get_db)
):
    """Get status of all agents"""
    service = TaskService(db)
    statuses = service.get_agent_statuses()
    return {"agents": statuses}


# ==================== Plan Routes ====================

@plans_router.post("", response_model=PlanResponse)
async def create_or_update_plan(
    data: PlanCreate,
    db: Session = Depends(get_db)
):
    """Create or update a plan for a conversation"""
    service = PlanService(db)

    try:
        plan = service.create_or_update_plan(data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Broadcast to conversation
    await manager.broadcast_to_conversation(
        data.conversation_id,
        {
            "type": WSEventType.PLAN_UPDATED,
            "data": PlanResponse.model_validate(plan).model_dump()
        }
    )

    return plan


@plans_router.get("/conversation/{conversation_id}", response_model=PlanResponse)
async def get_plan_by_conversation(
    conversation_id: str,
    db: Session = Depends(get_db)
):
    """Get plan for a conversation"""
    service = PlanService(db)
    plan = service.get_plan_by_conversation(conversation_id)

    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found for this conversation")

    return plan


@plans_router.get("/{plan_id}", response_model=PlanResponse)
async def get_plan(
    plan_id: str,
    db: Session = Depends(get_db)
):
    """Get a plan by ID"""
    service = PlanService(db)
    plan = service.get_plan(plan_id)

    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    return plan


@plans_router.patch("/{plan_id}", response_model=PlanResponse)
async def update_plan(
    plan_id: str,
    data: PlanUpdate,
    db: Session = Depends(get_db)
):
    """Update a plan"""
    service = PlanService(db)
    plan = service.update_plan(plan_id, data)

    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    # Broadcast to conversation
    await manager.broadcast_to_conversation(
        plan.conversation_id,
        {
            "type": WSEventType.PLAN_UPDATED,
            "data": PlanResponse.model_validate(plan).model_dump()
        }
    )

    return plan


@plans_router.delete("/{plan_id}")
async def delete_plan(
    plan_id: str,
    db: Session = Depends(get_db)
):
    """Delete a plan"""
    service = PlanService(db)
    success = service.delete_plan(plan_id)

    if not success:
        raise HTTPException(status_code=404, detail="Plan not found")

    return {"success": True, "message": "Plan deleted"}


# ==================== WebSocket Routes ====================

@ws_router.websocket("/ws/conversations/{conversation_id}")
async def websocket_conversation(
    websocket: WebSocket,
    conversation_id: str
):
    """WebSocket endpoint for conversation updates"""
    await manager.connect(websocket, conversation_id)

    try:
        while True:
            # Keep connection alive and handle client messages
            data = await websocket.receive_json()

            # Handle ping
            if data.get("type") == "ping":
                await manager.send_personal_message(websocket, {"type": "pong"})

    except WebSocketDisconnect:
        manager.disconnect(websocket, conversation_id)
    except Exception:
        manager.disconnect(websocket, conversation_id)


@ws_router.websocket("/ws")
async def websocket_global(websocket: WebSocket):
    """WebSocket endpoint for global updates"""
    await manager.connect(websocket)

    try:
        while True:
            data = await websocket.receive_json()

            if data.get("type") == "ping":
                await manager.send_personal_message(websocket, {"type": "pong"})

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
