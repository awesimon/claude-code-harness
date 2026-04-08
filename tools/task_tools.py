"""
任务管理工具模块
提供 TaskGetTool、TaskStopTool、TaskOutputTool 等任务管理功能
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, Awaitable
import asyncio
import time

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool
from agent.enums import TaskStatus, TaskPriority


# 全局任务管理器实例（将在实际应用中被注入）
_global_task_manager: Optional[Any] = None


def set_task_manager(task_manager: Any) -> None:
    """设置全局任务管理器"""
    global _global_task_manager
    _global_task_manager = task_manager


def get_task_manager() -> Optional[Any]:
    """获取全局任务管理器"""
    return _global_task_manager


@dataclass
class TaskGetInput:
    """获取任务详情的输入参数"""
    task_id: str


@dataclass
class TaskStopInput:
    """停止任务的输入参数"""
    task_id: str


@dataclass
class TaskOutputInput:
    """获取任务输出的输入参数"""
    task_id: str
    block: bool = True  # 是否阻塞等待输出
    timeout: Optional[float] = 30.0  # 超时时间（秒）


@dataclass
class TaskCreateInput:
    """创建任务的输入参数"""
    description: str
    task_type: str = "local_bash"  # local_bash, remote_bash, agent_task
    priority: str = "normal"  # low, normal, high, critical
    agent_id: Optional[str] = None  # 指定执行Agent
    dependencies: Optional[List[str]] = None  # 依赖任务ID列表
    metadata: Optional[Dict[str, Any]] = None  # 额外元数据


@dataclass
class TaskUpdateInput:
    """更新任务的输入参数"""
    task_id: str
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class TaskListInput:
    """列出任务的输入参数"""
    status: Optional[str] = None
    priority: Optional[str] = None
    agent_id: Optional[str] = None
    sort_by: str = "created"
    sort_order: str = "desc"
    limit: int = 100
    offset: int = 0


@dataclass
class TaskInfo:
    """任务信息"""
    id: str
    description: str
    type: str
    status: str
    priority: str
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    agent_id: Optional[str] = None
    tool_use_id: Optional[str] = None
    result: Optional[Any] = None
    error: Optional[str] = None


@dataclass
class TaskStopOutput:
    """停止任务的输出结果"""
    success: bool
    message: str
    task_id: str
    previous_status: Optional[str] = None


@dataclass
class TaskOutputOutput:
    """任务输出的输出结果"""
    task_id: str
    status: str
    output: Optional[str] = None
    partial: bool = False  # 是否为部分输出
    has_more: bool = False  # 是否还有更多输出


# 任务输出缓冲区（用于存储任务的输出）
_task_outputs: Dict[str, List[str]] = {}
_task_output_events: Dict[str, asyncio.Event] = {}


def register_task_output(task_id: str, output: str) -> None:
    """注册任务输出"""
    if task_id not in _task_outputs:
        _task_outputs[task_id] = []
        _task_output_events[task_id] = asyncio.Event()
    _task_outputs[task_id].append(output)
    _task_output_events[task_id].set()


def get_task_output_buffer(task_id: str) -> List[str]:
    """获取任务输出缓冲区"""
    return _task_outputs.get(task_id, [])


def clear_task_output_buffer(task_id: str) -> None:
    """清除任务输出缓冲区"""
    if task_id in _task_outputs:
        del _task_outputs[task_id]
    if task_id in _task_output_events:
        del _task_output_events[task_id]


@register_tool
class TaskCreateTool(Tool[TaskCreateInput, TaskInfo]):
    """
    创建新任务工具

    创建一个新的任务并添加到任务队列中。

    使用场景：
    - 创建异步执行的任务
    - 创建需要跟踪的长时间运行任务
    - 创建依赖其他任务的任务
    """

    name = "task_create"
    description = "创建新的任务"
    version = "1.0"

    async def validate(self, input_data: TaskCreateInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.description or not input_data.description.strip():
            return ToolValidationError("description 不能为空")

        valid_types = {"local_bash", "remote_bash", "agent_task"}
        if input_data.task_type not in valid_types:
            return ToolValidationError(
                f"无效的任务类型: {input_data.task_type}，有效值为: {', '.join(valid_types)}"
            )

        valid_priorities = {"low", "normal", "high", "critical"}
        if input_data.priority not in valid_priorities:
            return ToolValidationError(
                f"无效的优先级: {input_data.priority}，有效值为: {', '.join(valid_priorities)}"
            )

        return None

    async def execute(self, input_data: TaskCreateInput) -> ToolResult:
        """执行创建任务操作"""
        # 获取任务管理器
        task_manager = get_task_manager()
        if task_manager is None:
            return ToolResult.error(
                ToolExecutionError("任务管理器未初始化")
            )

        try:
            # 创建任务配置
            from agent.task import TaskConfig
            from agent.enums import TaskPriority

            priority_map = {
                "low": TaskPriority.LOW,
                "normal": TaskPriority.NORMAL,
                "high": TaskPriority.HIGH,
                "critical": TaskPriority.CRITICAL
            }

            config = TaskConfig(
                description=input_data.description,
                priority=priority_map.get(input_data.priority, TaskPriority.NORMAL),
                agent_id=input_data.agent_id,
                dependencies=input_data.dependencies or [],
                metadata=input_data.metadata or {}
            )

            # 创建任务
            from agent.task import Task
            task = Task(
                description=input_data.description,
                config=config,
                task_type=input_data.task_type
            )

            # 添加到任务管理器
            await task_manager.add_task(task)

            # 构建任务信息
            task_info = TaskInfo(
                id=task.id,
                description=task.description,
                type=task.task_type.value if hasattr(task.task_type, 'value') else str(task.task_type),
                status=task.status.value if hasattr(task.status, 'value') else str(task.status),
                priority=task.priority.value if hasattr(task.priority, 'value') else str(task.priority),
                agent_id=task.agent_id,
            )

            return ToolResult.ok(
                data=task_info,
                message=f"成功创建任务: {task.id}",
                metadata={
                    "task_id": task.id,
                    "description": task.description,
                    "type": task.task_type,
                    "priority": task.priority,
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(
                    f"创建任务失败: {str(e)}",
                    details={"exception_type": type(e).__name__}
                )
            )

    def is_read_only(self) -> bool:
        """是否为只读工具"""
        return False

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "任务描述"
                },
                "task_type": {
                    "type": "string",
                    "enum": ["local_bash", "remote_bash", "agent_task"],
                    "description": "任务类型",
                    "default": "local_bash"
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "critical"],
                    "description": "任务优先级",
                    "default": "normal"
                },
                "agent_id": {
                    "type": "string",
                    "description": "指定执行Agent的ID（可选）"
                },
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "依赖任务ID列表（可选）"
                },
                "metadata": {
                    "type": "object",
                    "description": "额外元数据（可选）"
                }
            },
            "required": ["description"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "任务 ID"},
                "description": {"type": "string", "description": "任务描述"},
                "type": {"type": "string", "description": "任务类型"},
                "status": {"type": "string", "description": "任务状态"},
                "priority": {"type": "string", "description": "任务优先级"},
                "agent_id": {"type": "string", "description": "执行Agent ID"}
            }
        }
        return schema


@register_tool
class TaskGetTool(Tool[TaskGetInput, TaskInfo]):
    """
    获取单个任务详情工具

    根据任务 ID 获取任务的完整信息，包括状态、结果、执行时间等。

    使用场景：
    - 查询特定任务的当前状态
    - 获取任务的执行结果
    - 监控长时间运行任务的进度
    """

    name = "task_get"
    description = "获取单个任务的详细信息，包括状态、结果、执行时间等"
    version = "1.0"

    async def validate(self, input_data: TaskGetInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.task_id or not input_data.task_id.strip():
            return ToolValidationError("task_id 不能为空")
        return None

    async def execute(self, input_data: TaskGetInput) -> ToolResult:
        """执行获取任务详情"""
        task_id = input_data.task_id.strip()

        # 获取任务管理器
        task_manager = get_task_manager()
        if task_manager is None:
            return ToolResult.error(
                ToolExecutionError("任务管理器未初始化")
            )

        try:
            # 从任务管理器获取任务
            task = await task_manager.get_task(task_id)

            if task is None:
                return ToolResult.error(
                    ToolValidationError(f"任务不存在: {task_id}")
                )

            # 构建任务信息
            task_info = TaskInfo(
                id=task.id,
                description=task.description,
                type=task.task_type.value if hasattr(task.task_type, 'value') else str(task.task_type),
                status=task.status.value if hasattr(task.status, 'value') else str(task.status),
                priority=task.priority.value if hasattr(task.priority, 'value') else str(task.priority),
                start_time=task.start_time,
                end_time=task.end_time,
                duration_ms=task.result.duration_ms if task.result else None,
                agent_id=task.agent_id,
                tool_use_id=task.tool_use_id,
            )

            # 添加结果或错误信息
            if task.result:
                if task.result.success:
                    task_info.result = task.result.data
                else:
                    task_info.error = task.result.error

            return ToolResult.ok(
                data=task_info,
                message=f"成功获取任务信息: {task_id}",
                metadata={
                    "task_id": task_id,
                    "status": task_info.status,
                    "has_result": task.result is not None,
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(
                    f"获取任务信息失败: {str(e)}",
                    details={"task_id": task_id, "exception_type": type(e).__name__}
                )
            )

    def is_read_only(self) -> bool:
        """是否为只读工具"""
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID"
                }
            },
            "required": ["task_id"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "任务 ID"},
                "description": {"type": "string", "description": "任务描述"},
                "type": {"type": "string", "description": "任务类型"},
                "status": {"type": "string", "description": "任务状态"},
                "priority": {"type": "string", "description": "任务优先级"},
                "start_time": {"type": "number", "description": "开始时间戳"},
                "end_time": {"type": "number", "description": "结束时间戳"},
                "duration_ms": {"type": "number", "description": "执行时长（毫秒）"},
                "agent_id": {"type": "string", "description": "执行代理 ID"},
                "tool_use_id": {"type": "string", "description": "工具使用 ID"},
                "result": {"description": "任务结果"},
                "error": {"type": "string", "description": "错误信息"}
            }
        }
        return schema


@register_tool
class TaskStopTool(Tool[TaskStopInput, TaskStopOutput]):
    """
    停止运行中任务工具

    根据任务 ID 停止正在运行或等待中的任务。

    使用场景：
    - 取消不再需要的长时间运行任务
    - 停止出现问题的任务
    - 在资源限制时释放任务槽位

    注意：
    - 只能停止处于 pending 或 in_progress 状态的任务
    - 已完成的任务无法停止
    - 停止任务不会撤销已经产生的副作用
    """

    name = "task_stop"
    description = "停止正在运行或等待中的任务"
    version = "1.0"

    async def validate(self, input_data: TaskStopInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.task_id or not input_data.task_id.strip():
            return ToolValidationError("task_id 不能为空")
        return None

    async def execute(self, input_data: TaskStopInput) -> ToolResult:
        """执行停止任务"""
        task_id = input_data.task_id.strip()

        # 获取任务管理器
        task_manager = get_task_manager()
        if task_manager is None:
            return ToolResult.error(
                ToolExecutionError("任务管理器未初始化")
            )

        try:
            # 获取任务
            task = await task_manager.get_task(task_id)

            if task is None:
                return ToolResult.error(
                    ToolValidationError(f"任务不存在: {task_id}")
                )

            # 获取任务当前状态
            previous_status = task.status.value if hasattr(task.status, 'value') else str(task.status)

            # 检查任务是否已在终止状态
            if task.is_terminal():
                return ToolResult.ok(
                    data=TaskStopOutput(
                        success=False,
                        message=f"任务已经处于终止状态: {previous_status}",
                        task_id=task_id,
                        previous_status=previous_status
                    ),
                    message=f"任务已经处于终止状态，无需停止"
                )

            # 停止任务
            task.abort()

            # 更新任务状态为取消
            await task._update_status(TaskStatus.CANCELLED)

            output = TaskStopOutput(
                success=True,
                message=f"任务已成功停止",
                task_id=task_id,
                previous_status=previous_status
            )

            return ToolResult.ok(
                data=output,
                message=f"成功停止任务: {task_id}",
                metadata={
                    "task_id": task_id,
                    "previous_status": previous_status,
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(
                    f"停止任务失败: {str(e)}",
                    details={"task_id": task_id, "exception_type": type(e).__name__}
                )
            )

    def is_read_only(self) -> bool:
        """是否为只读工具"""
        return False

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "要停止的任务 ID"
                }
            },
            "required": ["task_id"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "success": {
                    "type": "boolean",
                    "description": "是否成功停止"
                },
                "message": {
                    "type": "string",
                    "description": "操作结果消息"
                },
                "task_id": {
                    "type": "string",
                    "description": "任务 ID"
                },
                "previous_status": {
                    "type": "string",
                    "description": "停止前的任务状态"
                }
            }
        }
        return schema


@register_tool
class TaskOutputTool(Tool[TaskOutputInput, TaskOutputOutput]):
    """
    获取任务输出工具

    获取指定任务的输出内容。支持阻塞等待输出和非阻塞获取。

    使用场景：
    - 获取长时间运行任务的实时输出
    - 轮询任务进度信息
    - 收集任务执行日志

    特点：
    - 支持阻塞模式和非阻塞模式
    - 可配置超时时间
    - 支持获取部分输出
    """

    name = "task_output"
    description = "获取任务的输出内容，支持阻塞等待和非阻塞获取"
    version = "1.0"

    DEFAULT_TIMEOUT = 30.0

    async def validate(self, input_data: TaskOutputInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.task_id or not input_data.task_id.strip():
            return ToolValidationError("task_id 不能为空")
        if input_data.timeout is not None and input_data.timeout <= 0:
            return ToolValidationError("timeout 必须为正数")
        return None

    async def execute(self, input_data: TaskOutputInput) -> ToolResult:
        """执行获取任务输出"""
        task_id = input_data.task_id.strip()
        block = input_data.block
        timeout = input_data.timeout or self.DEFAULT_TIMEOUT

        # 获取任务管理器
        task_manager = get_task_manager()
        if task_manager is None:
            return ToolResult.error(
                ToolExecutionError("任务管理器未初始化")
            )

        try:
            # 获取任务
            task = await task_manager.get_task(task_id)

            if task is None:
                return ToolResult.error(
                    ToolValidationError(f"任务不存在: {task_id}")
                )

            task_status = task.status.value if hasattr(task.status, 'value') else str(task.status)

            # 如果任务已完成或失败，返回所有输出
            if task.is_terminal():
                outputs = get_task_output_buffer(task_id)
                output_text = "".join(outputs) if outputs else None

                # 如果任务有结果，也添加到输出
                if task.result and task.result.data:
                    result_text = str(task.result.data)
                    if output_text:
                        output_text += "\n" + result_text
                    else:
                        output_text = result_text

                return ToolResult.ok(
                    data=TaskOutputOutput(
                        task_id=task_id,
                        status=task_status,
                        output=output_text,
                        partial=False,
                        has_more=False
                    ),
                    message=f"任务已终止，返回完整输出",
                    metadata={"task_id": task_id, "status": task_status}
                )

            # 非阻塞模式：立即返回当前输出
            if not block:
                outputs = get_task_output_buffer(task_id)
                output_text = "".join(outputs) if outputs else None

                # 清空已读取的输出
                if task_id in _task_outputs:
                    _task_outputs[task_id] = []

                has_more = task_status in ("pending", "in_progress")

                return ToolResult.ok(
                    data=TaskOutputOutput(
                        task_id=task_id,
                        status=task_status,
                        output=output_text,
                        partial=True,
                        has_more=has_more
                    ),
                    message=f"返回当前输出（非阻塞模式）",
                    metadata={"task_id": task_id, "status": task_status}
                )

            # 阻塞模式：等待新输出或任务完成
            start_time = time.time()
            collected_outputs = []

            while time.time() - start_time < timeout:
                # 获取当前输出
                outputs = get_task_output_buffer(task_id)
                if outputs:
                    collected_outputs.extend(outputs)
                    # 清空已读取的输出
                    _task_outputs[task_id] = []

                # 检查任务是否已终止
                task = await task_manager.get_task(task_id)
                if task and task.is_terminal():
                    # 获取剩余输出
                    outputs = get_task_output_buffer(task_id)
                    if outputs:
                        collected_outputs.extend(outputs)
                        _task_outputs[task_id] = []

                    output_text = "".join(collected_outputs) if collected_outputs else None

                    # 添加任务结果
                    if task.result and task.result.data:
                        result_text = str(task.result.data)
                        if output_text:
                            output_text += "\n" + result_text
                        else:
                            output_text = result_text

                    task_status = task.status.value if hasattr(task.status, 'value') else str(task.status)

                    return ToolResult.ok(
                        data=TaskOutputOutput(
                            task_id=task_id,
                            status=task_status,
                            output=output_text,
                            partial=False,
                            has_more=False
                        ),
                        message=f"任务已完成，返回完整输出",
                        metadata={"task_id": task_id, "status": task_status}
                    )

                # 等待新输出或超时
                if task_id in _task_output_events:
                    try:
                        await asyncio.wait_for(
                            _task_output_events[task_id].wait(),
                            timeout=0.5
                        )
                        _task_output_events[task_id].clear()
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(0.1)

            # 超时，返回已收集的输出
            output_text = "".join(collected_outputs) if collected_outputs else None

            return ToolResult.ok(
                data=TaskOutputOutput(
                    task_id=task_id,
                    status=task_status,
                    output=output_text,
                    partial=True,
                    has_more=True
                ),
                message=f"等待超时，返回部分输出",
                metadata={"task_id": task_id, "status": task_status, "timeout": timeout}
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(
                    f"获取任务输出失败: {str(e)}",
                    details={"task_id": task_id, "exception_type": type(e).__name__}
                )
            )

    def is_read_only(self) -> bool:
        """是否为只读工具"""
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID"
                },
                "block": {
                    "type": "boolean",
                    "description": "是否阻塞等待输出，默认为 true",
                    "default": True
                },
                "timeout": {
                    "type": "number",
                    "description": "阻塞等待的超时时间（秒），默认为 30",
                    "default": 30.0
                }
            },
            "required": ["task_id"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID"
                },
                "status": {
                    "type": "string",
                    "description": "任务当前状态"
                },
                "output": {
                    "type": "string",
                    "description": "任务输出内容"
                },
                "partial": {
                    "type": "boolean",
                    "description": "是否为部分输出"
                },
                "has_more": {
                    "type": "boolean",
                    "description": "是否还有更多输出"
                }
            }
        }
        return schema


@register_tool
class TaskUpdateTool(Tool[TaskUpdateInput, Dict[str, Any]]):
    """
    更新任务工具

    更新现有任务的属性，如状态、优先级、描述等。

    使用场景：
    - 修改任务优先级
    - 更新任务状态
    - 修改任务描述
    - 调整任务配置

    注意：
    - 无法更新已完成的任务
    - 某些字段可能不可修改
    """

    name = "task_update"
    description = "更新现有任务的属性，如状态、优先级、描述等"
    version = "1.0"

    async def validate(self, input_data: TaskUpdateInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.task_id or not input_data.task_id.strip():
            return ToolValidationError("task_id 不能为空")

        # 验证状态值
        if input_data.status is not None:
            valid_statuses = {"pending", "in_progress", "completed", "failed", "cancelled"}
            if input_data.status not in valid_statuses:
                return ToolValidationError(
                    f"无效的状态值: {input_data.status}，有效值为: {', '.join(valid_statuses)}"
                )

        # 验证优先级值
        if input_data.priority is not None:
            valid_priorities = {"low", "medium", "high", "critical"}
            if input_data.priority not in valid_priorities:
                return ToolValidationError(
                    f"无效的优先级值: {input_data.priority}，有效值为: {', '.join(valid_priorities)}"
                )

        return None

    async def execute(self, input_data: TaskUpdateInput) -> ToolResult:
        """执行更新任务操作"""
        task_id = input_data.task_id.strip()

        # 获取任务管理器
        task_manager = get_task_manager()
        if task_manager is None:
            return ToolResult.error(
                ToolExecutionError("任务管理器未初始化")
            )

        try:
            # 获取任务
            task = await task_manager.get_task(task_id)

            if task is None:
                return ToolResult.error(
                    ToolValidationError(f"任务不存在: {task_id}")
                )

            # 检查任务是否已完成
            if task.is_terminal():
                return ToolResult.error(
                    ToolValidationError(f"任务 {task_id} 已处于终止状态，无法更新")
                )

            # 记录更新的字段
            updated_fields = []

            # 更新描述
            if input_data.description is not None:
                old_description = task.description
                task.description = input_data.description
                updated_fields.append(f"description: '{old_description}' -> '{input_data.description}'")

            # 更新状态
            if input_data.status is not None:
                old_status = task.status.value if hasattr(task.status, 'value') else str(task.status)
                # 更新任务状态
                new_status = TaskStatus(input_data.status)
                await task._update_status(new_status)
                updated_fields.append(f"status: '{old_status}' -> '{input_data.status}'")

            # 更新优先级
            if input_data.priority is not None:
                old_priority = task.priority.value if hasattr(task.priority, 'value') else str(task.priority)
                # 将字符串优先级转换为枚举
                priority_map = {
                    "low": TaskPriority.LOW,
                    "medium": TaskPriority.NORMAL,
                    "high": TaskPriority.HIGH,
                    "critical": TaskPriority.CRITICAL
                }
                new_priority = priority_map.get(input_data.priority.lower(), TaskPriority.NORMAL)
                task.priority = new_priority
                updated_fields.append(f"priority: '{old_priority}' -> '{input_data.priority}'")

            # 更新元数据
            if input_data.metadata is not None:
                task.config.metadata.update(input_data.metadata)
                updated_fields.append(f"metadata: 更新了 {len(input_data.metadata)} 个字段")

            # 构建响应
            task_info = {
                "id": task.id,
                "description": task.description,
                "status": task.status.value if hasattr(task.status, 'value') else str(task.status),
                "priority": task.priority.value if hasattr(task.priority, 'value') else str(task.priority),
                "updated_fields": updated_fields,
            }

            return ToolResult.ok(
                data=task_info,
                message=f"成功更新任务: {task_id}" + (f" ({', '.join(updated_fields)})" if updated_fields else ""),
                metadata={
                    "task_id": task_id,
                    "updated_fields": updated_fields,
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(
                    f"更新任务失败: {str(e)}",
                    details={"task_id": task_id, "exception_type": type(e).__name__}
                )
            )

    def is_read_only(self) -> bool:
        """是否为只读工具"""
        return False

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "要更新的任务 ID"
                },
                "description": {
                    "type": "string",
                    "description": "新的任务描述"
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "failed", "cancelled"],
                    "description": "新的任务状态"
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "新的任务优先级"
                },
                "metadata": {
                    "type": "object",
                    "description": "要更新的元数据字段"
                }
            },
            "required": ["task_id"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "任务 ID"},
                "description": {"type": "string", "description": "任务描述"},
                "status": {"type": "string", "description": "任务状态"},
                "priority": {"type": "string", "description": "任务优先级"},
                "updated_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "已更新的字段列表"
                }
            }
        }
        return schema


@register_tool
class TaskListTool(Tool[TaskListInput, List[Dict[str, Any]]]):
    """
    列出任务工具

    列出所有任务，支持多种过滤和排序选项。

    使用场景：
    - 查看所有任务
    - 按状态过滤任务
    - 按优先级过滤任务
    - 按Agent ID过滤任务
    - 分页查询

    特点：
    - 支持多种过滤条件
    - 支持排序
    - 支持分页
    """

    name = "task_list"
    description = "列出所有任务，支持多种过滤和排序选项"
    version = "1.0"

    async def validate(self, input_data: TaskListInput) -> Optional[ToolError]:
        """验证输入参数"""
        if input_data.limit <= 0:
            return ToolValidationError("limit 必须为正数")

        if input_data.offset < 0:
            return ToolValidationError("offset 不能为负数")

        # 验证状态值
        if input_data.status is not None:
            valid_statuses = {"pending", "in_progress", "completed", "failed", "cancelled"}
            if input_data.status not in valid_statuses:
                return ToolValidationError(
                    f"无效的状态值: {input_data.status}，有效值为: {', '.join(valid_statuses)}"
                )

        # 验证优先级值
        if input_data.priority is not None:
            valid_priorities = {"low", "medium", "high", "critical"}
            if input_data.priority not in valid_priorities:
                return ToolValidationError(
                    f"无效的优先级值: {input_data.priority}，有效值为: {', '.join(valid_priorities)}"
                )

        return None

    async def execute(self, input_data: TaskListInput) -> ToolResult:
        """执行列出任务操作"""
        # 获取任务管理器
        task_manager = get_task_manager()
        if task_manager is None:
            return ToolResult.error(
                ToolExecutionError("任务管理器未初始化")
            )

        try:
            # 获取所有任务
            tasks = await task_manager.get_all_tasks()

            # 应用过滤条件
            filtered_tasks = tasks

            if input_data.status is not None:
                filtered_tasks = [
                    t for t in filtered_tasks
                    if (t.status.value if hasattr(t.status, 'value') else str(t.status)) == input_data.status
                ]

            if input_data.priority is not None:
                filtered_tasks = [
                    t for t in filtered_tasks
                    if (t.priority.value if hasattr(t.priority, 'value') else str(t.priority)) == input_data.priority
                ]

            if input_data.agent_id is not None:
                filtered_tasks = [
                    t for t in filtered_tasks
                    if t.agent_id == input_data.agent_id
                ]

            # 获取统计信息
            total_count = len(filtered_tasks)

            # 应用排序
            if input_data.sort_by == "created":
                filtered_tasks.sort(key=lambda t: t.created_at, reverse=(input_data.sort_order == "desc"))
            elif input_data.sort_by == "priority":
                priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
                filtered_tasks.sort(
                    key=lambda t: priority_order.get(
                        t.priority.value if hasattr(t.priority, 'value') else str(t.priority), 4
                    ),
                    reverse=(input_data.sort_order == "desc")
                )
            elif input_data.sort_by == "status":
                status_order = {"pending": 0, "in_progress": 1, "completed": 2, "failed": 3, "cancelled": 4}
                filtered_tasks.sort(
                    key=lambda t: status_order.get(
                        t.status.value if hasattr(t.status, 'value') else str(t.status), 5
                    ),
                    reverse=(input_data.sort_order == "desc")
                )

            # 应用分页
            paginated_tasks = filtered_tasks[input_data.offset:input_data.offset + input_data.limit]

            # 构建任务列表
            task_list = []
            for task in paginated_tasks:
                task_info = {
                    "id": task.id,
                    "description": task.description,
                    "type": task.task_type.value if hasattr(task.task_type, 'value') else str(task.task_type),
                    "status": task.status.value if hasattr(task.status, 'value') else str(task.status),
                    "priority": task.priority.value if hasattr(task.priority, 'value') else str(task.priority),
                    "created_at": task.created_at,
                    "started_at": task.start_time,
                    "completed_at": task.end_time,
                    "agent_id": task.agent_id,
                    "has_result": task.result is not None,
                }
                task_list.append(task_info)

            return ToolResult.ok(
                data=task_list,
                message=f"找到 {total_count} 个任务（显示 {len(task_list)} 个）",
                metadata={
                    "total_count": total_count,
                    "returned_count": len(task_list),
                    "offset": input_data.offset,
                    "limit": input_data.limit,
                    "filters": {
                        "status": input_data.status,
                        "priority": input_data.priority,
                        "agent_id": input_data.agent_id,
                    },
                    "sort": {
                        "by": input_data.sort_by,
                        "order": input_data.sort_order,
                    }
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(
                    f"列出任务失败: {str(e)}",
                    details={"exception_type": type(e).__name__}
                )
            )

    def is_read_only(self) -> bool:
        """是否为只读工具"""
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "failed", "cancelled"],
                    "description": "按状态过滤"
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "按优先级过滤"
                },
                "agent_id": {
                    "type": "string",
                    "description": "按Agent ID过滤"
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["created", "priority", "status"],
                    "description": "排序字段",
                    "default": "created"
                },
                "sort_order": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                    "description": "排序顺序",
                    "default": "desc"
                },
                "limit": {
                    "type": "integer",
                    "description": "最大返回数量",
                    "default": 100,
                    "minimum": 1,
                    "maximum": 1000
                },
                "offset": {
                    "type": "integer",
                    "description": "分页偏移量",
                    "default": 0,
                    "minimum": 0
                }
            }
        }
        schema["returns"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "任务 ID"},
                    "description": {"type": "string", "description": "任务描述"},
                    "type": {"type": "string", "description": "任务类型"},
                    "status": {"type": "string", "description": "任务状态"},
                    "priority": {"type": "string", "description": "任务优先级"},
                    "created_at": {"type": "number", "description": "创建时间戳"},
                    "started_at": {"type": "number", "description": "开始时间戳"},
                    "completed_at": {"type": "number", "description": "完成时间戳"},
                    "agent_id": {"type": "string", "description": "执行Agent ID"},
                    "has_result": {"type": "boolean", "description": "是否有结果"}
                }
            }
        }
        return schema


# 辅助函数：为任务注册输出回调
def register_task_output_callbacks(task: Any) -> None:
    """为任务注册输出回调函数"""
    # 在任务完成时记录结果
    async def on_complete(t: Any) -> None:
        if t.result and t.result.data:
            register_task_output(t.id, f"[完成] {str(t.result.data)}")
        clear_task_output_buffer(t.id)

    # 在任务失败时记录错误
    async def on_fail(t: Any, error: str) -> None:
        register_task_output(t.id, f"[错误] {error}")
        clear_task_output_buffer(t.id)

    task.on_complete(on_complete)
    task.on_fail(on_fail)
