"""
计划模式工具模块
提供进入和退出计划模式的功能
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
import json
import asyncio
from pathlib import Path
import tempfile

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool


class PlanModeStatus(str, Enum):
    """计划模式状态"""
    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass
class PlanState:
    """计划状态数据结构"""
    status: PlanModeStatus = PlanModeStatus.INACTIVE
    prompt: Optional[str] = None
    allowed_prompts: List[str] = field(default_factory=list)
    started_at: Optional[str] = None
    plan_id: Optional[str] = None


@dataclass
class EnterPlanModeInput:
    """进入计划模式工具的输入参数"""
    prompt: Optional[str] = None  # 计划提示/目标
    plan_id: Optional[str] = None  # 计划 ID


@dataclass
class ExitPlanModeInput:
    """退出计划模式工具的输入参数"""
    allowed_prompts: Optional[List[str]] = None  # 允许继续使用的提示列表
    clear_state: bool = True  # 是否清除计划状态


class PlanModeManager:
    """计划模式状态管理器"""

    _instance = None
    _state: PlanState = None
    _storage_path: Optional[Path] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._state = PlanState()
            cls._instance._storage_path = Path(tempfile.gettempdir()) / "claude_plan_mode.json"
            # 尝试加载之前的状态
            cls._instance._load_state_sync()
        return cls._instance

    def _load_state_sync(self) -> None:
        """同步加载状态"""
        if self._storage_path and self._storage_path.exists():
            try:
                content = self._storage_path.read_text(encoding='utf-8')
                data = json.loads(content)
                self._state = PlanState(
                    status=PlanModeStatus(data.get("status", "inactive")),
                    prompt=data.get("prompt"),
                    allowed_prompts=data.get("allowed_prompts", []),
                    started_at=data.get("started_at"),
                    plan_id=data.get("plan_id"),
                )
            except Exception:
                self._state = PlanState()

    async def _load_state(self) -> None:
        """异步加载状态"""
        if self._storage_path and self._storage_path.exists():
            try:
                content = await asyncio.to_thread(self._storage_path.read_text, encoding='utf-8')
                data = json.loads(content)
                self._state = PlanState(
                    status=PlanModeStatus(data.get("status", "inactive")),
                    prompt=data.get("prompt"),
                    allowed_prompts=data.get("allowed_prompts", []),
                    started_at=data.get("started_at"),
                    plan_id=data.get("plan_id"),
                )
            except Exception:
                self._state = PlanState()

    async def _save_state(self) -> None:
        """保存状态到存储"""
        if self._storage_path:
            await asyncio.to_thread(self._storage_path.parent.mkdir, parents=True, exist_ok=True)
            data = {
                "status": self._state.status.value,
                "prompt": self._state.prompt,
                "allowed_prompts": self._state.allowed_prompts,
                "started_at": self._state.started_at,
                "plan_id": self._state.plan_id,
            }
            await asyncio.to_thread(
                self._storage_path.write_text,
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )

    def get_state(self) -> PlanState:
        """获取当前计划状态"""
        return self._state

    async def enter_plan_mode(self, prompt: Optional[str] = None, plan_id: Optional[str] = None) -> PlanState:
        """进入计划模式"""
        import datetime
        import uuid

        await self._load_state()

        self._state = PlanState(
            status=PlanModeStatus.ACTIVE,
            prompt=prompt,
            allowed_prompts=[],
            started_at=datetime.datetime.now().isoformat(),
            plan_id=plan_id or str(uuid.uuid4())[:8],
        )

        await self._save_state()
        return self._state

    async def exit_plan_mode(self, allowed_prompts: Optional[List[str]] = None, clear_state: bool = True) -> PlanState:
        """退出计划模式"""
        await self._load_state()

        if clear_state:
            self._state = PlanState(status=PlanModeStatus.INACTIVE)
        else:
            self._state.status = PlanModeStatus.INACTIVE
            self._state.allowed_prompts = allowed_prompts or []

        await self._save_state()
        return self._state


@register_tool
class EnterPlanModeTool(Tool[EnterPlanModeInput, PlanState]):
    """
    进入计划模式工具

    设置计划模式状态，用于开始一个计划任务
    可以指定计划提示和计划 ID
    """

    name = "enter_plan_mode"
    description = "进入计划模式，开始一个计划任务"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._manager = PlanModeManager()

    async def validate(self, input_data: EnterPlanModeInput) -> Optional[ToolError]:
        # 检查当前状态
        current_state = self._manager.get_state()
        if current_state.status == PlanModeStatus.ACTIVE:
            return ToolValidationError(
                f"当前已在计划模式中（计划 ID: {current_state.plan_id}），请先退出当前计划模式"
            )
        return None

    async def execute(self, input_data: EnterPlanModeInput) -> ToolResult:
        try:
            state = await self._manager.enter_plan_mode(
                prompt=input_data.prompt,
                plan_id=input_data.plan_id
            )

            message_parts = ["成功进入计划模式"]
            if state.plan_id:
                message_parts.append(f"（计划 ID: {state.plan_id}）")
            if state.prompt:
                message_parts.append(f"\n计划目标: {state.prompt}")

            return ToolResult.ok(
                data={
                    "status": state.status.value,
                    "plan_id": state.plan_id,
                    "prompt": state.prompt,
                    "started_at": state.started_at,
                },
                message=" ".join(message_parts),
                metadata={
                    "plan_id": state.plan_id,
                    "started_at": state.started_at,
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"进入计划模式失败: {str(e)}")
            )

    def is_destructive(self) -> bool:
        return False


@register_tool
class ExitPlanModeTool(Tool[ExitPlanModeInput, PlanState]):
    """
    退出计划模式工具

    清理计划模式状态，退出计划任务
    可以选择性地保留允许的提示列表
    """

    name = "exit_plan_mode"
    description = "退出计划模式，结束当前计划任务"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._manager = PlanModeManager()

    async def validate(self, input_data: ExitPlanModeInput) -> Optional[ToolError]:
        # 检查当前状态
        current_state = self._manager.get_state()
        if current_state.status != PlanModeStatus.ACTIVE:
            return ToolValidationError("当前不在计划模式中，无法退出")
        return None

    async def execute(self, input_data: ExitPlanModeInput) -> ToolResult:
        try:
            old_state = self._manager.get_state()

            state = await self._manager.exit_plan_mode(
                allowed_prompts=input_data.allowed_prompts,
                clear_state=input_data.clear_state
            )

            message_parts = ["成功退出计划模式"]
            if old_state.plan_id:
                message_parts.append(f"（计划 ID: {old_state.plan_id}）")

            if input_data.allowed_prompts:
                message_parts.append(f"\n保留 {len(input_data.allowed_prompts)} 个允许的提示")

            return ToolResult.ok(
                data={
                    "status": state.status.value,
                    "previous_plan_id": old_state.plan_id,
                    "allowed_prompts": state.allowed_prompts if not input_data.clear_state else [],
                },
                message=" ".join(message_parts),
                metadata={
                    "previous_plan_id": old_state.plan_id,
                    "clear_state": input_data.clear_state,
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"退出计划模式失败: {str(e)}")
            )

    def is_destructive(self) -> bool:
        return False
