"""One child-agent LLM/tool execution loop.

Durable lifecycle ownership belongs to :mod:`harness.agents`; this module only
executes one already-created agent record against its child harness.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
from typing import Any

from agents.types import (
    AgentDefinition,
    AgentExecutionConfig,
    AgentExecutionResult,
    AgentHooks,
    AgentIsolationMode,
    AgentMcpServerSpec,
    AgentMemoryScope,
    AgentPermissionMode,
    AgentSource,
    AgentToolResult,
    BuiltInAgentDefinition,
    CustomAgentDefinition,
    PluginAgentDefinition,
)
from harness import SessionHarness, TerminationReason
from services import ChatCompletionRequest, LLMService, Message
from state_core import AgentRecord
from tools import ToolRegistry
from tools.base import Tool, canonical_tool_name


class AgentExecutor:
    """Run a single child conversation without storing lifecycle state."""

    def __init__(
        self,
        agent_definition: AgentDefinition,
        *,
        config: AgentExecutionConfig | None = None,
        llm_service: LLMService | None = None,
    ) -> None:
        self.agent_definition = agent_definition
        self.config = config or AgentExecutionConfig()
        self.llm_service = llm_service or LLMService()

    @classmethod
    def from_record(
        cls,
        record: AgentRecord,
        *,
        config: AgentExecutionConfig | None = None,
        llm_service: LLMService | None = None,
    ) -> "AgentExecutor":
        return cls(
            _definition_from_snapshot(record.definition_snapshot),
            config=config,
            llm_service=llm_service,
        )

    def _resolve_tools(self, child_harness: SessionHarness) -> list[Tool]:
        registry = child_harness.tool_runtime.registry
        all_tools = [
            tool
            for name in registry.list_tools()
            if (tool := registry.get(name)) is not None
        ]
        allowed_tools = self.agent_definition.tools
        if allowed_tools is None or allowed_tools == ["*"]:
            resolved = all_tools
        else:
            allowed = {
                registry.resolve_name(name) or canonical_tool_name(name)
                for name in allowed_tools
            }
            resolved = [
                tool for tool in all_tools if self.tool_name(tool, registry) in allowed
            ]
        if self.agent_definition.disallowed_tools:
            denied = {
                registry.resolve_name(name) or canonical_tool_name(name)
                for name in self.agent_definition.disallowed_tools
            }
            resolved = [
                tool for tool in resolved if self.tool_name(tool, registry) not in denied
            ]
        context = child_harness.tool_runtime.tool_context(
            child_harness.runtime_context
        )
        return [tool for tool in resolved if _tool_is_enabled(tool, context)]

    @staticmethod
    def tool_name(tool: Tool, registry: Any = ToolRegistry) -> str:
        return registry.resolve_name(tool.name) or canonical_tool_name(tool.name)

    def _build_system_prompt(self) -> str:
        if self.agent_definition.get_system_prompt:
            return self.agent_definition.get_system_prompt()
        return (
            "You are an agent for Claude Code.\n"
            f"Agent Type: {self.agent_definition.agent_type}\n\n"
            f"{self.agent_definition.when_to_use}\n\n"
            "Complete the task and return a concise factual report."
        )

    async def run(
        self,
        record: AgentRecord,
        child_harness: SessionHarness,
        on_message: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentExecutionResult:
        if record.agent_id != child_harness.agent_id:
            raise ValueError("Agent record and child harness IDs must match")
        cancellation = child_harness.runtime_context.cancellation
        if cancellation.cancelled:
            raise asyncio.CancelledError

        registry = child_harness.tool_runtime.registry
        tools = self._resolve_tools(child_harness)
        exposed_tool_names = {self.tool_name(tool, registry) for tool in tools}
        llm_messages = [Message(role="system", content=self._build_system_prompt())]
        if self.agent_definition.initial_prompt:
            llm_messages.append(
                Message(role="user", content=self.agent_definition.initial_prompt)
            )
        llm_messages.append(Message(role="user", content=record.prompt))
        messages: list[dict[str, Any]] = []
        usage: dict[str, int] = {}
        tool_count = 0
        termination_reason = "max_turns"
        max_turns = self.agent_definition.max_turns or self.config.max_turns

        for _turn in range(max_turns):
            if cancellation.cancelled:
                raise asyncio.CancelledError
            schemas = []
            for tool in tools:
                spec = registry.get_spec(self.tool_name(tool, registry))
                if spec is not None:
                    schemas.append(spec.to_openai())
            model = record.definition_snapshot.get("model") or self.config.model
            if model == "inherit":
                model = self.config.model
            llm_task = asyncio.create_task(
                self.llm_service.chat_completion(
                    ChatCompletionRequest(
                        messages=llm_messages,
                        model=model,
                        temperature=self.config.temperature,
                        tools=schemas or None,
                        tool_choice="auto" if schemas else None,
                    )
                )
            )
            cancellation.track(llm_task)
            response = await llm_task
            for key, value in (response.usage or {}).items():
                if isinstance(value, int):
                    usage[key] = usage.get(key, 0) + value

            assistant: dict[str, Any] = {
                "role": "assistant",
                "content": response.content,
            }
            if response.tool_calls:
                assistant["tool_calls"] = response.tool_calls
            messages.append(assistant)
            llm_messages.append(
                Message(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )
            if on_message is not None:
                on_message(
                    {
                        "type": "assistant",
                        "agent_id": record.agent_id,
                        "content": response.content,
                    }
                )
            if not response.tool_calls:
                termination_reason = "completed"
                break

            tool_count += len(response.tool_calls)
            for tool_call in response.tool_calls:
                if cancellation.cancelled:
                    raise asyncio.CancelledError
                function = tool_call.get("function", {})
                tool_name = function.get("name", "")
                raw_arguments = function.get("arguments", "{}")
                try:
                    arguments = (
                        json.loads(raw_arguments)
                        if isinstance(raw_arguments, str)
                        else raw_arguments
                    )
                except json.JSONDecodeError:
                    arguments = {}
                canonical = registry.resolve_name(tool_name) or canonical_tool_name(
                    tool_name
                )
                if canonical not in exposed_tool_names:
                    execution_name = canonical
                    result_success = False
                    result_data = {"error": f"Tool {tool_name!r} is not available"}
                else:
                    execution = await child_harness.tool_runtime.execute(
                        tool_name, arguments, child_harness.runtime_context
                    )
                    if execution.termination_reason is TerminationReason.CANCELLED:
                        raise asyncio.CancelledError
                    execution_name = execution.tool_name
                    result = execution.result
                    result_success = result.success
                    result_data = (
                        result.data if result.success else {"error": str(result.error)}
                    )
                content = (
                    json.dumps(result_data, ensure_ascii=False)
                    if isinstance(result_data, dict)
                    else str(result_data)
                )
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", ""),
                    "name": execution_name,
                    "content": content,
                }
                messages.append(tool_message)
                llm_messages.append(
                    Message(
                        role="tool",
                        content=content,
                        tool_call_id=tool_message["tool_call_id"],
                        name=execution_name,
                    )
                )
                if on_message is not None:
                    on_message(
                        {
                            "type": "tool_result",
                            "agent_id": record.agent_id,
                            "tool_name": execution_name,
                            "success": result_success,
                        }
                    )

        content_blocks: list[dict[str, str]] = []
        for message in reversed(messages):
            if message.get("role") == "assistant" and message.get("content"):
                content_blocks = [{"type": "text", "text": message["content"]}]
                break
        return AgentExecutionResult(
            content=content_blocks,
            usage=usage,
            tool_count=tool_count,
            termination_reason=termination_reason,
        )


def _tool_is_enabled(tool: Tool, context: dict[str, Any]) -> bool:
    predicate = getattr(tool, "is_enabled", None)
    if predicate is None:
        return True
    if not callable(predicate):
        return bool(predicate)
    parameters = inspect.signature(predicate).parameters
    return bool(predicate() if not parameters else predicate(context))


def _definition_from_snapshot(snapshot: Any) -> AgentDefinition:
    if not isinstance(snapshot, dict):
        snapshot = dict(snapshot)
    hooks = snapshot.get("hooks")
    mcp_servers = snapshot.get("mcp_servers")
    common: dict[str, Any] = {
        "agent_type": snapshot["agent_type"],
        "when_to_use": snapshot["when_to_use"],
        "tools": snapshot.get("tools"),
        "disallowed_tools": snapshot.get("disallowed_tools"),
        "skills": snapshot.get("skills"),
        "mcp_servers": (
            [AgentMcpServerSpec(**item) for item in mcp_servers]
            if mcp_servers is not None
            else None
        ),
        "hooks": AgentHooks(**hooks) if hooks is not None else None,
        "color": snapshot.get("color"),
        "model": snapshot.get("model"),
        "effort": snapshot.get("effort"),
        "permission_mode": (
            AgentPermissionMode(snapshot["permission_mode"])
            if snapshot.get("permission_mode") is not None
            else None
        ),
        "max_turns": snapshot.get("max_turns"),
        "filename": snapshot.get("filename"),
        "base_dir": snapshot.get("base_dir"),
        "critical_system_reminder": snapshot.get("critical_system_reminder"),
        "required_mcp_servers": snapshot.get("required_mcp_servers"),
        "background": bool(snapshot.get("background", False)),
        "initial_prompt": snapshot.get("initial_prompt"),
        "memory": (
            AgentMemoryScope(snapshot["memory"])
            if snapshot.get("memory") is not None
            else None
        ),
        "isolation": (
            AgentIsolationMode(snapshot["isolation"])
            if snapshot.get("isolation") is not None
            else None
        ),
        "omit_claude_md": bool(snapshot.get("omit_claude_md", False)),
    }
    system_prompt = str(snapshot["system_prompt"])

    def get_system_prompt() -> str:
        return system_prompt

    source = AgentSource(snapshot["source"])
    if source is AgentSource.BUILT_IN:
        return BuiltInAgentDefinition(
            **common, source=AgentSource.BUILT_IN, get_system_prompt=get_system_prompt
        )
    if source is AgentSource.PLUGIN:
        return PluginAgentDefinition(
            **common,
            source=AgentSource.PLUGIN,
            plugin=str(snapshot.get("plugin", "")),
            get_system_prompt=get_system_prompt,
        )
    return CustomAgentDefinition(
        **common, source=source, get_system_prompt=get_system_prompt
    )


class SpawnAgentManager:
    """Temporary compatibility adapter around an explicitly supplied scheduler."""

    def __init__(self, scheduler=None) -> None:
        self.scheduler = scheduler

    def _require_scheduler(self):
        if self.scheduler is None:
            raise RuntimeError("SpawnAgentManager requires an explicit AgentScheduler")
        return self.scheduler

    async def spawn_agent(
        self,
        agent_type: str,
        prompt: str,
        parent_session_id: str | None = None,
        config: AgentExecutionConfig | None = None,
        is_async: bool = False,
    ) -> str:
        from agents.types import AgentRequest

        scheduler = self._require_scheduler()
        record = await scheduler.spawn(
            AgentRequest(
                prompt=prompt,
                agent_type=agent_type,
                description=(config.description if config else None) or prompt,
                background=is_async,
                parent_agent_id=parent_session_id,
                model=config.model if config else None,
                cwd=config.workspace_root if config else None,
            )
        )
        return record.agent_id

    async def wait_for_agent(
        self, agent_id: str, timeout: float | None = None
    ) -> AgentToolResult:
        record = await self._require_scheduler().wait(agent_id, timeout)
        output = record.output if isinstance(record.output, dict) else {}
        content = output.get("content", [])
        usage = dict(record.usage)
        return AgentToolResult(
            agent_id=record.agent_id,
            agent_type=record.agent_type,
            content=content,
            total_tool_use_count=int(output.get("tool_count", 0)),
            total_duration_ms=0,
            total_tokens=int(usage.get("total_tokens", 0)),
            usage=usage,
            termination_reason=(
                record.termination_reason.value
                if record.termination_reason is not None
                else record.status.value
            ),
            error=(record.error or {}).get("message") if record.error else None,
        )

    def get_agent_status(self, agent_id: str) -> dict[str, Any] | None:
        try:
            record = self._require_scheduler().status(agent_id)
        except Exception:
            return None
        return {
            "agent_id": record.agent_id,
            "agent_type": record.agent_type,
            "status": record.status.value,
            "tool_use_count": int(
                record.output.get("tool_count", 0)
                if isinstance(record.output, dict)
                else 0
            ),
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "completed_at": record.finished_at.isoformat() if record.finished_at else None,
        }

    def abort_agent(self, agent_id: str) -> None:
        asyncio.create_task(self._require_scheduler().stop(agent_id))


def get_spawn_agent_manager(scheduler=None) -> SpawnAgentManager:
    return SpawnAgentManager(scheduler)


def get_agent_manager(scheduler=None) -> SpawnAgentManager:
    return get_spawn_agent_manager(scheduler)


AgentManager = SpawnAgentManager


__all__ = [
    "AgentExecutor",
    "AgentManager",
    "SpawnAgentManager",
    "get_agent_manager",
    "get_spawn_agent_manager",
]
