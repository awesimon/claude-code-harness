"""One child-agent LLM/tool execution loop.

Durable lifecycle ownership belongs to :mod:`harness.agents`; this module only
executes one already-created agent record against its child harness.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable, Mapping
from typing import Any

from agents.types import (
    AgentDefinition,
    AgentExecutionConfig,
    AgentExecutionResult,
    AgentToolResult,
    parse_agent_definition,
)
from harness import SessionHarness, TerminationReason
from services import ChatCompletionRequest, LLMService, Message
from state_core import AgentRecord
from tools import ToolRegistry
from tools.base import Tool, canonical_tool_name, to_json_value


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
            parse_agent_definition(
                record.definition_snapshot,
                expected_agent_type=record.agent_type,
            ),
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

    def _available_tools(
        self, child_harness: SessionHarness
    ) -> tuple[dict[str, Tool], list[dict[str, Any]]]:
        registry = child_harness.tool_runtime.registry
        available: dict[str, Tool] = {}
        schemas: list[dict[str, Any]] = []
        for tool in self._resolve_tools(child_harness):
            name = self.tool_name(tool, registry)
            spec = registry.get_spec(name)
            if spec is None:
                continue
            available[name] = tool
            schemas.append(spec.to_openai())
        return available, schemas

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
            _, schemas = self._available_tools(child_harness)
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
                tool_call_id = ""
                tool_name = ""
                execution_name = "invalid_tool_call"
                result_success = False
                try:
                    if not isinstance(tool_call, Mapping):
                        raise ValueError("Tool call must be an object")
                    raw_id = tool_call.get("id", "")
                    if isinstance(raw_id, str):
                        tool_call_id = raw_id
                    function = tool_call.get("function")
                    if not isinstance(function, Mapping):
                        raise ValueError("Tool call function must be an object")
                    tool_name = function.get("name")
                    if not isinstance(tool_name, str) or not tool_name:
                        raise ValueError("Tool call name must be a non-empty string")
                    execution_name = (
                        registry.resolve_name(tool_name)
                        or canonical_tool_name(tool_name)
                    )
                    raw_arguments = function.get("arguments", "{}")
                    if isinstance(raw_arguments, str):
                        try:
                            arguments = json.loads(raw_arguments)
                        except json.JSONDecodeError as exc:
                            raise ValueError(
                                "Tool call arguments must be valid JSON"
                            ) from exc
                    elif isinstance(raw_arguments, Mapping):
                        arguments = dict(raw_arguments)
                    else:
                        raise ValueError(
                            "Tool call arguments must be a JSON object"
                        )
                    if not isinstance(arguments, dict):
                        raise ValueError(
                            "Tool call arguments must decode to a JSON object"
                        )

                    available, _ = self._available_tools(child_harness)
                    if execution_name not in available:
                        result_data: Any = {
                            "error": f"Tool {tool_name!r} is not available"
                        }
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
                            result.data
                            if result.success
                            else {"error": str(result.error)}
                        )
                except (TypeError, ValueError) as exc:
                    result_data = {"error": str(exc)}
                try:
                    json_result = to_json_value(result_data)
                except (TypeError, ValueError):
                    result_success = False
                    json_result = {"error": "Tool result is not valid JSON data"}
                content = json.dumps(json_result, ensure_ascii=False)
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
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

    def abort_agent(self, agent_id: str) -> asyncio.Task[Any]:
        return asyncio.create_task(self._require_scheduler().stop(agent_id))


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
