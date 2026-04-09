"""
测试工具模块

提供测试所需的通用工具函数和Fixture
"""

import pytest
import asyncio
from typing import Any, Callable, List, Optional
from dataclasses import dataclass
from unittest.mock import MagicMock, AsyncMock
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class MockLLMResponse:
    """模拟LLM响应"""
    content: str
    model: str = "gpt-4o"
    usage: dict = None
    finish_reason: str = "stop"

    def __post_init__(self):
        if self.usage is None:
            self.usage = {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150
            }


class AsyncIteratorMock:
    """异步迭代器Mock"""

    def __init__(self, items: List[Any]):
        self.items = items
        self.index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index >= len(self.items):
            raise StopAsyncIteration
        item = self.items[self.index]
        self.index += 1
        return item


def create_mock_message(role: str = "user", content: str = "Hello"):
    """创建模拟消息"""
    from services.llm_service import Message
    return Message(role=role, content=content)


def create_mock_request(
    messages: List = None,
    model: str = "gpt-4o",
    max_tokens: int = 4096,
    stream: bool = False
):
    """创建模拟请求"""
    from services.llm_service import ChatCompletionRequest
    if messages is None:
        messages = [create_mock_message()]
    return ChatCompletionRequest(
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        stream=stream
    )


@pytest.fixture
def event_loop():
    """提供事件循环"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_llm_service():
    """提供Mock LLM服务"""
    service = MagicMock()
    service.chat_completion = AsyncMock()
    service.chat_completion_stream = MagicMock(return_value=AsyncIteratorMock([]))
    return service


@pytest.fixture
def sample_messages():
    """提供示例消息列表"""
    from services.llm_service import Message
    return [
        Message(role="system", content="You are a helpful assistant."),
        Message(role="user", content="Hello, how are you?"),
        Message(role="assistant", content="I'm doing great!"),
    ]


@pytest.fixture
def long_messages():
    """提供长消息列表用于测试压缩"""
    from services.llm_service import Message
    long_content = "This is a very long message. " * 500  # 约15000字符
    return [
        Message(role="system", content="System prompt."),
        Message(role="user", content=long_content),
        Message(role="user", content=long_content),
        Message(role="assistant", content="Response to long message."),
    ]
