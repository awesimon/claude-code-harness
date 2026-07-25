"""
FastAPI主应用模块
提供Claude Code核心功能的RESTful API接口
"""

import json
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# 加载环境变量（必须在其他导入之前）
load_dotenv()

from tools import (
    ToolRegistry, ReadFileTool, WriteFileTool, EditFileTool,
    GlobTool, GrepTool, BashTool, ToolResult, BashInput,
    # Agent tools
    # Task tools
    # Web tools
    # Team tools
    # Todo tools
    # Notebook tools
    # Plan mode tools
    # User interaction tools
)
from tools.file_tools import ReadFileInput, WriteFileInput, EditFileInput
from tools.search_tools import GlobInput, GrepInput
from agents import get_agent_by_type
from services import LLMService, LLMProvider, Message, ChatCompletionRequest
from services.config_service import config_service
from services.task_service import TaskService
from query_engine import LEGACY_AGENT_SURFACE, QueryEngine
import app_context
from routers import models_router, plan_router, agents_router, chat_legacy_router
from routers.data_router import (
    conversations_router, tasks_router, plans_router, ws_router
)
from routers.team_router import teams_router
from models import get_db, init_db
from schemas import APIResponse, TaskCreate
from state_core import AgentRecord

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 创建用于过滤health检查的中间件
class HealthCheckFilter(logging.Filter):
    """过滤health检查日志"""
    def filter(self, record):
        if hasattr(record, 'message'):
            # 过滤health端点的访问日志
            if '/health' in str(record.message):
                return False
        return True

# 应用到uvicorn访问日志
uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.addFilter(HealthCheckFilter())

# 全局实例
llm_service = LLMService()
query_engine = QueryEngine()
app_context.bind(query_engine, llm_service)


# ========== Pydantic模型 ==========

class ReadFileRequest(BaseModel):
    file_path: str
    offset: Optional[int] = None
    limit: Optional[int] = None


class WriteFileRequest(BaseModel):
    file_path: str
    content: str
    overwrite: bool = False


class EditFileRequest(BaseModel):
    file_path: str
    old_string: str
    new_string: str


class GlobRequest(BaseModel):
    pattern: str
    path: Optional[str] = None
    exclude: Optional[List[str]] = None


class GrepRequest(BaseModel):
    pattern: str
    path: Optional[str] = None
    output_mode: str = "content"
    glob: Optional[str] = None
    case_sensitive: bool = False


class BashRequest(BaseModel):
    command: str
    timeout: Optional[float] = 120.0
    description: Optional[str] = None
    working_dir: Optional[str] = None


class CreateAgentRequest(BaseModel):
    name: Optional[str] = None
    capabilities: Optional[List[str]] = None
    max_concurrent_tasks: int = 1
    description: Optional[str] = None
    prompt: Optional[str] = None
    subagent_type: Optional[str] = None
    run_in_background: bool = True
    session_id: Optional[str] = None


class CreateTaskRequest(BaseModel):
    description: str
    subject: Optional[str] = None
    conversation_id: Optional[str] = None
    task_type: str = "local_bash"
    input_data: Dict[str, Any] = Field(default_factory=dict)
    priority: str = "normal"


class ChatMessage(BaseModel):
    role: str
    content: str
    name: Optional[str] = None


class ChatCompletionRequestModel(BaseModel):
    messages: List[ChatMessage]
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    stream: bool = False
    provider: str = "openai"


# ========== 生命周期管理 ==========

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 初始化数据库
    logger.info("初始化数据库...")
    init_db()

    yield

    await llm_service.close()


# ========== FastAPI应用 ==========

app = FastAPI(
    title="Claude Code Python API",
    description="Claude Code核心功能的Python服务端API，支持OpenAI/Anthropic LLM调用",
    version="0.3.0",
    lifespan=lifespan,
)

# Register routers
app.include_router(models_router)
app.include_router(plan_router)
app.include_router(agents_router)

# Register new data routers
app.include_router(conversations_router, prefix="/api/v1")
app.include_router(tasks_router, prefix="/api/v1")
app.include_router(plans_router, prefix="/api/v1")
app.include_router(teams_router, prefix="/api/v1")
app.include_router(ws_router, prefix="/api/v1")
app.include_router(chat_legacy_router)


# ========== 工具API ==========

@app.post("/tools/read-file", response_model=APIResponse)
async def read_file(request: ReadFileRequest):
    """读取文件内容"""
    tool = ReadFileTool()

    result = await tool.run(ReadFileInput(
        file_path=request.file_path,
        offset=request.offset,
        limit=request.limit,
    ))

    return _tool_result_to_response(result)


@app.post("/tools/write-file", response_model=APIResponse)
async def write_file(request: WriteFileRequest):
    """写入文件内容"""
    tool = WriteFileTool()

    result = await tool.run(WriteFileInput(
        file_path=request.file_path,
        content=request.content,
        overwrite=request.overwrite,
    ))

    return _tool_result_to_response(result)


@app.post("/tools/edit-file", response_model=APIResponse)
async def edit_file(request: EditFileRequest):
    """编辑文件内容"""
    tool = EditFileTool()

    result = await tool.run(EditFileInput(
        file_path=request.file_path,
        old_string=request.old_string,
        new_string=request.new_string,
    ))

    return _tool_result_to_response(result)


@app.post("/tools/glob", response_model=APIResponse)
async def glob_search(request: GlobRequest):
    """Glob文件搜索"""
    tool = GlobTool()

    result = await tool.run(GlobInput(
        pattern=request.pattern,
        path=request.path,
        exclude=request.exclude,
    ))

    return _tool_result_to_response(result)


@app.post("/tools/grep", response_model=APIResponse)
async def grep_search(request: GrepRequest):
    """Grep内容搜索"""
    tool = GrepTool()

    result = await tool.run(GrepInput(
        pattern=request.pattern,
        path=request.path,
        output_mode=request.output_mode,
        glob=request.glob,
        case_sensitive=request.case_sensitive,
    ))

    return _tool_result_to_response(result)


@app.post("/tools/bash", response_model=APIResponse)
async def bash_command(request: BashRequest):
    """执行Bash命令"""
    tool = BashTool()

    result = await tool.run(BashInput(
        command=request.command,
        timeout=request.timeout,
        description=request.description,
        working_dir=request.working_dir,
    ))

    return _tool_result_to_response(result)


@app.get("/tools", response_model=APIResponse)
async def list_tools():
    """列出所有可用工具"""
    schemas = ToolRegistry.get_all_schemas()
    return APIResponse(
        success=True,
        data=schemas,
        message=f"共 {len(schemas)} 个工具",
    )


# ========== Agent API ==========

def _legacy_agent_status(record: AgentRecord) -> str:
    if record.status.value == "pending":
        return "idle"
    if record.status.value == "running":
        return "busy"
    if record.status.value == "completed":
        return "completed"
    return "error"


def _agent_data(record: AgentRecord) -> Dict[str, Any]:
    output = record.output if isinstance(record.output, dict) else {}
    metadata = record.definition_snapshot.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    compat = metadata.get("api_compat")
    compat = compat if isinstance(compat, dict) else {}
    name = str(compat.get("name") or record.description)
    agent_type = str(compat.get("type") or "worker")
    capabilities = list(compat.get("capabilities") or [])
    max_concurrent_tasks = int(compat.get("max_concurrent_tasks", 1))
    legacy_status = _legacy_agent_status(record)
    created_at = record.created_at.timestamp()
    statistics = {
        "id": record.agent_id,
        "name": name,
        "type": agent_type,
        "status": legacy_status,
        "tools": capabilities,
        "current_tasks": 0 if record.finished_at else 1,
        "completed_tasks": int(record.status.value == "completed"),
        "failed_tasks": int(legacy_status == "error"),
        "total_tasks_executed": int(record.finished_at is not None),
        "created_at": created_at,
        "last_active_at": (
            record.updated_at.timestamp() if record.updated_at else None
        ),
    }
    return {
        "id": record.agent_id,
        "name": name,
        "type": agent_type,
        "status": legacy_status,
        "agent_status": record.status.value,
        "config": {
            "description": compat.get("description"),
            "tools": capabilities,
            "max_concurrent_tasks": max_concurrent_tasks,
        },
        "statistics": statistics,
        "agent_id": record.agent_id,
        "session_id": record.root_session_id,
        "agent_type": record.agent_type,
        "capabilities": capabilities,
        "max_concurrent_tasks": max_concurrent_tasks,
        "tool_use_count": int(output.get("tool_count", 0)),
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _all_agent_records() -> List[AgentRecord]:
    return query_engine.list_durable_agents(api_surface=LEGACY_AGENT_SURFACE)


@app.post("/agents", response_model=APIResponse)
async def create_agent(request: CreateAgentRequest):
    """创建由 durable session harness 管理的 Agent。"""
    agent_type = request.subagent_type or "general-purpose"
    if get_agent_by_type(agent_type) is None:
        raise HTTPException(status_code=400, detail=f"Unknown agent type: {agent_type}")
    if request.max_concurrent_tasks < 1:
        raise HTTPException(
            status_code=400, detail="max_concurrent_tasks must be positive"
        )
    prompt = request.prompt or request.description or request.name or "Agent task"
    session_id = request.session_id or f"agent-session-{uuid.uuid4().hex}"
    name = request.name or f"worker-{session_id[-8:]}"
    description = request.description
    compat = {
        "name": name,
        "type": "worker",
        "description": description,
        "capabilities": list(request.capabilities or []),
        "max_concurrent_tasks": request.max_concurrent_tasks,
    }
    definition = get_agent_by_type(agent_type)
    assert definition is not None
    if request.capabilities is not None:
        definition = replace(definition, tools=list(request.capabilities))
    if query_engine.get_conversation(session_id) is None:
        query_engine.create_conversation(session_id)
    try:
        record = await query_engine.spawn_durable_agent(
            session_id,
            agent_type,
            prompt,
            background=request.run_in_background,
            api_surface=LEGACY_AGENT_SURFACE,
            description=description or name,
            definition=definition,
            api_metadata=compat,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return APIResponse(
        success=True,
        data=_agent_data(record),
        message=f"Agent {name} 创建成功",
    )


@app.get("/agents", response_model=APIResponse)
async def list_agents(
    status: Optional[str] = Query(None, description="按状态过滤: idle, busy, completed, error"),
):
    """列出 durable Agent。"""
    agents = _all_agent_records()
    if status:
        agents = [agent for agent in agents if _legacy_agent_status(agent) == status]
    return APIResponse(
        success=True,
        data=[_agent_data(agent) for agent in agents],
        message=f"共 {len(agents)} 个Agent",
    )


@app.get("/agents/{agent_id}", response_model=APIResponse)
async def get_agent(agent_id: str):
    """获取Agent详情"""
    agent = query_engine.get_durable_agent(
        agent_id, api_surface=LEGACY_AGENT_SURFACE
    )
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} 不存在")
    return APIResponse(
        success=True,
        data=_agent_data(agent),
    )


@app.delete("/agents/{agent_id}", response_model=APIResponse)
async def remove_agent(agent_id: str):
    """停止Agent；durable lifecycle record 保留用于恢复。"""
    record = await query_engine.stop_durable_agent(
        agent_id, api_surface=LEGACY_AGENT_SURFACE
    )
    if record is None:
        raise HTTPException(status_code=400, detail=f"Agent {agent_id} 不存在")
    return APIResponse(
        success=True,
        message=f"Agent {agent_id} 已移除",
    )


# ========== 任务API ==========

@app.post("/tasks", response_model=APIResponse)
async def create_task(
    request: CreateTaskRequest, db: Session = Depends(get_db)
):
    """创建 durable Task V2 任务。"""
    task = TaskService(db).create_task(
        TaskCreate(
            conversation_id=request.conversation_id,
            subject=request.subject or request.description,
            description=request.description,
            meta={"task_type": request.task_type, "input_data": request.input_data},
        )
    )
    return APIResponse(
        success=True,
        data=task.__dict__,
        message="任务创建成功",
    )


@app.get("/tasks", response_model=APIResponse)
async def list_tasks(
    status: Optional[str] = Query(None, description="按状态过滤"),
    agent_id: Optional[str] = Query(None, description="按Agent过滤"),
    db: Session = Depends(get_db),
):
    """列出 durable Task V2 任务。"""
    tasks = TaskService(db).list_tasks(status=status, owner=agent_id)
    return APIResponse(
        success=True,
        data=[task.__dict__ for task in tasks],
        message=f"共 {len(tasks)} 个任务",
    )


@app.get("/tasks/{task_id}", response_model=APIResponse)
async def get_task(task_id: str, db: Session = Depends(get_db)):
    """获取任务详情"""
    task = TaskService(db).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return APIResponse(success=True, data=task.__dict__)


# ========== 统计API ==========

@app.get("/stats", response_model=APIResponse)
async def get_stats():
    """获取系统统计信息"""
    stats = {
        "agents": {
            "total": len(_all_agent_records()),
        },
        "tools": len(ToolRegistry.list_tools()),
    }
    return APIResponse(
        success=True,
        data=stats,
    )


# ========== LLM API ==========

@app.post("/llm/chat", response_model=APIResponse)
async def chat_completion(request: ChatCompletionRequestModel):
    """LLM聊天完成 - 支持OpenAI/Anthropic"""
    try:
        provider = LLMProvider(request.provider)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"不支持的provider: {request.provider}")

    messages = [Message(role=m.role, content=m.content, name=m.name) for m in request.messages]

    llm_request = ChatCompletionRequest(
        messages=messages,
        model=request.model,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        stream=request.stream,
        provider=provider,
    )

    try:
        response = await llm_service.chat_completion(llm_request)
        return APIResponse(
            success=True,
            data={
                "id": response.id,
                "model": response.model,
                "content": response.content,
                "role": response.role,
                "tool_calls": response.tool_calls,
                "usage": response.usage,
                "finish_reason": response.finish_reason,
            },
            message="LLM调用成功",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM调用失败: {str(e)}")


@app.post("/llm/chat/stream")
async def chat_completion_stream(request: ChatCompletionRequestModel):
    """LLM流式聊天完成"""
    try:
        provider = LLMProvider(request.provider)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"不支持的provider: {request.provider}")

    messages = [Message(role=m.role, content=m.content, name=m.name) for m in request.messages]

    llm_request = ChatCompletionRequest(
        messages=messages,
        model=request.model,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        stream=True,
        provider=provider,
    )

    async def generate():
        try:
            async for chunk in llm_service.chat_completion_stream(llm_request):
                data = {
                    "id": chunk.id,
                    "model": chunk.model,
                    "content": chunk.content,
                    "role": chunk.role,
                    "finish_reason": chunk.finish_reason,
                }
                yield f"data: {json.dumps(data)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
    )


@app.get("/llm/models", response_model=APIResponse)
async def list_models():
    """列出支持的模型"""
    models = {
        "openai": [
            {"id": "gpt-4o", "name": "GPT-4o", "description": "多模态旗舰模型"},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "description": "轻量级快速模型"},
            {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "description": "高性能模型"},
            {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo", "description": "经济型模型"},
        ],
        "anthropic": [
            {"id": "claude-3-opus-20240229", "name": "Claude 3 Opus", "description": "最强推理能力"},
            {"id": "claude-3-sonnet-20240229", "name": "Claude 3 Sonnet", "description": "平衡性能与速度"},
            {"id": "claude-3-haiku-20240307", "name": "Claude 3 Haiku", "description": "最快响应"},
        ],
    }
    return APIResponse(
        success=True,
        data=models,
        message="支持的模型列表",
    )


@app.get("/llm/config", response_model=APIResponse)
async def get_llm_config():
    """获取LLM配置"""
    config = config_service.config
    return APIResponse(
        success=True,
        data={
            "default_model": config.default_model,
            "default_max_tokens": config.default_max_tokens,
            "default_temperature": config.default_temperature,
            "openai_configured": bool(config.openai_api_key),
            "anthropic_configured": bool(config.anthropic_api_key),
        },
        message="LLM配置信息",
    )


# ========== 健康检查 ==========

@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "service": "claude-code-python-api", "version": "0.3.0"}


# ========== 辅助函数 ==========

def _tool_result_to_response(result: ToolResult) -> APIResponse:
    """转换工具结果为API响应"""
    return APIResponse(
        success=result.success,
        data=result.data,
        message=result.message,
        error=str(result.error) if result.error else None,
        metadata=result.metadata,
    )


# ========== 主入口 ==========

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
