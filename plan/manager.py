"""
Plan Mode 管理器
处理计划模式的进入、退出和状态管理
"""
import logging
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass, field
from datetime import datetime

from .types import (
    PlanModeState,
    PlanContext,
    NotInPlanModeError,
    AlreadyInPlanModeError,
    NoPlanContentError,
)
from .storage import get_plan_storage

logger = logging.getLogger(__name__)


@dataclass
class PlanModeSession:
    """计划模式会话状态"""
    session_id: str
    state: PlanModeState = PlanModeState.IDLE
    previous_mode: Optional[str] = None  # 进入plan mode之前的模式
    plan_context: PlanContext = field(default_factory=PlanContext)
    entered_at: Optional[datetime] = None
    exited_at: Optional[datetime] = None


class PlanModeManager:
    """
    计划模式管理器

    管理计划模式的生命周期：
    1. 进入计划模式 (enter_plan_mode)
    2. 保存/更新计划 (save_plan)
    3. 提交计划等待审批 (submit_plan)
    4. 审批计划 (approve_plan / reject_plan)
    5. 退出计划模式 (exit_plan_mode)
    """

    def __init__(self, plans_directory: Optional[str] = None):
        self._sessions: Dict[str, PlanModeSession] = {}
        self._storage = get_plan_storage(plans_directory)
        self._callbacks: List[Callable[[str, PlanModeState, PlanModeState], None]] = []

    def register_state_callback(
        self,
        callback: Callable[[str, PlanModeState, PlanModeState], None]
    ):
        """注册状态变更回调"""
        self._callbacks.append(callback)

    def _notify_state_change(
        self,
        session_id: str,
        old_state: PlanModeState,
        new_state: PlanModeState
    ):
        """通知状态变更"""
        for callback in self._callbacks:
            try:
                callback(session_id, old_state, new_state)
            except Exception as e:
                logger.error(f"State callback error: {e}")

    def _get_or_create_session(self, session_id: str) -> PlanModeSession:
        """获取或创建会话"""
        if session_id not in self._sessions:
            self._sessions[session_id] = PlanModeSession(session_id=session_id)
        return self._sessions[session_id]

    def is_in_plan_mode(self, session_id: str) -> bool:
        """检查是否在计划模式中"""
        session = self._sessions.get(session_id)
        if not session:
            return False
        return session.state == PlanModeState.PLANNING

    def get_state(self, session_id: str) -> PlanModeState:
        """获取当前状态"""
        session = self._sessions.get(session_id)
        if not session:
            return PlanModeState.IDLE
        return session.state

    async def enter_plan_mode(
        self,
        session_id: str,
        previous_mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        进入计划模式

        Args:
            session_id: 会话ID
            previous_mode: 进入前的模式（用于恢复）

        Returns:
            包含确认消息的字典
        """
        session = self._get_or_create_session(session_id)

        if session.state == PlanModeState.PLANNING:
            raise AlreadyInPlanModeError(f"Session {session_id} is already in plan mode")

        old_state = session.state

        # 更新状态
        session.state = PlanModeState.PLANNING
        session.previous_mode = previous_mode or "default"
        session.entered_at = datetime.now()
        session.plan_context = PlanContext(
            created_at=datetime.now(),
            plan_file_path=self._storage.get_plan_file_path(session_id)
        )

        self._notify_state_change(session_id, old_state, session.state)

        logger.info(f"Session {session_id} entered plan mode")

        return {
            "success": True,
            "message": (
                "Entered plan mode. You should now focus on exploring the codebase "
                "and designing an implementation approach.\n\n"
                "In plan mode, you should:\n"
                "1. Thoroughly explore the codebase to understand existing patterns\n"
                "2. Identify similar features and architectural approaches\n"
                "3. Consider multiple approaches and their trade-offs\n"
                "4. Use AskUserQuestion if you need to clarify the approach\n"
                "5. Design a concrete implementation strategy\n"
                "6. When ready, use ExitPlanMode to present your plan for approval\n\n"
                "Remember: DO NOT write or edit any files yet. "
                "This is a read-only exploration and planning phase."
            ),
            "state": session.state.value,
            "plan_file_path": session.plan_context.plan_file_path,
        }

    async def save_plan(
        self,
        session_id: str,
        content: str,
        is_edited: bool = False
    ) -> Dict[str, Any]:
        """
        保存计划内容

        Args:
            session_id: 会话ID
            content: 计划内容
            is_edited: 是否被编辑过

        Returns:
            包含保存结果的字典
        """
        session = self._get_or_create_session(session_id)

        # 保存到文件
        file_path = await self._storage.save_plan(session_id, content)

        # 更新上下文
        session.plan_context.plan_content = content
        session.plan_context.plan_file_path = file_path
        session.plan_context.updated_at = datetime.now()
        session.plan_context.is_edited = is_edited

        logger.info(f"Plan saved for session {session_id} to {file_path}")

        return {
            "success": True,
            "file_path": file_path,
            "content_length": len(content),
        }

    async def submit_plan_for_approval(
        self,
        session_id: str,
        allowed_prompts: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        """
        提交计划等待审批

        Args:
            session_id: 会话ID
            allowed_prompts: 允许的提示列表（用于权限控制）

        Returns:
            包含提交结果的字典
        """
        session = self._get_or_create_session(session_id)

        if session.state != PlanModeState.PLANNING:
            raise NotInPlanModeError(
                f"Session {session_id} is not in plan mode (current: {session.state.value})"
            )

        # 检查是否有计划内容
        plan_content = session.plan_context.plan_content
        if not plan_content:
            # 尝试从文件加载
            plan_content = await self._storage.load_plan(session_id)
            if not plan_content:
                raise NoPlanContentError("No plan content found. Please write a plan first.")
            session.plan_context.plan_content = plan_content

        old_state = session.state

        # 更新状态为等待审批
        session.state = PlanModeState.PENDING_APPROVAL
        session.plan_context.allowed_prompts = allowed_prompts or []

        self._notify_state_change(session_id, old_state, session.state)

        logger.info(f"Plan submitted for approval for session {session_id}")

        return {
            "success": True,
            "message": "Plan submitted for approval",
            "state": session.state.value,
            "plan_file_path": session.plan_context.plan_file_path,
            "has_plan_content": bool(plan_content),
        }

    async def approve_plan(
        self,
        session_id: str,
        edited_content: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        批准计划

        Args:
            session_id: 会话ID
            edited_content: 用户编辑后的计划内容（可选）

        Returns:
            包含批准结果的字典
        """
        session = self._sessions.get(session_id)
        if not session:
            raise NotInPlanModeError(f"Session {session_id} not found")

        if session.state != PlanModeState.PENDING_APPROVAL:
            raise NotInPlanModeError(
                f"Session {session_id} is not pending approval (current: {session.state.value})"
            )

        old_state = session.state

        # 如果用户编辑了内容，更新它
        if edited_content is not None:
            session.plan_context.plan_content = edited_content
            session.plan_context.is_edited = True
            await self._storage.save_plan(session_id, edited_content)

        # 更新状态
        session.state = PlanModeState.APPROVED
        session.plan_context.approved_at = datetime.now()
        session.plan_context.approved_by = "user"

        self._notify_state_change(session_id, old_state, session.state)

        logger.info(f"Plan approved for session {session_id}")

        return {
            "success": True,
            "message": "Plan approved",
            "state": session.state.value,
            "plan_content": session.plan_context.plan_content,
            "is_edited": session.plan_context.is_edited,
        }

    async def reject_plan(
        self,
        session_id: str,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        拒绝计划

        Args:
            session_id: 会话ID
            reason: 拒绝原因

        Returns:
            包含拒绝结果的字典
        """
        session = self._sessions.get(session_id)
        if not session:
            raise NotInPlanModeError(f"Session {session_id} not found")

        if session.state != PlanModeState.PENDING_APPROVAL:
            raise NotInPlanModeError(
                f"Session {session_id} is not pending approval (current: {session.state.value})"
            )

        old_state = session.state

        # 回到计划模式继续编辑
        session.state = PlanModeState.PLANNING

        self._notify_state_change(session_id, old_state, session.state)

        logger.info(f"Plan rejected for session {session_id}: {reason}")

        return {
            "success": True,
            "message": f"Plan rejected: {reason}" if reason else "Plan rejected",
            "state": session.state.value,
            "can_continue_planning": True,
        }

    async def exit_plan_mode(
        self,
        session_id: str,
        skip_approval: bool = False
    ) -> Dict[str, Any]:
        """
        退出计划模式

        Args:
            session_id: 会话ID
            skip_approval: 是否跳过审批流程（用于自愿计划模式）

        Returns:
            包含退出结果的字典
        """
        session = self._sessions.get(session_id)
        if not session:
            raise NotInPlanModeError(f"Session {session_id} not found")

        if session.state not in [PlanModeState.PLANNING, PlanModeState.APPROVED]:
            raise NotInPlanModeError(
                f"Session {session_id} is not in a valid plan mode state (current: {session.state.value})"
            )

        # 如果还没有批准且不是跳过模式，需要先提交审批
        if session.state == PlanModeState.PLANNING and not skip_approval:
            raise PlanApprovalRequiredError(
                "Plan must be submitted for approval before exiting plan mode. "
                "Use submit_plan_for_approval first."
            )

        old_state = session.state

        # 获取计划内容
        plan_content = session.plan_context.plan_content
        if not plan_content:
            plan_content = await self._storage.load_plan(session_id)

        # 更新状态
        session.state = PlanModeState.IDLE
        session.exited_at = datetime.now()

        # 恢复之前的模式
        previous_mode = session.previous_mode or "default"

        self._notify_state_change(session_id, old_state, session.state)

        logger.info(f"Session {session_id} exited plan mode")

        result = {
            "success": True,
            "message": "Exited plan mode. You can now start coding.",
            "previous_mode": previous_mode,
            "plan_content": plan_content,
            "plan_file_path": session.plan_context.plan_file_path,
            "is_edited": session.plan_context.is_edited,
        }

        return result

    def get_plan_context(self, session_id: str) -> Optional[PlanContext]:
        """获取计划上下文"""
        session = self._sessions.get(session_id)
        if not session:
            return None
        return session.plan_context

    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话信息"""
        session = self._sessions.get(session_id)
        if not session:
            return None

        return {
            "session_id": session.session_id,
            "state": session.state.value,
            "previous_mode": session.previous_mode,
            "entered_at": session.entered_at.isoformat() if session.entered_at else None,
            "exited_at": session.exited_at.isoformat() if session.exited_at else None,
            "plan_context": session.plan_context.to_dict(),
        }

    def clear_session(self, session_id: str):
        """清除会话数据"""
        if session_id in self._sessions:
            del self._sessions[session_id]
        self._storage.clear_session(session_id)


# 全局管理器实例
_plan_mode_manager: Optional[PlanModeManager] = None


def get_plan_mode_manager(plans_directory: Optional[str] = None) -> PlanModeManager:
    """获取全局计划模式管理器实例"""
    global _plan_mode_manager
    if _plan_mode_manager is None:
        _plan_mode_manager = PlanModeManager(plans_directory)
    return _plan_mode_manager


def reset_plan_mode_manager():
    """重置全局管理器实例（用于测试）"""
    global _plan_mode_manager
    _plan_mode_manager = None
