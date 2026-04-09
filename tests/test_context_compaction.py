"""
上下文压缩功能测试

测试范围：
- TokenCounter: 基本计数功能
- ContextCompactor: 压缩策略
- AutoCompactor: 自动压缩
- ResponsiveCompactor: 响应式压缩
"""

import pytest
from dataclasses import dataclass
from typing import List

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.compact.context_compactor import (
    TokenCounter,
    ContextCompactor,
    AutoCompactor,
    ResponsiveCompactor,
    CompressionStrategy,
    CompressionResult,
    compact_messages,
    TokenCount,
)


@dataclass
class MockMessage:
    """模拟消息对象"""
    role: str
    content: str


class TestTokenCounter:
    """Token计数器测试"""

    def test_count_text_empty(self):
        """测试空文本"""
        counter = TokenCounter()
        assert counter.count_text("") == 0

    def test_count_text_simple(self):
        """测试简单文本计数"""
        counter = TokenCounter()
        # 简单的近似计算
        text = "Hello world"
        result = counter.count_text(text)
        assert result > 0
        assert isinstance(result, int)

    def test_count_text_long(self):
        """测试长文本计数"""
        counter = TokenCounter()
        text = "This is a test. " * 100
        result = counter.count_text(text)
        assert result > 100  # 应该有超过100个token

    def test_count_messages(self):
        """测试消息列表计数"""
        counter = TokenCounter()
        messages = [
            MockMessage(role="system", content="You are helpful."),
            MockMessage(role="user", content="Hello!"),
            MockMessage(role="assistant", content="Hi there!"),
        ]

        count = counter.count_messages(messages)
        assert isinstance(count, TokenCount)
        assert count.total > 0
        assert count.messages_count == 3
        assert len(count.messages) == 3

    def test_model_ratios(self):
        """测试不同模型的比例"""
        gpt4_counter = TokenCounter("gpt-4")
        claude_counter = TokenCounter("claude-3")

        assert gpt4_counter.ratio == 0.25
        assert claude_counter.ratio == 0.30

    def test_count_messages_empty(self):
        """测试空消息列表"""
        counter = TokenCounter()
        count = counter.count_messages([])
        assert count.total == 0
        assert count.messages_count == 0


class TestContextCompactor:
    """上下文压缩器测试"""

    def create_long_messages(self, count: int = 10) -> List[MockMessage]:
        """创建长消息列表用于测试"""
        messages = [
            MockMessage(role="system", content="System prompt."),
        ]
        for i in range(count):
            messages.append(MockMessage(
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i}: " + "This is a long message. " * 100
            ))
        return messages

    def test_no_compression_needed(self):
        """测试不需要压缩的情况"""
        compactor = ContextCompactor(max_tokens=10000)
        messages = [
            MockMessage(role="user", content="Short message."),
        ]

        result = compactor.compact(messages)

        assert result.success
        assert result.strategy == CompressionStrategy.NONE
        assert result.messages_removed == 0
        assert len(result.compressed_messages) == 1

    def test_truncate_strategy(self):
        """测试截断策略"""
        compactor = ContextCompactor(
            max_tokens=2000,
            strategy=CompressionStrategy.TRUNCATE
        )
        messages = self.create_long_messages(5)

        result = compactor.compact(messages)

        assert result.success
        assert result.strategy == CompressionStrategy.TRUNCATE
        assert result.original_tokens > result.compressed_tokens
        assert result.messages_compressed > 0

    def test_remove_oldest_strategy(self):
        """测试移除最早消息策略"""
        compactor = ContextCompactor(
            max_tokens=3000,
            strategy=CompressionStrategy.REMOVE_OLDEST
        )
        messages = self.create_long_messages(10)

        result = compactor.compact(messages)

        assert result.success
        assert result.strategy == CompressionStrategy.REMOVE_OLDEST
        assert result.original_tokens > result.compressed_tokens

    def test_smart_strategy(self):
        """测试智能策略"""
        compactor = ContextCompactor(
            max_tokens=2000,
            strategy=CompressionStrategy.SMART
        )
        messages = self.create_long_messages(10)

        result = compactor.compact(messages)

        assert result.success
        assert result.strategy == CompressionStrategy.SMART
        assert result.original_tokens > result.compressed_tokens

    def test_system_message_preserved(self):
        """测试系统消息被保留"""
        compactor = ContextCompactor(
            max_tokens=3000,
            strategy=CompressionStrategy.REMOVE_OLDEST
        )
        messages = [
            MockMessage(role="system", content="Important system prompt."),
            MockMessage(role="user", content="User message 1."),
            MockMessage(role="user", content="User message 2."),
        ]

        result = compactor.compact(messages)

        assert any(
            getattr(m, 'role', '') == 'system'
            for m in result.compressed_messages
        )

    def test_should_compact(self):
        """测试是否需要压缩检查"""
        compactor = ContextCompactor(max_tokens=100)

        short_messages = [MockMessage(role="user", content="Hi")]
        long_messages = self.create_long_messages(10)

        assert compactor.should_compact(long_messages) is True
        assert compactor.should_compact(short_messages) is False

    def test_compact_empty_messages(self):
        """测试空消息列表"""
        compactor = ContextCompactor()
        result = compactor.compact([])

        assert result.success
        assert result.strategy == CompressionStrategy.NONE
        assert len(result.compressed_messages) == 0

    def test_message_copy(self):
        """测试消息复制"""
        compactor = ContextCompactor()
        original = MockMessage(role="user", content="Original content")
        copy = compactor._create_message_copy(original, "New content")

        assert copy.role == original.role
        assert copy.content == "New content"


class TestAutoCompactor:
    """自动压缩器测试"""

    def test_auto_compact_enabled(self):
        """测试自动压缩开启"""
        compactor = ContextCompactor(max_tokens=3000)
        auto = AutoCompactor(compactor=compactor, auto_compact=True)

        long_messages = [
            MockMessage(role="user", content="Message. " * 500)
            for _ in range(10)
        ]

        result = auto.check_and_compact(long_messages)

        assert result.success
        assert result.original_tokens > result.compressed_tokens

    def test_auto_compact_disabled(self):
        """测试自动压缩关闭"""
        compactor = ContextCompactor(max_tokens=100)
        auto = AutoCompactor(compactor=compactor, auto_compact=False)

        long_messages = [
            MockMessage(role="user", content="Message. " * 100)
            for _ in range(5)
        ]

        result = auto.check_and_compact(long_messages)

        assert not result.success  # 需要压缩但没有执行
        assert result.metadata.get("needs_compact") is True

    def test_on_compact_callback(self):
        """测试压缩回调"""
        callback_called = False
        callback_result = None

        def on_compact(result):
            nonlocal callback_called, callback_result
            callback_called = True
            callback_result = result

        compactor = ContextCompactor(max_tokens=3000)
        auto = AutoCompactor(
            compactor=compactor,
            auto_compact=True,
            on_compact=on_compact
        )

        long_messages = [
            MockMessage(role="user", content="Long message. " * 500)
            for _ in range(10)
        ]

        auto.check_and_compact(long_messages)

        assert callback_called is True
        assert callback_result is not None
        assert callback_result.strategy != CompressionStrategy.NONE

    def test_get_stats(self):
        """测试统计信息"""
        compactor = ContextCompactor(max_tokens=2000)
        auto = AutoCompactor(compactor=compactor)

        # 空历史
        stats = auto.get_stats()
        assert stats["total_compactions"] == 0

        # 执行压缩
        long_messages = [
            MockMessage(role="user", content="Long message. " * 500)
            for _ in range(10)
        ]
        auto.check_and_compact(long_messages)

        stats = auto.get_stats()
        assert stats["total_compactions"] == 1
        assert stats["total_tokens_saved"] > 0


class TestResponsiveCompactor:
    """响应式压缩器测试"""

    def test_on_error_token_limit(self):
        """测试token限制错误响应"""
        responsive = ResponsiveCompactor()

        error = Exception("max_output_tokens exceeded")
        strategy = responsive.on_error(error)

        assert strategy == CompressionStrategy.TRUNCATE
        assert responsive._consecutive_errors == 1

    def test_on_error_consecutive(self):
        """测试连续错误"""
        responsive = ResponsiveCompactor(aggressive_threshold=2)

        error = Exception("context length exceeded")

        # 第一次
        responsive.on_error(error)
        assert responsive._consecutive_errors == 1

        # 第二次 - 达到阈值
        strategy = responsive.on_error(error)
        assert responsive._consecutive_errors == 2
        assert strategy == CompressionStrategy.REMOVE_OLDEST

    def test_on_error_reset(self):
        """测试非token错误重置计数"""
        responsive = ResponsiveCompactor()

        # 先增加计数
        responsive.on_error(Exception("token limit"))
        assert responsive._consecutive_errors == 1

        # 其他错误类型
        strategy = responsive.on_error(Exception("network error"))
        assert responsive._consecutive_errors == 0

    def test_compact_with_response_error(self):
        """测试带错误响应的压缩"""
        compactor = ContextCompactor(max_tokens=2000)
        responsive = ResponsiveCompactor(compactor=compactor)

        messages = [
            MockMessage(role="user", content="Long message. " * 500)
            for _ in range(10)
        ]

        error = Exception("max_output_tokens exceeded")
        result = responsive.compact_with_response(messages, last_error=error)

        assert result.success
        assert result.original_tokens > result.compressed_tokens

    def test_compact_with_response_no_error(self):
        """测试无错误时的压缩"""
        compactor = ContextCompactor(max_tokens=2000)
        responsive = ResponsiveCompactor(compactor=compactor)

        messages = [
            MockMessage(role="user", content="Long message. " * 500)
            for _ in range(10)
        ]

        result = responsive.compact_with_response(messages, last_error=None)

        assert result.success
        # 重置了错误计数
        assert responsive._consecutive_errors == 0


class TestConvenienceFunctions:
    """便捷函数测试"""

    def test_compact_messages(self):
        """测试compact_messages便捷函数"""
        messages = [
            MockMessage(role="user", content="Long message. " * 500)
            for _ in range(10)
        ]

        result = compact_messages(messages, max_tokens=2000)

        assert isinstance(result, CompressionResult)
        assert result.success

    def test_compact_messages_short(self):
        """测试短消息不压缩"""
        messages = [MockMessage(role="user", content="Short")]

        result = compact_messages(messages, max_tokens=1000)

        assert result.success
        assert result.strategy == CompressionStrategy.NONE


class TestIntegration:
    """集成测试"""

    def test_full_compression_flow(self):
        """测试完整压缩流程"""
        # 创建大量消息
        messages = [
            MockMessage(role="system", content="You are helpful."),
        ]
        for i in range(20):
            messages.append(MockMessage(
                role="user" if i % 2 == 0 else "assistant",
                content=f"Conversation turn {i}: " + "Lots of text here. " * 50
            ))

        # 使用自动压缩器
        compactor = ContextCompactor(max_tokens=3000)
        auto = AutoCompactor(compactor=compactor)

        result = auto.check_and_compact(messages)

        assert result.success
        assert result.original_tokens > result.compressed_tokens
        assert len(result.compressed_messages) < len(messages)

        # 检查系统消息保留
        assert any(
            getattr(m, 'role', '') == 'system'
            for m in result.compressed_messages
        )

    def test_responsive_with_consecutive_errors(self):
        """测试响应式压缩处理连续错误"""
        compactor = ContextCompactor(max_tokens=2000)
        responsive = ResponsiveCompactor(
            compactor=compactor,
            aggressive_threshold=2
        )

        messages = [
            MockMessage(role="user", content="Long content. " * 500)
            for _ in range(15)
        ]

        # 模拟连续错误
        error = Exception("max_output_tokens exceeded")

        # 第一次调用
        result1 = responsive.compact_with_response(messages, error)
        assert result1.success

        # 第二次调用（更激进）
        result2 = responsive.compact_with_response(messages, error)
        assert result2.success
        assert result2.compressed_tokens <= result1.compressed_tokens

    def test_token_counter_with_different_models(self):
        """测试不同模型的token计数"""
        messages = [
            MockMessage(role="user", content="Hello world, this is a test message.")
        ]

        # GPT-4
        gpt4_counter = TokenCounter("gpt-4")
        gpt4_count = gpt4_counter.count_messages(messages)

        # Claude
        claude_counter = TokenCounter("claude-3")
        claude_count = claude_counter.count_messages(messages)

        # Claude应该有更高的比例
        assert claude_count.total >= gpt4_count.total


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
