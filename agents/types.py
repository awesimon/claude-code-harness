"""
Agent 系统核心类型定义
全面对齐 Claude Code 源码架构
"""
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Union,
    runtime_checkable,
)

if TYPE_CHECKING:
    from harness import SessionHarness
    from state_core import AgentRecord


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


class AgentDefinitionError(ValueError):
    """Raised when an agent definition or durable snapshot is malformed."""


_CUSTOM_SOURCES = {
    AgentSource.USER_SETTINGS,
    AgentSource.PROJECT_SETTINGS,
    AgentSource.POLICY_SETTINGS,
    AgentSource.FLAG_SETTINGS,
}
_STRING_LIST_FIELDS = (
    "tools",
    "disallowed_tools",
    "skills",
    "required_mcp_servers",
)
_NULLABLE_STRING_FIELDS = (
    "color",
    "model",
    "filename",
    "base_dir",
    "critical_system_reminder",
    "initial_prompt",
)
_SNAPSHOT_FIELDS = {
    "agent_type",
    "when_to_use",
    *_STRING_LIST_FIELDS,
    *_NULLABLE_STRING_FIELDS,
    "mcp_servers",
    "hooks",
    "effort",
    "permission_mode",
    "max_turns",
    "background",
    "memory",
    "isolation",
    "omit_claude_md",
    "source",
    "plugin",
    "system_prompt",
    "metadata",
    "execution_timeout",
}


def _definition_error(field_name: str, expected: str) -> AgentDefinitionError:
    return AgentDefinitionError(f"Agent definition {field_name} must be {expected}")


def _validate_string(value: Any, field_name: str, *, required: bool = False) -> None:
    if not isinstance(value, str) or (required and not value):
        qualifier = "a non-empty string" if required else "a string or None"
        raise _definition_error(field_name, qualifier)


def _validate_string_list(value: Any, field_name: str) -> None:
    if value is not None and (
        type(value) is not list
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise _definition_error(field_name, "a list of non-empty strings or None")


def _validate_hooks(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, AgentHooks):
        raise _definition_error("hooks", "an AgentHooks value or None")
    for field_name in ("pre_start", "post_complete", "on_error"):
        _validate_string_list(getattr(value, field_name), f"hooks.{field_name}")


def _validate_mcp_servers(value: Any) -> None:
    if value is None:
        return
    if type(value) is not list or any(
        not isinstance(server, AgentMcpServerSpec) for server in value
    ):
        raise _definition_error(
            "mcp_servers", "a list of AgentMcpServerSpec values or None"
        )
    for server in value:
        if server.name is not None:
            _validate_string(server.name, "mcp_servers.name")
        if server.config is not None and not isinstance(server.config, Mapping):
            raise _definition_error("mcp_servers.config", "a mapping or None")


def _definition_source(definition: BaseAgentDefinition) -> AgentSource:
    if not isinstance(definition.source, AgentSource):
        raise _definition_error("source", "an AgentSource value")
    source = definition.source
    if isinstance(definition, BuiltInAgentDefinition):
        valid = source is AgentSource.BUILT_IN
    elif isinstance(definition, PluginAgentDefinition):
        valid = source is AgentSource.PLUGIN
    elif isinstance(definition, CustomAgentDefinition):
        valid = source in _CUSTOM_SOURCES
    else:
        raise AgentDefinitionError(
            "Agent definition must be built-in, custom, or plugin"
        )
    if not valid:
        raise AgentDefinitionError(
            "Agent definition class does not match its source"
        )
    return source


def _validate_definition(
    definition: BaseAgentDefinition, *, expected_agent_type: str | None
) -> AgentDefinition:
    _validate_string(definition.agent_type, "agent_type", required=True)
    _validate_string(definition.when_to_use, "when_to_use", required=True)
    if expected_agent_type is not None and definition.agent_type != expected_agent_type:
        raise AgentDefinitionError("Agent definition type does not match record or request")
    source = _definition_source(definition)
    for field_name in _STRING_LIST_FIELDS:
        _validate_string_list(getattr(definition, field_name), field_name)
    for field_name in _NULLABLE_STRING_FIELDS:
        value = getattr(definition, field_name)
        if value is not None:
            _validate_string(value, field_name)
    if definition.effort is not None and type(definition.effort) not in (str, int):
        raise _definition_error("effort", "a string, integer, or None")
    if definition.permission_mode is not None and not isinstance(
        definition.permission_mode, AgentPermissionMode
    ):
        raise _definition_error(
            "permission_mode", "an AgentPermissionMode value or None"
        )
    if definition.max_turns is not None and (
        type(definition.max_turns) is not int or definition.max_turns < 1
    ):
        raise _definition_error("max_turns", "a positive integer or None")
    if type(definition.background) is not bool:
        raise _definition_error("background", "a boolean")
    if type(definition.omit_claude_md) is not bool:
        raise _definition_error("omit_claude_md", "a boolean")
    if definition.memory is not None and not isinstance(
        definition.memory, AgentMemoryScope
    ):
        raise _definition_error("memory", "an AgentMemoryScope value or None")
    if definition.isolation is not None and not isinstance(
        definition.isolation, AgentIsolationMode
    ):
        raise _definition_error("isolation", "an AgentIsolationMode value or None")
    if definition.get_system_prompt is not None and not callable(
        definition.get_system_prompt
    ):
        raise _definition_error("get_system_prompt", "callable or None")
    _validate_hooks(definition.hooks)
    _validate_mcp_servers(definition.mcp_servers)
    if source is AgentSource.PLUGIN:
        plugin = getattr(definition, "plugin", None)
        _validate_string(plugin, "plugin", required=True)
    return definition


def _snapshot_hooks(value: Any) -> AgentHooks | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) - {
        "pre_start",
        "post_complete",
        "on_error",
    }:
        raise _definition_error("hooks", "a hooks mapping or None")
    hooks = AgentHooks(
        pre_start=value.get("pre_start"),
        post_complete=value.get("post_complete"),
        on_error=value.get("on_error"),
    )
    _validate_hooks(hooks)
    return hooks


def _snapshot_mcp_servers(value: Any) -> List[AgentMcpServerSpec] | None:
    if value is None:
        return None
    if type(value) is not list:
        raise _definition_error("mcp_servers", "a list of server mappings or None")
    servers: List[AgentMcpServerSpec] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) - {"name", "config"}:
            raise _definition_error("mcp_servers", "a list of server mappings")
        name = item.get("name")
        config = item.get("config")
        if name is not None:
            _validate_string(name, "mcp_servers.name")
        if config is not None and not isinstance(config, Mapping):
            raise _definition_error("mcp_servers.config", "a mapping or None")
        servers.append(
            AgentMcpServerSpec(
                name=name,
                config=dict(config) if config is not None else None,
            )
        )
    return servers


def _snapshot_enum(
    snapshot: Mapping[str, Any], field_name: str, enum_type: type[Enum]
) -> Enum | None:
    value = snapshot.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _definition_error(field_name, "a supported string or None")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _definition_error(field_name, "a supported string or None") from exc


def _definition_from_mapping(
    snapshot: Mapping[str, Any], *, expected_agent_type: str | None
) -> AgentDefinition:
    unknown = set(snapshot) - _SNAPSHOT_FIELDS
    if unknown:
        raise AgentDefinitionError(
            f"Agent definition snapshot has unknown fields: {sorted(unknown)!r}"
        )
    for required in ("agent_type", "when_to_use", "source", "system_prompt"):
        if required not in snapshot:
            raise AgentDefinitionError(
                f"Agent definition snapshot is missing {required}"
            )
    system_prompt = snapshot["system_prompt"]
    _validate_string(system_prompt, "system_prompt", required=True)
    source_value = snapshot["source"]
    if not isinstance(source_value, str):
        raise _definition_error("source", "a supported source string")
    try:
        source = AgentSource(source_value)
    except ValueError as exc:
        raise _definition_error("source", "a supported source string") from exc
    if source is not AgentSource.PLUGIN and "plugin" in snapshot:
        raise AgentDefinitionError(
            "Agent definition snapshot plugin is only valid for plugin sources"
        )
    if source is AgentSource.BUILT_IN and snapshot.get("base_dir") != "built-in":
        raise AgentDefinitionError(
            "Agent definition snapshot built-in base_dir must be 'built-in'"
        )
    if "metadata" in snapshot and not isinstance(snapshot["metadata"], Mapping):
        raise _definition_error("metadata", "a mapping")
    timeout = snapshot.get("execution_timeout")
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
        or not math.isfinite(float(timeout))
    ):
        raise _definition_error("execution_timeout", "a positive finite number or None")

    common: Dict[str, Any] = {
        "agent_type": snapshot["agent_type"],
        "when_to_use": snapshot["when_to_use"],
        "tools": snapshot.get("tools"),
        "disallowed_tools": snapshot.get("disallowed_tools"),
        "skills": snapshot.get("skills"),
        "mcp_servers": _snapshot_mcp_servers(snapshot.get("mcp_servers")),
        "hooks": _snapshot_hooks(snapshot.get("hooks")),
        "color": snapshot.get("color"),
        "model": snapshot.get("model"),
        "effort": snapshot.get("effort"),
        "permission_mode": _snapshot_enum(
            snapshot, "permission_mode", AgentPermissionMode
        ),
        "max_turns": snapshot.get("max_turns"),
        "filename": snapshot.get("filename"),
        "base_dir": snapshot.get("base_dir"),
        "critical_system_reminder": snapshot.get("critical_system_reminder"),
        "required_mcp_servers": snapshot.get("required_mcp_servers"),
        "background": snapshot.get("background", False),
        "initial_prompt": snapshot.get("initial_prompt"),
        "memory": _snapshot_enum(snapshot, "memory", AgentMemoryScope),
        "isolation": _snapshot_enum(snapshot, "isolation", AgentIsolationMode),
        "omit_claude_md": snapshot.get("omit_claude_md", False),
    }

    def get_system_prompt() -> str:
        return system_prompt

    try:
        if source is AgentSource.BUILT_IN:
            definition: BaseAgentDefinition = BuiltInAgentDefinition(
                **common,
                source=AgentSource.BUILT_IN,
                get_system_prompt=get_system_prompt,
            )
        elif source is AgentSource.PLUGIN:
            definition = PluginAgentDefinition(
                **common,
                source=AgentSource.PLUGIN,
                plugin=snapshot.get("plugin"),
                get_system_prompt=get_system_prompt,
            )
        else:
            definition = CustomAgentDefinition(
                **common, source=source, get_system_prompt=get_system_prompt
            )
    except (TypeError, ValueError) as exc:
        raise AgentDefinitionError("Agent definition snapshot has invalid enum fields") from exc
    return _validate_definition(
        definition, expected_agent_type=expected_agent_type
    )


def parse_agent_definition(
    value: AgentDefinition | Mapping[str, Any],
    *,
    expected_agent_type: str | None = None,
) -> AgentDefinition:
    """Strictly validate a live definition or reconstruct a durable snapshot."""

    if isinstance(value, Mapping):
        return _definition_from_mapping(
            value, expected_agent_type=expected_agent_type
        )
    if not isinstance(value, BaseAgentDefinition):
        raise AgentDefinitionError(
            "Agent definition must be a complete definition or snapshot mapping"
        )
    return _validate_definition(value, expected_agent_type=expected_agent_type)


@dataclass(frozen=True)
class AgentRequest:
    """A validated request to schedule one durable child agent."""

    prompt: str
    agent_type: str
    description: str
    background: bool = False
    parent_agent_id: Optional[str] = None
    model: Optional[str] = None
    cwd: Optional[Union[str, Path]] = None
    worktree_id: Optional[str] = None
    definition: Optional[AgentDefinition] = None
    definition_metadata: Mapping[str, Any] = field(default_factory=dict)
    timeout: Optional[float] = None


@dataclass(frozen=True)
class AgentExecutionResult:
    """Storage-neutral output returned by one child execution loop."""

    content: Any = field(default_factory=list)
    usage: Mapping[str, Any] = field(default_factory=dict)
    tool_count: int = 0
    termination_reason: str = "completed"
    error: Optional[Mapping[str, Any]] = None
    output: Any = None


@runtime_checkable
class AgentRunner(Protocol):
    async def run(
        self, record: "AgentRecord", child_harness: "SessionHarness"
    ) -> AgentExecutionResult: ...


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
    termination_reason: str = "completed"
    error: Optional[str] = None


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
    workspace_root: Optional[Path] = None
    approval_callback: Optional[Callable[[Any], Any]] = None
    tool_timeout: Optional[float] = 60.0
    parent_cancellation: Optional[Any] = None
    session_runtime: Optional[Any] = None


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
