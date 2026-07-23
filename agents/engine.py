"""
Agent 执行引擎
实现 Agent 的完整生命周期管理
对齐 Claude Code 的 runAgent.ts
"""
import asyncio
import logging
import uuid
import json
from typing import Optional, Dict, Any, List, Callable, AsyncIterator
from datetime import datetime
from pathlib import Path

from agents.types import (
    AgentDefinition,
    AgentContext,
    AgentToolResult,
    AgentExecutionConfig,
    AgentError,
    AgentExecutionError,
)
from agents.built_in import get_agent_by_type
from tools import ToolRegistry
from tools.base import Tool, canonical_tool_name
from harness import CancellationToken, PermissionMode, RuntimeContext, ToolRuntime
from services import LLMService, Message, ChatCompletionRequest

logger = logging.getLogger(__name__)


class AgentExecutor:
    """
    Agent 执行器

    负责执行 Agent 的完整生命周期：
    1. 初始化 Agent 上下文
    2. 准备工具列表
    3. 运行对话循环
    4. 收集结果
    5. 清理资源
    """

    def __init__(
        self,
        agent_definition: AgentDefinition,
        prompt: str,
        parent_session_id: Optional[str] = None,
        config: Optional[AgentExecutionConfig] = None,
        llm_service: Optional[LLMService] = None,
        tool_runtime: Optional[ToolRuntime] = None,
    ):
        self.agent_definition = agent_definition
        self.prompt = prompt
        self.parent_session_id = parent_session_id
        self.config = config or AgentExecutionConfig()
        self.llm_service = llm_service or LLMService()
        self.tool_runtime = tool_runtime or ToolRuntime(
            ToolRegistry,
            default_timeout=self.config.tool_timeout,
        )
        self.agent_id = self._generate_agent_id()
        self.context = AgentContext(
            agent_id=self.agent_id,
            agent_type=agent_definition.agent_type,
            session_id=self.agent_id,
            parent_session_id=parent_session_id,
            started_at=datetime.now(),
            is_async=self.config.is_async,
        )
        self.cancellation = CancellationToken(parent=self.config.parent_cancellation)
        self._termination_reason = "max_turns"
        self._usage: Dict[str, int] = {}

    def _generate_agent_id(self) -> str:
        """生成 Agent ID"""
        return f"agent-{self.agent_definition.agent_type.lower()}-{uuid.uuid4().hex[:8]}"

    def _resolve_tools(self) -> List[Tool]:
        """
        解析 Agent 可用工具

        根据 agent_definition.tools 和 disallowed_tools 过滤
        """
        all_tools: List[Tool] = []
        for name in ToolRegistry.list_tools():
            tool = ToolRegistry.get(name)
            if tool:
                all_tools.append(tool)

        # 如果 tools 为 None 或 ['*']，允许所有工具
        allowed_tools = self.agent_definition.tools
        if allowed_tools is None or (len(allowed_tools) == 1 and allowed_tools[0] == "*"):
            resolved = all_tools
        else:
            allowed = {canonical_tool_name(name) for name in allowed_tools}
            resolved = [t for t in all_tools if self.tool_name(t) in allowed]

        # 应用禁止工具列表
        if self.agent_definition.disallowed_tools:
            denied = {
                canonical_tool_name(name) for name in self.agent_definition.disallowed_tools
            }
            resolved = [t for t in resolved if self.tool_name(t) not in denied]

        return resolved

    @staticmethod
    def tool_name(tool: Tool) -> str:
        return ToolRegistry.resolve_name(tool.name) or canonical_tool_name(tool.name)

    def _build_system_prompt(self) -> str:
        """构建系统提示词"""
        base_prompt = f"""You are an agent for Claude Code, Anthropic's official CLI for Claude.
Agent Type: {self.agent_definition.agent_type}

{self.agent_definition.when_to_use}

CRITICAL RULES:
1. Use tools silently - DO NOT output text between tool calls
2. Complete the task fully—don't gold-plate, but don't leave it half-done
3. When you complete the task, respond with ONLY a concise report covering what was done and key findings
4. Your response MUST begin with "Scope:" followed by your findings
5. Be factual and concise, under 500 words unless specified otherwise

Output format:
  Scope: <one sentence summary of what you did>
  Result: <key findings and actions taken>
  Key files: <relevant file paths if applicable>"""

        # 如果是内置Agent且有自定义prompt，使用自定义的
        if self.agent_definition.get_system_prompt:
            return self.agent_definition.get_system_prompt()

        return base_prompt

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

        # 构建初始消息
        system_prompt = self._build_system_prompt()
        llm_messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=self.prompt),
        ]

        # 如果有 initial_prompt，添加到用户消息前
        if self.agent_definition.initial_prompt:
            llm_messages.insert(1, Message(role="user", content=self.agent_definition.initial_prompt))

        max_turns = self.agent_definition.max_turns or self.config.max_turns

        for turn in range(max_turns):
            if self.cancellation.cancelled:
                logger.info(f"Agent {self.agent_id} aborted")
                self._termination_reason = "cancelled"
                break

            # 构建工具 schema
            tools_schema = [
                ToolRegistry.get_spec(self.tool_name(tool)).to_openai()
                for tool in tools
            ]

            try:
                # 调用 LLM
                llm_task = asyncio.create_task(
                    self.llm_service.chat_completion(
                        ChatCompletionRequest(
                            messages=llm_messages,
                            model=self.config.model,
                            temperature=self.config.temperature,
                            tools=tools_schema if tools_schema else None,
                            tool_choice="auto" if tools_schema else None,
                        )
                    )
                )
                self.cancellation.track(llm_task)
                response = await llm_task
                for key, value in (response.usage or {}).items():
                    if isinstance(value, int):
                        self._usage[key] = self._usage.get(key, 0) + value
            except Exception as e:
                logger.error(f"LLM call failed in agent {self.agent_id}: {e}")
                raise AgentExecutionError(f"LLM call failed: {e}")

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
                tool_calls=response.tool_calls,
            ))

            if on_message:
                on_message({
                    "type": "assistant",
                    "agent_id": self.agent_id,
                    "content": response.content,
                })

            # 检查是否有工具调用
            if not response.tool_calls:
                # 没有工具调用，任务完成
                logger.info(f"Agent {self.agent_id} completed after {turn + 1} turns")
                self._termination_reason = "completed"
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

                permission_mode = PermissionMode.DEFAULT
                configured_mode = self.agent_definition.permission_mode
                if configured_mode and configured_mode.value == "bypass":
                    permission_mode = PermissionMode.BYPASS
                elif configured_mode and configured_mode.value == "plan":
                    permission_mode = PermissionMode.PLAN
                runtime_context = RuntimeContext(
                    session_id=self.agent_id,
                    workspace_root=(self.config.workspace_root or Path.cwd()).resolve(),
                    permission_mode=permission_mode,
                    approval_callback=self.config.approval_callback,
                    cancellation=self.cancellation,
                    tool_timeout=self.config.tool_timeout,
                    metadata={"agent_context": self.context},
                )
                execution = await self.tool_runtime.execute(
                    tool_name,
                    tool_args,
                    runtime_context,
                )
                tool_name = execution.tool_name
                tool_result = execution.result
                result_data = (
                    tool_result.data
                    if tool_result.success
                    else {"error": str(tool_result.error)}
                )
                result_success = tool_result.success

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

    async def execute(
        self,
        on_message: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> AgentToolResult:
        """
        执行 Agent

        Args:
            on_message: 消息回调函数

        Returns:
            AgentToolResult: 执行结果
        """
        start_time = datetime.now()
        logger.info(f"Starting agent {self.agent_id} (type: {self.agent_definition.agent_type})")

        try:
            # 解析工具
            tools = self._resolve_tools()
            logger.debug(f"Agent {self.agent_id} resolved {len(tools)} tools")

            # 运行对话循环
            messages = await self._run_conversation_loop(tools, on_message)

            # 构建结果
            end_time = datetime.now()
            duration_ms = int((end_time - start_time).total_seconds() * 1000)

            # 提取最后的助手消息内容
            content = []
            for msg in reversed(messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    content.append({"type": "text", "text": msg["content"]})
                    break

            if not content:
                content = [{"type": "text", "text": "Agent completed without output"}]

            self.context.status = (
                "killed" if self._termination_reason == "cancelled" else "completed"
            )
            self.context.completed_at = end_time
            self.context.messages = messages

            result = AgentToolResult(
                agent_id=self.agent_id,
                agent_type=self.agent_definition.agent_type,
                content=content,
                total_tool_use_count=self.context.tool_use_count,
                total_duration_ms=duration_ms,
                total_tokens=self._usage.get(
                    "total_tokens",
                    self._usage.get("input_tokens", 0) + self._usage.get("output_tokens", 0),
                ),
                usage=dict(self._usage),
                termination_reason=self._termination_reason,
            )

            logger.info(f"Agent {self.agent_id} completed in {duration_ms}ms")
            return result

        except asyncio.CancelledError:
            self.context.status = "killed"
            self.context.completed_at = datetime.now()
            raise
        except Exception as e:
            self.context.status = "failed"
            logger.error(f"Agent {self.agent_id} failed: {e}")
            raise AgentExecutionError(f"Agent execution failed: {e}")

    async def execute_stream(
        self,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        流式执行 Agent

        Yields:
            事件字典
        """
        start_time = datetime.now()

        # 发送开始事件
        yield {
            "type": "agent_start",
            "agent_id": self.agent_id,
            "agent_type": self.agent_definition.agent_type,
            "timestamp": start_time.isoformat(),
        }

        try:
            # 运行对话循环，带回调
            messages = []

            def on_message(msg: Dict[str, Any]):
                messages.append(msg)

            result = await self.execute(on_message=on_message)

            # 发送完成事件
            yield {
                "type": "agent_complete",
                "agent_id": self.agent_id,
                "result": {
                    "content": result.content,
                    "total_tool_use_count": result.total_tool_use_count,
                    "total_duration_ms": result.total_duration_ms,
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
        self.cancellation.cancel()
        self._termination_reason = "cancelled"
        self.context.status = "killed"
        logger.info(f"Agent {self.agent_id} abort requested")


class SpawnAgentManager:
    """
    Claude Code 对齐的「子 Agent / spawn」运行时注册表。

    管理由 `AgentExecutor` 承载的、通过 `spawn_agent` 创建的任务会话。
    """

    def __init__(self, executor_factory: Callable[..., AgentExecutor] = AgentExecutor):
        self._executor_factory = executor_factory
        self._agents: Dict[str, AgentExecutor] = {}
        self._results: Dict[str, AgentToolResult] = {}
        self._tasks: Dict[str, asyncio.Task[AgentToolResult]] = {}

    async def spawn_agent(
        self,
        agent_type: str,
        prompt: str,
        parent_session_id: Optional[str] = None,
        config: Optional[AgentExecutionConfig] = None,
        is_async: bool = False,
    ) -> str:
        """
        创建并启动 Agent

        Args:
            agent_type: Agent 类型
            prompt: 任务描述
            parent_session_id: 父会话ID
            config: 执行配置
            is_async: 是否异步执行

        Returns:
            Agent ID
        """
        # 获取 Agent 定义
        agent_def = get_agent_by_type(agent_type)
        if not agent_def:
            raise AgentError(f"Unknown agent type: {agent_type}")

        # 创建执行器
        executor = self._executor_factory(
            agent_definition=agent_def,
            prompt=prompt,
            parent_session_id=parent_session_id,
            config=config or AgentExecutionConfig(is_async=is_async),
        )

        self._agents[executor.agent_id] = executor

        if is_async:
            # 异步执行，立即返回 Agent ID
            self._tasks[executor.agent_id] = asyncio.create_task(
                self._run_async(executor)
            )
            return executor.agent_id
        else:
            # 同步执行
            await self._run_async(executor)
            return executor.agent_id

    async def _run_async(self, executor: AgentExecutor) -> AgentToolResult:
        """异步运行 Agent"""
        try:
            result = await executor.execute()
            self._results[executor.agent_id] = result
            return result
        except asyncio.CancelledError:
            executor.context.status = "killed"
            result = self._cancelled_result(executor)
            self._results[executor.agent_id] = result
            return result
        except Exception as e:
            logger.error(f"Async agent {executor.agent_id} failed: {e}")
            executor.context.status = "failed"
            result = self._failed_result(executor, e)
            self._results[executor.agent_id] = result
            return result

    @staticmethod
    def _cancelled_result(executor: AgentExecutor) -> AgentToolResult:
        return AgentToolResult(
            agent_id=executor.agent_id,
            agent_type=executor.context.agent_type,
            content=[{"type": "text", "text": "Agent execution cancelled"}],
            total_tool_use_count=executor.context.tool_use_count,
            total_duration_ms=0,
            total_tokens=0,
            usage={},
            termination_reason="cancelled",
            error="Agent execution cancelled",
        )

    @staticmethod
    def _failed_result(executor: AgentExecutor, error: Exception) -> AgentToolResult:
        return AgentToolResult(
            agent_id=executor.agent_id,
            agent_type=executor.context.agent_type,
            content=[{"type": "text", "text": f"Agent execution failed: {error}"}],
            total_tool_use_count=executor.context.tool_use_count,
            total_duration_ms=0,
            total_tokens=0,
            usage={},
            termination_reason="failed",
            error=str(error),
        )

    async def wait_for_agent(self, agent_id: str, timeout: Optional[float] = None) -> AgentToolResult:
        """等待 Agent 完成"""
        executor = self._agents.get(agent_id)
        if not executor:
            raise AgentError(f"Agent {agent_id} not found")

        if agent_id in self._results:
            return self._results[agent_id]

        task = self._tasks.get(agent_id)
        if task is None:
            raise AgentError(f"Agent {agent_id} has not been started")

        try:
            if timeout is None:
                return await asyncio.shield(task)
            return await asyncio.wait_for(asyncio.shield(task), timeout)
        except asyncio.TimeoutError as exc:
            raise AgentError(f"Timed out waiting for agent {agent_id}") from exc
        except asyncio.CancelledError:
            result = self._cancelled_result(executor)
            self._results[agent_id] = result
            return result

    def get_agent_status(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """获取 Agent 状态"""
        executor = self._agents.get(agent_id)
        if not executor:
            return None

        return {
            "agent_id": agent_id,
            "agent_type": executor.context.agent_type,
            "status": executor.context.status,
            "tool_use_count": executor.context.tool_use_count,
            "started_at": executor.context.started_at.isoformat() if executor.context.started_at else None,
            "completed_at": executor.context.completed_at.isoformat() if executor.context.completed_at else None,
        }

    def abort_agent(self, agent_id: str):
        """中止 Agent"""
        executor = self._agents.get(agent_id)
        if executor:
            executor.abort()
        task = self._tasks.get(agent_id)
        if task and not task.done():
            task.cancel()

    def cleanup_agent(self, agent_id: str):
        """清理 Agent 资源"""
        task = self._tasks.pop(agent_id, None)
        if task and not task.done():
            task.cancel()
        if agent_id in self._agents:
            del self._agents[agent_id]
        if agent_id in self._results:
            del self._results[agent_id]


# 全局 spawn 运行时（与 agents.worker_pool.WorkerPoolManager 不同）
_spawn_agent_manager: Optional[SpawnAgentManager] = None


def get_spawn_agent_manager() -> SpawnAgentManager:
    """获取全局 SpawnAgentManager（子 Agent 执行）。"""
    global _spawn_agent_manager
    if _spawn_agent_manager is None:
        _spawn_agent_manager = SpawnAgentManager()
    return _spawn_agent_manager


def get_agent_manager() -> SpawnAgentManager:
    """兼容旧名：等同于 get_spawn_agent_manager。"""
    return get_spawn_agent_manager()


# 兼容旧代码中的类型名
AgentManager = SpawnAgentManager
