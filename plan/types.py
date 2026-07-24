"""Compatibility types for the durable plan state contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from state_core import PlanState

PlanModeState = PlanState


@dataclass
class PlanContext:
    plan_content: str | None = None
    plan_file_path: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None
    is_edited: bool = False
    allowed_prompts: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_content": self.plan_content,
            "plan_file_path": self.plan_file_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "approved_by": self.approved_by,
            "is_edited": self.is_edited,
            "allowed_prompts": [dict(item) for item in self.allowed_prompts],
        }


@dataclass
class PlanModeConfig:
    enabled: bool = True
    plans_directory: str | None = None
    require_approval: bool = True
    auto_save_interval: int = 30


class PlanModeError(Exception):
    pass


class NotInPlanModeError(PlanModeError):
    pass


class AlreadyInPlanModeError(PlanModeError):
    pass


class NoPlanContentError(PlanModeError):
    pass


class PlanApprovalRequiredError(PlanModeError):
    pass
