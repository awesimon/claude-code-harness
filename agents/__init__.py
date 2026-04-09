"""
Agent 模块

全面对齐 Claude Code 的 Agent 系统
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
    AgentManager,
    get_agent_manager,
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

__all__ = [
    # 类型
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
    # 常量
    "ONE_SHOT_BUILTIN_AGENT_TYPES",
    "VERIFICATION_AGENT_TYPE",
    # 内置Agent
    "EXPLORE_AGENT",
    "PLAN_AGENT",
    "GENERAL_PURPOSE_AGENT",
    "CODE_AGENT",
    "TEST_AGENT",
    "VERIFICATION_AGENT",
    "get_built_in_agents",
    "get_agent_by_type",
    # 执行引擎
    "AgentExecutor",
    "AgentManager",
    "get_agent_manager",
    # Fork机制
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
    # 工具
    "AgentTool",
    "AgentToolInput",
    # 工具函数
    "is_built_in_agent",
    "is_custom_agent",
    "is_plugin_agent",
    "is_one_shot_agent",
]
