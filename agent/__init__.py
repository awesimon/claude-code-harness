"""
Agent模块
提供Agent管理和任务调度功能
"""

from .enums import AgentStatus, TaskStatus, TaskType, TaskPriority, Result
from .agent_manager import AgentManager
from .agent import Agent, AgentConfig, AgentCapabilities
from .task import Task, TaskConfig, TaskResult
from .task_queue import TaskQueue

# 新增：Agent Runner 和专用 Agent
from .agent_runner import (
    AgentRunner,
    AgentConfig as RunnerAgentConfig,
    AgentResult,
    AgentContext,
    AgentStatus as RunnerAgentStatus,
    run_agent,
    run_agent_stream,
)
from .plan_agent import (
    PlanAgent,
    PlanAgentOptions,
    run_plan_agent,
    create_plan_agent_config,
    format_plan_result,
)
from .explore_agent import (
    ExploreAgent,
    ExploreAgentOptions,
    run_explore_agent,
    create_explore_agent_config,
    format_explore_result,
)

__all__ = [
    # 原有导出
    "AgentStatus",
    "TaskStatus",
    "TaskType",
    "TaskPriority",
    "Result",
    "AgentManager",
    "Agent",
    "AgentConfig",
    "AgentCapabilities",
    "Task",
    "TaskConfig",
    "TaskResult",
    "TaskQueue",
    # Agent Runner
    "AgentRunner",
    "RunnerAgentConfig",
    "AgentResult",
    "AgentContext",
    "RunnerAgentStatus",
    "run_agent",
    "run_agent_stream",
    # Plan Agent
    "PlanAgent",
    "PlanAgentOptions",
    "run_plan_agent",
    "create_plan_agent_config",
    "format_plan_result",
    # Explore Agent
    "ExploreAgent",
    "ExploreAgentOptions",
    "run_explore_agent",
    "create_explore_agent_config",
    "format_explore_result",
]
