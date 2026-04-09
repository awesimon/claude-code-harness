"""
恢复管理器

整合错误分类、重试机制和token恢复的主入口
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, List, AsyncIterator, Union
from enum import Enum, auto

from .error_types import (
    APIError,
    classify_error,
    RecoverableError,
    NonRecoverableError,
    TokenLimitError,
    PromptTooLongError,
    RateLimitError,
    ServerError,
    TimeoutError,
    NetworkError,
)
from .retry_handler import RetryConfig, ExponentialBackoff, RetryContext
from .token_recovery import (
    TokenRecoveryManager,
    TokenRecoveryResult,
    RecoveryAction,
)

logger = logging.getLogger(__name__)


class RecoveryPhase(Enum):
    """恢复阶段"""
    INITIAL = auto()
    RETRYING = auto()
    RECOVERING_TOKENS = auto()
    FAILED = auto()
    SUCCESS = auto()


@dataclass
class RecoveryResult:
    """恢复操作结果"""
    success: bool
    phase: RecoveryPhase
    message: str
    original_error: Optional[Exception] = None
    final_error: Optional[Exception] = None
    attempts: int = 0
    recovery_actions: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    response: Any = None  # 成功时的响应


@dataclass
class RecoveryConfig:
    """恢复配置"""
    # 重试配置
    retry_config: RetryConfig = field(default_factory=RetryConfig)

    # Token恢复配置
    enable_token_recovery: bool = True
    max_token_recovery_attempts: int = 5

    # 全局配置
    max_total_attempts: int = 10  # 包括重试和恢复的总尝试次数
    enable_circuit_breaker: bool = False  # 是否启用熔断
    circuit_breaker_threshold: int = 5    # 熔断阈值

    # 回调
    on_recovery_start: Optional[Callable[[Exception], None]] = None
    on_recovery_success: Optional[Callable[[RecoveryResult], None]] = None
    on_recovery_failed: Optional[Callable[[RecoveryResult], None]] = None


class CircuitBreaker:
    """熔断器 - 防止级联失败"""

    def __init__(self, threshold: int = 5, reset_timeout: float = 60.0):
        self.threshold = threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = "closed"  # closed, open, half-open

    def record_success(self):
        """记录成功"""
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self) -> bool:
        """
        记录失败
        返回是否应该触发熔断
        """
        self.failure_count += 1
        self.last_failure_time = asyncio.get_event_loop().time()

        if self.failure_count >= self.threshold:
            self.state = "open"
            return True
        return False

    def can_execute(self) -> bool:
        """检查是否可以执行操作"""
        if self.state == "closed":
            return True
        elif self.state == "open":
            # 检查是否应该进入half-open
            if self.last_failure_time:
                elapsed = asyncio.get_event_loop().time() - self.last_failure_time
                if elapsed >= self.reset_timeout:
                    self.state = "half-open"
                    return True
            return False
        else:  # half-open
            return True


class RecoveryManager:
    """
    统一的恢复管理器

    整合错误分类、重试、token恢复等功能，提供统一的错误恢复接口
    """

    def __init__(self, config: Optional[RecoveryConfig] = None):
        self.config = config or RecoveryConfig()
        self.token_recovery_manager = TokenRecoveryManager()
        self.backoff = ExponentialBackoff(self.config.retry_config)
        self.circuit_breaker = CircuitBreaker(
            threshold=self.config.circuit_breaker_threshold
        ) if self.config.enable_circuit_breaker else None

    async def execute_with_recovery(
        self,
        operation: Callable[..., Any],
        *args,
        **kwargs
    ) -> RecoveryResult:
        """
        执行操作并带完整恢复机制

        Args:
            operation: 要执行的操作
            *args, **kwargs: 传递给操作的参数

        Returns:
            RecoveryResult
        """
        # 检查熔断器
        if self.circuit_breaker and not self.circuit_breaker.can_execute():
            return RecoveryResult(
                success=False,
                phase=RecoveryPhase.FAILED,
                message="Circuit breaker is open",
                attempts=0,
            )

        total_attempts = 0
        recovery_actions = []
        last_error = None

        if self.config.on_recovery_start:
            self.config.on_recovery_start(None)

        while total_attempts < self.config.max_total_attempts:
            total_attempts += 1

            try:
                # 尝试执行
                result = await operation(*args, **kwargs)

                # 成功
                if self.circuit_breaker:
                    self.circuit_breaker.record_success()

                recovery_result = RecoveryResult(
                    success=True,
                    phase=RecoveryPhase.SUCCESS,
                    message="Operation succeeded",
                    attempts=total_attempts,
                    recovery_actions=recovery_actions,
                    response=result,
                )

                if self.config.on_recovery_success:
                    self.config.on_recovery_success(recovery_result)

                return recovery_result

            except Exception as e:
                last_error = e
                error = classify_error(e)

                logger.warning(
                    f"Attempt {total_attempts} failed: {error.category.name} - {error.message}"
                )

                # 不可恢复错误，直接失败
                if not error.retryable:
                    if self.circuit_breaker:
                        self.circuit_breaker.record_failure()

                    recovery_result = RecoveryResult(
                        success=False,
                        phase=RecoveryPhase.FAILED,
                        message=f"Non-recoverable error: {error.message}",
                        original_error=e,
                        final_error=e,
                        attempts=total_attempts,
                        recovery_actions=recovery_actions,
                    )

                    if self.config.on_recovery_failed:
                        self.config.on_recovery_failed(recovery_result)

                    return recovery_result

                # 特殊处理TokenLimitError - 尝试增加max_tokens
                if isinstance(error, TokenLimitError) and self.config.enable_token_recovery:
                    recovery_actions.append("token_recovery")

                    # 这里需要从kwargs或args中提取request
                    request = kwargs.get('request') or (args[0] if args else None)

                    if request:
                        recovery_result = await self.token_recovery_manager.try_recover(
                            e, request, {"recovery_attempts": total_attempts}
                        )

                        if recovery_result.success:
                            # 更新请求参数
                            if 'request' in kwargs:
                                kwargs['request'] = recovery_result.new_request
                            elif args:
                                args = (recovery_result.new_request,) + args[1:]

                            logger.info(f"Token recovery: {recovery_result.message}")
                            continue  # 使用新参数重试
                        else:
                            logger.error(f"Token recovery failed: {recovery_result.message}")

                # 检查是否还有重试次数
                if total_attempts >= self.config.max_total_attempts:
                    break

                # 计算退避延迟
                retry_after = getattr(error, 'retry_after', None)
                delay = self.backoff.calculate_delay(total_attempts, retry_after)

                recovery_actions.append(f"retry_delay_{delay:.1f}s")
                logger.info(f"Retrying after {delay:.2f}s...")

                await asyncio.sleep(delay)

        # 所有尝试失败
        if self.circuit_breaker:
            self.circuit_breaker.record_failure()

        recovery_result = RecoveryResult(
            success=False,
            phase=RecoveryPhase.FAILED,
            message=f"All {total_attempts} attempts failed",
            original_error=last_error,
            final_error=last_error,
            attempts=total_attempts,
            recovery_actions=recovery_actions,
        )

        if self.config.on_recovery_failed:
            self.config.on_recovery_failed(recovery_result)

        return recovery_result

    async def execute_stream_with_recovery(
        self,
        operation: Callable[..., AsyncIterator[Any]],
        *args,
        **kwargs
    ) -> AsyncIterator[Union[Any, RecoveryResult]]:
        """
        执行流式操作并带恢复机制

        生成器会在错误时尝试恢复，如果恢复失败则抛出异常
        """
        total_attempts = 0
        current_args = args
        current_kwargs = kwargs

        while total_attempts < self.config.max_total_attempts:
            total_attempts += 1
            buffer = []  # 缓冲已产生的数据

            try:
                async for chunk in operation(*current_args, **current_kwargs):
                    buffer.append(chunk)
                    yield chunk

                # 成功完成
                return

            except Exception as e:
                error = classify_error(e)
                logger.warning(
                    f"Stream attempt {total_attempts} failed: {error.category.name}"
                )

                # 不可恢复错误
                if not error.retryable:
                    raise e

                # Token恢复
                if isinstance(error, TokenLimitError) and self.config.enable_token_recovery:
                    request = current_kwargs.get('request') or (current_args[0] if current_args else None)

                    if request:
                        recovery_result = await self.token_recovery_manager.try_recover(
                            e, request, {"recovery_attempts": total_attempts}
                        )

                        if recovery_result.success:
                            if 'request' in current_kwargs:
                                current_kwargs['request'] = recovery_result.new_request
                            elif current_args:
                                current_args = (recovery_result.new_request,) + current_args[1:]

                            logger.info(f"Stream token recovery: {recovery_result.message}")
                            continue

                # 检查是否还有重试次数
                if total_attempts >= self.config.max_total_attempts:
                    raise e

                # 退避延迟
                retry_after = getattr(error, 'retry_after', None)
                delay = self.backoff.calculate_delay(total_attempts, retry_after)

                logger.info(f"Stream retry after {delay:.2f}s...")
                await asyncio.sleep(delay)

        # 超过最大尝试
        raise Exception(f"Stream failed after {total_attempts} attempts")

    def create_retry_decorator(self):
        """创建重试装饰器"""
        from .retry_handler import with_retry
        return with_retry(config=self.config.retry_config)


# 便捷函数

async def with_recovery(
    operation: Callable[..., Any],
    *args,
    config: Optional[RecoveryConfig] = None,
    **kwargs
) -> RecoveryResult:
    """
    便捷函数：使用恢复管理器执行操作

    Example:
        result = await with_recovery(
            llm_service.chat_completion,
            request,
            config=RecoveryConfig(max_total_attempts=5)
        )
    """
    manager = RecoveryManager(config)
    return await manager.execute_with_recovery(operation, *args, **kwargs)


def classify_for_user(error: Exception) -> Dict[str, Any]:
    """
    为用户界面分类错误，提供友好的错误信息

    Returns:
        {
            "type": "error_category",
            "message": "user-friendly message",
            "action": "suggested_action",
            "retryable": bool,
        }
    """
    api_error = classify_error(error)

    user_messages = {
        "RATE_LIMIT": {
            "message": "Too many requests. Please wait a moment and try again.",
            "action": "wait",
        },
        "SERVER_ERROR": {
            "message": "The AI service is temporarily unavailable. Retrying...",
            "action": "retry",
        },
        "TOKEN_LIMIT": {
            "message": "The response was too long. Increasing output limit...",
            "action": "auto_recover",
        },
        "PROMPT_TOO_LONG": {
            "message": "This conversation is too long. Please start a new one.",
            "action": "new_conversation",
        },
        "TIMEOUT": {
            "message": "The request timed out. Retrying...",
            "action": "retry",
        },
        "NETWORK": {
            "message": "Network connection issue. Please check your internet.",
            "action": "check_network",
        },
        "AUTHENTICATION": {
            "message": "Authentication failed. Please check your API key.",
            "action": "check_api_key",
        },
        "PERMISSION": {
            "message": "You don't have permission to perform this action.",
            "action": "none",
        },
        "CONTENT_FILTER": {
            "message": "The content was flagged. Please try a different request.",
            "action": "modify_request",
        },
        "UNKNOWN": {
            "message": "An unexpected error occurred. Please try again.",
            "action": "retry",
        },
    }

    category_name = api_error.category.name
    info = user_messages.get(category_name, user_messages["UNKNOWN"])

    return {
        "type": category_name.lower(),
        "message": getattr(api_error, 'user_message', info["message"]),
        "action": info["action"],
        "retryable": api_error.retryable,
        "error_code": api_error.error_code,
    }
