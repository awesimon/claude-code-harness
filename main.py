"""
FastAPI主应用模块
提供Claude Code核心功能的RESTful API接口
"""

import json
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
import logging

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# 加载环境变量（必须在其他导入之前）
load_dotenv()

from tools import (
    ToolRegistry, ReadFileTool, WriteFileTool, EditFileTool,
    GlobTool, GrepTool, BashTool, ToolResult, BashInput,
    # Agent tools
    AgentTool, AgentListTool, AgentDestroyTool,
    AgentToolInput, AgentListInput, AgentDestroyInput,
    # Task tools
    TaskGetTool, TaskCreateTool, TaskUpdateTool, TaskListTool,
    TaskGetInput, TaskCreateInput, TaskUpdateInput, TaskListInput,
    # Web tools
    WebSearchTool, WebFetchTool, WebSearchInput, WebFetchInput,
    # Team tools
    TeamCreateTool, TeamDeleteTool, TeamCreateInput, TeamDeleteInput,
    # Todo tools
    TodoWriteTool, TodoWriteInput,
    # Notebook tools
    NotebookEditTool, NotebookEditInput,
    # Plan mode tools
    EnterPlanModeTool, ExitPlanModeTool,
    # User interaction tools
    AskUserQuestionTool, AskUserQuestionInput,
)
from tools.base import ToolError
from tools.file_tools import ReadFileInput, WriteFileInput, EditFileInput
from tools.search_tools import GlobInput, GrepInput
from agent import AgentManager, Agent, Task, AgentStatus, TaskStatus, TaskType, TaskPriority
from services import LLMService, LLMProvider, Message, ChatCompletionRequest, ChatCompletionResponse
from services.config_service import config_service
from query_engine import QueryEngine, ConversationState
from routers import models_router, plan_router, agents_router
from routers.data_router import (
    conversations_router, tasks_router, plans_router, ws_router
)
from routers.team_router import teams_router
from models import init_db

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
agent_manager = AgentManager()
llm_service = LLMService()
query_engine = QueryEngine()


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


class CreateTaskRequest(BaseModel):
    description: str
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


class APIResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    message: str = ""
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


# ========== 生命周期管理 ==========

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动
    logger.info("启动Agent管理器...")
    await agent_manager.start()

    # 初始化数据库
    logger.info("初始化数据库...")
    init_db()

    yield

    # 关闭
    logger.info("停止Agent管理器...")
    await agent_manager.stop()
    await llm_service.close()


# ========== FastAPI应用 ==========

app = FastAPI(
    title="Claude Code Python API",
    description="Claude Code核心功能的Python服务端API，支持OpenAI/Anthropic LLM调用",
    version="0.3.0",
    lifespan=lifespan,
)

# Initialize database
init_db()

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

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    """返回前端页面"""
    return FileResponse("static/index.html")


@app.get("/chat.html")
async def chat_page():
    """返回Chat页面"""
    return FileResponse("static/chat.html")


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

@app.post("/agents", response_model=APIResponse)
async def create_agent(request: CreateAgentRequest):
    """创建新Agent"""
    from agent import AgentConfig, AgentCapabilities

    config = AgentConfig(
        name=request.name,
        max_concurrent_tasks=request.max_concurrent_tasks,
    )
    if request.capabilities:
        for cap in request.capabilities:
            config.tools.add(cap)

    result = await agent_manager.create_agent(config=config, agent_type="worker")

    if result.is_err():
        raise HTTPException(status_code=400, detail=result.error)

    agent = result.data
    return APIResponse(
        success=True,
        data=agent.to_dict(),
        message=f"Agent {agent.name} 创建成功",
    )


@app.get("/agents", response_model=APIResponse)
async def list_agents(
    status: Optional[str] = Query(None, description="按状态过滤: idle, busy, completed, error"),
):
    """列出所有Agent"""
    agents = list(agent_manager._agents.values())
    if status:
        agents = [a for a in agents if a.status.value == status]
    return APIResponse(
        success=True,
        data=[a.to_dict() for a in agents],
        message=f"共 {len(agents)} 个Agent",
    )


@app.get("/agents/{agent_id}", response_model=APIResponse)
async def get_agent(agent_id: str):
    """获取Agent详情"""
    agent = agent_manager._agents.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} 不存在")
    return APIResponse(
        success=True,
        data=agent.to_dict(),
    )


@app.delete("/agents/{agent_id}", response_model=APIResponse)
async def remove_agent(agent_id: str):
    """移除Agent"""
    result = await agent_manager.destroy_agent(agent_id)
    if result.is_err():
        raise HTTPException(status_code=400, detail=result.error)
    return APIResponse(
        success=True,
        message=f"Agent {agent_id} 已移除",
    )


# ========== 任务API ==========

@app.post("/tasks", response_model=APIResponse)
async def create_task(request: CreateTaskRequest):
    """创建新任务"""
    return APIResponse(
        success=True,
        data={"message": "Task creation endpoint - to be implemented with Coordinator"},
        message="任务创建接口（需要Coordinator实现）",
    )


@app.get("/tasks", response_model=APIResponse)
async def list_tasks(
    status: Optional[str] = Query(None, description="按状态过滤"),
    agent_id: Optional[str] = Query(None, description="按Agent过滤"),
):
    """列出所有任务"""
    return APIResponse(
        success=True,
        data=[],
        message="任务列表接口（需要Coordinator实现）",
    )


@app.get("/tasks/{task_id}", response_model=APIResponse)
async def get_task(task_id: str):
    """获取任务详情"""
    raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")


# ========== 统计API ==========

@app.get("/stats", response_model=APIResponse)
async def get_stats():
    """获取系统统计信息"""
    stats = {
        "agents": {
            "total": len(agent_manager._agents),
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


# ========== QueryEngine API ==========

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    type: str
    content: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


@app.post("/chat/create")
async def create_conversation():
    """创建新对话"""
    conversation_id = query_engine.create_conversation()
    return APIResponse(
        success=True,
        data={"conversation_id": conversation_id},
        message="对话创建成功"
    )


@app.post("/chat")
async def chat(request: ChatRequest):
    """发送消息并获取完整响应（非流式）"""
    conversation_id = request.conversation_id

    # 如果没有提供conversation_id，创建新对话
    if not conversation_id:
        conversation_id = query_engine.create_conversation()

    # 检查对话是否存在
    if not query_engine.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail=f"对话 {conversation_id} 不存在")

    # 收集所有事件
    events = []
    async for event in query_engine.chat(conversation_id, request.message):
        events.append(event)

    return APIResponse(
        success=True,
        data={
            "conversation_id": conversation_id,
            "events": events
        },
        message="对话完成"
    )


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """发送消息并获取流式响应（SSE）"""
    conversation_id = request.conversation_id

    if not conversation_id:
        conversation_id = query_engine.create_conversation()

    # Check if conversation exists in query_engine, if not, try to load from database
    if not query_engine.get_conversation(conversation_id):
        # Try to load from database
        from models import SessionLocal
        from services.conversation_service import ConversationService
        db = SessionLocal()
        try:
            service = ConversationService(db)
            conversation = service.get_conversation(conversation_id)
            if conversation:
                # Create conversation in query_engine
                query_engine.create_conversation(conversation_id)
                # Load messages
                messages = service.get_messages(conversation_id)
                context = query_engine.get_conversation(conversation_id)
                if context:
                    for msg in messages:
                        from query_engine import ConversationTurn, ToolCall, ToolObservation
                        from tools.base import ToolResult

                        # 解析 tool_calls
                        tool_calls = None
                        if msg.tool_calls:
                            tool_calls = [
                                ToolCall(
                                    id=tc.get("id", ""),
                                    name=tc.get("name", ""),
                                    arguments=tc.get("arguments", {})
                                )
                                for tc in msg.tool_calls
                            ]

                        # 解析 tool_results
                        tool_observations = None
                        if msg.tool_results:
                            tool_observations = []
                            for tr in msg.tool_results:
                                result_data = tr.get("result", {})
                                # 创建 ToolResult
                                result = ToolResult(
                                    success=tr.get("success", False),
                                    data=result_data,
                                    message="",
                                    error=None
                                )
                                tool_observations.append(
                                    ToolObservation(
                                        tool_call_id=tr.get("tool_call_id", ""),
                                        name=tr.get("name", ""),
                                        result=result,
                                        execution_time=tr.get("execution_time", 0)
                                    )
                                )

                        context.messages.append(ConversationTurn(
                            role=msg.role,
                            content=msg.content,
                            tool_calls=tool_calls,
                            tool_observations=tool_observations
                        ))
            else:
                raise HTTPException(status_code=404, detail=f"对话 {conversation_id} 不存在")
        finally:
            db.close()

    async def generate():
        # Track state for saving messages
        assistant_content = ""
        assistant_thinking = ""
        current_tool_calls = []
        current_tool_results = []
        has_user_message_saved = False
        assistant_message_saved = False  # Track if assistant message has been saved

        from schemas import MessageCreate

        def flush_pre_tool_assistant():
            """工具执行完成后落库：助手先说的话 + 工具调用/结果；清空缓冲供下一轮助手续写。"""
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
                logger.error(f"Failed to save pre-tool assistant: {e}")
            finally:
                db.close()
            assistant_content = ""
            assistant_thinking = ""
            current_tool_calls = []
            current_tool_results = []

        async for event in query_engine.chat_stream(conversation_id, request.message):
            # Save user message on first event
            if not has_user_message_saved:
                db = SessionLocal()
                try:
                    service = ConversationService(db)
                    # Check if conversation exists in DB, if not create it
                    conv = service.get_conversation(conversation_id)
                    if not conv:
                        from schemas import ConversationCreate
                        service.create_conversation(ConversationCreate())
                    # Save user message
                    service.add_message(conversation_id, MessageCreate(
                        role="user",
                        content=request.message
                    ))
                except Exception as e:
                    logger.error(f"Failed to save user message: {e}")
                finally:
                    db.close()
                has_user_message_saved = True

            event_type = event.get("type")

            # Track thinking content
            if event_type == "thinking":
                thinking_content = event.get("content", "")
                if thinking_content:
                    assistant_thinking += thinking_content

            # Track assistant content
            elif event_type == "assistant_message":
                content = event.get("content", "")
                if content:
                    assistant_content += content

                # Check if this is the final message (has finish_reason)
                finish_reason = event.get("finish_reason")
                if finish_reason and not assistant_message_saved:
                    # 仅保存「工具后」最终助手正文（工具轮次已在 observing 时落库）
                    db = SessionLocal()
                    try:
                        service = ConversationService(db)
                        service.add_message(conversation_id, MessageCreate(
                            role="assistant",
                            content=assistant_content,
                            thinking=assistant_thinking if assistant_thinking else None,
                            tool_calls=None,
                            tool_results=None,
                        ))
                        assistant_message_saved = True
                        logger.info(f"Saved assistant message with content length: {len(assistant_content)}")
                    except Exception as e:
                        logger.error(f"Failed to save assistant message: {e}")
                    finally:
                        db.close()

            # Track tool calls
            elif event_type == "tool_call":
                tool_calls = event.get("tool_calls", [])
                if tool_calls:
                    current_tool_calls = [
                        {
                            "id": tc.get("id", ""),
                            "name": tc.get("name", ""),
                            "arguments": tc.get("arguments", {})
                        }
                        for tc in tool_calls
                    ]

            # Track tool results
            elif event_type == "tool_result":
                tool_result = {
                    "tool_call_id": event.get("tool_call_id", ""),
                    "name": event.get("name", ""),
                    "success": event.get("success", False),
                    "result": event.get("result"),
                    "execution_time": event.get("execution_time", 0)
                }
                current_tool_results.append(tool_result)

            # 工具跑完后进入 observing：此时先落库「工具前」助手 + 工具，再展示后续续写
            elif event_type == "state_change" and event.get("state") == "observing":
                flush_pre_tool_assistant()

            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        # 未收到 observing 就结束流时（如 PlanMode 提前 return），补落库工具轮
        if current_tool_calls or current_tool_results:
            flush_pre_tool_assistant()

        # Ensure assistant message is saved at the end if not already saved
        if not assistant_message_saved and assistant_content:
            db = SessionLocal()
            try:
                service = ConversationService(db)
                service.add_message(conversation_id, MessageCreate(
                    role="assistant",
                    content=assistant_content,
                    thinking=assistant_thinking if assistant_thinking else None,
                    tool_calls=None,
                    tool_results=None,
                ))
                logger.info(f"Saved assistant message at end with content length: {len(assistant_content)}")
            except Exception as e:
                logger.error(f"Failed to save assistant message at end: {e}")
            finally:
                db.close()

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.get("/chat/{conversation_id}/history")
async def get_conversation_history(conversation_id: str):
    """获取对话历史"""
    history = query_engine.get_conversation_history(conversation_id)
    if history is None:
        raise HTTPException(status_code=404, detail=f"对话 {conversation_id} 不存在")

    return APIResponse(
        success=True,
        data={"history": history},
        message="获取对话历史成功"
    )


@app.delete("/chat/{conversation_id}")
async def clear_conversation(conversation_id: str):
    """清空对话"""
    query_engine.clear_conversation(conversation_id)
    return APIResponse(
        success=True,
        message=f"对话 {conversation_id} 已清空"
    )


@app.get("/chat/{conversation_id}/status")
async def get_conversation_status(conversation_id: str):
    """获取对话状态"""
    context = query_engine.get_conversation(conversation_id)
    if not context:
        raise HTTPException(status_code=404, detail=f"对话 {conversation_id} 不存在")

    return APIResponse(
        success=True,
        data={
            "conversation_id": conversation_id,
            "state": context.state.value,
            "message_count": len(context.messages)
        },
        message="获取对话状态成功"
    )


# ========== 主入口 ==========

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
