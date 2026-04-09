"""
Agent 路由
提供 Agent 相关的API端点
"""
from typing import Optional, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from query_engine import query_engine
from agents import get_agent_manager, get_built_in_agents, get_agent_by_type
from agents.types import AgentExecutionConfig

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentTypeResponse(BaseModel):
    """Agent类型响应"""
    agent_type: str
    when_to_use: str
    tools: Optional[List[str]]
    disallowed_tools: Optional[List[str]]
    model: Optional[str]


class SpawnAgentRequest(BaseModel):
    """创建Agent请求"""
    agent_type: str
    prompt: str
    is_async: bool = False


class SpawnAgentResponse(BaseModel):
    """创建Agent响应"""
    success: bool
    agent_id: str
    message: str


class AgentStatusResponse(BaseModel):
    """Agent状态响应"""
    agent_id: str
    agent_type: str
    status: str
    tool_use_count: int
    started_at: Optional[str]
    completed_at: Optional[str]


class AgentResultResponse(BaseModel):
    """Agent结果响应"""
    agent_id: str
    content: List[dict]
    total_tool_use_count: int
    total_duration_ms: int
    total_tokens: int


@router.get("/types", response_model=List[AgentTypeResponse])
async def list_agent_types():
    """
    获取所有可用的Agent类型
    """
    agents = get_built_in_agents()
    return [
        AgentTypeResponse(
            agent_type=a.agent_type,
            when_to_use=a.when_to_use,
            tools=a.tools,
            disallowed_tools=a.disallowed_tools,
            model=a.model,
        )
        for a in agents
    ]


@router.get("/types/{agent_type}", response_model=AgentTypeResponse)
async def get_agent_type(agent_type: str):
    """
    获取特定Agent类型的详细信息
    """
    agent = get_agent_by_type(agent_type)
    if not agent:
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
    """
    创建并启动Agent

    - agent_type: Agent类型 (Explore, Plan, general-purpose, Code, Test)
    - prompt: 任务描述
    - is_async: 是否异步执行
    """
    # 验证Agent类型
    agent_def = get_agent_by_type(request.agent_type)
    if not agent_def:
        raise HTTPException(status_code=400, detail=f"Unknown agent type: {request.agent_type}")

    try:
        agent_id = await query_engine.spawn_agent(
            conversation_id="standalone",  # 独立会话
            agent_type=request.agent_type,
            prompt=request.prompt,
            is_async=request.is_async,
        )

        return SpawnAgentResponse(
            success=True,
            agent_id=agent_id,
            message=f"Agent {request.agent_type} spawned successfully",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}/status", response_model=AgentStatusResponse)
async def get_agent_status(agent_id: str):
    """
    获取Agent状态
    """
    status = query_engine.get_agent_status(agent_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    return AgentStatusResponse(**status)


@router.post("/{agent_id}/abort")
async def abort_agent(agent_id: str):
    """
    中止Agent执行
    """
    status = query_engine.get_agent_status(agent_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    query_engine.abort_agent(agent_id)

    return {
        "success": True,
        "message": f"Agent {agent_id} abort requested",
    }


@router.get("/{agent_id}/result", response_model=AgentResultResponse)
async def get_agent_result(agent_id: str):
    """
    获取Agent执行结果
    """
    manager = get_agent_manager()
    result = manager._results.get(agent_id)

    if not result:
        raise HTTPException(status_code=404, detail=f"Result for agent {agent_id} not found")

    return AgentResultResponse(
        agent_id=result.agent_id,
        content=result.content,
        total_tool_use_count=result.total_tool_use_count,
        total_duration_ms=result.total_duration_ms,
        total_tokens=result.total_tokens,
    )
