"""
指数退避重试机制

实现自动重试策略，支持可配置的重试次数、退避策略和抖动
"""

import asyncio
import random
import logging
from dataclasses import dataclass, field
from typing import Optional, TypeVar, Callable, Any, Awaitable
from functools import wraps
import time

from .error_types import (
    APIError,
    RecoverableError,
    RateLimitError,
    ServerError,
    TimeoutError,
    NetworkError,
    TokenLimitError,
    classify_error,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class RetryConfig:
    """重试配置"""
    max_retries: int = 3
    base_delay: float = 1.0          # 初始延迟（秒）
    max_delay: float = 60.0          # 最大延迟（秒）
    exponential_base: float = 2.0    # 指数基数
    jitter: bool = True              # 是否添加抖动
    jitter_max: float = 1.0          # 最大抖动时间（秒）
    retryable_errors: Optional[set] = None  # 指定可重试的错误类型

    def __post_init__(self):
        if self.retryable_errors is None:
            self.retryable_errors = {
                RateLimitError,
                ServerError,
                TimeoutError,
                NetworkError,
                TokenLimitError,  # Token超限特殊处理
            }


@dataclass
class ExponentialBackoff:
    """指数退避计算器"""
    config: RetryConfig = field(default_factory=RetryConfig)

    def calculate_delay(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """
        计算重试延迟

        Args:
            attempt: 当前尝试次数（从1开始）
            retry_after: 服务器建议的等待时间（如果有）

        Returns:
            延迟时间（秒）
        """
        # 如果服务器提供了retry_after，优先使用
        if retry_after is not None and retry_after > 0:
            base_delay = retry_after
        else:
            # 指数退避计算
            base_delay = self.config.base_delay * (
                self.config.exponential_base ** (attempt - 1)
            )

        # 应用最大延迟限制
        delay = min(base_delay, self.config.max_delay)

        # 添加抖动避免惊群效应
        if self.config.jitter:
            jitter = random.uniform(0, self.config.jitter_max)
            delay += jitter

        return delay


@dataclass
class RetryContext:
    """重试上下文"""
    attempt: int = 0
    max_attempts: int = 0
    total_delay: float = 0.0
    errors: list = field(default_factory=list)

    @property
    def has_more_attempts(self) -> bool:
        return self.attempt < self.max_attempts


def with_retry(
    config: Optional[RetryConfig] = None,
    on_retry: Optional[Callable[[APIError, RetryContext], Awaitable[None]]] = None,
    on_error: Optional[Callable[[APIError, RetryContext], Awaitable[None]]] = None,
):
    """
    装饰器：为异步函数添加重试逻辑

    Args:
        config: 重试配置
        on_retry: 每次重试前的回调
        on_error: 所有重试失败后的回调

    Example:
        @with_retry(config=RetryConfig(max_retries=3))
        async def api_call():
            return await some_api()
    """
    config = config or RetryConfig()

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            backoff = ExponentialBackoff(config)
            context = RetryContext(max_attempts=config.max_retries + 1)

            last_error: Optional[APIError] = None

            while context.attempt < context.max_attempts:
                context.attempt += 1

                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    # 分类错误
                    api_error = classify_error(e)
                    context.errors.append(api_error)
                    last_error = api_error

                    # 检查是否可重试
                    if not api_error.retryable:
                        logger.warning(f"Non-retryable error: {api_error}")
                        raise api_error.original_error or api_error

                    # 检查是否还有重试次数
                    if context.attempt >= context.max_attempts:
                        break

                    # 检查错误类型是否在可重试列表中
                    if config.retryable_errors:
                        error_type = type(api_error)
                        if not any(issubclass(error_type, t) for t in config.retryable_errors):
                            logger.warning(f"Error type {error_type} not in retryable list")
                            raise api_error.original_error or api_error

                    # 计算延迟
                    retry_after = None
                    if isinstance(api_error, RateLimitError):
                        retry_after = api_error.retry_after

                    delay = backoff.calculate_delay(context.attempt, retry_after)
                    context.total_delay += delay

                    logger.info(
                        f"Retry {context.attempt}/{config.max_retries} for {func.__name__}: "
                        f"{api_error}. Waiting {delay:.2f}s..."
                    )

                    # 重试回调
                    if on_retry:
                        await on_retry(api_error, context)

                    # 等待
                    await asyncio.sleep(delay)

            # 所有重试失败
            logger.error(
                f"All {context.attempt} attempts failed for {func.__name__}. "
                f"Total delay: {context.total_delay:.2f}s"
            )

            if on_error:
                await on_error(last_error, context)

            # 抛出最后一个错误
            raise last_error.original_error or last_error

        return wrapper
    return decorator


class RetryHandler:
    """可复用的重试处理器"""

    def __init__(self, config: Optional[RetryConfig] = None):
        self.config = config or RetryConfig()
        self.backoff = ExponentialBackoff(self.config)

    async def execute(
        self,
        operation: Callable[[], Awaitable[T]],
        operation_name: str = "operation",
        context: Optional[dict] = None,
    ) -> T:
        """
        执行带重试的操作

        Args:
            operation: 异步操作函数
            operation_name: 操作名称（用于日志）
            context: 额外上下文

        Returns:
            操作结果
        """
        retry_context = RetryContext(max_attempts=self.config.max_retries + 1)
        last_error: Optional[APIError] = None

        while retry_context.attempt < retry_context.max_attempts:
            retry_context.attempt += 1

            try:
                return await operation()
            except Exception as e:
                api_error = classify_error(e, context=context)
                retry_context.errors.append(api_error)
                last_error = api_error

                if not api_error.retryable:
                    logger.warning(f"[{operation_name}] Non-retryable error: {api_error}")
                    raise api_error.original_error or api_error

                if retry_context.attempt >= retry_context.max_attempts:
                    break

                # 检查错误类型
                if self.config.retryable_errors:
                    error_type = type(api_error)
                    if not any(issubclass(error_type, t) for t in self.config.retryable_errors):
                        raise api_error.original_error or api_error

                # 计算延迟
                retry_after = None
                if isinstance(api_error, RateLimitError):
                    retry_after = api_error.retry_after

                delay = self.backoff.calculate_delay(retry_context.attempt, retry_after)
                retry_context.total_delay += delay

                logger.info(
                    f"[{operation_name}] Retry {retry_context.attempt}/{self.config.max_retries}: "
                    f"{api_error}. Waiting {delay:.2f}s..."
                )

                await asyncio.sleep(delay)

        # 所有重试失败
        logger.error(
            f"[{operation_name}] All {retry_context.attempt} attempts failed. "
            f"Total delay: {retry_context.total_delay:.2f}s"
        )

        raise last_error.original_error or last_error


# 便捷函数

async def retry_with_backoff(
    operation: Callable[[], Awaitable[T]],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    operation_name: str = "operation",
) -> T:
    """
    简单重试包装函数

    Args:
        operation: 异步操作
        max_retries: 最大重试次数
        base_delay: 基础延迟
        max_delay: 最大延迟
        operation_name: 操作名称

    Returns:
        操作结果
    """
    config = RetryConfig(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
    )
    handler = RetryHandler(config)
    return await handler.execute(operation, operation_name)
