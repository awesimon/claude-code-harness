"""
定时任务和 Cron 工具模块
提供任务调度、定时执行和 Cron 表达式管理功能
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable, Awaitable
import asyncio
import json
import re
from datetime import datetime, timedelta
from enum import Enum

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool


# 全局调度器实例
_scheduler: Optional['TaskScheduler'] = None


def get_scheduler() -> 'TaskScheduler':
    """获取全局任务调度器实例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
    return _scheduler


def set_scheduler(scheduler: 'TaskScheduler') -> None:
    """设置全局任务调度器"""
    global _scheduler
    _scheduler = scheduler


class ScheduleType(Enum):
    """调度类型"""
    ONCE = "once"           # 执行一次
    INTERVAL = "interval"   # 固定间隔
    CRON = "cron"          # Cron 表达式
    DAILY = "daily"        # 每天
    WEEKLY = "weekly"      # 每周


@dataclass
class ScheduledTask:
    """定时任务信息"""
    id: str
    name: str
    schedule_type: ScheduleType
    schedule_config: Dict[str, Any]
    command: str
    enabled: bool = True
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    run_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScheduleCreateInput:
    """创建定时任务的输入参数"""
    name: str
    schedule_type: str  # once, interval, cron, daily, weekly
    command: str
    config: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class ScheduleListInput:
    """列出定时任务的输入参数"""
    include_disabled: bool = False


@dataclass
class ScheduleDeleteInput:
    """删除定时任务的输入参数"""
    task_id: str


@dataclass
class ScheduleToggleInput:
    """启用/禁用定时任务的输入参数"""
    task_id: str
    enabled: bool


class CronParser:
    """Cron 表达式解析器"""

    @staticmethod
    def validate(cron_expr: str) -> bool:
        """验证 Cron 表达式格式"""
        # 基础 Cron 格式: * * * * * (分 时 日 月 周)
        pattern = r'^[\*\d\-\,\/\?\w]+\s+[\*\d\-\,\/\?\w]+\s+[\*\d\-\,\/\?\w]+\s+[\*\d\-\,\/\?\w]+\s+[\*\d\-\,\/\?\w]+$'
        return bool(re.match(pattern, cron_expr.strip()))

    @staticmethod
    def get_next_run(cron_expr: str, from_time: Optional[datetime] = None) -> Optional[datetime]:
        """
        计算下一次执行时间

        这是一个简化版本，实际应该使用完整的 croniter 库
        """
        # 简化实现，仅支持基本的 */n 格式
        if not CronParser.validate(cron_expr):
            return None

        parts = cron_expr.strip().split()
        minutes, hours, days, months, weekdays = parts

        base_time = from_time or datetime.now()

        # 简化处理: 如果分钟是 */n，则加上对应的间隔
        if minutes.startswith('*/'):
            try:
                interval = int(minutes[2:])
                return base_time + timedelta(minutes=interval)
            except ValueError:
                pass

        # 默认返回 1 小时后
        return base_time + timedelta(hours=1)


class TaskScheduler:
    """
    任务调度器 - 管理定时任务

    功能:
    - 创建和管理定时任务
    - 支持 Cron 表达式
    - 支持固定间隔
    - 任务执行历史记录
    """

    _id_counter = 0

    def __init__(self):
        self._tasks: Dict[str, ScheduledTask] = {}
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def _generate_id(self) -> str:
        """生成任务 ID"""
        TaskScheduler._id_counter += 1
        return f"schedule-{TaskScheduler._id_counter:06d}"

    async def create_task(
        self,
        name: str,
        schedule_type: ScheduleType,
        command: str,
        config: Dict[str, Any],
        enabled: bool = True
    ) -> ScheduledTask:
        """
        创建定时任务

        Args:
            name: 任务名称
            schedule_type: 调度类型
            command: 要执行的命令
            config: 调度配置
            enabled: 是否启用

        Returns:
            创建的任务
        """
        task_id = self._generate_id()

        # 计算下次执行时间
        next_run = None
        if schedule_type == ScheduleType.INTERVAL:
            minutes = config.get('minutes', 60)
            next_run = datetime.now() + timedelta(minutes=minutes)
        elif schedule_type == ScheduleType.CRON:
            cron_expr = config.get('cron', '* * * * *')
            next_run = CronParser.get_next_run(cron_expr)
        elif schedule_type == ScheduleType.ONCE:
            # 立即执行
            next_run = datetime.now()

        task = ScheduledTask(
            id=task_id,
            name=name,
            schedule_type=schedule_type,
            schedule_config=config,
            command=command,
            enabled=enabled,
            next_run=next_run
        )

        async with self._lock:
            self._tasks[task_id] = task

        return task

    async def delete_task(self, task_id: str) -> bool:
        """删除定时任务"""
        async with self._lock:
            if task_id in self._tasks:
                # 停止正在运行的任务
                if task_id in self._running_tasks:
                    self._running_tasks[task_id].cancel()
                    del self._running_tasks[task_id]

                del self._tasks[task_id]
                return True
            return False

    async def toggle_task(self, task_id: str, enabled: bool) -> bool:
        """启用或禁用定时任务"""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.enabled = enabled
                return True
            return False

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """获取定时任务"""
        return self._tasks.get(task_id)

    def list_tasks(self, include_disabled: bool = False) -> List[ScheduledTask]:
        """列出所有定时任务"""
        tasks = list(self._tasks.values())
        if not include_disabled:
            tasks = [t for t in tasks if t.enabled]
        return tasks

    async def execute_task(self, task_id: str) -> Dict[str, Any]:
        """立即执行任务"""
        task = self._tasks.get(task_id)
        if not task:
            raise ToolExecutionError(f"任务不存在: {task_id}")

        task.last_run = datetime.now()
        task.run_count += 1

        # 更新下次执行时间
        if task.schedule_type == ScheduleType.INTERVAL:
            minutes = task.schedule_config.get('minutes', 60)
            task.next_run = datetime.now() + timedelta(minutes=minutes)
        elif task.schedule_type == ScheduleType.CRON:
            cron_expr = task.schedule_config.get('cron', '* * * * *')
            task.next_run = CronParser.get_next_run(cron_expr, datetime.now())

        return {
            "task_id": task_id,
            "command": task.command,
            "executed_at": task.last_run.isoformat(),
            "run_count": task.run_count
        }


@register_tool
class ScheduleCreateTool(Tool[ScheduleCreateInput, Dict[str, Any]]):
    """
    创建定时任务工具

    创建一个新的定时任务，支持多种调度方式:
    - once: 立即执行一次
    - interval: 固定时间间隔
    - cron: Cron 表达式
    - daily: 每天指定时间
    - weekly: 每周指定时间

    使用场景:
    - 定期执行维护任务
    - 定时数据同步
    - 周期性报告生成
    """

    name = "schedule_create"
    description = "创建定时任务，支持间隔、Cron表达式等多种调度方式"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._scheduler: Optional[TaskScheduler] = None

    def _get_scheduler(self) -> TaskScheduler:
        """获取调度器"""
        if self._scheduler is None:
            self._scheduler = get_scheduler()
        return self._scheduler

    async def validate(self, input_data: ScheduleCreateInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.name or not input_data.name.strip():
            return ToolValidationError("name（任务名称）不能为空")

        if not input_data.command or not input_data.command.strip():
            return ToolValidationError("command（命令）不能为空")

        valid_types = {"once", "interval", "cron", "daily", "weekly"}
        schedule_type = input_data.schedule_type.lower()
        if schedule_type not in valid_types:
            return ToolValidationError(
                f"无效的调度类型: {input_data.schedule_type}，可选值: {', '.join(valid_types)}"
            )

        # 验证 Cron 表达式
        if schedule_type == "cron":
            cron_expr = input_data.config.get('cron', '')
            if not CronParser.validate(cron_expr):
                return ToolValidationError(
                    f"无效的 Cron 表达式: {cron_expr}，格式应为: 分 时 日 月 周"
                )

        return None

    async def execute(self, input_data: ScheduleCreateInput) -> ToolResult:
        """执行创建定时任务操作"""
        try:
            scheduler = self._get_scheduler()

            schedule_type = ScheduleType(input_data.schedule_type.lower())

            task = await scheduler.create_task(
                name=input_data.name.strip(),
                schedule_type=schedule_type,
                command=input_data.command.strip(),
                config=input_data.config,
                enabled=input_data.enabled
            )

            return ToolResult.ok(
                data={
                    "id": task.id,
                    "name": task.name,
                    "schedule_type": task.schedule_type.value,
                    "command": task.command,
                    "enabled": task.enabled,
                    "next_run": task.next_run.isoformat() if task.next_run else None,
                },
                message=f"成功创建定时任务: {task.name}",
                metadata={
                    "task_id": task.id,
                    "schedule_type": task.schedule_type.value,
                    "next_run": task.next_run
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"创建定时任务失败: {str(e)}")
            )

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "任务名称"
                },
                "schedule_type": {
                    "type": "string",
                    "enum": ["once", "interval", "cron", "daily", "weekly"],
                    "description": "调度类型"
                },
                "command": {
                    "type": "string",
                    "description": "要执行的命令"
                },
                "config": {
                    "type": "object",
                    "description": "调度配置，如 cron 表达式、间隔分钟数等",
                    "default": {}
                },
                "enabled": {
                    "type": "boolean",
                    "description": "是否立即启用",
                    "default": True
                }
            },
            "required": ["name", "schedule_type", "command"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "任务 ID"},
                "name": {"type": "string", "description": "任务名称"},
                "schedule_type": {"type": "string", "description": "调度类型"},
                "command": {"type": "string", "description": "执行命令"},
                "enabled": {"type": "boolean", "description": "是否启用"},
                "next_run": {"type": "string", "description": "下次执行时间"}
            }
        }
        return schema


@register_tool
class ScheduleListTool(Tool[ScheduleListInput, List[Dict[str, Any]]]):
    """
    列出定时任务工具

    列出所有配置的定时任务及其状态。

    使用场景:
    - 查看所有定时任务
    - 检查任务执行状态
    - 监控任务调度
    """

    name = "schedule_list"
    description = "列出所有定时任务"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._scheduler: Optional[TaskScheduler] = None

    def _get_scheduler(self) -> TaskScheduler:
        """获取调度器"""
        if self._scheduler is None:
            self._scheduler = get_scheduler()
        return self._scheduler

    async def execute(self, input_data: ScheduleListInput) -> ToolResult:
        """执行列出任务操作"""
        try:
            scheduler = self._get_scheduler()
            tasks = scheduler.list_tasks(input_data.include_disabled)

            task_list = []
            for task in tasks:
                task_list.append({
                    "id": task.id,
                    "name": task.name,
                    "schedule_type": task.schedule_type.value,
                    "command": task.command,
                    "enabled": task.enabled,
                    "last_run": task.last_run.isoformat() if task.last_run else None,
                    "next_run": task.next_run.isoformat() if task.next_run else None,
                    "run_count": task.run_count,
                })

            return ToolResult.ok(
                data=task_list,
                message=f"找到 {len(task_list)} 个定时任务",
                metadata={"count": len(task_list)}
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"列出定时任务失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "include_disabled": {
                    "type": "boolean",
                    "description": "是否包含已禁用的任务",
                    "default": False
                }
            }
        }
        schema["returns"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "任务 ID"},
                    "name": {"type": "string", "description": "任务名称"},
                    "schedule_type": {"type": "string", "description": "调度类型"},
                    "command": {"type": "string", "description": "执行命令"},
                    "enabled": {"type": "boolean", "description": "是否启用"},
                    "last_run": {"type": "string", "description": "上次执行时间"},
                    "next_run": {"type": "string", "description": "下次执行时间"},
                    "run_count": {"type": "integer", "description": "执行次数"}
                }
            }
        }
        return schema


@register_tool
class ScheduleDeleteTool(Tool[ScheduleDeleteInput, Dict[str, Any]]):
    """
    删除定时任务工具

    删除指定的定时任务。

    使用场景:
    - 清理不再需要的定时任务
    - 取消计划任务
    """

    name = "schedule_delete"
    description = "删除定时任务"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._scheduler: Optional[TaskScheduler] = None

    def _get_scheduler(self) -> TaskScheduler:
        """获取调度器"""
        if self._scheduler is None:
            self._scheduler = get_scheduler()
        return self._scheduler

    async def validate(self, input_data: ScheduleDeleteInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.task_id or not input_data.task_id.strip():
            return ToolValidationError("task_id 不能为空")
        return None

    async def execute(self, input_data: ScheduleDeleteInput) -> ToolResult:
        """执行删除任务操作"""
        try:
            scheduler = self._get_scheduler()
            success = await scheduler.delete_task(input_data.task_id.strip())

            if success:
                return ToolResult.ok(
                    data={"deleted": True, "task_id": input_data.task_id},
                    message=f"成功删除定时任务: {input_data.task_id}"
                )
            else:
                return ToolResult.error(
                    ToolValidationError(f"任务不存在: {input_data.task_id}")
                )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"删除定时任务失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        return False

    def is_destructive(self) -> bool:
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "要删除的任务 ID"
                }
            },
            "required": ["task_id"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "deleted": {"type": "boolean", "description": "是否删除成功"},
                "task_id": {"type": "string", "description": "任务 ID"}
            }
        }
        return schema


@register_tool
class ScheduleToggleTool(Tool[ScheduleToggleInput, Dict[str, Any]]):
    """
    启用/禁用定时任务工具

    启用或禁用指定的定时任务。

    使用场景:
    - 临时暂停定时任务
    - 重新启用任务
    """

    name = "schedule_toggle"
    description = "启用或禁用定时任务"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._scheduler: Optional[TaskScheduler] = None

    def _get_scheduler(self) -> TaskScheduler:
        """获取调度器"""
        if self._scheduler is None:
            self._scheduler = get_scheduler()
        return self._scheduler

    async def validate(self, input_data: ScheduleToggleInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.task_id or not input_data.task_id.strip():
            return ToolValidationError("task_id 不能为空")
        return None

    async def execute(self, input_data: ScheduleToggleInput) -> ToolResult:
        """执行启用/禁用任务操作"""
        try:
            scheduler = self._get_scheduler()
            success = await scheduler.toggle_task(
                input_data.task_id.strip(),
                input_data.enabled
            )

            if success:
                action = "启用" if input_data.enabled else "禁用"
                return ToolResult.ok(
                    data={
                        "task_id": input_data.task_id,
                        "enabled": input_data.enabled
                    },
                    message=f"成功{action}定时任务: {input_data.task_id}"
                )
            else:
                return ToolResult.error(
                    ToolValidationError(f"任务不存在: {input_data.task_id}")
                )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"切换任务状态失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        return False

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
                "enabled": {
                    "type": "boolean",
                    "description": "是否启用"
                }
            },
            "required": ["task_id", "enabled"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID"},
                "enabled": {"type": "boolean", "description": "当前状态"}
            }
        }
        return schema


@register_tool
class CronValidateTool(Tool[Dict[str, Any], Dict[str, Any]]):
    """
    验证 Cron 表达式工具

    验证 Cron 表达式的有效性，并显示下次执行时间。

    使用场景:
    - 验证 Cron 表达式格式
    - 预览执行时间
    """

    name = "cron_validate"
    description = "验证 Cron 表达式的有效性"
    version = "1.0"

    async def validate(self, input_data: Dict[str, Any]) -> Optional[ToolError]:
        """验证输入参数"""
        cron_expr = input_data.get('cron', '')
        if not cron_expr or not str(cron_expr).strip():
            return ToolValidationError("cron（Cron 表达式）不能为空")
        return None

    async def execute(self, input_data: Dict[str, Any]) -> ToolResult:
        """执行验证操作"""
        try:
            cron_expr = str(input_data.get('cron', '')).strip()
            is_valid = CronParser.validate(cron_expr)

            result = {
                "valid": is_valid,
                "cron": cron_expr,
                "next_run": None
            }

            if is_valid:
                next_run = CronParser.get_next_run(cron_expr)
                result["next_run"] = next_run.isoformat() if next_run else None

            return ToolResult.ok(
                data=result,
                message=f"Cron 表达式验证{'通过' if is_valid else '失败'}: {cron_expr}",
                metadata={"valid": is_valid}
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"验证 Cron 表达式失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "cron": {
                    "type": "string",
                    "description": "Cron 表达式 (格式: 分 时 日 月 周)"
                }
            },
            "required": ["cron"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "valid": {"type": "boolean", "description": "是否有效"},
                "cron": {"type": "string", "description": "表达式"},
                "next_run": {"type": "string", "description": "下次执行时间"}
            }
        }
        return schema
