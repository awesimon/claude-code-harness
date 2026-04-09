"""
测试错误恢复机制
"""
import asyncio
import sys
sys.path.insert(0, '/Users/simon/github/claude-code/python_api')

from services.error_recovery import (
    RecoveryManager,
    RecoveryConfig,
    RetryConfig,
    classify_error,
    classify_for_user,
    TokenLimitError,
    PromptTooLongError,
    RateLimitError,
    ServerError,
    AuthenticationError,
    TimeoutError,
    NetworkError,
)


def test_error_classification():
    """测试错误分类"""
    print("=== 测试错误分类 ===")

    # 测试各种错误类型
    test_errors = [
        (Exception("rate limit exceeded"), "RATE_LIMIT"),
        (Exception("max_output_tokens reached"), "TOKEN_LIMIT"),
        (Exception("prompt too long"), "PROMPT_TOO_LONG"),
        (Exception("unauthorized access"), "AUTHENTICATION"),
        (Exception("internal server error"), "SERVER_ERROR"),
        (Exception("timeout connecting to server"), "TIMEOUT"),
        (Exception("connection failed"), "NETWORK"),
        (Exception("some random error"), "UNKNOWN"),
    ]

    for error, expected_category in test_errors:
        classified = classify_error(error)
        status = "✓" if classified.category.name == expected_category else "✗"
        print(f"{status} {error} -> {classified.category.name} (retryable={classified.retryable})")

    print()


def test_user_friendly_messages():
    """测试用户友好的错误信息"""
    print("=== 测试用户友好的错误信息 ===")

    errors = [
        TokenLimitError("max tokens reached"),
        PromptTooLongError("prompt too long"),
        RateLimitError("rate limited", retry_after=30),
        ServerError("server error", status_code=500),
        TimeoutError("timeout"),
        NetworkError("network error"),
        AuthenticationError("auth failed"),
    ]

    for error in errors:
        info = classify_for_user(error)
        print(f"- {type(error).__name__}: {info['message']}")
        print(f"  action: {info['action']}, retryable: {info['retryable']}")

    print()


async def test_retry_mechanism():
    """测试重试机制"""
    print("=== 测试重试机制 ===")

    retry_manager = RecoveryManager(
        RecoveryConfig(
            retry_config=RetryConfig(
                max_retries=2,
                base_delay=0.1,
                max_delay=1.0,
            ),
            max_total_attempts=3,
        )
    )

    # 模拟一个最终成功的操作
    call_count = 0

    async def failing_then_succeeding():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("simulated temporary error")
        return "success"

    result = await retry_manager.execute_with_recovery(failing_then_succeeding)
    print(f"✓ Retry test: attempts={result.attempts}, success={result.success}")

    # 模拟一个总是失败的操作
    call_count = 0

    async def always_failing():
        raise Exception("simulated permanent error")

    result = await retry_manager.execute_with_recovery(always_failing)
    print(f"✓ Permanent failure test: attempts={result.attempts}, success={result.success}")

    print()


def test_token_recovery_strategies():
    """测试token恢复策略"""
    print("=== 测试Token恢复策略 ===")

    from services.error_recovery import (
        TokenRecoveryManager,
        MaxOutputTokensRecovery,
        TruncateHistoryRecovery,
        PromptCompressionRecovery,
    )

    manager = TokenRecoveryManager()

    print(f"✓ TokenRecoveryManager initialized with {len(manager.strategies)} strategies")
    for i, strategy in enumerate(manager.strategies):
        print(f"  {i+1}. {strategy.__class__.__name__}")

    print()


async def main():
    """主测试函数"""
    print("错误恢复机制测试\n")
    print("=" * 50)

    test_error_classification()
    test_user_friendly_messages()
    await test_retry_mechanism()
    test_token_recovery_strategies()

    print("=" * 50)
    print("所有测试完成!")


if __name__ == "__main__":
    asyncio.run(main())
