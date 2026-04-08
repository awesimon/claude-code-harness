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
            logger.info(f"Deleted conversation: {conversation_id}")

    def _build_tools_schema(self) -> List[Dict[str, Any]]:
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
            tools = self._build_tools_schema()

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

            observations = await self._execute_tools(tool_calls)

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
        tool_calls: List[ToolCall]
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

            try:
                result = await tool.run(tool_call.arguments)
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
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        流式对话（简化版，实际实现需要更复杂的SSE处理）
        """
        # 目前先使用非流式，但按token模拟流式效果
        async for event in self.chat(conversation_id, user_message, stream=False):
            yield event

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

    def clear_conversation(self, conversation_id: str):
        """清空对话历史"""
        context = self._conversations.get(conversation_id)
        if context:
            context.messages.clear()
            context.state = ConversationState.IDLE


# 全局 QueryEngine 实例
query_engine = QueryEngine()
