"""
QueryEngine - 核心对话引擎
实现 LLM → Tool → Observation → LLM 的闭环
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from agents import AgentRequest
from harness import (
    CompactionSummary,
    ContextControlConfig,
    ContextController,
    PermissionMode,
    SessionHarness,
    SessionHarnessFactory,
)
from harness.budget import BudgetKind
from services import ChatCompletionRequest, LLMProvider, LLMService, Message
from services.error_recovery import (
    PromptTooLongError,
    RecoveryConfig,
    RecoveryManager,
    RetryConfig,
    classify_for_user,
)

# 启动时加载所有已安装的 skills
from services.skill_manager import skill_manager
from state_core import (
    EventType,
    SessionRuntime,
    SessionRuntimeFactory,
    SQLAlchemyStateStore,
)
from state_core import (
    PlanState as PlanModeState,
)
from tools import ToolRegistry
from tools.base import ToolResult, tool_flag

skill_manager.load_all_skills()

# 系统提示词 - 定义AI助手的行为和能力
SYSTEM_PROMPT = """You are Claude Code, a powerful AI coding assistant created by Anthropic.

Your goal is to help users with software engineering tasks by:
1. Understanding their requests thoroughly
2. Using available tools to explore, analyze, and modify code
3. Providing clear explanations and reasoning
4. Following best practices for software development

When using tools:
- **CRITICAL**: When user asks about a URL or web content, you MUST use the `web_fetch` tool to get the actual content. Do NOT hallucinate or make up information about websites.
- Always think step by step about what you need to do
- Use file tools to read and understand code before making changes
- Use bash tools to run commands when necessary
- Use search tools to find relevant code
- Use `web_fetch` tool when you need to analyze a GitHub repository, documentation, or any web page
- Explain your actions and reasoning to the user

Be proactive but careful:
- Ask for clarification if the request is ambiguous
- Validate your understanding before making destructive changes
- Provide code examples when helpful
- Consider edge cases and potential issues

You have access to a wide range of tools including:

**File Operations**
- Read, Write, Edit - 文件操作
- Glob, Grep - 文件和内容搜索

**Git Operations**
- git_status - 查看仓库状态
- git_diff - 查看代码差异
- git_commit - 创建提交（自动生成提交信息）
- branch_list - 列出分支
- branch_create - 创建分支
- branch_switch - 切换分支
- branch_delete - 删除分支

**Pull Request**
- pr_list - 列出开放的 PR
- pr_view - 查看 PR 详情
- pr_diff - 查看 PR 代码差异

**Skill Management**
- skill_install - 从 Git/本地安装 Skill（动态添加工具）
- skill_list - 列出已安装的 Skills
- skill_uninstall - 卸载 Skill
- skill_enable/skill_disable - 启用/禁用 Skill

**Hooks Configuration**
- hooks_list - 列出已配置的 hooks
- hooks_add - 添加 hook
- hooks_remove - 移除 hook
- hooks_events - 列出可用的 hook 事件类型

**User Preferences**
- theme_get/theme_set - 获取/设置主题
- editor_mode_get/editor_mode_set - 获取/设置编辑器模式 (normal/vim)
- user_config_get - 获取所有用户配置

**System**
- Bash - 执行命令
- doctor - 运行系统诊断
- stats - 查看使用统计
- help - 显示帮助信息
- version - 显示版本信息

**Web**
- web_search - 网络搜索
- web_fetch - 获取网页内容

**Agent & Plan**
- Agent - 创建子代理执行复杂任务
- EnterPlanMode/ExitPlanMode - 计划模式

## Skill System

Skills are dynamically installable tool packages:
1. Install from Git: `skill_install` with Git URL
2. Install from local: `skill_install` with local path
3. Skills are stored in ~/.claude_code/skills/
4. Each skill contains:
   - skill.json (manifest)
   - skill.py (entry point with tools)
   - requirements.txt (optional dependencies)

## Git Safety Protocol

When using git tools:
- NEVER update git config
- NEVER use --no-verify or skip hooks
- ALWAYS create NEW commits, never amend unless explicitly requested
- Do not commit files with secrets (.env, credentials)

## Plan Mode

For complex tasks that require exploration and design before implementation, you can use Plan Mode:

1. Use `EnterPlanMode` when you need to:
   - Explore the codebase thoroughly before making changes
   - Design an architectural approach
   - Consider multiple implementation options
   - Get user approval before implementing

2. In Plan Mode:
   - You are in READ-ONLY mode - DO NOT write or edit files
   - Explore the codebase to understand patterns
   - Design a concrete implementation plan
   - Consider trade-offs and alternatives

3. Use `ExitPlanMode` when ready to:
   - Present your plan for user approval
   - Start implementing the approved plan

## Agent System

For complex tasks, you can spawn specialized agents:

1. Use `Agent` tool with appropriate `subagent_type`:
   - `Explore` - Fast read-only agent for searching codebases
   - `Plan` - Software architect for designing implementation plans
   - `general-purpose` - General research and multi-step tasks
   - `Code` - Code implementation tasks
   - `Test` - Writing and running tests

2. Agents run independently and return structured reports

Always respond in a helpful, clear, and professional manner."""

logger = logging.getLogger(__name__)


class ConversationState(Enum):
    """对话状态"""
    IDLE = "idle"
    THINKING = "thinking"
    TOOL_CALLING = "tool_calling"
    OBSERVING = "observing"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class ToolCall:
    """工具调用"""
    id: str
    name: str
    arguments: Dict[str, Any]

    @classmethod
    def from_openai(cls, tool_call_dict: Dict) -> "ToolCall":
        """从OpenAI格式创建"""
        function = tool_call_dict["function"]
        raw_arguments = function.get("arguments")
        arguments: Dict[str, Any]
        if isinstance(raw_arguments, dict):
            arguments = dict(raw_arguments)
        elif not raw_arguments:
            arguments = {}
        else:
            try:
                parsed = json.loads(raw_arguments)
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning(
                    "Malformed tool input JSON; deferring to tool validation: "
                    "tool=%s call_id=%s input_len=%s error=%s",
                    function.get("name", ""),
                    tool_call_dict.get("id", ""),
                    len(raw_arguments) if isinstance(raw_arguments, str) else None,
                    exc,
                )
                parsed = {}
            if isinstance(parsed, dict):
                arguments = parsed
            else:
                logger.warning(
                    "Tool input is not a JSON object; deferring to tool validation: "
                    "tool=%s call_id=%s input_type=%s",
                    function.get("name", ""),
                    tool_call_dict.get("id", ""),
                    type(parsed).__name__,
                )
                arguments = {}
        return cls(
            id=tool_call_dict["id"],
            name=function["name"],
            arguments=arguments,
        )


@dataclass
class ToolObservation:
    """工具执行结果/观察"""
    tool_call_id: str
    name: str
    result: ToolResult
    execution_time: float = 0.0


@dataclass
class ConversationTurn:
    """对话回合"""
    role: str
    content: str = ""
    tool_calls: Optional[List[ToolCall]] = None
    tool_observations: Optional[List[ToolObservation]] = None
    thinking: Optional[str] = None  # 推理/思考内容
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class ConversationContext:
    """对话上下文"""
    conversation_id: str
    messages: List[ConversationTurn] = field(default_factory=list)
    state: ConversationState = ConversationState.IDLE
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_llm_messages(self) -> List[Message]:
        """转换为LLM消息格式"""
        llm_messages = []

        # 添加系统提示词作为第一条消息
        llm_messages.append(Message(role="system", content=SYSTEM_PROMPT))

        for turn in self.messages:
            if turn.role == "assistant" and turn.tool_calls:
                # 助手消息带工具调用
                tool_calls_payload = []
                for tc in turn.tool_calls:
                    try:
                        arg_str = json.dumps(
                            tc.arguments, ensure_ascii=False, default=str, allow_nan=False
                        )
                    except (TypeError, ValueError):
                        arg_str = "{}"
                    tool_calls_payload.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": arg_str},
                    })
                msg = Message(
                    role="assistant",
                    content=turn.content,
                    tool_calls=tool_calls_payload,
                )
                llm_messages.append(msg)
            elif turn.role == "tool":
                # 工具观察消息
                for obs in (turn.tool_observations or []):
                    content = obs.result.data if obs.result.success else str(obs.result.error)
                    msg = Message(
                        role="tool",
                        content=self._format_tool_result(content),
                        tool_call_id=obs.tool_call_id,
                        name=obs.name
                    )
                    llm_messages.append(msg)
            else:
                # 普通消息
                llm_messages.append(Message(role=turn.role, content=turn.content))

        return llm_messages

    def _format_tool_result(self, data: Any) -> str:
        """格式化工具结果为字符串（过长时截断，避免偶发超出网关单条 message 限制而 400）"""
        max_chars = int(os.getenv("LLM_TOOL_RESULT_MAX_CHARS", "120000"))
        if isinstance(data, str):
            s = data
        else:
            try:
                s = json.dumps(data, ensure_ascii=False, indent=2, default=str, allow_nan=False)
            except Exception:
                s = str(data)
        if len(s) > max_chars:
            return s[:max_chars] + "\n\n[工具输出过长已截断，可通过 LLM_TOOL_RESULT_MAX_CHARS 调整上限]"
        return s


class QueryEngine:
    """
    核心对话引擎

    实现完整的对话闭环：
    1. 接收用户输入
    2. 调用 LLM 获取响应
    3. 解析 tool_calls（如果有）
    4. 并行执行工具
    5. 将结果反馈给 LLM
    6. 重复直到没有 tool_calls
    """

    def __init__(
        self,
        llm_service: Optional[LLMService] = None,
        max_iterations: int = 10,
        provider: Optional[LLMProvider] = None,
        model: Optional[str] = None,
        enable_error_recovery: bool = True,
        workspace_root: Optional[Path] = None,
        approval_callback: Optional[Callable[[Any], Any]] = None,
        tool_timeout: Optional[float] = 60.0,
        session_runtime_factory: Optional[SessionRuntimeFactory] = None,
        context_control_config: Optional[ContextControlConfig] = None,
        context_summary_callback: Optional[Callable[[List[Message]], Any]] = None,
    ):
        self.llm_service = llm_service or LLMService()
        self.max_iterations = max_iterations
        # 从环境变量读取默认 provider
        self.provider = provider or self._get_default_provider()
        self.model = model or os.getenv("DEFAULT_MODEL")
        self._conversations: Dict[str, ConversationContext] = {}
        self._state_callbacks: List[Callable[[str, ConversationState, ConversationState], None]] = []
        if session_runtime_factory is None:
            from models import SessionLocal, init_db

            init_db()
            session_runtime_factory = SessionRuntimeFactory(SQLAlchemyStateStore(SessionLocal))
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.approval_callback = approval_callback
        self.tool_timeout = tool_timeout
        self._harness_factory = SessionHarnessFactory(
            session_runtime_factory,
            tool_registry=ToolRegistry,
            workspace_root=self.workspace_root,
            approval_callback=approval_callback,
            tool_timeout=tool_timeout,
        )
        self._session_harnesses: Dict[str, SessionHarness] = {}
        self._context_control_config = context_control_config or ContextControlConfig()
        self._context_summary_callback = context_summary_callback

        # 初始化错误恢复管理器
        self._recovery_manager: Optional[RecoveryManager] = None
        if enable_error_recovery:
            recovery_config = RecoveryConfig(
                retry_config=RetryConfig(
                    max_retries=3,
                    base_delay=1.0,
                    max_delay=30.0,
                ),
                enable_token_recovery=True,
                max_token_recovery_attempts=5,
                max_total_attempts=8,
            )
            self._recovery_manager = RecoveryManager(recovery_config)

    def _get_default_provider(self) -> LLMProvider:
        """根据环境变量确定默认 provider"""
        # 检查是否强制指定了 provider
        forced_provider = os.getenv("DEFAULT_PROVIDER", "").lower()
        if forced_provider == "openai":
            return LLMProvider.OPENAI
        elif forced_provider == "anthropic":
            return LLMProvider.ANTHROPIC

        # 检查是否有 OpenAI API key
        if os.getenv("OPENAI_API_KEY"):
            return LLMProvider.OPENAI
        # 检查是否有 Anthropic API key
        elif os.getenv("ANTHROPIC_API_KEY"):
            return LLMProvider.ANTHROPIC
        # 默认使用 OpenAI
        return LLMProvider.OPENAI

    def on_state_change(
        self,
        callback: Callable[[str, ConversationState, ConversationState], None]
    ):
        """注册状态变更回调"""
        self._state_callbacks.append(callback)

    def _notify_state_change(
        self,
        conversation_id: str,
        old_state: ConversationState,
        new_state: ConversationState
    ):
        """通知状态变更"""
        for callback in self._state_callbacks:
            try:
                callback(conversation_id, old_state, new_state)
            except Exception as e:
                logger.error(f"State callback error: {e}")

    def _update_state(
        self,
        context: ConversationContext,
        new_state: ConversationState
    ):
        """更新对话状态"""
        old_state = context.state
        context.state = new_state
        self._notify_state_change(context.conversation_id, old_state, new_state)

    def create_conversation(self, conversation_id: Optional[str] = None) -> str:
        """创建新对话"""
        import uuid
        cid = conversation_id or f"conv-{uuid.uuid4().hex[:8]}"
        self._conversations[cid] = ConversationContext(conversation_id=cid)
        self._session_harnesses[cid] = self._harness_factory.create(
            cid, tool_timeout=self.tool_timeout
        )
        logger.info(f"Created conversation: {cid}")
        return cid

    def get_conversation(self, conversation_id: str) -> Optional[ConversationContext]:
        """获取对话上下文"""
        return self._conversations.get(conversation_id)

    def delete_conversation(self, conversation_id: str):
        """删除对话"""
        if conversation_id in self._conversations:
            del self._conversations[conversation_id]
            harness = self._session_harnesses.pop(conversation_id, None)
            if harness is not None:
                harness.runtime_context.cancellation.cancel()
            logger.info(f"Deleted conversation: {conversation_id}")

    def resume_conversation(self, conversation_id: str) -> str:
        """Restore the in-process handle from the durable transcript."""
        harness = self._harness_factory.resume(
            conversation_id, tool_timeout=self.tool_timeout
        )
        self._session_harnesses[conversation_id] = harness
        runtime = harness.session_runtime
        context = ConversationContext(conversation_id=conversation_id)
        for event in runtime.events():
            if event.event_type in {EventType.USER_MESSAGE, EventType.ASSISTANT_MESSAGE}:
                role = "user" if event.event_type is EventType.USER_MESSAGE else "assistant"
                context.messages.append(
                    ConversationTurn(
                        role=role,
                        content=str(event.payload.get("content") or ""),
                        thinking=event.payload.get("thinking"),
                    )
                )
            elif event.event_type is EventType.TOOL_CALL:
                if not context.messages or context.messages[-1].role != "assistant":
                    context.messages.append(ConversationTurn(role="assistant", content=""))
                turn = context.messages[-1]
                turn.tool_calls = list(turn.tool_calls or [])
                turn.tool_calls.append(
                    ToolCall(
                        id=str(event.payload.get("toolCallId") or ""),
                        name=str(event.payload.get("name") or ""),
                        arguments=event.payload.get("input") or {},
                    )
                )
            elif event.event_type is EventType.TOOL_RESULT:
                result = (
                    ToolResult.ok(event.payload.get("result"))
                    if event.payload.get("success", True)
                    else ToolResult.fail(str(event.payload.get("result") or "tool failed"))
                )
                observation = ToolObservation(
                    tool_call_id=str(event.payload.get("toolCallId") or ""),
                    name=str(event.payload.get("name") or ""),
                    result=result,
                    execution_time=0.0,
                )
                if context.messages and context.messages[-1].role == "tool":
                    context.messages[-1].tool_observations = [
                        *(context.messages[-1].tool_observations or []),
                        observation,
                    ]
                else:
                    context.messages.append(
                        ConversationTurn(role="tool", tool_observations=[observation])
                    )
        self._conversations[conversation_id] = context
        return conversation_id

    def _session_harness(self, conversation_id: str) -> SessionHarness:
        harness = self._session_harnesses.get(conversation_id)
        if harness is None:
            harness = self._harness_factory.resume(
                conversation_id, tool_timeout=self.tool_timeout
            )
            self._session_harnesses[conversation_id] = harness
        return harness

    def _session_runtime(self, conversation_id: str) -> SessionRuntime:
        return self._session_harness(conversation_id).session_runtime

    async def _prepare_model_messages(
        self,
        conversation_id: str,
        messages: List[Message],
    ) -> List[Message]:
        controller = ContextController(
            self._session_harness(conversation_id),
            config=self._context_control_config,
            summarize=self._context_summary_callback or self._summarize_context,
        )
        return await controller.prepare_messages(messages)

    async def _summarize_context(self, messages) -> CompactionSummary:
        request = ChatCompletionRequest(
            messages=[
                *messages,
                Message(
                    role="user",
                    content=(
                        "Summarize the conversation for continuation. Preserve decisions, "
                        "constraints, current work, relevant files, and unresolved tasks."
                    ),
                ),
            ],
            model=self.model,
            provider=self.provider,
            temperature=0,
        )
        response = await self.llm_service.chat_completion(request)
        return CompactionSummary(response.content, response.usage or {})

    @staticmethod
    def _classify_model_error(error: Exception) -> Dict[str, Any]:
        category = getattr(error, "category", None)
        if isinstance(category, str) and category:
            retryable = bool(getattr(error, "retryable", False))
            return {
                "type": category,
                "message": str(error),
                "retryable": retryable,
                "action": "retry" if retryable else "review_context",
            }
        return classify_for_user(error)

    def is_in_plan_mode(self, conversation_id: str) -> bool:
        """检查对话是否在计划模式中"""
        return self._session_runtime(conversation_id).state.plan.state is not PlanModeState.IDLE

    def get_plan_mode_state(self, conversation_id: str) -> PlanModeState:
        """获取计划模式状态"""
        return self._session_runtime(conversation_id).state.plan.state

    def _filter_tools_for_plan_mode(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        在计划模式下过滤工具
        只允许只读工具：Read, Glob, Grep, Bash(只读命令)
        """
        allowed_tools = {
            "read_file",
            "glob",
            "grep",
            "bash",
            "enter_plan_mode",
            "exit_plan_mode",
            "ask_user_question",
        }

        filtered = []
        for tool in tools:
            tool_name = tool.get("function", {}).get("name", "")
            if tool_name in allowed_tools:
                filtered.append(tool)

        return filtered

    def _build_tools_schema(self, conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """构建工具 schema 列表"""
        runtime = self._session_runtime(conversation_id) if conversation_id else None
        tools = []
        for name in ToolRegistry.list_tools():
            tool = ToolRegistry.get(name)
            if tool is None:
                continue
            enabled = getattr(tool, "is_enabled", None)
            if runtime is not None and callable(enabled):
                try:
                    is_enabled = enabled({"session_runtime": runtime})
                except TypeError:
                    is_enabled = enabled()
                if not is_enabled:
                    continue
            elif runtime is not None and enabled is False:
                continue
            spec = ToolRegistry.get_spec(name)
            if spec is not None:
                tools.append(spec.to_openai())

        # 如果在计划模式下，过滤工具
        if conversation_id and self.is_in_plan_mode(conversation_id):
            tools = self._filter_tools_for_plan_mode(tools)

        return tools

    async def chat(
        self,
        conversation_id: str,
        user_message: str,
        stream: bool = False,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        主对话入口

        Args:
            conversation_id: 对话ID
            user_message: 用户消息
            stream: 是否流式返回

        Yields:
            事件字典，包含不同类型的事件
        """
        context = self._conversations.get(conversation_id)
        if not context:
            yield {"type": "error", "error": f"Conversation {conversation_id} not found"}
            return
        session_runtime = self._session_runtime(conversation_id)

        # 添加用户消息（避免重复添加）
        if not context.messages or context.messages[-1].role != "user" or context.messages[-1].content != user_message:
            context.messages.append(ConversationTurn(
                role="user",
                content=user_message
            ))
            session_runtime.append_event(EventType.USER_MESSAGE, {"content": user_message})

        yield {"type": "user_message", "content": user_message}

        # 开始对话循环
        iteration = 0
        while iteration < self.max_iterations:
            iteration += 1
            logger.info(f"Iteration {iteration} for conversation {conversation_id}")

            self._update_state(context, ConversationState.THINKING)
            yield {"type": "state_change", "state": "thinking"}

            try:
                # Context control is part of the model-call boundary so its
                # failures use the same classified result path.
                llm_messages = await self._prepare_model_messages(
                    conversation_id, context.to_llm_messages()
                )
                tools = self._build_tools_schema(conversation_id)
                # 使用恢复管理器执行LLM调用
                async def complete_model_call():
                    if self._recovery_manager:
                        result = await self._recovery_manager.execute_with_recovery(
                            self._call_llm_with_recovery,
                            llm_messages,
                            tools,
                            temperature,
                        )
                        if result.success:
                            return result.response
                        raise result.final_error or Exception(result.message)
                    return await self.llm_service.chat_completion(
                        ChatCompletionRequest(
                            messages=llm_messages,
                            model=self.model,
                            tools=tools if tools else None,
                            tool_choice="auto" if tools else None,
                            provider=self.provider,
                            temperature=temperature,
                        )
                    )

                response = await self._budgeted_model_call(
                    conversation_id, complete_model_call
                )
            except Exception as e:
                # 使用错误分类提供友好的错误信息
                error_info = self._classify_model_error(e)
                logger.error(f"LLM call failed: {e} (category: {error_info['type']})")
                self._update_state(context, ConversationState.ERROR)

                # 如果是不可恢复的错误，提供更具体的建议
                if not error_info['retryable']:
                    if isinstance(e, PromptTooLongError):
                        yield {
                            "type": "error",
                            "error": "对话历史太长，请开始新对话或清空当前对话",
                            "error_category": "prompt_too_long",
                            "action": "clear_conversation"
                        }
                    else:
                        yield {
                            "type": "error",
                            "error": error_info['message'],
                            "error_category": error_info['type'],
                            "action": error_info['action']
                        }
                else:
                    yield {
                        "type": "error",
                        "error": f"LLM调用失败: {str(e)}",
                        "error_category": error_info['type'],
                        "action": error_info['action']
                    }
                return

            # 检查是否有工具调用
            if not response.tool_calls:
                # 没有工具调用，对话完成
                assistant_turn = ConversationTurn(
                    role="assistant",
                    content=response.content,
                    thinking=response.reasoning_content
                )
                context.messages.append(assistant_turn)
                session_runtime.append_event(
                    EventType.ASSISTANT_MESSAGE,
                    {"content": response.content, "thinking": response.reasoning_content},
                )
                self._update_state(context, ConversationState.COMPLETED)

                yield {
                    "type": "assistant_message",
                    "content": response.content,
                    "finish_reason": "stop"
                }
                return

            # 有工具调用
            tool_calls = [ToolCall.from_openai(tc) for tc in response.tool_calls]

            assistant_turn = ConversationTurn(
                role="assistant",
                content=response.content or "",
                tool_calls=tool_calls,
                thinking=response.reasoning_content
            )
            context.messages.append(assistant_turn)
            session_runtime.append_event(
                EventType.ASSISTANT_MESSAGE,
                {"content": response.content or "", "thinking": response.reasoning_content},
            )

            # 发送助手消息（带工具调用意图）
            yield {
                "type": "assistant_message",
                "content": response.content or "",
            }

            # 发送工具调用事件
            yield {
                "type": "tool_call",
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in tool_calls
                ]
            }

            # 执行工具
            self._update_state(context, ConversationState.TOOL_CALLING)
            yield {"type": "state_change", "state": "tool_calling"}

            observations = self._record_tool_observations(
                session_runtime,
                await self._execute_tools(tool_calls, conversation_id),
            )

            # 检查是否是 ExitPlanMode 工具调用
            exit_plan_mode_called = any(
                ToolRegistry.resolve_name(tc.name) == "exit_plan_mode" for tc in tool_calls
            )

            # 添加工具观察消息
            tool_turn = ConversationTurn(
                role="tool",
                tool_observations=observations
            )
            context.messages.append(tool_turn)

            # 发送工具结果
            for obs in observations:
                yield {
                    "type": "tool_result",
                    "tool_call_id": obs.tool_call_id,
                    "name": obs.name,
                    "success": obs.result.success,
                    "result": obs.result.data if obs.result.success else str(obs.result.error),
                    "execution_time": obs.execution_time
                }

            # 如果调用了 ExitPlanMode，处理审批流程
            if exit_plan_mode_called:
                plan_state = self.get_plan_mode_state(conversation_id)
                if plan_state == PlanModeState.PENDING_APPROVAL:
                    # 发送等待审批事件
                    yield {
                        "type": "plan_mode",
                        "event": "pending_approval",
                        "message": "Plan submitted for approval. Waiting for user..."
                    }
                    # 暂停对话循环，等待用户审批
                    return

            self._update_state(context, ConversationState.OBSERVING)
            yield {"type": "state_change", "state": "observing"}

        # 达到最大迭代次数
        logger.warning(f"Max iterations ({self.max_iterations}) reached")
        self._update_state(context, ConversationState.COMPLETED)
        yield {
            "type": "assistant_message",
            "content": "（已达到最大迭代次数，对话结束）",
            "finish_reason": "max_iterations"
        }

    async def _execute_tools(
        self,
        tool_calls: List[ToolCall],
        conversation_id: Optional[str] = None
    ) -> List[ToolObservation]:
        """
        并行执行工具调用
        """
        async def execute_single(tool_call: ToolCall) -> ToolObservation:
            start_time = asyncio.get_event_loop().time()
            mode = (
                PermissionMode.PLAN
                if conversation_id and self.is_in_plan_mode(conversation_id)
                else PermissionMode.DEFAULT
            )
            if conversation_id is None:
                raise RuntimeError("conversation_id is required for tool execution")
            harness = self._session_harness(conversation_id)
            runtime_context = replace(
                harness.runtime_context,
                permission_mode=mode,
            )
            execution = await harness.tool_runtime.execute(
                tool_call.name,
                tool_call.arguments,
                runtime_context,
                tool_call_id=tool_call.id,
            )

            execution_time = asyncio.get_event_loop().time() - start_time

            return ToolObservation(
                tool_call_id=tool_call.id,
                name=execution.tool_name,
                result=execution.result,
                execution_time=execution_time
            )

        observations: List[Optional[ToolObservation]] = [None] * len(tool_calls)
        read_batch: List[tuple[int, ToolCall]] = []

        async def flush_read_batch() -> None:
            if not read_batch:
                return
            results = await asyncio.gather(*(execute_single(call) for _, call in read_batch))
            for (index, _), observation in zip(read_batch, results):
                observations[index] = observation
            read_batch.clear()

        for index, tool_call in enumerate(tool_calls):
            tool = ToolRegistry.get(tool_call.name)
            read_only = bool(tool and tool_flag(tool, "is_read_only"))
            if read_only:
                read_batch.append((index, tool_call))
                continue
            await flush_read_batch()
            observations[index] = await execute_single(tool_call)
        await flush_read_batch()

        return [observation for observation in observations if observation is not None]

    @staticmethod
    def _record_tool_observations(
        session_runtime: SessionRuntime,
        observations: List[ToolObservation],
    ) -> List[ToolObservation]:
        """Compatibility boundary; ToolRuntime already normalized and persisted."""

        del session_runtime
        return observations

    async def chat_stream(
        self,
        conversation_id: str,
        user_message: str,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        流式对话 - 实现真正的流式输出
        """
        context = self._conversations.get(conversation_id)
        if not context:
            yield {"type": "error", "error": f"Conversation {conversation_id} not found"}
            return
        session_runtime = self._session_runtime(conversation_id)

        # 添加用户消息（避免重复添加）
        if not context.messages or context.messages[-1].role != "user" or context.messages[-1].content != user_message:
            context.messages.append(ConversationTurn(
                role="user",
                content=user_message
            ))
            session_runtime.append_event(EventType.USER_MESSAGE, {"content": user_message})

        yield {"type": "user_message", "content": user_message}

        # 开始对话循环
        iteration = 0
        while iteration < self.max_iterations:
            iteration += 1
            logger.info(f"Iteration {iteration} for conversation {conversation_id}")

            self._update_state(context, ConversationState.THINKING)
            yield {"type": "state_change", "state": "thinking"}

            try:
                llm_messages = await self._prepare_model_messages(
                    conversation_id, context.to_llm_messages()
                )
                tools = self._build_tools_schema(conversation_id)
                # 使用流式API，带错误恢复
                full_content = ""
                # 使用字典来累积工具调用，key是index
                tool_calls_accumulator: Dict[int, Dict[str, Any]] = {}

                # 创建请求
                stream_request = ChatCompletionRequest(
                    messages=llm_messages,
                    model=self.model,
                    tools=tools if tools else None,
                    tool_choice="auto" if tools else None,
                    provider=self.provider,
                    temperature=temperature,
                )

                # 使用恢复机制执行流式调用
                stream_iter = None
                if self._recovery_manager:
                    # 使用带恢复的流式执行 - 创建一个异步生成器包装器
                    async def stream_with_recovery():
                        async for chunk in self._recovery_manager.execute_stream_with_recovery(
                            self._call_llm_stream_with_recovery,
                            stream_request,
                        ):
                            yield chunk
                    stream_iter = stream_with_recovery()
                else:
                    stream_iter = self.llm_service.chat_completion_stream(stream_request)

                # 累积思考内容
                full_thinking = ""

                async for chunk in self._budgeted_model_stream(
                    conversation_id, stream_iter
                ):
                    # 检查是否是工具调用
                    if chunk.tool_calls:
                        # 累积工具调用片段（处理流式delta格式）
                        for tc in chunk.tool_calls:
                            idx = tc.get("index", 0)
                            if idx not in tool_calls_accumulator:
                                tool_calls_accumulator[idx] = {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""}
                                }
                            # 累积各个字段
                            if tc.get("id"):
                                tool_calls_accumulator[idx]["id"] = tc["id"]
                            func = tc.get("function", {})
                            if func.get("name"):
                                tool_calls_accumulator[idx]["function"]["name"] = func["name"]
                            if func.get("arguments"):
                                tool_calls_accumulator[idx]["function"]["arguments"] += func["arguments"]

                    # 发送推理内容（thinking）
                    if chunk.reasoning_content:
                        full_thinking += chunk.reasoning_content
                        yield {
                            "type": "thinking",
                            "content": chunk.reasoning_content,
                        }

                    # 发送内容片段
                    if chunk.content:
                        full_content += chunk.content
                        yield {
                            "type": "assistant_message",
                            "content": chunk.content,
                            "is_streaming": True
                        }

                    # Consume the terminal usage chunk so model budgets and
                    # traces settle with actual usage.

                # 处理工具调用
                if tool_calls_accumulator:
                    # 将累积的工具调用转换为列表
                    tool_calls_buffer = [tool_calls_accumulator[i] for i in sorted(tool_calls_accumulator.keys())]
                    tool_calls = [ToolCall.from_openai(tc) for tc in tool_calls_buffer]

                    assistant_turn = ConversationTurn(
                        role="assistant",
                        content=full_content or "",
                        tool_calls=tool_calls,
                        thinking=full_thinking if full_thinking else None
                    )
                    context.messages.append(assistant_turn)
                    session_runtime.append_event(
                        EventType.ASSISTANT_MESSAGE,
                        {"content": full_content or "", "thinking": full_thinking or None},
                    )

                    # 发送工具调用事件
                    yield {
                        "type": "tool_call",
                        "tool_calls": [
                            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                            for tc in tool_calls
                        ]
                    }

                    # 执行工具
                    self._update_state(context, ConversationState.TOOL_CALLING)
                    yield {"type": "state_change", "state": "tool_calling"}

                    observations = self._record_tool_observations(
                        session_runtime,
                        await self._execute_tools(tool_calls, conversation_id),
                    )

                    # 检查是否是 ExitPlanMode 工具调用
                    exit_plan_mode_called = any(
                        ToolRegistry.resolve_name(tc.name) == "exit_plan_mode"
                        for tc in tool_calls
                    )

                    # 添加工具观察消息
                    tool_turn = ConversationTurn(
                        role="tool",
                        tool_observations=observations
                    )
                    context.messages.append(tool_turn)

                    # 发送工具结果
                    for obs in observations:
                        yield {
                            "type": "tool_result",
                            "tool_call_id": obs.tool_call_id,
                            "name": obs.name,
                            "success": obs.result.success,
                            "result": (
                                obs.result.data
                                if obs.result.success
                                else str(obs.result.error)
                            ),
                            "execution_time": obs.execution_time
                        }

                    # 如果调用了 ExitPlanMode，处理审批流程
                    if exit_plan_mode_called:
                        plan_state = self.get_plan_mode_state(conversation_id)
                        if plan_state == PlanModeState.PENDING_APPROVAL:
                            # 发送等待审批事件
                            yield {
                                "type": "plan_mode",
                                "event": "pending_approval",
                                "message": "Plan submitted for approval. Waiting for user..."
                            }
                            # 暂停对话循环，等待用户审批
                            return

                    self._update_state(context, ConversationState.OBSERVING)
                    yield {"type": "state_change", "state": "observing"}

                else:
                    # 没有工具调用，对话完成
                    assistant_turn = ConversationTurn(
                        role="assistant",
                        content=full_content,
                        thinking=full_thinking if full_thinking else None
                    )
                    context.messages.append(assistant_turn)
                    session_runtime.append_event(
                        EventType.ASSISTANT_MESSAGE,
                        {"content": full_content, "thinking": full_thinking or None},
                    )
                    self._update_state(context, ConversationState.COMPLETED)

                    yield {
                        "type": "assistant_message",
                        "content": full_content,
                        "finish_reason": "stop",
                        "is_streaming": False
                    }
                    return

            except Exception as e:
                error_info = self._classify_model_error(e)
                logger.error(
                    "LLM call failed: %s (category: %s)", e, error_info["type"]
                )
                self._update_state(context, ConversationState.ERROR)
                yield {
                    "type": "error",
                    "error": error_info["message"],
                    "error_category": error_info["type"],
                    "action": error_info["action"],
                }
                return

        # 达到最大迭代次数
        logger.warning(f"Max iterations ({self.max_iterations}) reached")
        self._update_state(context, ConversationState.COMPLETED)
        yield {
            "type": "assistant_message",
            "content": "（已达到最大迭代次数，对话结束）",
            "finish_reason": "max_iterations"
        }

    async def _call_llm_with_recovery(
        self,
        llm_messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
    ) -> Any:
        """LLM调用包装器 - 用于错误恢复机制"""
        request = ChatCompletionRequest(
            messages=llm_messages,
            model=self.model,
            tools=tools if tools else None,
            tool_choice="auto" if tools else None,
            provider=self.provider,
            temperature=temperature,
        )
        return await self.llm_service.chat_completion(request)

    async def _budgeted_model_call(self, conversation_id: str, operation):
        harness = self._session_harness(conversation_id)
        budget = harness.budget
        turn = budget.reserve(BudgetKind.MODEL_TURNS, 1)
        try:
            async with harness.traces.span("model", self.model or "default") as span:
                response = await operation()
                span.set_usage(response.usage or {})
        except BaseException:
            turn.release()
            raise
        turn.consume()
        budget.record_model_usage(response.usage or {})
        return response

    async def _budgeted_model_stream(self, conversation_id: str, stream_iter):
        harness = self._session_harness(conversation_id)
        budget = harness.budget
        turn = budget.reserve(BudgetKind.MODEL_TURNS, 1)
        usage: dict[str, Any] = {}
        try:
            async with harness.traces.span("model", self.model or "default") as span:
                async for chunk in stream_iter:
                    if chunk.usage:
                        usage = dict(chunk.usage)
                    yield chunk
                span.set_usage(usage)
        except BaseException:
            turn.release()
            raise
        turn.consume()
        budget.record_model_usage(usage)

    async def _call_llm_stream_with_recovery(
        self,
        request: ChatCompletionRequest
    ) -> AsyncIterator[Any]:
        """
        流式LLM调用包装器 - 用于错误恢复机制
        """
        async for chunk in self.llm_service.chat_completion_stream(request):
            yield chunk

    def get_conversation_history(self, conversation_id: str) -> Optional[List[Dict]]:
        """获取对话历史"""
        context = self._conversations.get(conversation_id)
        if not context:
            return None

        return [
            {
                "role": turn.role,
                "content": turn.content,
                "thinking": turn.thinking,
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in (turn.tool_calls or [])
                ] if turn.tool_calls else None,
                "tool_observations": [
                    {
                        "tool_call_id": obs.tool_call_id,
                        "name": obs.name,
                        "success": obs.result.success,
                        "result": obs.result.data if obs.result.success else str(obs.result.error),
                        "execution_time": obs.execution_time
                    }
                    for obs in (turn.tool_observations or [])
                ] if turn.tool_observations else None,
                "timestamp": turn.timestamp
            }
            for turn in context.messages
        ]

    def get_plan_mode_info(self, conversation_id: str) -> Optional[Dict]:
        """获取计划模式信息"""
        return self._session_runtime(conversation_id).state.plan.to_dict()

    async def approve_plan(self, conversation_id: str, edited_content: Optional[str] = None) -> Dict[str, Any]:
        """批准计划"""
        runtime = self._session_runtime(conversation_id)
        if edited_content is not None and runtime.state.plan.file_path:
            Path(runtime.state.plan.file_path).write_text(edited_content, encoding="utf-8")
        runtime.approve_plan()
        runtime.exit_plan()
        return {
            "success": True,
            "state": runtime.state.plan.state.value,
            "restored_mode": runtime.state.permission_mode,
        }

    async def reject_plan(self, conversation_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """拒绝计划"""
        runtime = self._session_runtime(conversation_id)
        runtime.reject_plan()
        return {"success": True, "state": "planning", "reason": reason}

    async def spawn_agent(
        self,
        conversation_id: str,
        agent_type: str,
        prompt: str,
        is_async: bool = False
    ) -> str:
        """
        在对话中创建 Agent

        Args:
            conversation_id: 对话ID
            agent_type: Agent 类型
            prompt: 任务描述
            is_async: 是否异步执行

        Returns:
            Agent ID
        """
        harness = self._session_harness(conversation_id)
        record = await harness.agent_scheduler.spawn(
            AgentRequest(
                prompt=prompt,
                agent_type=agent_type,
                description=prompt,
                background=is_async,
                cwd=self.workspace_root,
            ),
            harness=harness,
        )
        return record.agent_id

    def _find_agent_scheduler(self, agent_id: str):
        from harness.agents import AgentNotFound, AgentOwnershipError

        for harness in self._session_harnesses.values():
            scheduler = harness.agent_scheduler
            try:
                scheduler.status(agent_id)
            except (AgentNotFound, AgentOwnershipError):
                continue
            return scheduler
        return None

    def get_agent_status(self, agent_id: str) -> Optional[Dict]:
        """获取 Agent 状态"""
        scheduler = self._find_agent_scheduler(agent_id)
        if scheduler is None:
            return None
        record = scheduler.status(agent_id)
        output = record.output if isinstance(record.output, dict) else {}
        return {
            "agent_id": record.agent_id,
            "agent_type": record.agent_type,
            "status": record.status.value,
            "tool_use_count": int(output.get("tool_count", 0)),
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "completed_at": record.finished_at.isoformat() if record.finished_at else None,
        }

    def abort_agent(self, agent_id: str):
        """中止 Agent"""
        scheduler = self._find_agent_scheduler(agent_id)
        if scheduler is None:
            return None
        task = asyncio.create_task(scheduler.stop(agent_id))

        def consume(completed: asyncio.Task[Any]) -> None:
            try:
                completed.result()
            except BaseException:
                logger.exception("Agent abort failed", exc_info=True)

        task.add_done_callback(consume)
        return task

    def clear_conversation(self, conversation_id: str):
        """清空对话历史"""
        context = self._conversations.get(conversation_id)
        if context:
            context.messages.clear()
            context.state = ConversationState.IDLE
            harness = self._session_harnesses.get(conversation_id)
            if harness is not None:
                harness.runtime_context.cancellation.cancel()
                self._session_harnesses[conversation_id] = self._harness_factory.resume(
                    conversation_id, tool_timeout=self.tool_timeout
                )


# 全局 QueryEngine 实例
query_engine = QueryEngine()
