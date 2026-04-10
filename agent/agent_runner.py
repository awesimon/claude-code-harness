"""
Agent Runner - 通用 Agent 执行器

实现 Agent 的完整生命周期管理：
1. 初始化 Agent 上下文和系统提示词
2. 运行对话循环（LLM → Tool → Observation → LLM）
3. 收集和处理结果
4. 清理资源

参考: agents/engine.py, 但更专注于特定 Agent 类型
"""

import asyncio
import logging
import uuid
import json
from typing import Optional, Dict, Any, List, Callable, AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from services.llm_service import LLMService, Message, ChatCompletionRequest
from tools.base import Tool, ToolRegistry

logger = logging.getLogger(__name__)


class AgentStatus(Enum):
    """Agent 执行状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass
class AgentContext:
    """Agent 执行上下文"""
    agent_id: str
    agent_type: str
    session_id: str
    parent_session_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    tool_use_count: int = 0
    token_count: int = 0
    status: AgentStatus = AgentStatus.PENDING


@dataclass
class AgentResult:
    """Agent 执行结果"""
    agent_id: str
    agent_type: str
    status: AgentStatus
    content: str
    tool_use_count: int
    duration_ms: int
    token_count: int
    messages: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class AgentConfig:
    """Agent 配置"""
    name: str
    agent_type: str
    system_prompt: str
    tools: List[str] = field(default_factory=list)
    disallowed_tools: List[str] = field(default_factory=list)
    max_turns: int = 50
    model: Optional[str] = None
    temperature: float = 0.7
    is_read_only: bool = False  # 是否为只读 Agent（禁止修改文件）


class AgentRunner:
    """
    通用 Agent 运行器

    负责执行 Agent 的完整生命周期：
    1. 准备工具和环境
    2. 运行对话循环
    3. 收集结果
    4. 清理资源
    """

    def __init__(
        self,
        config: AgentConfig,
        prompt: str,
        parent_session_id: Optional[str] = None,
    ):
        self.config = config
        self.prompt = prompt
        self.parent_session_id = parent_session_id
        self.agent_id = self._generate_agent_id()
        self.context = AgentContext(
            agent_id=self.agent_id,
            agent_type=config.agent_type,
            session_id=self.agent_id,
            parent_session_id=parent_session_id,
            started_at=datetime.now(),
        )
        self._abort_event = asyncio.Event()
        self._llm_service = LLMService()

    def _generate_agent_id(self) -> str:
        """生成 Agent ID"""
        return f"{self.config.agent_type.lower()}-{uuid.uuid4().hex[:8]}"

    def _resolve_tools(self) -> List[Tool]:
        """解析 Agent 可用工具"""
        all_tools = []
        for name in ToolRegistry.list_tools():
            tool = ToolRegistry.get(name)
            if tool:
                all_tools.append(tool)

        # 如果 tools 为空或包含 '*'，允许所有工具
        allowed_tools = self.config.tools
        if not allowed_tools or (len(allowed_tools) == 1 and allowed_tools[0] == "*"):
            resolved = all_tools
        else:
            resolved = [t for t in all_tools if t.name in allowed_tools]

        # 应用禁止工具列表
        if self.config.disallowed_tools:
            resolved = [t for t in resolved if t.name not in self.config.disallowed_tools]

        # 对于只读 Agent，过滤掉可能修改文件的工具
        if self.config.is_read_only:
            write_tools = {"write_file", "edit_file", "write", "edit"}
            resolved = [t for t in resolved if t.name not in write_tools]

        return resolved

    def _build_messages(self) -> List[Message]:
        """构建初始消息列表"""
        return [
            Message(role="system", content=self.config.system_prompt),
            Message(role="user", content=self.prompt),
        ]

    async def _run_conversation_loop(
        self,
        tools: List[Tool],
        on_message: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        运行对话循环

        实现 LLM → Tool → Observation → LLM 的闭环
        """
        messages: List[Dict[str, Any]] = []
        llm_messages = self._build_messages()

        for turn in range(self.config.max_turns):
            if self._abort_event.is_set():
                logger.info(f"Agent {self.agent_id} aborted at turn {turn}")
                break

            # 构建工具 schema
            tools_schema = []
            for tool in tools:
                schema = tool.get_schema()
                tools_schema.append({
                    "type": "function",
                    "function": {
                        "name": schema.get("name", tool.name),
                        "description": schema.get("description", tool.description),
                        "parameters": schema.get("parameters", schema.get("input_schema", {"type": "object", "properties": {}}))
                    }
                })

            try:
                # 调用 LLM
                response = await self._llm_service.chat_completion(
                    ChatCompletionRequest(
                        messages=llm_messages,
                        model=self.config.model,
                        temperature=self.config.temperature,
                        tools=tools_schema if tools_schema else None,
                        tool_choice="auto" if tools_schema else None,
                    )
                )
            except Exception as e:
                logger.error(f"LLM call failed in agent {self.agent_id}: {e}")
                raise RuntimeError(f"LLM call failed: {e}")

            # 处理助手消息
            assistant_message = {
                "role": "assistant",
                "content": response.content,
            }
            if response.tool_calls:
                assistant_message["tool_calls"] = response.tool_calls

            messages.append(assistant_message)
            llm_messages.append(Message(
                role="assistant",
                content=response.content,
            ))

            if on_message:
                on_message({
                    "type": "assistant",
                    "agent_id": self.agent_id,
                    "content": response.content,
                })

            # 检查是否有工具调用
            if not response.tool_calls:
                logger.info(f"Agent {self.agent_id} completed after {turn + 1} turns")
                break

            # 执行工具
            self.context.tool_use_count += len(response.tool_calls)

            for tool_call in response.tool_calls:
                tool_name = tool_call.get("function", {}).get("name", "")
                tool_args_str = tool_call.get("function", {}).get("arguments", "{}")

                try:
                    tool_args = json.loads(tool_args_str) if isinstance(tool_args_str, str) else tool_args_str
                except json.JSONDecodeError:
                    tool_args = {}

                # 只读 Agent 安全检查
                if self.config.is_read_only:
                    if tool_name in {"write_file", "edit_file", "write", "edit"}:
                        result_data = {"error": f"Tool '{tool_name}' is not allowed in read-only mode"}
                        result_success = False
                    elif tool_name == "bash":
                        # 检查 Bash 命令是否安全
                        command = tool_args.get("command", "")
                        if self._is_dangerous_command(command):
                            result_data = {"error": f"Command not allowed in read-only mode: {command}"}
                            result_success = False
                        else:
                            result_data, result_success = await self._execute_tool(tool_name, tool_args)
                    else:
                        result_data, result_success = await self._execute_tool(tool_name, tool_args)
                else:
                    result_data, result_success = await self._execute_tool(tool_name, tool_args)

                # 添加工具结果到消息
                tool_result_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", ""),
                    "name": tool_name,
                    "content": json.dumps(result_data, ensure_ascii=False) if isinstance(result_data, dict) else str(result_data),
                }
                messages.append(tool_result_message)
                llm_messages.append(Message(
                    role="tool",
                    content=tool_result_message["content"],
                    tool_call_id=tool_result_message["tool_call_id"],
                    name=tool_name,
                ))

                if on_message:
                    on_message({
                        "type": "tool_result",
                        "agent_id": self.agent_id,
                        "tool_name": tool_name,
                        "success": result_success,
                    })

        return messages

    def _is_dangerous_command(self, command: str) -> bool:
        """
        检查 Bash 命令是否在只读模式下危险

        禁止的命令：创建、修改、删除文件的操作
        """
        dangerous_patterns = [
            # 文件创建/修改
            r">\s*\S", r">>\s*\S",  # 重定向到文件
            r"\btouch\b", r"\bmkdir\b", r"\bmkfile\b",
            r"\brm\b", r"\brmdir\b", r"\bdel\b", r"\bremove\b",
            r"\bmv\b", r"\bmove\b", r"\bcp\b", r"\bcopy\b",
            r"git\s+add\b", r"git\s+commit\b", r"git\s+push\b",
            r"npm\s+install\b", r"pip\s+install\b", r"pip3\s+install\b",
            r"\bchmod\b", r"\bchown\b",
            r"echo\s+.*>",  # echo 重定向
            r"cat\s+.*>",   # cat 重定向
            # 危险操作
            r"\bsudo\b", r"\bsu\s+-",
        ]

        import re
        command_lower = command.lower().strip()

        for pattern in dangerous_patterns:
            if re.search(pattern, command_lower):
                return True

        return False

    async def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> tuple:
        """执行单个工具"""
        tool = ToolRegistry.get(tool_name)
        if not tool:
            return {"error": f"Tool '{tool_name}' not found"}, False

        try:
            tool_result = await tool.run(tool_args, {
                "session_id": self.agent_id,
                "agent_context": self.context,
            })
            result_data = tool_result.data if tool_result.success else {"error": str(tool_result.error)}
            return result_data, tool_result.success
        except Exception as e:
            return {"error": str(e)}, False

    async def run(
        self,
        on_message: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> AgentResult:
        """
        运行 Agent

        Args:
            on_message: 消息回调函数

        Returns:
            AgentResult: 执行结果
        """
        start_time = datetime.now()
        self.context.status = AgentStatus.RUNNING
        logger.info(f"Starting agent {self.agent_id} (type: {self.config.agent_type})")

        try:
            # 解析工具
            tools = self._resolve_tools()
            logger.debug(f"Agent {self.agent_id} resolved {len(tools)} tools: {[t.name for t in tools]}")

            # 运行对话循环
            messages = await self._run_conversation_loop(tools, on_message)

            # 构建结果
            end_time = datetime.now()
            duration_ms = int((end_time - start_time).total_seconds() * 1000)

            # 提取最后的助手消息内容
            content = ""
            for msg in reversed(messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    content = msg["content"]
                    break

            if not content:
                content = "Agent completed without output"

            self.context.status = AgentStatus.COMPLETED
            self.context.completed_at = end_time
            self.context.messages = messages

            result = AgentResult(
                agent_id=self.agent_id,
                agent_type=self.config.agent_type,
                status=AgentStatus.COMPLETED,
                content=content,
                tool_use_count=self.context.tool_use_count,
                duration_ms=duration_ms,
                token_count=self.context.token_count,
                messages=messages,
            )

            logger.info(f"Agent {self.agent_id} completed in {duration_ms}ms")
            return result

        except Exception as e:
            self.context.status = AgentStatus.FAILED
            logger.error(f"Agent {self.agent_id} failed: {e}")

            end_time = datetime.now()
            duration_ms = int((end_time - start_time).total_seconds() * 1000)

            return AgentResult(
                agent_id=self.agent_id,
                agent_type=self.config.agent_type,
                status=AgentStatus.FAILED,
                content="",
                tool_use_count=self.context.tool_use_count,
                duration_ms=duration_ms,
                token_count=self.context.token_count,
                messages=self.context.messages,
                error=str(e),
            )

    async def run_stream(
        self,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        流式运行 Agent

        Yields:
            事件字典
        """
        start_time = datetime.now()
        self.context.status = AgentStatus.RUNNING

        # 发送开始事件
        yield {
            "type": "agent_start",
            "agent_id": self.agent_id,
            "agent_type": self.config.agent_type,
            "timestamp": start_time.isoformat(),
        }

        messages_buffer: List[Dict[str, Any]] = []

        def on_message(msg: Dict[str, Any]):
            messages_buffer.append(msg)
            # 转发消息
            msg["agent_id"] = self.agent_id

        try:
            result = await self.run(on_message=on_message)

            # 发送完成事件
            yield {
                "type": "agent_complete",
                "agent_id": self.agent_id,
                "result": {
                    "status": result.status.value,
                    "content": result.content,
                    "tool_use_count": result.tool_use_count,
                    "duration_ms": result.duration_ms,
                },
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            yield {
                "type": "agent_error",
                "agent_id": self.agent_id,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }

    def abort(self):
        """中止 Agent 执行"""
        self._abort_event.set()
        self.context.status = AgentStatus.ABORTED
        logger.info(f"Agent {self.agent_id} abort requested")


# 便利函数

async def run_agent(
    config: AgentConfig,
    prompt: str,
    parent_session_id: Optional[str] = None,
) -> AgentResult:
    """
    便捷函数：运行 Agent 并返回结果

    Args:
        config: Agent 配置
        prompt: 用户提示词
        parent_session_id: 父会话 ID

    Returns:
        AgentResult: 执行结果
    """
    runner = AgentRunner(config, prompt, parent_session_id)
    return await runner.run()


async def run_agent_stream(
    config: AgentConfig,
    prompt: str,
    parent_session_id: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """
    便捷函数：流式运行 Agent

    Args:
        config: Agent 配置
        prompt: 用户提示词
        parent_session_id: 父会话 ID

    Yields:
        事件字典
    """
    runner = AgentRunner(config, prompt, parent_session_id)
    async for event in runner.run_stream():
        yield event
