"""
Token 恢复策略

处理 max_output_tokens 超限错误，支持自动增加token限制并恢复对话
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, List, AsyncIterator
from enum import Enum, auto

from services.llm_service import ChatCompletionRequest, ChatCompletionResponse

logger = logging.getLogger(__name__)


class RecoveryAction(Enum):
    """恢复动作"""
    INCREASE_MAX_TOKENS = auto()   # 增加max_tokens
    TRUNCATE_HISTORY = auto()      # 截断对话历史
    COMPRESS_PROMPT = auto()       # 压缩提示
    SPLIT_REQUEST = auto()         # 分割请求
    ABORT = auto()                 # 中止


@dataclass
class TokenRecoveryResult:
    """Token恢复结果"""
    success: bool
    action: RecoveryAction
    message: str
    new_request: Optional[ChatCompletionRequest] = None
    original_error: Optional[Exception] = None
    attempts: int = 0
    context: Dict[str, Any] = field(default_factory=dict)


class TokenRecoveryStrategy(ABC):
    """Token恢复策略基类"""

    @abstractmethod
    async def attempt_recovery(
        self,
        error: Exception,
        request: ChatCompletionRequest,
        context: Optional[Dict[str, Any]] = None,
    ) -> TokenRecoveryResult:
        """
        尝试恢复

        Args:
            error: 原始错误
            request: 原始请求
            context: 上下文信息

        Returns:
            恢复结果
        """
        pass

    @abstractmethod
    def can_handle(self, error: Exception) -> bool:
        """检查是否能处理该错误"""
        pass


class MaxOutputTokensRecovery(TokenRecoveryStrategy):
    """
    Max output tokens 超限恢复策略

    通过逐步增加 max_tokens 来恢复，支持多个增长级别：
    - 级别1: 1.5x
    - 级别2: 2x
    - 级别3: 4x
    - 级别4: 8x（最大）
    """

    # 增长倍数
    MULTIPLIERS = [1.5, 2.0, 4.0, 8.0]
    # 最大token限制
    ABSOLUTE_MAX_TOKENS = 128000  # 对于 Claude 3.5/4，最大可达 128k

    def __init__(
        self,
        multipliers: Optional[List[float]] = None,
        absolute_max: int = ABSOLUTE_MAX_TOKENS,
    ):
        self.multipliers = multipliers or self.MULTIPLIERS
        self.absolute_max = absolute_max
        self._attempt_count: Dict[str, int] = {}  # 按会话记录尝试次数

    def can_handle(self, error: Exception) -> bool:
        """检查是否是max_output_tokens错误"""
        error_str = str(error).lower()
        return any(pattern in error_str for pattern in [
            "max_output_tokens",
            "output token limit",
            "maximum output",
        ])

    def _get_conversation_key(self, request: ChatCompletionRequest) -> str:
        """生成会话唯一键"""
        # 使用消息内容的前100个字符作为键
        if request.messages:
            first_msg = request.messages[0].content[:100] if request.messages[0].content else ""
            return f"{request.model or 'default'}:{hash(first_msg)}"
        return "default"

    def _get_current_attempt(self, request: ChatCompletionRequest) -> int:
        """获取当前尝试级别"""
        key = self._get_conversation_key(request)
        return self._attempt_count.get(key, 0)

    def _increment_attempt(self, request: ChatCompletionRequest):
        """增加尝试计数"""
        key = self._get_conversation_key(request)
        self._attempt_count[key] = self._attempt_count.get(key, 0) + 1

    def _reset_attempt(self, request: ChatCompletionRequest):
        """重置尝试计数"""
        key = self._get_conversation_key(request)
        if key in self._attempt_count:
            del self._attempt_count[key]

    async def attempt_recovery(
        self,
        error: Exception,
        request: ChatCompletionRequest,
        context: Optional[Dict[str, Any]] = None,
    ) -> TokenRecoveryResult:
        """
        尝试恢复 - 增加max_tokens
        """
        attempt = self._get_current_attempt(request)

        if attempt >= len(self.multipliers):
            # 所有级别都尝试了，仍然失败
            return TokenRecoveryResult(
                success=False,
                action=RecoveryAction.ABORT,
                message=f"All token increase levels exhausted after {attempt} attempts",
                original_error=error,
                attempts=attempt,
            )

        # 计算新的max_tokens
        current_max = request.max_tokens or 4096
        multiplier = self.multipliers[attempt]
        new_max_tokens = int(current_max * multiplier)

        # 应用绝对上限
        new_max_tokens = min(new_max_tokens, self.absolute_max)

        # 如果已经达到上限，无法继续增加
        if new_max_tokens >= self.absolute_max and current_max >= self.absolute_max:
            return TokenRecoveryResult(
                success=False,
                action=RecoveryAction.ABORT,
                message=f"Already at absolute max tokens limit ({self.absolute_max})",
                original_error=error,
                attempts=attempt,
            )

        # 创建新的请求
        new_request = ChatCompletionRequest(
            messages=request.messages,
            model=request.model,
            temperature=request.temperature,
            max_tokens=new_max_tokens,
            stream=request.stream,
            tools=request.tools,
            tool_choice=request.tool_choice,
            provider=request.provider,
        )

        self._increment_attempt(request)

        logger.info(
            f"MaxOutputTokensRecovery: Increasing max_tokens from {current_max} "
            f"to {new_max_tokens} (level {attempt + 1}/{len(self.multipliers)})"
        )

        return TokenRecoveryResult(
            success=True,
            action=RecoveryAction.INCREASE_MAX_TOKENS,
            message=f"Increased max_tokens to {new_max_tokens}",
            new_request=new_request,
            attempts=attempt + 1,
            context={
                "previous_max_tokens": current_max,
                "new_max_tokens": new_max_tokens,
                "multiplier": multiplier,
                "attempt_level": attempt + 1,
            },
        )


class TruncateHistoryRecovery(TokenRecoveryStrategy):
    """
    对话历史截断恢复策略

    当增加token限制仍然失败时，尝试截断历史对话
    """

    def __init__(
        self,
        keep_last_n: int = 2,           # 保留最后N条消息
        min_history_length: int = 4,    # 最少保留的消息数
    ):
        self.keep_last_n = keep_last_n
        self.min_history_length = min_history_length

    def can_handle(self, error: Exception) -> bool:
        """检查是否是token相关错误"""
        error_str = str(error).lower()
        return any(pattern in error_str for pattern in [
            "max_output_tokens",
            "output token limit",
            "context length",
            "prompt too long",
        ])

    async def attempt_recovery(
        self,
        error: Exception,
        request: ChatCompletionRequest,
        context: Optional[Dict[str, Any]] = None,
    ) -> TokenRecoveryResult:
        """
        尝试恢复 - 截断历史
        """
        messages = request.messages

        if len(messages) <= self.min_history_length:
            return TokenRecoveryResult(
                success=False,
                action=RecoveryAction.ABORT,
                message=f"History too short to truncate ({len(messages)} messages)",
                original_error=error,
                attempts=1,
            )

        # 保留系统消息和最后N条消息
        system_messages = [m for m in messages if m.role == "system"]
        non_system = [m for m in messages if m.role != "system"]

        if len(non_system) <= self.keep_last_n:
            return TokenRecoveryResult(
                success=False,
                action=RecoveryAction.ABORT,
                message="Not enough non-system messages to truncate",
                original_error=error,
                attempts=1,
            )

        # 保留最后N条
        kept_messages = system_messages + non_system[-self.keep_last_n:]
        truncated_count = len(messages) - len(kept_messages)

        # 创建新请求
        new_request = ChatCompletionRequest(
            messages=kept_messages,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=request.stream,
            tools=request.tools,
            tool_choice=request.tool_choice,
            provider=request.provider,
        )

        logger.info(
            f"TruncateHistoryRecovery: Truncated {truncated_count} messages, "
            f"keeping {len(kept_messages)} messages"
        )

        return TokenRecoveryResult(
            success=True,
            action=RecoveryAction.TRUNCATE_HISTORY,
            message=f"Truncated history to {len(kept_messages)} messages",
            new_request=new_request,
            attempts=1,
            context={
                "truncated_count": truncated_count,
                "kept_count": len(kept_messages),
                "original_count": len(messages),
            },
        )


class PromptCompressionRecovery(TokenRecoveryStrategy):
    """
    提示压缩恢复策略

    通过压缩过长的消息内容来减少token使用
    """

    def __init__(
        self,
        max_message_length: int = 8000,  # 单条消息最大长度
        compression_ratio: float = 0.5,   # 压缩比例
    ):
        self.max_message_length = max_message_length
        self.compression_ratio = compression_ratio

    def can_handle(self, error: Exception) -> bool:
        error_str = str(error).lower()
        return "prompt" in error_str or "context" in error_str

    async def attempt_recovery(
        self,
        error: Exception,
        request: ChatCompletionRequest,
        context: Optional[Dict[str, Any]] = None,
    ) -> TokenRecoveryResult:
        """
        尝试恢复 - 压缩长消息
        """
        original_messages = request.messages
        compressed_messages = []
        compression_stats = {"compressed_count": 0, "total_saved": 0}

        for msg in original_messages:
            content = msg.content or ""

            if len(content) > self.max_message_length:
                # 压缩长消息
                original_len = len(content)
                new_length = int(original_len * self.compression_ratio)

                # 保留开头和结尾，中间用省略号
                keep_each = new_length // 2 - 50
                compressed = (
                    content[:keep_each] +
                    f"\n... [{original_len - 2 * keep_each} characters truncated] ...\n" +
                    content[-keep_each:]
                )

                from services.llm_service import Message
                compressed_messages.append(Message(
                    role=msg.role,
                    content=compressed,
                    name=msg.name,
                    tool_calls=msg.tool_calls,
                    tool_call_id=msg.tool_call_id,
                ))

                compression_stats["compressed_count"] += 1
                compression_stats["total_saved"] += original_len - len(compressed)
            else:
                compressed_messages.append(msg)

        if compression_stats["compressed_count"] == 0:
            return TokenRecoveryResult(
                success=False,
                action=RecoveryAction.ABORT,
                message="No messages needed compression",
                original_error=error,
                attempts=1,
            )

        # 创建新请求
        new_request = ChatCompletionRequest(
            messages=compressed_messages,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=request.stream,
            tools=request.tools,
            tool_choice=request.tool_choice,
            provider=request.provider,
        )

        logger.info(
            f"PromptCompressionRecovery: Compressed {compression_stats['compressed_count']} messages, "
            f"saved {compression_stats['total_saved']} characters"
        )

        return TokenRecoveryResult(
            success=True,
            action=RecoveryAction.COMPRESS_PROMPT,
            message=f"Compressed {compression_stats['compressed_count']} long messages",
            new_request=new_request,
            attempts=1,
            context=compression_stats,
        )


@dataclass
class TokenRecoveryManager:
    """
    Token恢复管理器

    管理多个恢复策略，按优先级尝试恢复
    """

    strategies: List[TokenRecoveryStrategy] = field(default_factory=list)
    max_recovery_attempts: int = 5  # 每个请求最大恢复尝试次数

    def __post_init__(self):
        if not self.strategies:
            # 默认策略链
            self.strategies = [
                MaxOutputTokensRecovery(),
                TruncateHistoryRecovery(),
                PromptCompressionRecovery(),
            ]

    async def try_recover(
        self,
        error: Exception,
        request: ChatCompletionRequest,
        context: Optional[Dict[str, Any]] = None,
    ) -> TokenRecoveryResult:
        """
        尝试恢复token错误

        按优先级依次尝试各个策略，直到成功或所有策略都失败
        """
        total_attempts = context.get("recovery_attempts", 0) if context else 0

        if total_attempts >= self.max_recovery_attempts:
            return TokenRecoveryResult(
                success=False,
                action=RecoveryAction.ABORT,
                message=f"Max recovery attempts ({self.max_recovery_attempts}) exceeded",
                original_error=error,
                attempts=total_attempts,
            )

        for strategy in self.strategies:
            if strategy.can_handle(error):
                logger.info(f"Trying recovery strategy: {strategy.__class__.__name__}")

                result = await strategy.attempt_recovery(
                    error, request, context
                )

                result.attempts = total_attempts + 1

                if result.success:
                    logger.info(f"Recovery successful: {result.message}")
                    return result
                else:
                    logger.warning(f"Recovery failed: {result.message}")

        # 所有策略都失败
        return TokenRecoveryResult(
            success=False,
            action=RecoveryAction.ABORT,
            message="All recovery strategies failed",
            original_error=error,
            attempts=total_attempts + 1,
        )

    def add_strategy(self, strategy: TokenRecoveryStrategy, priority: int = -1):
        """添加恢复策略"""
        if priority >= 0:
            self.strategies.insert(priority, strategy)
        else:
            self.strategies.append(strategy)

    def remove_strategy(self, strategy_class: type):
        """移除指定类型的策略"""
        self.strategies = [
            s for s in self.strategies
            if not isinstance(s, strategy_class)
        ]


async def execute_with_token_recovery(
    operation: Callable[[ChatCompletionRequest], AsyncIterator[ChatCompletionResponse]],
    request: ChatCompletionRequest,
    recovery_manager: Optional[TokenRecoveryManager] = None,
    max_attempts: int = 5,
) -> AsyncIterator[ChatCompletionResponse]:
    """
    执行带token恢复的操作

    这是一个生成器包装器，用于流式API调用
    """
    recovery_manager = recovery_manager or TokenRecoveryManager()
    current_request = request
    context = {"recovery_attempts": 0}

    last_error = None

    while context["recovery_attempts"] < max_attempts:
        try:
            async for chunk in operation(current_request):
                yield chunk
            return  # 成功完成
        except Exception as e:
            last_error = e

            # 尝试恢复
            result = await recovery_manager.try_recover(
                e, current_request, context
            )

            if not result.success:
                # 恢复失败，抛出原始错误
                raise e

            # 更新请求和上下文
            current_request = result.new_request
            context["recovery_attempts"] = result.attempts

            logger.info(f"Retrying with recovered request (attempt {result.attempts})")

    # 超过最大尝试次数
    if last_error:
        raise last_error
