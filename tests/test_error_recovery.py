"""
错误恢复机制测试

测试范围：
- error_types: 错误分类
- token_recovery: Token恢复策略
- retry_handler: 重试机制
- recovery_manager: 恢复管理器
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass
from typing import List, Dict, Any

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.error_recovery.error_types import (
    ErrorCategory,
    APIError,
    RecoverableError,
    NonRecoverableError,
    TokenLimitError,
    PromptTooLongError,
    RateLimitError,
    ServerError,
    TimeoutError,
    NetworkError,
    AuthenticationError,
    PermissionError,
    ContentFilterError,
    classify_error,
    is_retryable,
)

from services.error_recovery.token_recovery import (
    TokenRecoveryManager,
    MaxOutputTokensRecovery,
    TruncateHistoryRecovery,
    PromptCompressionRecovery,
    RecoveryAction,
    TokenRecoveryResult,
)

from services.error_recovery.retry_handler import (
    RetryConfig,
    ExponentialBackoff,
    with_retry,
    RetryHandler,
    retry_with_backoff,
)

from services.error_recovery.recovery_manager import (
    RecoveryManager,
    RecoveryConfig,
    RecoveryResult,
    RecoveryPhase,
    CircuitBreaker,
    with_recovery,
    classify_for_user,
)


@dataclass
class MockMessage:
    """模拟消息"""
    role: str
    content: str
    name: str = None
    tool_calls: List = None
    tool_call_id: str = None


@dataclass
class MockRequest:
    """模拟请求"""
    messages: List[MockMessage]
    model: str = "gpt-4o"
    temperature: float = 0.7
    max_tokens: int = 4096
    stream: bool = False
    tools: List = None
    tool_choice: str = None
    provider: str = "openai"


# ============== 错误类型测试 ==============

class TestErrorTypes:
    """错误类型基础测试"""

    def test_api_error_creation(self):
        """测试APIError创建"""
        error = APIError(
            message="Test error",
            category=ErrorCategory.UNKNOWN,
            error_code="test_001",
            retryable=False
        )
        assert error.message == "Test error"
        assert error.retryable is False
        assert "Test error" in str(error)

    def test_recoverable_error(self):
        """测试可恢复错误"""
        error = RecoverableError(
            message="Rate limit",
            category=ErrorCategory.RATE_LIMIT,
            suggested_action="wait"
        )
        assert error.retryable is True
        assert error.suggested_action == "wait"

    def test_non_recoverable_error(self):
        """测试不可恢复错误"""
        error = NonRecoverableError(
            message="Auth failed",
            category=ErrorCategory.AUTHENTICATION,
            user_message="Please check your API key"
        )
        assert error.retryable is False
        assert error.user_message == "Please check your API key"

    def test_token_limit_error(self):
        """测试Token限制错误"""
        error = TokenLimitError(
            message="Max tokens exceeded",
            current_max_tokens=4096,
            suggested_max_tokens=8192
        )
        assert error.category == ErrorCategory.TOKEN_LIMIT
        assert error.current_max_tokens == 4096
        assert error.suggested_max_tokens == 8192

    def test_rate_limit_error(self):
        """测试速率限制错误"""
        error = RateLimitError(
            message="Too many requests",
            retry_after=5.0
        )
        assert error.retry_after == 5.0
        assert error.suggested_action == "wait_and_retry"


class TestErrorClassification:
    """错误分类测试"""

    def test_classify_rate_limit(self):
        """测试速率限制分类"""
        error = Exception("rate limit exceeded")
        classified = classify_error(error)
        assert isinstance(classified, RateLimitError)
        assert classified.retryable is True

    def test_classify_token_limit(self):
        """测试Token限制分类"""
        error = Exception("max_output_tokens exceeded")
        classified = classify_error(error)
        assert isinstance(classified, TokenLimitError)

    def test_classify_prompt_too_long(self):
        """测试提示太长分类"""
        error = Exception("prompt too long")
        classified = classify_error(error)
        assert isinstance(classified, PromptTooLongError)
        assert classified.retryable is False

    def test_classify_with_status_code(self):
        """测试带状态码的分类"""
        error = Exception("Server error")
        classified = classify_error(error, status_code=500)
        assert isinstance(classified, ServerError)
        assert classified.status_code == 500

    def test_classify_429(self):
        """测试429状态码"""
        error = Exception("Too many requests")
        classified = classify_error(error, status_code=429)
        assert isinstance(classified, RateLimitError)

    def test_classify_authentication(self):
        """测试认证错误分类"""
        error = Exception("Authentication failed")
        classified = classify_error(error, status_code=401)
        assert isinstance(classified, AuthenticationError)

    def test_classify_unknown(self):
        """测试未知错误分类"""
        error = Exception("Something weird happened")
        classified = classify_error(error)
        assert isinstance(classified, NonRecoverableError)

    def test_is_retryable(self):
        """测试可重试检查"""
        assert is_retryable(Exception("rate limit")) is True
        assert is_retryable(Exception("invalid api key")) is False


# ============== Token恢复测试 ==============

class TestMaxOutputTokensRecovery:
    """Max Output Tokens恢复测试"""

    @pytest.mark.asyncio
    async def test_can_handle_max_tokens_error(self):
        """测试能否处理max_tokens错误"""
        strategy = MaxOutputTokensRecovery()
        assert strategy.can_handle(Exception("max_output_tokens exceeded")) is True
        assert strategy.can_handle(Exception("output token limit reached")) is True
        assert strategy.can_handle(Exception("other error")) is False

    @pytest.mark.asyncio
    async def test_recovery_increases_tokens(self):
        """测试恢复增加token限制"""
        strategy = MaxOutputTokensRecovery()
        request = MockRequest(
            messages=[MockMessage(role="user", content="Hello")],
            max_tokens=4096
        )
        error = Exception("max_output_tokens exceeded")

        result = await strategy.attempt_recovery(error, request)

        assert result.success is True
        assert result.action == RecoveryAction.INCREASE_MAX_TOKENS
        assert result.new_request.max_tokens > request.max_tokens

    @pytest.mark.asyncio
    async def test_recovery_multiple_attempts(self):
        """测试多次恢复尝试"""
        strategy = MaxOutputTokensRecovery()
        request = MockRequest(
            messages=[MockMessage(role="user", content="Hello")],
            max_tokens=1000
        )
        error = Exception("max_output_tokens exceeded")

        # 第一次
        result1 = await strategy.attempt_recovery(error, request)
        assert result1.success is True
        assert result1.attempts == 1

        # 第二次使用新请求
        result2 = await strategy.attempt_recovery(error, result1.new_request)
        assert result2.success is True
        assert result2.attempts == 2
        assert result2.new_request.max_tokens > result1.new_request.max_tokens

    @pytest.mark.asyncio
    async def test_recovery_exhausted(self):
        """测试恢复尝试耗尽"""
        strategy = MaxOutputTokensRecovery(
            multipliers=[1.5],  # 只有1个级别
            absolute_max=2000
        )
        request = MockRequest(
            messages=[MockMessage(role="user", content="Hello")],
            max_tokens=1500
        )
        error = Exception("max_output_tokens exceeded")

        # 第一次成功
        result1 = await strategy.attempt_recovery(error, request)
        assert result1.success is True

        # 第二次失败（已达到限制）
        result2 = await strategy.attempt_recovery(error, result1.new_request)
        assert result2.success is False
        assert result2.action == RecoveryAction.ABORT


class TestTruncateHistoryRecovery:
    """历史截断恢复测试"""

    @pytest.mark.asyncio
    async def test_truncate_history(self):
        """测试历史截断"""
        strategy = TruncateHistoryRecovery(keep_last_n=2)
        messages = [
            MockMessage(role="system", content="System"),
            MockMessage(role="user", content="Message 1"),
            MockMessage(role="assistant", content="Response 1"),
            MockMessage(role="user", content="Message 2"),
            MockMessage(role="assistant", content="Response 2"),
            MockMessage(role="user", content="Message 3"),
        ]
        request = MockRequest(messages=messages)
        error = Exception("context length exceeded")

        result = await strategy.attempt_recovery(error, request)

        assert result.success is True
        assert result.action == RecoveryAction.TRUNCATE_HISTORY
        assert len(result.new_request.messages) < len(messages)

    @pytest.mark.asyncio
    async def test_truncate_preserves_system(self):
        """测试保留系统消息"""
        strategy = TruncateHistoryRecovery()
        messages = [
            MockMessage(role="system", content="Important system prompt"),
            MockMessage(role="user", content="User message"),
        ]
        request = MockRequest(messages=messages)
        error = Exception("context length exceeded")

        result = await strategy.attempt_recovery(error, request)

        system_messages = [m for m in result.new_request.messages if m.role == "system"]
        assert len(system_messages) == 1

    @pytest.mark.asyncio
    async def test_truncate_too_short(self):
        """测试历史太短无法截断"""
        strategy = TruncateHistoryRecovery(min_history_length=5)
        messages = [
            MockMessage(role="user", content="Short"),
        ]
        request = MockRequest(messages=messages)
        error = Exception("context length exceeded")

        result = await strategy.attempt_recovery(error, request)

        assert result.success is False
        assert result.action == RecoveryAction.ABORT


class TestPromptCompressionRecovery:
    """提示压缩恢复测试"""

    @pytest.mark.asyncio
    async def test_compress_long_messages(self):
        """测试压缩长消息"""
        strategy = PromptCompressionRecovery(
            max_message_length=100,
            compression_ratio=0.5
        )
        messages = [
            MockMessage(role="user", content="Short"),
            MockMessage(role="user", content="A" * 1000),  # 长消息
        ]
        request = MockRequest(messages=messages)
        error = Exception("prompt too long")

        result = await strategy.attempt_recovery(error, request)

        assert result.success is True
        assert result.action == RecoveryAction.COMPRESS_PROMPT
        assert result.context["compressed_count"] == 1

    @pytest.mark.asyncio
    async def test_no_compression_needed(self):
        """测试不需要压缩"""
        strategy = PromptCompressionRecovery(max_message_length=1000)
        messages = [
            MockMessage(role="user", content="Short message"),
        ]
        request = MockRequest(messages=messages)
        error = Exception("prompt too long")

        result = await strategy.attempt_recovery(error, request)

        assert result.success is False
        assert result.action == RecoveryAction.ABORT


class TestTokenRecoveryManager:
    """Token恢复管理器测试"""

    @pytest.mark.asyncio
    async def test_manager_tries_strategies(self):
        """测试管理器按顺序尝试策略"""
        manager = TokenRecoveryManager()
        request = MockRequest(
            messages=[MockMessage(role="user", content="Hello")],
            max_tokens=1000
        )
        error = Exception("max_output_tokens exceeded")

        result = await manager.try_recover(error, request)

        assert isinstance(result, TokenRecoveryResult)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_manager_max_attempts(self):
        """测试最大尝试次数"""
        manager = TokenRecoveryManager(max_recovery_attempts=1)
        request = MockRequest(messages=[MockMessage(role="user", content="Hi")])
        error = Exception("max_output_tokens exceeded")

        context = {"recovery_attempts": 5}  # 已经超过最大尝试
        result = await manager.try_recover(error, request, context)

        assert result.success is False
        assert "exceeded" in result.message.lower()


# ============== 重试机制测试 ==============

class TestRetryConfig:
    """重试配置测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0

    def test_custom_config(self):
        """测试自定义配置"""
        config = RetryConfig(max_retries=5, base_delay=0.5)
        assert config.max_retries == 5
        assert config.base_delay == 0.5


class TestExponentialBackoff:
    """指数退避测试"""

    def test_calculate_delay(self):
        """测试延迟计算"""
        config = RetryConfig(base_delay=1.0, exponential_base=2.0)
        backoff = ExponentialBackoff(config)

        # 第一次重试
        delay1 = backoff.calculate_delay(attempt=1)
        assert 1.0 <= delay1 <= 2.0  # 基础延迟 + 可能的抖动

        # 第二次重试
        delay2 = backoff.calculate_delay(attempt=2)
        assert delay2 >= delay1 or delay2 >= 1.5  # 指数增长

    def test_max_delay(self):
        """测试最大延迟限制"""
        config = RetryConfig(base_delay=1.0, max_delay=5.0, exponential_base=10.0)
        backoff = ExponentialBackoff(config)

        delay = backoff.calculate_delay(attempt=10)
        assert delay <= 6.0  # 最大延迟 + 最大抖动

    def test_retry_after(self):
        """测试使用服务器的retry_after"""
        config = RetryConfig()
        backoff = ExponentialBackoff(config)

        delay = backoff.calculate_delay(attempt=1, retry_after=10.0)
        assert 10.0 <= delay <= 11.0  # retry_after + 可能的抖动


class TestRetryHandler:
    """重试处理器测试"""

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        """测试成功执行"""
        handler = RetryHandler(RetryConfig(max_retries=1))

        async def success_op():
            return "success"

        result = await handler.execute(success_op, "test_op")
        assert result == "success"

    @pytest.mark.asyncio
    async def test_retry_on_error(self):
        """测试错误时重试"""
        handler = RetryHandler(RetryConfig(max_retries=2, base_delay=0.1))
        call_count = 0

        async def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("rate limit")
            return "success"

        result = await handler.execute(fail_then_succeed, "test_op")
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_non_recoverable(self):
        """测试不可恢复错误不重试"""
        handler = RetryHandler(RetryConfig(max_retries=3))

        async def fail_with_auth():
            raise Exception("invalid api key")

        with pytest.raises(Exception):
            await handler.execute(fail_with_auth, "test_op")

    @pytest.mark.asyncio
    async def test_all_retries_failed(self):
        """测试所有重试失败"""
        handler = RetryHandler(RetryConfig(max_retries=2, base_delay=0.1))

        async def always_fail():
            raise Exception("server error")

        with pytest.raises(Exception):
            await handler.execute(always_fail, "test_op")


class TestWithRetryDecorator:
    """重试装饰器测试"""

    @pytest.mark.asyncio
    async def test_decorator_success(self):
        """测试装饰器成功执行"""
        config = RetryConfig(max_retries=1)

        @with_retry(config=config)
        async def my_func():
            return "success"

        result = await my_func()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_decorator_retry(self):
        """测试装饰器重试"""
        config = RetryConfig(max_retries=2, base_delay=0.1)
        call_count = 0

        @with_retry(config=config)
        async def my_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("rate limit")
            return "success"

        result = await my_func()
        assert result == "success"
        assert call_count == 2


# ============== 恢复管理器测试 ==============

class TestCircuitBreaker:
    """熔断器测试"""

    def test_initial_state(self):
        """测试初始状态"""
        cb = CircuitBreaker(threshold=3)
        assert cb.state == "closed"
        assert cb.can_execute() is True

    def test_record_failure(self):
        """测试记录失败"""
        cb = CircuitBreaker(threshold=3)

        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"

        cb.record_failure()
        assert cb.state == "open"
        assert cb.can_execute() is False

    def test_record_success(self):
        """测试记录成功"""
        cb = CircuitBreaker(threshold=3)

        cb.record_failure()
        cb.record_failure()
        cb.record_success()

        assert cb.failure_count == 0
        assert cb.state == "closed"

    def test_half_open(self):
        """测试half-open状态"""
        cb = CircuitBreaker(threshold=2, reset_timeout=0.1)

        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"

        # 等待超时
        import time
        time.sleep(0.15)

        assert cb.can_execute() is True
        assert cb.state == "half-open"


class TestRecoveryManager:
    """恢复管理器测试"""

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        """测试成功执行"""
        manager = RecoveryManager()

        async def success_op():
            return "success"

        result = await manager.execute_with_recovery(success_op)

        assert isinstance(result, RecoveryResult)
        assert result.success is True
        assert result.phase == RecoveryPhase.SUCCESS

    @pytest.mark.asyncio
    async def test_recovery_from_error(self):
        """测试从错误恢复"""
        config = RecoveryConfig(max_total_attempts=2)
        manager = RecoveryManager(config)

        call_count = 0
        async def fail_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("rate limit")
            return "success"

        result = await manager.execute_with_recovery(fail_once)

        assert result.success is True
        assert result.attempts == 2
        assert "retry" in result.recovery_actions[0]

    @pytest.mark.asyncio
    async def test_non_recoverable_error(self):
        """测试不可恢复错误"""
        manager = RecoveryManager()

        async def fail_auth():
            raise Exception("invalid api key")

        result = await manager.execute_with_recovery(fail_auth)

        assert result.success is False
        assert result.phase == RecoveryPhase.FAILED
        assert "non-recoverable" in result.message.lower()


class TestWithRecovery:
    """便捷函数测试"""

    @pytest.mark.asyncio
    async def test_with_recovery_success(self):
        """测试成功执行"""
        async def my_op():
            return "success"

        result = await with_recovery(my_op)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_with_recovery_custom_config(self):
        """测试自定义配置"""
        async def my_op():
            return "success"

        config = RecoveryConfig(max_total_attempts=5)
        result = await with_recovery(my_op, config=config)
        assert result.success is True


class TestClassifyForUser:
    """用户错误分类测试"""

    def test_rate_limit_user_message(self):
        """测试速率限制用户消息"""
        error = Exception("rate limit exceeded")
        info = classify_for_user(error)

        assert info["type"] == "rate_limit"
        assert info["retryable"] is True
        assert "wait" in info["action"]

    def test_auth_user_message(self):
        """测试认证错误用户消息"""
        error = Exception("invalid api key")
        info = classify_for_user(error)

        assert info["type"] == "authentication"
        assert info["retryable"] is False
        assert "api key" in info["message"].lower()


# ============== 集成测试 ==============

class TestIntegration:
    """集成测试"""

    @pytest.mark.asyncio
    async def test_full_recovery_flow(self):
        """测试完整恢复流程"""
        config = RecoveryConfig(
            max_total_attempts=3,
            enable_token_recovery=True,
        )
        manager = RecoveryManager(config)

        call_count = 0
        async def operation_with_recovery():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("max_output_tokens exceeded")
            return "success"

        result = await manager.execute_with_recovery(operation_with_recovery)

        assert result.success is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_token_recovery_integration(self):
        """测试Token恢复集成"""
        manager = RecoveryManager()
        request = MockRequest(
            messages=[MockMessage(role="user", content="Test")],
            max_tokens=1000
        )

        call_count = 0
        async def operation(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("max_output_tokens exceeded")
            return "success"

        result = await manager.execute_with_recovery(operation, request=request)

        assert result.success is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_with_backoff(self):
        """测试便捷重试函数"""
        call_count = 0
        async def operation():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("server error")
            return "success"

        result = await retry_with_backoff(
            operation,
            max_retries=2,
            base_delay=0.1
        )

        assert result == "success"
        assert call_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
