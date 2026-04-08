"""
Agent间通信工具模块
提供子Agent之间的消息发送功能，支持广播和点对点通信
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable, Awaitable
import asyncio
import time
from enum import Enum
from datetime import datetime

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool


class MessageType(Enum):
    """消息类型"""
    DIRECT = "direct"      # 点对点消息
    BROADCAST = "broadcast"  # 广播消息
    SYSTEM = "system"      # 系统消息


@dataclass
class Message:
    """消息数据结构"""
    id: str
    sender: str            # 发送者ID
    recipient: str         # 接收者ID（"*"表示广播）
    content: str           # 消息内容
    message_type: MessageType
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "sender": self.sender,
            "recipient": self.recipient,
            "content": self.content,
            "type": self.message_type.value,
            "timestamp": self.timestamp,
            "datetime": datetime.fromtimestamp(self.timestamp).isoformat(),
            "metadata": self.metadata,
        }


# 全局消息管理器
_message_manager: Optional['MessageManager'] = None


def get_message_manager() -> 'MessageManager':
    """获取全局消息管理器实例"""
    global _message_manager
    if _message_manager is None:
        _message_manager = MessageManager()
    return _message_manager


def set_message_manager(manager: 'MessageManager') -> None:
    """设置全局消息管理器"""
    global _message_manager
    _message_manager = manager


class MessageManager:
    """
    消息管理器 - 负责Agent间的消息路由和传递

    功能:
    - 存储和转发点对点消息
    - 处理广播消息
    - 管理消息历史
    - 提供消息订阅机制
    """

    _id_counter = 0

    def __init__(self, max_history: int = 1000):
        self.max_history = max_history

        # 消息存储
        self._messages: List[Message] = []
        self._messages_lock = asyncio.Lock()

        # 未读消息（按接收者ID分组）
        self._unread_messages: Dict[str, List[Message]] = {}
        self._unread_lock = asyncio.Lock()

        # 消息订阅回调（按接收者ID）
        self._subscribers: Dict[str, List[Callable[[Message], Awaitable[None]]]] = {}
        self._subscribers_lock = asyncio.Lock()

        # 广播订阅
        self._broadcast_subscribers: List[Callable[[Message], Awaitable[None]]] = []

    def _generate_message_id(self) -> str:
        """生成消息ID"""
        MessageManager._id_counter += 1
        return f"msg-{MessageManager._id_counter:06d}-{int(time.time() * 1000) % 1000000}"

    async def send_message(
        self,
        sender: str,
        recipient: str,
        content: str,
        message_type: MessageType = MessageType.DIRECT,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Message:
        """
        发送消息

        Args:
            sender: 发送者ID
            recipient: 接收者ID（"*"表示广播）
            content: 消息内容
            message_type: 消息类型
            metadata: 额外元数据

        Returns:
            发送的消息对象
        """
        # 创建消息
        message = Message(
            id=self._generate_message_id(),
            sender=sender,
            recipient=recipient,
            content=content,
            message_type=message_type if recipient != "*" else MessageType.BROADCAST,
            timestamp=time.time(),
            metadata=metadata or {}
        )

        # 存储消息
        async with self._messages_lock:
            self._messages.append(message)
            # 限制历史消息数量
            if len(self._messages) > self.max_history:
                self._messages = self._messages[-self.max_history:]

        # 处理消息路由
        if recipient == "*":
            # 广播消息
            await self._broadcast(message)
        else:
            # 点对点消息
            await self._deliver_to_recipient(message)

        return message

    async def _deliver_to_recipient(self, message: Message) -> None:
        """将消息传递给指定接收者"""
        recipient = message.recipient

        # 添加到未读消息
        async with self._unread_lock:
            if recipient not in self._unread_messages:
                self._unread_messages[recipient] = []
            self._unread_messages[recipient].append(message)

        # 通知订阅者
        async with self._subscribers_lock:
            callbacks = self._subscribers.get(recipient, [])

        for callback in callbacks:
            try:
                await callback(message)
            except Exception as e:
                print(f"消息订阅回调错误: {e}")

    async def _broadcast(self, message: Message) -> None:
        """广播消息给所有订阅者"""
        # 通知广播订阅者
        for callback in self._broadcast_subscribers:
            try:
                await callback(message)
            except Exception as e:
                print(f"广播回调错误: {e}")

        # 通知所有个人订阅者（如果他们也想接收广播）
        async with self._subscribers_lock:
            all_callbacks = []
            for callbacks in self._subscribers.values():
                all_callbacks.extend(callbacks)

        for callback in all_callbacks:
            try:
                await callback(message)
            except Exception as e:
                print(f"广播回调错误: {e}")

    async def subscribe(
        self,
        recipient_id: str,
        callback: Callable[[Message], Awaitable[None]]
    ) -> None:
        """
        订阅消息

        Args:
            recipient_id: 接收者ID
            callback: 消息回调函数
        """
        async with self._subscribers_lock:
            if recipient_id not in self._subscribers:
                self._subscribers[recipient_id] = []
            self._subscribers[recipient_id].append(callback)

    async def unsubscribe(
        self,
        recipient_id: str,
        callback: Callable[[Message], Awaitable[None]]
    ) -> None:
        """取消订阅"""
        async with self._subscribers_lock:
            if recipient_id in self._subscribers:
                if callback in self._subscribers[recipient_id]:
                    self._subscribers[recipient_id].remove(callback)

    async def subscribe_broadcast(
        self,
        callback: Callable[[Message], Awaitable[None]]
    ) -> None:
        """订阅广播消息"""
        self._broadcast_subscribers.append(callback)

    async def unsubscribe_broadcast(
        self,
        callback: Callable[[Message], Awaitable[None]]
    ) -> None:
        """取消广播订阅"""
        if callback in self._broadcast_subscribers:
            self._broadcast_subscribers.remove(callback)

    async def get_unread_messages(self, recipient_id: str) -> List[Message]:
        """获取未读消息"""
        async with self._unread_lock:
            messages = self._unread_messages.get(recipient_id, []).copy()
            self._unread_messages[recipient_id] = []
            return messages

    async def get_message_history(
        self,
        sender: Optional[str] = None,
        recipient: Optional[str] = None,
        limit: int = 100
    ) -> List[Message]:
        """获取消息历史"""
        async with self._messages_lock:
            messages = self._messages.copy()

        # 应用过滤
        if sender:
            messages = [m for m in messages if m.sender == sender]
        if recipient:
            messages = [m for m in messages if m.recipient == recipient or m.recipient == "*"]

        # 限制数量
        messages = messages[-limit:]

        return messages

    async def clear_history(self) -> None:
        """清除消息历史"""
        async with self._messages_lock:
            self._messages.clear()
        async with self._unread_lock:
            self._unread_messages.clear()


@dataclass
class SendMessageInput:
    """发送消息的输入参数"""
    to: str                # 接收者ID（"*"表示广播给所有Agent）
    message: str           # 消息内容
    sender: Optional[str] = None  # 发送者ID（可选，默认为当前Agent）
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外元数据


@dataclass
class MessageHistoryInput:
    """获取消息历史的输入参数"""
    sender: Optional[str] = None     # 按发送者过滤
    recipient: Optional[str] = None  # 按接收者过滤
    limit: int = 100                 # 最大返回数量
    unread_only: bool = False        # 是否只返回未读消息


@register_tool
class SendMessageTool(Tool[SendMessageInput, Dict[str, Any]]):
    """
    Agent间通信工具

    用于子Agent之间发送消息，支持点对点通信和广播。

    使用场景:
    - 向特定Agent发送消息
    - 广播消息给所有Agent
    - 协调多Agent任务
    - 报告进度和状态

    特点:
    - 支持点对点消息（指定接收者ID）
    - 支持广播（to="*"）
    - 消息持久化存储
    - 支持消息订阅机制
    """

    name = "send_message"
    description = "向其他Agent发送消息，支持点对点通信和广播"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._manager: Optional[MessageManager] = None

    def _get_manager(self) -> MessageManager:
        """获取消息管理器"""
        if self._manager is None:
            self._manager = get_message_manager()
        return self._manager

    async def validate(self, input_data: SendMessageInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.to or not input_data.to.strip():
            return ToolValidationError("to（接收者）不能为空，使用 '*' 表示广播")

        if not input_data.message or not input_data.message.strip():
            return ToolValidationError("message（消息内容）不能为空")

        return None

    async def execute(self, input_data: SendMessageInput) -> ToolResult:
        """执行发送消息操作"""
        manager = self._get_manager()

        try:
            recipient = input_data.to.strip()
            content = input_data.message.strip()
            sender = input_data.sender or "anonymous"

            # 确定消息类型
            message_type = MessageType.BROADCAST if recipient == "*" else MessageType.DIRECT

            # 发送消息
            message = await manager.send_message(
                sender=sender,
                recipient=recipient,
                content=content,
                message_type=message_type,
                metadata=input_data.metadata
            )

            return ToolResult.ok(
                data=message.to_dict(),
                message=f"消息已发送给 {recipient}",
                metadata={
                    "message_id": message.id,
                    "recipient": recipient,
                    "is_broadcast": recipient == "*",
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"发送消息失败: {str(e)}")
            )

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "接收者ID，使用 '*' 表示广播给所有Agent"
                },
                "message": {
                    "type": "string",
                    "description": "消息内容"
                },
                "sender": {
                    "type": "string",
                    "description": "发送者ID（可选，默认为当前Agent）"
                },
                "metadata": {
                    "type": "object",
                    "description": "额外元数据（可选）"
                }
            },
            "required": ["to", "message"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "消息ID"},
                "sender": {"type": "string", "description": "发送者ID"},
                "recipient": {"type": "string", "description": "接收者ID"},
                "content": {"type": "string", "description": "消息内容"},
                "type": {"type": "string", "description": "消息类型"},
                "timestamp": {"type": "number", "description": "时间戳"},
                "datetime": {"type": "string", "description": "ISO格式时间"},
                "metadata": {"type": "object", "description": "元数据"}
            }
        }
        return schema


@register_tool
class MessageHistoryTool(Tool[MessageHistoryInput, List[Dict[str, Any]]]):
    """
    消息历史工具

    获取Agent间的消息历史，支持多种过滤条件。
    """

    name = "message_history"
    description = "获取Agent间的消息历史记录"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._manager: Optional[MessageManager] = None

    def _get_manager(self) -> MessageManager:
        """获取消息管理器"""
        if self._manager is None:
            self._manager = get_message_manager()
        return self._manager

    async def validate(self, input_data: MessageHistoryInput) -> Optional[ToolError]:
        """验证输入参数"""
        if input_data.limit <= 0:
            return ToolValidationError("limit 必须为正数")

        if input_data.limit > 1000:
            return ToolValidationError("limit 最大为 1000")

        return None

    async def execute(self, input_data: MessageHistoryInput) -> ToolResult:
        """执行获取消息历史操作"""
        manager = self._get_manager()

        try:
            if input_data.unread_only and input_data.recipient:
                # 只获取未读消息
                messages = await manager.get_unread_messages(input_data.recipient)
            else:
                # 获取消息历史
                messages = await manager.get_message_history(
                    sender=input_data.sender,
                    recipient=input_data.recipient,
                    limit=input_data.limit
                )

            # 转换为字典列表
            message_list = [msg.to_dict() for msg in messages]

            return ToolResult.ok(
                data=message_list,
                message=f"获取到 {len(message_list)} 条消息",
                metadata={
                    "count": len(message_list),
                    "filter_sender": input_data.sender,
                    "filter_recipient": input_data.recipient,
                    "unread_only": input_data.unread_only,
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"获取消息历史失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "sender": {
                    "type": "string",
                    "description": "按发送者ID过滤（可选）"
                },
                "recipient": {
                    "type": "string",
                    "description": "按接收者ID过滤（可选）"
                },
                "limit": {
                    "type": "integer",
                    "description": "最大返回数量，默认100",
                    "default": 100,
                    "minimum": 1,
                    "maximum": 1000
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "是否只返回未读消息",
                    "default": False
                }
            }
        }
        schema["returns"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "sender": {"type": "string"},
                    "recipient": {"type": "string"},
                    "content": {"type": "string"},
                    "type": {"type": "string"},
                    "timestamp": {"type": "number"},
                    "datetime": {"type": "string"},
                    "metadata": {"type": "object"}
                }
            }
        }
        return schema
