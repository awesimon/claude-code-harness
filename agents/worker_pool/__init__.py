"""
Worker 池与 Coordinator 模式：多 Agent 并行、任务队列、Agent Runner（Plan/Explore 等）。

与包内根级的 `agents`（Claude Code 对齐的 spawn/subagent 运行时）并列；
本模块侧重线程池式 Worker 与 Coordinator。
"""

from .enums import AgentStatus, TaskStatus, TaskType, TaskPriority, Result
from .agent_manager import WorkerPoolManager
from .agent import Agent, AgentConfig, AgentCapabilities
from .task import Task, TaskConfig, TaskResult
from .task_queue import TaskQueue
from .coordinator import (
    Coordinator,
    CoordinatorConfig,
    ExecutionPlan,
    TaskNotification,
)

# 旧名兼容（deprecated）：请改用 WorkerPoolManager
AgentManager = WorkerPoolManager

# Agent Runner 与专用 Agent
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
    "AgentStatus",
    "TaskStatus",
    "TaskType",
    "TaskPriority",
    "Result",
    "WorkerPoolManager",
    "AgentManager",
    "Agent",
    "AgentConfig",
    "AgentCapabilities",
    "Task",
    "TaskConfig",
    "TaskResult",
    "TaskQueue",
    "Coordinator",
    "CoordinatorConfig",
    "ExecutionPlan",
    "TaskNotification",
    "AgentRunner",
    "RunnerAgentConfig",
    "AgentResult",
    "AgentContext",
    "RunnerAgentStatus",
    "run_agent",
    "run_agent_stream",
    "PlanAgent",
    "PlanAgentOptions",
    "run_plan_agent",
    "create_plan_agent_config",
    "format_plan_result",
    "ExploreAgent",
    "ExploreAgentOptions",
    "run_explore_agent",
    "create_explore_agent_config",
    "format_explore_result",
]
