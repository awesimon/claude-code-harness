"""
Plan Service - Manage implementation plans and plan mode state
"""
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from datetime import datetime

from models import Plan, Conversation, ConversationState
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
            # Update existing plan - increment version
            existing_plan.content = data.content
            existing_plan.version = existing_plan.version + 1
            self.db.commit()
            self.db.refresh(existing_plan)
            return existing_plan
        else:
            # Create new plan
            plan = Plan(
                conversation_id=data.conversation_id,
                content=data.content,
                version=1
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
            plan.version = plan.version + 1

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

    # ==================== Plan Mode State Management ====================

    def enter_plan_mode(self, conversation_id: str) -> Dict[str, Any]:
        """
        Enter plan mode for a conversation
        Sets conversation.state = 'planning'
        """
        conversation = self.db.query(Conversation).filter(
            Conversation.id == conversation_id
        ).first()
        if not conversation:
            raise ValueError(f"Conversation {conversation_id} not found")

        if conversation.state == ConversationState.PLANNING:
            return {
                "success": True,
                "message": "Already in plan mode",
                "state": conversation.state,
                "conversation_id": conversation_id
            }

        # Set state to planning
        conversation.state = ConversationState.PLANNING
        self.db.commit()

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
            "state": conversation.state,
            "conversation_id": conversation_id
        }

    def exit_plan_mode(
        self,
        conversation_id: str,
        plan_content: Optional[str] = None,
        allowed_prompts: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        """
        Exit plan mode for a conversation
        Saves plan content and sets conversation.state = 'normal'
        """
        conversation = self.db.query(Conversation).filter(
            Conversation.id == conversation_id
        ).first()
        if not conversation:
            raise ValueError(f"Conversation {conversation_id} not found")

        if conversation.state != ConversationState.PLANNING:
            return {
                "success": True,
                "message": "Not in plan mode, no action needed",
                "state": conversation.state,
                "conversation_id": conversation_id,
                "plan": None,
                "file_path": None
            }

        result = {
            "success": True,
            "message": "Exited plan mode",
            "state": ConversationState.NORMAL,
            "conversation_id": conversation_id,
            "plan": None,
            "file_path": None
        }

        # Save plan content if provided
        if plan_content:
            plan = self.create_or_update_plan(PlanCreate(
                conversation_id=conversation_id,
                content=plan_content
            ))
            result["plan"] = plan.content
            result["file_path"] = f"plans/{conversation_id}_v{plan.version}.md"
            result["version"] = plan.version

        # Store allowed_prompts in conversation meta if provided
        if allowed_prompts:
            # Get or create meta
            meta = self.db.query(Conversation).filter(
                Conversation.id == conversation_id
            ).first()
            # Note: allowed_prompts are not persisted to conversation
            # They are returned in the result for the caller to handle
            result["allowed_prompts"] = allowed_prompts

        # Set state back to normal
        conversation.state = ConversationState.NORMAL
        self.db.commit()

        return result

    def get_plan_mode_state(self, conversation_id: str) -> str:
        """Get the current plan mode state for a conversation"""
        conversation = self.db.query(Conversation).filter(
            Conversation.id == conversation_id
        ).first()
        if not conversation:
            return ConversationState.NORMAL
        return conversation.state

    def is_in_plan_mode(self, conversation_id: str) -> bool:
        """Check if a conversation is in plan mode"""
        return self.get_plan_mode_state(conversation_id) == ConversationState.PLANNING

    def get_plan_versions(self, conversation_id: str) -> List[Dict[str, Any]]:
        """Get all versions of a plan for a conversation"""
        # For now, we only store the latest version
        # In a full implementation, this could query a plan_versions table
        plan = self.get_plan_by_conversation(conversation_id)
        if not plan:
            return []

        return [{
            "version": plan.version,
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
            "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
            "content_preview": plan.content[:200] + "..." if len(plan.content) > 200 else plan.content
        }]

    def recover_plan(self, conversation_id: str, version: int) -> Optional[Plan]:
        """
        Recover a specific version of a plan
        Note: In the current implementation, we only keep the latest version.
        This method is a placeholder for future versioning support.
        """
        # Currently just returns the current plan
        # Future: could query a plan_versions table
        plan = self.get_plan_by_conversation(conversation_id)
        if plan and plan.version == version:
            return plan
        return None
