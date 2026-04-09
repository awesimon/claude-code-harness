"""
上下文压缩模块

提供Token计数、压缩策略和自动压缩功能
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Callable
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class CompressionStrategy(Enum):
    """压缩策略"""
    NONE = "none"                    # 不压缩
    TRUNCATE = "truncate"            # 截断
    SUMMARIZE = "summarize"          # 摘要（简单实现）
    REMOVE_OLDEST = "remove_oldest"  # 移除最早消息
    SMART = "smart"                  # 智能压缩


@dataclass
class TokenCount:
    """Token计数结果"""
    total: int
    prompt_tokens: int
    completion_tokens: int
    messages_count: int
    messages: List[Dict[str, int]] = field(default_factory=list)


@dataclass
class CompressionResult:
    """压缩结果"""
    success: bool
    strategy: CompressionStrategy
    original_tokens: int
    compressed_tokens: int
    messages_removed: int
    messages_compressed: int
    compressed_messages: List[Any]
    metadata: Dict[str, Any] = field(default_factory=dict)


class TokenCounter:
    """
    Token计数器

    提供准确的Token计数，支持不同模型的分词规则
    """

    # 各模型的平均token/字符比例（近似值）
    MODEL_RATIOS = {
        "gpt-4": 0.25,
        "gpt-4o": 0.25,
        "gpt-3.5-turbo": 0.25,
        "claude": 0.30,
        "claude-3": 0.30,
        "default": 0.25,
    }

    def __init__(self, model: str = "default"):
        self.model = model
        self.ratio = self._get_ratio(model)

    def _get_ratio(self, model: str) -> float:
        """获取模型的token比例"""
        for key, ratio in self.MODEL_RATIOS.items():
            if key in model.lower():
                return ratio
        return self.MODEL_RATIOS["default"]

    def count_text(self, text: str) -> int:
        """
        计算文本的token数（近似）

        简单实现：使用字符数 * 比例
        实际应用中应该使用tiktoken或模型的tokenizer
        """
        if not text:
            return 0
        # 更精确的估算：考虑空格和标点
        words = len(text.split())
        chars = len(text)
        # 英文平均每个词1.3个token，中文每个字约2个token
        return int(words * 1.3 + chars * 0.1)

    def count_messages(self, messages: List[Any]) -> TokenCount:
        """
        计算消息列表的总token数

        Args:
            messages: 消息列表

        Returns:
            TokenCount对象
        """
        total = 0
        message_counts = []

        for i, msg in enumerate(messages):
            content = getattr(msg, 'content', str(msg))
            count = self.count_text(content)
            total += count
            message_counts.append({
                "index": i,
                "role": getattr(msg, 'role', 'unknown'),
                "tokens": count,
            })

        return TokenCount(
            total=total,
            prompt_tokens=total,
            completion_tokens=0,
            messages_count=len(messages),
            messages=message_counts,
        )


class ContextCompactor:
    """
    上下文压缩器

    根据策略压缩对话上下文以适应模型限制
    """

    # 默认限制
    DEFAULT_MAX_TOKENS = 4000
    SAFETY_MARGIN = 500  # 安全余量

    def __init__(
        self,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        strategy: CompressionStrategy = CompressionStrategy.SMART,
        token_counter: Optional[TokenCounter] = None,
    ):
        self.max_tokens = max_tokens
        self.strategy = strategy
        self.token_counter = token_counter or TokenCounter()
        self.safety_margin = self.SAFETY_MARGIN

    def _estimate_tokens(self, messages: List[Any]) -> int:
        """估算token数"""
        count = self.token_counter.count_messages(messages)
        return count.total

    def _truncate_message(self, content: str, max_length: int) -> str:
        """截断单条消息"""
        if len(content) <= max_length:
            return content

        # 保留开头和结尾
        keep = max_length // 2 - 30
        return (
            content[:keep] +
            f"\n... [{len(content) - 2 * keep} characters truncated] ...\n" +
            content[-keep:]
        )

    def _compress_truncate(self, messages: List[Any], target_tokens: int) -> CompressionResult:
        """截断压缩策略"""
        original_count = len(messages)
        original_tokens = self._estimate_tokens(messages)

        compressed = []
        compressed_count = 0

        for msg in messages:
            content = getattr(msg, 'content', str(msg))
            role = getattr(msg, 'role', 'user')

            # 系统消息不压缩
            if role == 'system':
                compressed.append(msg)
                continue

            # 压缩长消息
            if self.token_counter.count_text(content) > 500:
                new_content = self._truncate_message(content, 1000)
                # 创建新消息对象
                compressed_msg = self._create_message_copy(msg, new_content)
                compressed.append(compressed_msg)
                compressed_count += 1
            else:
                compressed.append(msg)

        final_tokens = self._estimate_tokens(compressed)

        return CompressionResult(
            success=True,
            strategy=CompressionStrategy.TRUNCATE,
            original_tokens=original_tokens,
            compressed_tokens=final_tokens,
            messages_removed=0,
            messages_compressed=compressed_count,
            compressed_messages=compressed,
            metadata={"target_tokens": target_tokens},
        )

    def _compress_remove_oldest(self, messages: List[Any], target_tokens: int) -> CompressionResult:
        """移除最早消息策略"""
        original_count = len(messages)
        original_tokens = self._estimate_tokens(messages)

        # 保留系统消息
        system_messages = [m for m in messages if getattr(m, 'role', '') == 'system']
        non_system = [m for m in messages if getattr(m, 'role', '') != 'system']

        # 从最早的消息开始移除，直到满足目标
        kept_messages = list(system_messages)
        removed_count = 0

        # 保留最近的消息
        for msg in reversed(non_system):
            test_messages = kept_messages + [msg]
            if self._estimate_tokens(test_messages) <= target_tokens:
                kept_messages.append(msg)
            else:
                removed_count += 1

        # 恢复消息顺序
        kept_messages = system_messages + list(reversed(kept_messages[len(system_messages):]))

        final_tokens = self._estimate_tokens(kept_messages)

        return CompressionResult(
            success=True,
            strategy=CompressionStrategy.REMOVE_OLDEST,
            original_tokens=original_tokens,
            compressed_tokens=final_tokens,
            messages_removed=removed_count,
            messages_compressed=0,
            compressed_messages=kept_messages,
            metadata={"target_tokens": target_tokens},
        )

    def _compress_smart(self, messages: List[Any], target_tokens: int) -> CompressionResult:
        """
        智能压缩策略

        结合多种策略：
        1. 先尝试截断长消息
        2. 如果还不够，移除最早的消息
        """
        original_tokens = self._estimate_tokens(messages)

        # 第一步：截断长消息
        truncate_result = self._compress_truncate(messages, target_tokens)

        # 检查是否还需要进一步压缩
        if truncate_result.compressed_tokens <= target_tokens:
            return CompressionResult(
                success=True,
                strategy=CompressionStrategy.SMART,
                original_tokens=original_tokens,
                compressed_tokens=truncate_result.compressed_tokens,
                messages_removed=0,
                messages_compressed=truncate_result.messages_compressed,
                compressed_messages=truncate_result.compressed_messages,
                metadata={"phases": ["truncate"]},
            )

        # 第二步：移除旧消息
        remove_result = self._compress_remove_oldest(
            truncate_result.compressed_messages,
            target_tokens
        )

        return CompressionResult(
            success=True,
            strategy=CompressionStrategy.SMART,
            original_tokens=original_tokens,
            compressed_tokens=remove_result.compressed_tokens,
            messages_removed=remove_result.messages_removed,
            messages_compressed=truncate_result.messages_compressed,
            compressed_messages=remove_result.compressed_messages,
            metadata={"phases": ["truncate", "remove_oldest"]},
        )

    def _create_message_copy(self, original: Any, new_content: str):
        """创建消息的副本，使用新内容"""
        # 如果原始对象有dataclass的__dataclass_fields__，重新创建
        if hasattr(original, '__dataclass_fields__'):
            from dataclasses import fields
            kwargs = {}
            for f in fields(original):
                if f.name == 'content':
                    kwargs[f.name] = new_content
                else:
                    kwargs[f.name] = getattr(original, f.name)
            return type(original)(**kwargs)
        else:
            # 简单对象，尝试复制
            try:
                new_msg = type(original).__new__(type(original))
                for attr in dir(original):
                    if not attr.startswith('_') and hasattr(original, attr):
                        if attr == 'content':
                            setattr(new_msg, attr, new_content)
                        else:
                            setattr(new_msg, attr, getattr(original, attr))
                return new_msg
            except:
                # 无法复制，返回原始对象
                return original

    def compact(self, messages: List[Any], target_tokens: Optional[int] = None) -> CompressionResult:
        """
        压缩消息列表

        Args:
            messages: 消息列表
            target_tokens: 目标token数（默认为max_tokens - safety_margin）

        Returns:
            CompressionResult
        """
        if not messages:
            return CompressionResult(
                success=True,
                strategy=CompressionStrategy.NONE,
                original_tokens=0,
                compressed_tokens=0,
                messages_removed=0,
                messages_compressed=0,
                compressed_messages=[],
            )

        target = target_tokens or (self.max_tokens - self.safety_margin)
        current_tokens = self._estimate_tokens(messages)

        # 如果已经在限制内，不需要压缩
        if current_tokens <= target:
            return CompressionResult(
                success=True,
                strategy=CompressionStrategy.NONE,
                original_tokens=current_tokens,
                compressed_tokens=current_tokens,
                messages_removed=0,
                messages_compressed=0,
                compressed_messages=list(messages),
            )

        # 根据策略执行压缩
        if self.strategy == CompressionStrategy.TRUNCATE:
            return self._compress_truncate(messages, target)
        elif self.strategy == CompressionStrategy.REMOVE_OLDEST:
            return self._compress_remove_oldest(messages, target)
        elif self.strategy == CompressionStrategy.SMART:
            return self._compress_smart(messages, target)
        else:
            # 未知策略，不压缩
            return CompressionResult(
                success=False,
                strategy=CompressionStrategy.NONE,
                original_tokens=current_tokens,
                compressed_tokens=current_tokens,
                messages_removed=0,
                messages_compressed=0,
                compressed_messages=list(messages),
                metadata={"error": "Unknown compression strategy"},
            )

    def should_compact(self, messages: List[Any]) -> bool:
        """
        检查是否需要压缩

        Args:
            messages: 消息列表

        Returns:
            是否需要压缩
        """
        current_tokens = self._estimate_tokens(messages)
        return current_tokens > (self.max_tokens - self.safety_margin)


class AutoCompactor:
    """
    自动压缩器

    自动监控上下文大小并在需要时触发压缩
    """

    def __init__(
        self,
        compactor: Optional[ContextCompactor] = None,
        auto_compact: bool = True,
        on_compact: Optional[Callable[[CompressionResult], None]] = None,
    ):
        self.compactor = compactor or ContextCompactor()
        self.auto_compact = auto_compact
        self.on_compact = on_compact
        self._history: List[CompressionResult] = []

    def check_and_compact(self, messages: List[Any]) -> CompressionResult:
        """
        检查并自动压缩

        Args:
            messages: 消息列表

        Returns:
            CompressionResult
        """
        if not self.auto_compact:
            # 只检查但不压缩
            needs_compact = self.compactor.should_compact(messages)
            return CompressionResult(
                success=not needs_compact,
                strategy=CompressionStrategy.NONE,
                original_tokens=self.compactor._estimate_tokens(messages),
                compressed_tokens=self.compactor._estimate_tokens(messages),
                messages_removed=0,
                messages_compressed=0,
                compressed_messages=list(messages),
                metadata={"needs_compact": needs_compact},
            )

        result = self.compactor.compact(messages)
        self._history.append(result)

        if self.on_compact and result.strategy != CompressionStrategy.NONE:
            self.on_compact(result)

        return result

    def get_stats(self) -> Dict[str, Any]:
        """获取压缩统计信息"""
        if not self._history:
            return {
                "total_compactions": 0,
                "total_tokens_saved": 0,
                "avg_compression_ratio": 0,
            }

        total_saved = sum(
            r.original_tokens - r.compressed_tokens
            for r in self._history
        )

        compression_ratios = [
            r.compressed_tokens / r.original_tokens
            for r in self._history
            if r.original_tokens > 0
        ]

        return {
            "total_compactions": len(self._history),
            "total_tokens_saved": total_saved,
            "avg_compression_ratio": sum(compression_ratios) / len(compression_ratios) if compression_ratios else 0,
        }


class ResponsiveCompactor:
    """
    响应式压缩器

    根据模型响应动态调整压缩策略
    """

    def __init__(
        self,
        compactor: Optional[ContextCompactor] = None,
        aggressive_threshold: int = 3,  # 连续错误阈值
    ):
        self.compactor = compactor or ContextCompactor()
        self.aggressive_threshold = aggressive_threshold
        self._consecutive_errors = 0
        self._last_strategy = CompressionStrategy.SMART

    def on_error(self, error: Exception) -> CompressionStrategy:
        """
        根据错误调整策略

        Args:
            error: 发生的错误

        Returns:
            推荐的压缩策略
        """
        error_str = str(error).lower()

        # Token相关错误，增加压缩强度
        if any(kw in error_str for kw in ["token", "context", "length"]):
            self._consecutive_errors += 1

            if self._consecutive_errors >= self.aggressive_threshold:
                # 使用更激进的策略
                return CompressionStrategy.REMOVE_OLDEST
            else:
                return CompressionStrategy.TRUNCATE
        else:
            # 其他错误，重置计数
            self._consecutive_errors = 0
            return self.compactor.strategy

    def compact_with_response(
        self,
        messages: List[Any],
        last_error: Optional[Exception] = None,
    ) -> CompressionResult:
        """
        根据响应历史执行压缩

        Args:
            messages: 消息列表
            last_error: 上一次的错误（如果有）

        Returns:
            CompressionResult
        """
        if last_error:
            # 根据错误调整策略
            strategy = self.on_error(last_error)
            original_strategy = self.compactor.strategy
            self.compactor.strategy = strategy

            result = self.compactor.compact(messages)

            # 恢复策略
            self.compactor.strategy = original_strategy

            return result
        else:
            # 没有错误，正常压缩
            self._consecutive_errors = 0
            return self.compactor.compact(messages)


# 便捷函数

def compact_messages(
    messages: List[Any],
    max_tokens: int = 4000,
    strategy: CompressionStrategy = CompressionStrategy.SMART,
) -> CompressionResult:
    """
    便捷函数：压缩消息列表

    Example:
        result = compact_messages(messages, max_tokens=8000)
        if result.success:
            messages = result.compressed_messages
    """
    compactor = ContextCompactor(max_tokens=max_tokens, strategy=strategy)
    return compactor.compact(messages)
