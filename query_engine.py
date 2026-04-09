"""
QueryEngine - 核心对话引擎
实现 LLM → Tool → Observation → LLM 的闭环
"""

import json
import os
import asyncio
from typing import List, Dict, Any, Optional, AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import Enum
import logging

from services import LLMService, LLMProvider, Message, ChatCompletionRequest
from tools import ToolRegistry
from tools.base import ToolResult
from plan import get_plan_mode_manager, PlanModeState, NotInPlanModeError
from agents import get_agent_manager, AgentExecutionConfig

# 系统提示词 - 定义AI助手的行为和能力
SYSTEM_PROMPT = """You are Claude Code, a powerful AI coding assistant created by Anthropic.

Your goal is to help users with software engineering tasks by:
1. Understanding their requests thoroughly
2. Using available tools to explore, analyze, and modify code
3. Providing clear explanations and reasoning
4. Following best practices for software development

When using tools:
- Always think step by step about what you need to do
- Use file tools to read and understand code before making changes
- Use bash tools to run commands when necessary
- Use search tools to find relevant code
- Explain your actions and reasoning to the user

Be proactive but careful:
- Ask for clarification if the request is ambiguous
- Validate your understanding before making destructive changes
- Provide code examples when helpful
- Consider edge cases and potential issues

You have access to a wide range of tools including:
- File operations (read, write, edit)
- Code search (glob, grep)
- Command execution (bash)
- Web search and fetch
- Agent management for complex tasks
- Plan mode for complex implementation tasks
- And more...

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
        return cls(
            id=tool_call_dict["id"],
            name=tool_call_dict["function"]["name"],
            arguments=json.loads(tool_call_dict["function"]["arguments"]),
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
    timestamp: float = field(default_factory=lambda: asyncio.get_event_loop().time())


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
                msg = Message(
                    role="assistant",
                    content=turn.content,
                    tool_calls=[{
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    } for tc in turn.tool_calls]
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
        """格式化工具结果为字符串"""
        if isinstance(data, str):
            return data
        try:
            return json.dumps(data, ensure_ascii=False, indent=2)
        except:
            return str(data)


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
    ):
        self.llm_service = llm_service or LLMService()
        self.max_iterations = max_iterations
        # 从环境变量读取默认 provider
        self.provider = provider or self._get_default_provider()
        self.model = model or os.getenv("DEFAULT_MODEL")
        self._conversations: Dict[str, ConversationContext] = {}
        self._state_callbacks: List[Callable[[str, ConversationState, ConversationState], None]] = []
        self._plan_mode_manager = get_plan_mode_manager()
        self._agent_manager = get_agent_manager()

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
        logger.info(f"Created conversation: {cid}")
        return cid

    def get_conversation(self, conversation_id: str) -> Optional[ConversationContext]:
        """获取对话上下文"""
        return self._conversations.get(conversation_id)

    def delete_conversation(self, conversation_id: str):
        """删除对话"""
        if conversation_id in self._conversations:
            del self._conversations[conversation_id]
            # 清理计划模式相关数据
            self._plan_mode_manager.clear_session(conversation_id)
            logger.info(f"Deleted conversation: {conversation_id}")

    def is_in_plan_mode(self, conversation_id: str) -> bool:
        """检查对话是否在计划模式中"""
        return self._plan_mode_manager.is_in_plan_mode(conversation_id)

    def get_plan_mode_state(self, conversation_id: str) -> PlanModeState:
        """获取计划模式状态"""
        return self._plan_mode_manager.get_state(conversation_id)

    def _filter_tools_for_plan_mode(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        在计划模式下过滤工具
        只允许只读工具：Read, Glob, Grep, Bash(只读命令)
        """
        allowed_tools = {"Read", "Glob", "Grep", "Bash", "EnterPlanMode", "ExitPlanMode", "AskUserQuestion"}

        filtered = []
        for tool in tools:
            tool_name = tool.get("function", {}).get("name", "")
            if tool_name in allowed_tools:
                filtered.append(tool)

        return filtered

    def _build_tools_schema(self, conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """构建工具 schema 列表"""
        tools = []
        for name in ToolRegistry.list_tools():
            tool = ToolRegistry.get(name)
            if tool:
                schema = tool.get_schema()
                tools.append({
                    "type": "function",
                    "function": {
                        "name": schema["name"],
                        "description": schema["description"],
                        "parameters": schema.get("parameters", {"type": "object", "properties": {}})
                    }
                })

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

        # 添加用户消息
        context.messages.append(ConversationTurn(
            role="user",
            content=user_message
        ))

        yield {"type": "user_message", "content": user_message}

        # 开始对话循环
        iteration = 0
        while iteration < self.max_iterations:
            iteration += 1
            logger.info(f"Iteration {iteration} for conversation {conversation_id}")

            self._update_state(context, ConversationState.THINKING)
            yield {"type": "state_change", "state": "thinking"}

            # 调用 LLM
            llm_messages = context.to_llm_messages()
            tools = self._build_tools_schema(conversation_id)

            try:
                response = await self.llm_service.chat_completion(
                    ChatCompletionRequest(
                        messages=llm_messages,
                        model=self.model,
                        tools=tools if tools else None,
                        tool_choice="auto" if tools else None,
                        provider=self.provider,
                    )
                )
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                self._update_state(context, ConversationState.ERROR)
                yield {"type": "error", "error": f"LLM call failed: {str(e)}"}
                return

            # 检查是否有工具调用
            if not response.tool_calls:
                # 没有工具调用，对话完成
                assistant_turn = ConversationTurn(
                    role="assistant",
                    content=response.content
                )
                context.messages.append(assistant_turn)
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
                tool_calls=tool_calls
            )
            context.messages.append(assistant_turn)

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

            observations = await self._execute_tools(tool_calls, conversation_id)

            # 检查是否是 ExitPlanMode 工具调用
            exit_plan_mode_called = any(
                tc.name == "ExitPlanMode" for tc in tool_calls
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

            tool = ToolRegistry.get(tool_call.name)
            if not tool:
                return ToolObservation(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    result=ToolResult.error(
                        Exception(f"Tool '{tool_call.name}' not found")
                    ),
                    execution_time=0
                )

            # 检查计划模式权限
            if conversation_id and self.is_in_plan_mode(conversation_id):
                # 在计划模式下，只允许只读工具
                allowed_tools = {"Read", "Glob", "Grep", "Bash", "EnterPlanMode", "ExitPlanMode", "AskUserQuestion"}
                if tool_call.name not in allowed_tools:
                    return ToolObservation(
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        result=ToolResult.error(
                            Exception(
                                f"Tool '{tool_call.name}' is not allowed in plan mode. "
                                "Only read-only tools are permitted during planning."
                            )
                        ),
                        execution_time=0
                    )

                # 对于 Bash 工具，需要额外检查命令
                if tool_call.name == "Bash":
                    args = tool_call.arguments
                    command = args.get("command", "")
                    # 检查是否是只读命令
                    write_commands = ["touch", "mkdir", "rm", "cp", "mv", ">", ">>", "|", "git add", "git commit"]
                    if any(cmd in command for cmd in write_commands):
                        return ToolObservation(
                            tool_call_id=tool_call.id,
                            name=tool_call.name,
                            result=ToolResult.error(
                                Exception(
                                    "Write operations are not allowed in plan mode. "
                                    f"Command '{command}' contains write operations."
                                )
                            ),
                            execution_time=0
                        )

            try:
                # 构建工具上下文
                tool_context = {
                    "session_id": conversation_id,
                    "current_mode": "plan" if (conversation_id and self.is_in_plan_mode(conversation_id)) else "default",
                }
                result = await tool.run(tool_call.arguments, tool_context)
            except Exception as e:
                result = ToolResult.error(
                    Exception(f"Tool execution error: {str(e)}")
                )

            execution_time = asyncio.get_event_loop().time() - start_time

            return ToolObservation(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                result=result,
                execution_time=execution_time
            )

        # 并行执行所有工具
        tasks = [execute_single(tc) for tc in tool_calls]
        observations = await asyncio.gather(*tasks)

        return list(observations)

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

        # 添加用户消息
        context.messages.append(ConversationTurn(
            role="user",
            content=user_message
        ))

        yield {"type": "user_message", "content": user_message}

        # 开始对话循环
        iteration = 0
        while iteration < self.max_iterations:
            iteration += 1
            logger.info(f"Iteration {iteration} for conversation {conversation_id}")

            self._update_state(context, ConversationState.THINKING)
            yield {"type": "state_change", "state": "thinking"}

            # 调用 LLM（流式）
            llm_messages = context.to_llm_messages()
            tools = self._build_tools_schema(conversation_id)

            try:
                # 使用流式API
                full_content = ""
                # 使用字典来累积工具调用，key是index
                tool_calls_accumulator: Dict[int, Dict[str, Any]] = {}

                async for chunk in self.llm_service.chat_completion_stream(
                    ChatCompletionRequest(
                        messages=llm_messages,
                        model=self.model,
                        tools=tools if tools else None,
                        tool_choice="auto" if tools else None,
                        provider=self.provider,
                        temperature=temperature,
                    )
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

                    # 发送内容片段
                    if chunk.content:
                        full_content += chunk.content
                        yield {
                            "type": "assistant_message",
                            "content": chunk.content,
                            "is_streaming": True
                        }

                    # 检查是否完成
                    if chunk.finish_reason:
                        break

                # 处理工具调用
                if tool_calls_accumulator:
                    # 将累积的工具调用转换为列表
                    tool_calls_buffer = [tool_calls_accumulator[i] for i in sorted(tool_calls_accumulator.keys())]
                    tool_calls = [ToolCall.from_openai(tc) for tc in tool_calls_buffer]

                    assistant_turn = ConversationTurn(
                        role="assistant",
                        content=full_content or "",
                        tool_calls=tool_calls
                    )
                    context.messages.append(assistant_turn)

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

                    observations = await self._execute_tools(tool_calls, conversation_id)

                    # 检查是否是 ExitPlanMode 工具调用
                    exit_plan_mode_called = any(
                        tc.name == "ExitPlanMode" for tc in tool_calls
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

                else:
                    # 没有工具调用，对话完成
                    assistant_turn = ConversationTurn(
                        role="assistant",
                        content=full_content
                    )
                    context.messages.append(assistant_turn)
                    self._update_state(context, ConversationState.COMPLETED)

                    yield {
                        "type": "assistant_message",
                        "content": full_content,
                        "finish_reason": "stop",
                        "is_streaming": False
                    }
                    return

            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                self._update_state(context, ConversationState.ERROR)
                yield {"type": "error", "error": f"LLM call failed: {str(e)}"}
                return

        # 达到最大迭代次数
        logger.warning(f"Max iterations ({self.max_iterations}) reached")
        self._update_state(context, ConversationState.COMPLETED)
        yield {
            "type": "assistant_message",
            "content": "（已达到最大迭代次数，对话结束）",
            "finish_reason": "max_iterations"
        }

    def get_conversation_history(self, conversation_id: str) -> Optional[List[Dict]]:
        """获取对话历史"""
        context = self._conversations.get(conversation_id)
        if not context:
            return None

        return [
            {
                "role": turn.role,
                "content": turn.content,
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in (turn.tool_calls or [])
                ] if turn.tool_calls else None,
                "timestamp": turn.timestamp
            }
            for turn in context.messages
        ]

    def get_plan_mode_info(self, conversation_id: str) -> Optional[Dict]:
        """获取计划模式信息"""
        return self._plan_mode_manager.get_session_info(conversation_id)

    async def approve_plan(self, conversation_id: str, edited_content: Optional[str] = None) -> Dict[str, Any]:
        """批准计划"""
        result = await self._plan_mode_manager.approve_plan(conversation_id, edited_content)
        return result

    async def reject_plan(self, conversation_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """拒绝计划"""
        result = await self._plan_mode_manager.reject_plan(conversation_id, reason)
        return result

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
        agent_id = await self._agent_manager.spawn_agent(
            agent_type=agent_type,
            prompt=prompt,
            parent_session_id=conversation_id,
            config=AgentExecutionConfig(is_async=is_async),
            is_async=is_async,
        )
        return agent_id

    def get_agent_status(self, agent_id: str) -> Optional[Dict]:
        """获取 Agent 状态"""
        return self._agent_manager.get_agent_status(agent_id)

    def abort_agent(self, agent_id: str):
        """中止 Agent"""
        self._agent_manager.abort_agent(agent_id)

    def clear_conversation(self, conversation_id: str):
        """清空对话历史"""
        context = self._conversations.get(conversation_id)
        if context:
            context.messages.clear()
            context.state = ConversationState.IDLE


# 全局 QueryEngine 实例
query_engine = QueryEngine()
