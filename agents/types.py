"""
Agent 系统核心类型定义
全面对齐 Claude Code 源码架构
"""
from enum import Enum
from typing import Optional, Dict, Any, List, Callable, Union, Literal
from dataclasses import dataclass, field
from datetime import datetime


class AgentSource(str, Enum):
    """Agent来源类型"""
    BUILT_IN = "built-in"
    USER_SETTINGS = "userSettings"
    PROJECT_SETTINGS = "projectSettings"
    POLICY_SETTINGS = "policySettings"
    FLAG_SETTINGS = "flagSettings"
    PLUGIN = "plugin"


class AgentMemoryScope(str, Enum):
    """Agent记忆范围"""
    USER = "user"
    PROJECT = "project"
    LOCAL = "local"


class AgentIsolationMode(str, Enum):
    """Agent隔离模式"""
    WORKTREE = "worktree"
    REMOTE = "remote"


class AgentPermissionMode(str, Enum):
    """Agent权限模式"""
    DEFAULT = "default"
    AUTO = "auto"
    PLAN = "plan"
    BYPASS = "bypass"


@dataclass
class AgentHooks:
    """Agent钩子配置"""
    pre_start: Optional[List[str]] = None
    post_complete: Optional[List[str]] = None
    on_error: Optional[List[str]] = None


@dataclass
class AgentMcpServerSpec:
    """Agent MCP服务器配置"""
    name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


@dataclass
class BaseAgentDefinition:
    """
    Agent定义基类

    对齐 Claude Code 的 BaseAgentDefinition
    """
    agent_type: str
    when_to_use: str
    tools: Optional[List[str]] = None  # None 或 ['*'] 表示所有工具
    disallowed_tools: Optional[List[str]] = None
    skills: Optional[List[str]] = None
    mcp_servers: Optional[List[AgentMcpServerSpec]] = None
    hooks: Optional[AgentHooks] = None
    color: Optional[str] = None
    model: Optional[str] = None  # 'inherit' 或具体模型名
    effort: Optional[Union[str, int]] = None  # 'low', 'medium', 'high' 或整数
    permission_mode: Optional[AgentPermissionMode] = None
    max_turns: Optional[int] = None
    filename: Optional[str] = None
    base_dir: Optional[str] = None
    critical_system_reminder: Optional[str] = None
    required_mcp_servers: Optional[List[str]] = None
    background: bool = False  # 是否始终在后台运行
    initial_prompt: Optional[str] = None  # 首次用户消息前添加
    memory: Optional[AgentMemoryScope] = None
    isolation: Optional[AgentIsolationMode] = None
    omit_claude_md: bool = False  # 是否省略 CLAUDE.md


@dataclass
class BuiltInAgentDefinition(BaseAgentDefinition):
    """
    内置Agent定义

    对齐 Claude Code 的 BuiltInAgentDefinition
    """
    source: Literal[AgentSource.BUILT_IN] = AgentSource.BUILT_IN
    base_dir: Literal["built-in"] = "built-in"
    get_system_prompt: Optional[Callable[[], str]] = None


@dataclass
class CustomAgentDefinition(BaseAgentDefinition):
    """
    自定义Agent定义（用户/项目/策略设置）

    对齐 Claude Code 的 CustomAgentDefinition
    """
    source: Literal[
        AgentSource.USER_SETTINGS,
        AgentSource.PROJECT_SETTINGS,
        AgentSource.POLICY_SETTINGS,
        AgentSource.FLAG_SETTINGS
    ] = AgentSource.USER_SETTINGS
    get_system_prompt: Optional[Callable[[], str]] = None


@dataclass
class PluginAgentDefinition(BaseAgentDefinition):
    """
    插件Agent定义

    对齐 Claude Code 的 PluginAgentDefinition
    """
    source: Literal[AgentSource.PLUGIN] = AgentSource.PLUGIN
    plugin: str = ""  # 插件名称
    get_system_prompt: Optional[Callable[[], str]] = None


AgentDefinition = Union[
    BuiltInAgentDefinition,
    CustomAgentDefinition,
    PluginAgentDefinition
]


# 内置Agent类型常量
ONE_SHOT_BUILTIN_AGENT_TYPES = {"Explore", "Plan"}
VERIFICATION_AGENT_TYPE = "verification"


@dataclass
class AgentContext:
    """Agent执行上下文"""
    agent_id: str
    agent_type: str
    session_id: str
    parent_session_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    tool_use_count: int = 0
    token_count: int = 0
    status: Literal["running", "completed", "failed", "killed"] = "running"
    is_async: bool = False
    worktree_path: Optional[str] = None


@dataclass
class AgentToolResult:
    """Agent工具执行结果"""
    agent_id: str
    agent_type: Optional[str]
    content: List[Dict[str, str]]  # [{"type": "text", "text": "..."}]
    total_tool_use_count: int
    total_duration_ms: int
    total_tokens: int
    usage: Dict[str, Any]


@dataclass
class AgentExecutionConfig:
    """Agent执行配置"""
    max_turns: int = 50
    model: Optional[str] = None
    temperature: float = 0.7
    is_async: bool = False
    can_show_permission_prompts: bool = True
    preserve_tool_use_results: bool = False
    use_exact_tools: bool = False
    worktree_path: Optional[str] = None
    description: Optional[str] = None


class AgentError(Exception):
    """Agent错误基类"""
    pass


class AgentNotFoundError(AgentError):
    """Agent未找到"""
    pass


class AgentValidationError(AgentError):
    """Agent验证错误"""
    pass


class AgentExecutionError(AgentError):
    """Agent执行错误"""
    pass


def is_built_in_agent(agent: AgentDefinition) -> bool:
    """检查是否为内置Agent"""
    return isinstance(agent, BuiltInAgentDefinition) or agent.source == AgentSource.BUILT_IN


def is_custom_agent(agent: AgentDefinition) -> bool:
    """检查是否为自定义Agent"""
    return isinstance(agent, CustomAgentDefinition)


def is_plugin_agent(agent: AgentDefinition) -> bool:
    """检查是否为插件Agent"""
    return isinstance(agent, PluginAgentDefinition) or agent.source == AgentSource.PLUGIN


def is_one_shot_agent(agent_type: str) -> bool:
    """检查是否为一次性Agent（Explore, Plan）"""
    return agent_type in ONE_SHOT_BUILTIN_AGENT_TYPES
