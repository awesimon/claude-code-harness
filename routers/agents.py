"""Agent compatibility routes backed by the durable session harness."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import app_context
from agents import get_agent_by_type, get_built_in_agents
from query_engine import API_AGENT_SURFACE
from state_core import AgentRecord

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentTypeResponse(BaseModel):
    agent_type: str
    when_to_use: str
    tools: Optional[List[str]]
    disallowed_tools: Optional[List[str]]
    model: Optional[str]


class SpawnAgentRequest(BaseModel):
    agent_type: str
    prompt: str
    is_async: bool = False


class SpawnAgentResponse(BaseModel):
    success: bool
    agent_id: str
    message: str


class AgentStatusResponse(BaseModel):
    agent_id: str
    agent_type: str
    status: str
    tool_use_count: int
    started_at: Optional[str]
    completed_at: Optional[str]


class AgentResultResponse(BaseModel):
    agent_id: str
    content: List[dict]
    total_tool_use_count: int
    total_duration_ms: int
    total_tokens: int


def _engine():
    engine = app_context.query_engine
    if engine is None:
        raise RuntimeError("app_context.query_engine is not bound")
    return engine


def _status(record: AgentRecord) -> AgentStatusResponse:
    output = record.output if isinstance(record.output, dict) else {}
    return AgentStatusResponse(
        agent_id=record.agent_id,
        agent_type=record.agent_type,
        status=record.status.value,
        tool_use_count=int(output.get("tool_count", 0)),
        started_at=record.started_at.isoformat() if record.started_at else None,
        completed_at=record.finished_at.isoformat() if record.finished_at else None,
    )


@router.get("/types", response_model=List[AgentTypeResponse])
async def list_agent_types():
    return [
        AgentTypeResponse(
            agent_type=agent.agent_type,
            when_to_use=agent.when_to_use,
            tools=agent.tools,
            disallowed_tools=agent.disallowed_tools,
            model=agent.model,
        )
        for agent in get_built_in_agents()
    ]


@router.get("/types/{agent_type}", response_model=AgentTypeResponse)
async def get_agent_type(agent_type: str):
    agent = get_agent_by_type(agent_type)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent type '{agent_type}' not found")
    return AgentTypeResponse(
        agent_type=agent.agent_type,
        when_to_use=agent.when_to_use,
        tools=agent.tools,
        disallowed_tools=agent.disallowed_tools,
        model=agent.model,
    )


@router.post("/spawn", response_model=SpawnAgentResponse)
async def spawn_agent(request: SpawnAgentRequest):
    if get_agent_by_type(request.agent_type) is None:
        raise HTTPException(
            status_code=400, detail=f"Unknown agent type: {request.agent_type}"
        )
    engine = _engine()
    session_id = "standalone"
    if engine.get_conversation(session_id) is None:
        engine.create_conversation(session_id)
    try:
        record = await engine.spawn_durable_agent(
            session_id,
            request.agent_type,
            request.prompt,
            background=request.is_async,
            api_surface=API_AGENT_SURFACE,
            api_metadata={"type": request.agent_type},
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SpawnAgentResponse(
        success=True,
        agent_id=record.agent_id,
        message=f"Agent {request.agent_type} spawned successfully",
    )


@router.get("/{agent_id}/status", response_model=AgentStatusResponse)
async def get_agent_status(agent_id: str):
    record = _engine().get_durable_agent(
        agent_id, api_surface=API_AGENT_SURFACE
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    return _status(record)


@router.post("/{agent_id}/abort")
async def abort_agent(agent_id: str):
    record = await _engine().stop_durable_agent(
        agent_id, api_surface=API_AGENT_SURFACE
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    return {
        "success": True,
        "message": f"Agent {agent_id} abort requested",
        "status": record.status.value,
    }


@router.get("/{agent_id}/result", response_model=AgentResultResponse)
async def get_agent_result(agent_id: str):
    record = _engine().get_durable_agent(
        agent_id, api_surface=API_AGENT_SURFACE
    )
    if record is None or record.output is None:
        raise HTTPException(status_code=404, detail=f"Result for agent {agent_id} not found")
    output = record.output if isinstance(record.output, dict) else {}
    duration = 0
    if record.started_at is not None and record.finished_at is not None:
        duration = int((record.finished_at - record.started_at).total_seconds() * 1000)
    return AgentResultResponse(
        agent_id=record.agent_id,
        content=list(output.get("content", [])),
        total_tool_use_count=int(output.get("tool_count", 0)),
        total_duration_ms=duration,
        total_tokens=int(record.usage.get("total_tokens", 0)),
    )
