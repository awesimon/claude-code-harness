"""Compatibility exports for harness-backed agent runtime tools."""

from typing import Any

from .agent_runtime_tools import (
    AgentDestroyInput,
    AgentDestroyTool,
    AgentListInput,
    AgentListTool,
    AgentTool,
    AgentToolInput,
)

AgentToolOutput = dict[str, Any]

__all__ = [
    "AgentDestroyInput",
    "AgentDestroyTool",
    "AgentListInput",
    "AgentListTool",
    "AgentTool",
    "AgentToolInput",
    "AgentToolOutput",
]
