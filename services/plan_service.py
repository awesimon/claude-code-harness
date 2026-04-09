"""
Plan Service - Manage implementation plans
"""
from typing import Optional
from sqlalchemy.orm import Session

from models import Plan, Conversation
from schemas import PlanCreate, PlanUpdate


class PlanService:
    def __init__(self, db: Session):
        self.db = db

    def create_or_update_plan(self, data: PlanCreate) -> Plan:
        """Create or update a plan for a conversation"""
        # Check if conversation exists
        conversation = self.db.query(Conversation).filter(
            Conversation.id == data.conversation_id
        ).first()
        if not conversation:
            raise ValueError(f"Conversation {data.conversation_id} not found")

        # Check if plan already exists
        existing_plan = self.db.query(Plan).filter(
            Plan.conversation_id == data.conversation_id
        ).first()

        if existing_plan:
            # Update existing plan
            existing_plan.content = data.content
            self.db.commit()
            self.db.refresh(existing_plan)
            return existing_plan
        else:
            # Create new plan
            plan = Plan(
                conversation_id=data.conversation_id,
                content=data.content
            )
            self.db.add(plan)
            self.db.commit()
            self.db.refresh(plan)
            return plan

    def get_plan(self, plan_id: str) -> Optional[Plan]:
        """Get a plan by ID"""
        return self.db.query(Plan).filter(Plan.id == plan_id).first()

    def get_plan_by_conversation(self, conversation_id: str) -> Optional[Plan]:
        """Get plan for a conversation"""
        return self.db.query(Plan).filter(
            Plan.conversation_id == conversation_id
        ).first()

    def update_plan(self, plan_id: str, updates: PlanUpdate) -> Optional[Plan]:
        """Update a plan"""
        plan = self.get_plan(plan_id)
        if not plan:
            return None

        if updates.content is not None:
            plan.content = updates.content

        self.db.commit()
        self.db.refresh(plan)
        return plan

    def delete_plan(self, plan_id: str) -> bool:
        """Delete a plan"""
        plan = self.get_plan(plan_id)
        if not plan:
            return False

        self.db.delete(plan)
        self.db.commit()
        return True
