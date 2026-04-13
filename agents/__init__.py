"""
Agent 子系统（统一包）

- **根模块**（本目录 `types` / `engine` / `built_in` / `fork` / `tool`）：对齐 Claude Code 的
  内置 Agent 定义、`AgentExecutor`、`SpawnAgentManager`（spawn/abort 子会话）。
- **`worker_pool`**：线程池式 Worker、`Coordinator`、Plan/Explore Runner 等。

导入建议：
- 子 Agent 执行：`from agents import get_spawn_agent_manager, SpawnAgentManager`
- Worker 池：`from agents.worker_pool import WorkerPoolManager, Agent, Task`
"""

from .types import (
    AgentDefinition,
    BuiltInAgentDefinition,
    CustomAgentDefinition,
    PluginAgentDefinition,
    AgentContext,
    AgentToolResult,
    AgentExecutionConfig,
    AgentSource,
    AgentMemoryScope,
    AgentIsolationMode,
    AgentPermissionMode,
    AgentError,
    AgentNotFoundError,
    AgentValidationError,
    AgentExecutionError,
    is_built_in_agent,
    is_custom_agent,
    is_plugin_agent,
    is_one_shot_agent,
    ONE_SHOT_BUILTIN_AGENT_TYPES,
    VERIFICATION_AGENT_TYPE,
)
from .built_in import (
    EXPLORE_AGENT,
    PLAN_AGENT,
    GENERAL_PURPOSE_AGENT,
    CODE_AGENT,
    TEST_AGENT,
    VERIFICATION_AGENT,
    get_built_in_agents,
    get_agent_by_type,
)
from .engine import (
    AgentExecutor,
    SpawnAgentManager,
    get_agent_manager,
    get_spawn_agent_manager,
)
from .fork import (
    ForkSubagentManager,
    ForkConfig,
    build_forked_messages,
    build_child_message,
    build_worktree_notice,
    is_in_fork_child,
    get_fork_manager,
    FORK_BOILERPLATE_TAG,
    FORK_DIRECTIVE_PREFIX,
    FORK_PLACEHOLDER_RESULT,
)
from .tool import AgentTool, AgentToolInput

# 兼容：旧代码 `from agents import AgentManager` 仍指向 SpawnAgentManager
from .engine import AgentManager

__all__ = [
    "AgentDefinition",
    "BuiltInAgentDefinition",
    "CustomAgentDefinition",
    "PluginAgentDefinition",
    "AgentContext",
    "AgentToolResult",
    "AgentExecutionConfig",
    "AgentSource",
    "AgentMemoryScope",
    "AgentIsolationMode",
    "AgentPermissionMode",
    "AgentError",
    "AgentNotFoundError",
    "AgentValidationError",
    "AgentExecutionError",
    "ONE_SHOT_BUILTIN_AGENT_TYPES",
    "VERIFICATION_AGENT_TYPE",
    "EXPLORE_AGENT",
    "PLAN_AGENT",
    "GENERAL_PURPOSE_AGENT",
    "CODE_AGENT",
    "TEST_AGENT",
    "VERIFICATION_AGENT",
    "get_built_in_agents",
    "get_agent_by_type",
    "AgentExecutor",
    "SpawnAgentManager",
    "AgentManager",
    "get_agent_manager",
    "get_spawn_agent_manager",
    "ForkSubagentManager",
    "ForkConfig",
    "build_forked_messages",
    "build_child_message",
    "build_worktree_notice",
    "is_in_fork_child",
    "get_fork_manager",
    "FORK_BOILERPLATE_TAG",
    "FORK_DIRECTIVE_PREFIX",
    "FORK_PLACEHOLDER_RESULT",
    "AgentTool",
    "AgentToolInput",
    "is_built_in_agent",
    "is_custom_agent",
    "is_plugin_agent",
    "is_one_shot_agent",
]
