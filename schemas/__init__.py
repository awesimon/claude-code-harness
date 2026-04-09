"""
Pydantic schemas for API request/response
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum


# ==================== Task Schemas ====================

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TaskBase(BaseModel):
    subject: str
    description: str
    active_form: Optional[str] = None
    owner: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    blocks: List[str] = Field(default_factory=list)
    blocked_by: List[str] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)  # Use 'meta' not 'metadata'


class TaskCreate(TaskBase):
    conversation_id: Optional[str] = None


class TaskUpdate(BaseModel):
    subject: Optional[str] = None
    description: Optional[str] = None
    active_form: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[TaskStatus] = None
    blocks: Optional[List[str]] = None
    blocked_by: Optional[List[str]] = None
    meta: Optional[Dict[str, Any]] = None


class TaskResponse(TaskBase):
    id: str
    conversation_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TaskClaimRequest(BaseModel):
    agent_id: str
    check_agent_busy: bool = False


class TaskClaimResponse(BaseModel):
    success: bool
    reason: Optional[str] = None  # 'task_not_found', 'already_claimed', 'already_resolved', 'blocked', 'agent_busy'
    task: Optional[TaskResponse] = None
    busy_with_tasks: Optional[List[str]] = None
    blocked_by_tasks: Optional[List[str]] = None


# ==================== Message Schemas ====================

class MessageBase(BaseModel):
    role: str  # 'user', 'assistant', 'system'
    content: str
    thinking: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_results: Optional[List[Dict[str, Any]]] = None


class MessageCreate(MessageBase):
    pass


class MessageResponse(MessageBase):
    id: str
    conversation_id: str
    timestamp: datetime

    class Config:
        from_attributes = True


# ==================== Conversation Schemas ====================

class ConversationBase(BaseModel):
    title: Optional[str] = None


class ConversationCreate(ConversationBase):
    pass


class ConversationUpdate(ConversationBase):
    pass


class ConversationResponse(ConversationBase):
    id: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0

    class Config:
        from_attributes = True


class ConversationDetailResponse(ConversationResponse):
    messages: List[MessageResponse] = []
    tasks: List[TaskResponse] = []


# ==================== Plan Schemas ====================

class PlanBase(BaseModel):
    content: str


class PlanCreate(PlanBase):
    conversation_id: str


class PlanUpdate(BaseModel):
    content: Optional[str] = None


class PlanResponse(PlanBase):
    id: str
    conversation_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ==================== Agent Schemas ====================

class AgentBase(BaseModel):
    name: str
    agent_type: str = "worker"
    capabilities: List[str] = Field(default_factory=list)
    max_concurrent_tasks: int = 1


class AgentCreate(AgentBase):
    pass


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    capabilities: Optional[List[str]] = None
    status: Optional[str] = None
    max_concurrent_tasks: Optional[int] = None


class AgentResponse(AgentBase):
    id: str
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AgentStatusResponse(BaseModel):
    agent_id: str
    name: str
    agent_type: Optional[str]
    status: str  # 'idle' or 'busy'
    current_tasks: List[str]


# ==================== WebSocket Schemas ====================

class WSMessage(BaseModel):
    type: str  # 'task_update', 'message', 'status_change', etc.
    data: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TaskUpdateEvent(BaseModel):
    type: str = "task_update"
    task: TaskResponse
    action: str  # 'created', 'updated', 'deleted', 'claimed'


class MessageUpdateEvent(BaseModel):
    type: str = "message_update"
    message: MessageResponse
    action: str  # 'created', 'updated'
    conversation_id: str
