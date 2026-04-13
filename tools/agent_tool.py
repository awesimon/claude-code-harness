"""
Agent 管理工具模块
提供创建和管理子 Agent 的功能
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import asyncio

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool
from agents.worker_pool import AgentManager, AgentConfig, AgentCapabilities, Agent
from agents.worker_pool.enums import Result


@dataclass
class AgentToolInput:
    """Agent 工具的输入参数"""
    description: str  # Agent 的描述/提示词
    prompt: Optional[str] = None  # 初始提示词（与 description 类似，用于兼容）
    subagent_type: str = "worker"  # Agent 类型: worker, coordinator, researcher 等
    model: Optional[str] = None  # 使用的模型名称
    run_in_background: bool = False  # 是否在后台运行
    name: Optional[str] = None  # Agent 名称
    team_name: Optional[str] = None  # 所属团队名称
    mode: str = "sync"  # 运行模式: sync, async, isolated
    isolation: str = "none"  # 隔离级别: none, process, container
    cwd: Optional[str] = None  # 工作目录
    tools: List[str] = field(default_factory=list)  # 可用工具列表
    max_concurrent_tasks: int = 1  # 最大并发任务数
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外元数据


@dataclass
class AgentToolOutput:
    """Agent 工具的输出结果"""
    agent_id: str
    name: str
    status: str
    created_at: float
    tools: List[str]


@register_tool
class AgentTool(Tool[AgentToolInput, AgentToolOutput]):
    """
    Agent 管理工具 - 创建和管理子 Agent

    用于创建新的子 Agent 来并行执行任务，支持多种配置选项：
    - 指定 Agent 类型和名称
    - 配置可用工具集
    - 设置工作目录和隔离级别
    - 支持后台运行模式
    """

    name = "agent"
    description = "创建和管理子 Agent，支持并行任务执行和团队协作"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._agent_manager: Optional[AgentManager] = None

    def _get_manager(self) -> AgentManager:
        """获取或创建 AgentManager 实例"""
        if self._agent_manager is None:
            self._agent_manager = AgentManager()
        return self._agent_manager

    async def validate(self, input_data: AgentToolInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.description and not input_data.prompt:
            return ToolValidationError("description 或 prompt 必须提供其中一个")

        # 验证 subagent_type
        valid_types = {"worker", "coordinator", "researcher", "planner", "reviewer"}
        if input_data.subagent_type not in valid_types:
            return ToolValidationError(
                f"无效的 subagent_type: {input_data.subagent_type}，"
                f"有效值为: {', '.join(valid_types)}"
            )

        # 验证 mode
        valid_modes = {"sync", "async", "isolated"}
        if input_data.mode not in valid_modes:
            return ToolValidationError(
                f"无效的 mode: {input_data.mode}，"
                f"有效值为: {', '.join(valid_modes)}"
            )

        # 验证 isolation
        valid_isolation = {"none", "process", "container"}
        if input_data.isolation not in valid_isolation:
            return ToolValidationError(
                f"无效的 isolation: {input_data.isolation}，"
                f"有效值为: {', '.join(valid_isolation)}"
            )

        # 验证 tools
        available_tools = AgentCapabilities.ALL_TOOLS
        for tool in input_data.tools:
            if tool not in available_tools:
                return ToolValidationError(
                    f"无效的工具: {tool}，"
                    f"可用工具: {', '.join(available_tools)}"
                )

        return None

    async def execute(self, input_data: AgentToolInput) -> ToolResult:
        """执行 Agent 创建操作"""
        manager = self._get_manager()

        try:
            # 准备 Agent 配置
            description = input_data.description or input_data.prompt or ""
            agent_name = input_data.name or f"{input_data.subagent_type}-{id(input_data) % 10000}"

            # 构建工具集
            tools = set(input_data.tools) if input_data.tools else AgentCapabilities.DEFAULT_WORKER_TOOLS.copy()

            # 创建 Agent 配置
            config = AgentConfig(
                name=agent_name,
                description=description,
                tools=tools,
                max_concurrent_tasks=input_data.max_concurrent_tasks,
                metadata={
                    "team_name": input_data.team_name,
                    "model": input_data.model,
                    "mode": input_data.mode,
                    "isolation": input_data.isolation,
                    "cwd": input_data.cwd,
                    **input_data.metadata,
                }
            )

            # 创建 Agent
            result: Result[Agent] = await manager.create_agent(
                config=config,
                agent_type=input_data.subagent_type,
                start=True,
            )

            if result.is_err():
                return ToolResult.error(
                    ToolExecutionError(f"创建 Agent 失败: {result.error}")
                )

            agent = result.unwrap()

            # 构建输出
            output = AgentToolOutput(
                agent_id=agent.id,
                name=agent.name,
                status=agent.status.value,
                created_at=agent.created_at,
                tools=list(agent.config.tools),
            )

            # 如果是后台运行模式，启动后台任务
            if input_data.run_in_background:
                # 在后台持续运行 Agent
                asyncio.create_task(self._run_in_background(agent, description))

            return ToolResult.ok(
                data={
                    "agent_id": output.agent_id,
                    "name": output.name,
                    "status": output.status,
                    "created_at": output.created_at,
                    "tools": output.tools,
                },
                message=f"成功创建 Agent: {output.name} (ID: {output.agent_id})",
                metadata={
                    "agent_id": output.agent_id,
                    "agent_type": input_data.subagent_type,
                    "run_in_background": input_data.run_in_background,
                    "team_name": input_data.team_name,
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"创建 Agent 时发生错误: {str(e)}")
            )

    async def _run_in_background(self, agent: Agent, prompt: str) -> None:
        """在后台运行 Agent"""
        try:
            # 这里可以实现更复杂的后台任务逻辑
            # 例如持续监听消息、处理任务队列等
            while agent.status.value not in ("stopped", "error"):
                await asyncio.sleep(1)
        except Exception as e:
            print(f"后台 Agent {agent.id} 运行错误: {e}")

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["input_schema"] = {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Agent 的描述或提示词"
                },
                "prompt": {
                    "type": "string",
                    "description": "初始提示词（与 description 类似）"
                },
                "subagent_type": {
                    "type": "string",
                    "enum": ["worker", "coordinator", "researcher", "planner", "reviewer"],
                    "default": "worker",
                    "description": "Agent 类型"
                },
                "model": {
                    "type": "string",
                    "description": "使用的模型名称"
                },
                "run_in_background": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否在后台运行"
                },
                "name": {
                    "type": "string",
                    "description": "Agent 名称"
                },
                "team_name": {
                    "type": "string",
                    "description": "所属团队名称"
                },
                "mode": {
                    "type": "string",
                    "enum": ["sync", "async", "isolated"],
                    "default": "sync",
                    "description": "运行模式"
                },
                "isolation": {
                    "type": "string",
                    "enum": ["none", "process", "container"],
                    "default": "none",
                    "description": "隔离级别"
                },
                "cwd": {
                    "type": "string",
                    "description": "工作目录"
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可用工具列表"
                },
                "max_concurrent_tasks": {
                    "type": "integer",
                    "default": 1,
                    "description": "最大并发任务数"
                },
                "metadata": {
                    "type": "object",
                    "description": "额外元数据"
                },
            },
            "required": ["description"],
        }
        return schema


@dataclass
class AgentListInput:
    """列出 Agent 的输入参数"""
    agent_type: Optional[str] = None  # 按类型过滤
    status: Optional[str] = None  # 按状态过滤
    team_name: Optional[str] = None  # 按团队过滤


@register_tool
class AgentListTool(Tool[AgentListInput, List[Dict[str, Any]]]):
    """
    列出所有 Agent 工具

    支持按类型、状态、团队等条件过滤
    """

    name = "agent_list"
    description = "列出所有 Agent，支持多种过滤条件"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._agent_manager: Optional[AgentManager] = None

    def _get_manager(self) -> AgentManager:
        """获取或创建 AgentManager 实例"""
        if self._agent_manager is None:
            self._agent_manager = AgentManager()
        return self._agent_manager

    async def execute(self, input_data: AgentListInput) -> ToolResult:
        """执行列出 Agent 操作"""
        manager = self._get_manager()

        try:
            # 获取所有 Agent
            agents = await manager.get_all_agents()

            # 应用过滤条件
            filtered_agents = agents
            if input_data.agent_type:
                filtered_agents = [a for a in filtered_agents if a.agent_type == input_data.agent_type]
            if input_data.status:
                filtered_agents = [a for a in filtered_agents if a.status.value == input_data.status]
            if input_data.team_name:
                filtered_agents = [
                    a for a in filtered_agents
                    if a.config.metadata.get("team_name") == input_data.team_name
                ]

            # 构建结果
            agent_list = [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "type": agent.agent_type,
                    "status": agent.status.value,
                    "tools": list(agent.config.tools),
                    "created_at": agent.created_at,
                    "team_name": agent.config.metadata.get("team_name"),
                }
                for agent in filtered_agents
            ]

            return ToolResult.ok(
                data=agent_list,
                message=f"找到 {len(agent_list)} 个 Agent",
                metadata={
                    "total_count": len(agents),
                    "filtered_count": len(agent_list),
                    "filters": {
                        "agent_type": input_data.agent_type,
                        "status": input_data.status,
                        "team_name": input_data.team_name,
                    }
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"列出 Agent 失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        return True


@dataclass
class AgentDestroyInput:
    """销毁 Agent 的输入参数"""
    agent_id: str  # Agent ID
    wait: bool = True  # 是否等待任务完成
    timeout: Optional[float] = None  # 超时时间


@register_tool
class AgentDestroyTool(Tool[AgentDestroyInput, bool]):
    """
    销毁 Agent 工具

    安全地停止并销毁指定的 Agent
    """

    name = "agent_destroy"
    description = "销毁指定的 Agent，释放资源"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._agent_manager: Optional[AgentManager] = None

    def _get_manager(self) -> AgentManager:
        """获取或创建 AgentManager 实例"""
        if self._agent_manager is None:
            self._agent_manager = AgentManager()
        return self._agent_manager

    async def validate(self, input_data: AgentDestroyInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.agent_id:
            return ToolValidationError("agent_id 不能为空")
        return None

    async def execute(self, input_data: AgentDestroyInput) -> ToolResult:
        """执行销毁 Agent 操作"""
        manager = self._get_manager()

        try:
            result = await manager.destroy_agent(
                agent_id=input_data.agent_id,
                wait=input_data.wait,
                timeout=input_data.timeout,
            )

            if result.is_err():
                return ToolResult.error(
                    ToolExecutionError(f"销毁 Agent 失败: {result.error}")
                )

            return ToolResult.ok(
                data=True,
                message=f"成功销毁 Agent: {input_data.agent_id}",
                metadata={
                    "agent_id": input_data.agent_id,
                    "wait": input_data.wait,
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"销毁 Agent 时发生错误: {str(e)}")
            )

    def is_destructive(self) -> bool:
        return True

    def requires_confirmation(self) -> bool:
        return True
