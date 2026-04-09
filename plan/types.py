"""
Plan Mode 核心类型定义
"""
from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime


class PlanModeState(Enum):
    """计划模式状态"""
    IDLE = "idle"           # 正常模式
    PLANNING = "planning"   # 计划模式中（只读探索）
    PENDING_APPROVAL = "pending_approval"  # 等待审批
    APPROVED = "approved"   # 计划已批准
    REJECTED = "rejected"   # 计划被拒绝


@dataclass
class PlanContext:
    """计划上下文"""
    plan_content: Optional[str] = None
    plan_file_path: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None  # 'user' 或 agent_id
    is_edited: bool = False
    allowed_prompts: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_content": self.plan_content,
            "plan_file_path": self.plan_file_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "approved_by": self.approved_by,
            "is_edited": self.is_edited,
            "allowed_prompts": self.allowed_prompts,
        }


@dataclass
class PlanModeConfig:
    """计划模式配置"""
    enabled: bool = True
    plans_directory: Optional[str] = None  # 计划文件存储目录
    require_approval: bool = True  # 是否需要用户审批
    auto_save_interval: int = 30  # 自动保存间隔（秒）


class PlanModeError(Exception):
    """计划模式错误基类"""
    pass


class NotInPlanModeError(PlanModeError):
    """不在计划模式中时调用"""
    pass


class AlreadyInPlanModeError(PlanModeError):
    """已经在计划模式中"""
    pass


class NoPlanContentError(PlanModeError):
    """没有计划内容"""
    pass


class PlanApprovalRequiredError(PlanModeError):
    """需要计划审批"""
    pass
