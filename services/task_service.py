"""
Task Service - Core business logic for task management
Implements task CRUD, claiming, assignment, and dependency management
"""
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import or_
from fastapi import HTTPException

from models import Task, TaskStatus, Conversation
from schemas import TaskCreate, TaskUpdate, TaskClaimResponse, TaskResponse


class TaskService:
    def __init__(self, db: Session):
        self.db = db

    def create_task(self, task_data: TaskCreate) -> Task:
        """Create a new task"""
        # Validate conversation exists if provided
        if task_data.conversation_id:
            conversation = self.db.query(Conversation).filter(
                Conversation.id == task_data.conversation_id
            ).first()
            if not conversation:
                raise HTTPException(status_code=404, detail="Conversation not found")

        task = Task(
            subject=task_data.subject,
            description=task_data.description,
            active_form=task_data.active_form,
            owner=task_data.owner,
            status=TaskStatus(task_data.status),
            blocks=task_data.blocks,
            blocked_by=task_data.blocked_by,
            meta=task_data.meta,
            conversation_id=task_data.conversation_id
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID"""
        return self.db.query(Task).filter(Task.id == task_id).first()

    def list_tasks(
        self,
        conversation_id: Optional[str] = None,
        status: Optional[str] = None,
        owner: Optional[str] = None
    ) -> List[Task]:
        """List tasks with optional filters"""
        query = self.db.query(Task)

        if conversation_id:
            query = query.filter(Task.conversation_id == conversation_id)
        if status:
            query = query.filter(Task.status == TaskStatus(status))
        if owner:
            query = query.filter(Task.owner == owner)

        return query.order_by(Task.created_at.desc()).all()

    def update_task(self, task_id: str, updates: TaskUpdate) -> Optional[Task]:
        """Update a task"""
        task = self.get_task(task_id)
        if not task:
            return None

        update_data = updates.model_dump(exclude_unset=True)

        # Convert status string to enum if present
        if 'status' in update_data and update_data['status']:
            update_data['status'] = TaskStatus(update_data['status'])

        for field, value in update_data.items():
            setattr(task, field, value)

        self.db.commit()
        self.db.refresh(task)
        return task

    def delete_task(self, task_id: str) -> bool:
        """Delete a task and clean up references"""
        task = self.get_task(task_id)
        if not task:
            return False

        # Remove references from other tasks
        all_tasks = self.list_tasks()
        for t in all_tasks:
            if task_id in t.blocks:
                t.blocks = [b for b in t.blocks if b != task_id]
            if task_id in t.blocked_by:
                t.blocked_by = [b for b in t.blocked_by if b != task_id]

        self.db.delete(task)
        self.db.commit()
        return True

    def claim_task(
        self,
        task_id: str,
        agent_id: str,
        check_agent_busy: bool = False
    ) -> TaskClaimResponse:
        """
        Claim a task for an agent.
        Returns success or reason for failure.
        """
        task = self.get_task(task_id)
        if not task:
            return TaskClaimResponse(success=False, reason="task_not_found")

        # Check if already claimed by another agent
        if task.owner and task.owner != agent_id:
            return TaskClaimResponse(
                success=False,
                reason="already_claimed",
                task=TaskResponse.model_validate(task)
            )

        # Check if already resolved
        if task.status == TaskStatus.COMPLETED:
            return TaskClaimResponse(
                success=False,
                reason="already_resolved",
                task=TaskResponse.model_validate(task)
            )

        # Check for unresolved blockers
        all_tasks = {t.id: t for t in self.list_tasks()}
        blocked_by_tasks = []
        for blocker_id in task.blocked_by:
            if blocker_id in all_tasks:
                blocker = all_tasks[blocker_id]
                if blocker.status != TaskStatus.COMPLETED:
                    blocked_by_tasks.append(blocker_id)

        if blocked_by_tasks:
            return TaskClaimResponse(
                success=False,
                reason="blocked",
                task=TaskResponse.model_validate(task),
                blocked_by_tasks=blocked_by_tasks
            )

        # Check if agent is busy
        if check_agent_busy:
            agent_open_tasks = self.db.query(Task).filter(
                Task.owner == agent_id,
                Task.status != TaskStatus.COMPLETED,
                Task.id != task_id
            ).all()

            if agent_open_tasks:
                return TaskClaimResponse(
                    success=False,
                    reason="agent_busy",
                    task=TaskResponse.model_validate(task),
                    busy_with_tasks=[t.id for t in agent_open_tasks]
                )

        # Claim the task
        task.owner = agent_id
        if task.status == TaskStatus.PENDING:
            task.status = TaskStatus.IN_PROGRESS

        self.db.commit()
        self.db.refresh(task)

        return TaskClaimResponse(success=True, task=TaskResponse.model_validate(task))

    def unassign_task(self, task_id: str) -> Optional[Task]:
        """Unassign a task from its owner"""
        task = self.get_task(task_id)
        if not task:
            return None

        task.owner = None
        if task.status == TaskStatus.IN_PROGRESS:
            task.status = TaskStatus.PENDING

        self.db.commit()
        self.db.refresh(task)
        return task

    def block_task(self, from_task_id: str, to_task_id: str) -> bool:
        """
        Make from_task block to_task.
        Returns True if successful.
        """
        from_task = self.get_task(from_task_id)
        to_task = self.get_task(to_task_id)

        if not from_task or not to_task:
            return False

        # Update from_task: A blocks B
        if to_task_id not in from_task.blocks:
            from_task.blocks.append(to_task_id)

        # Update to_task: B is blocked_by A
        if from_task_id not in to_task.blocked_by:
            to_task.blocked_by.append(from_task_id)

        self.db.commit()
        return True

    def get_agent_statuses(self) -> List[dict]:
        """Get status of all agents based on task ownership"""
        from collections import defaultdict

        # Get all non-completed tasks grouped by owner
        tasks_by_owner = defaultdict(list)
        all_tasks = self.list_tasks()

        for task in all_tasks:
            if task.status != TaskStatus.COMPLETED and task.owner:
                tasks_by_owner[task.owner].append(task.id)

        # Build agent statuses
        agent_statuses = []
        for owner, task_ids in tasks_by_owner.items():
            agent_statuses.append({
                "agent_id": owner,
                "name": owner,  # Simplified - in real app, lookup agent name
                "status": "busy" if task_ids else "idle",
                "current_tasks": task_ids
            })

        return agent_statuses

    def get_next_available_task(self, agent_id: str) -> Optional[Task]:
        """
        Get the next available task for an agent.
        Returns a task that:
        - Is pending
        - Is not blocked
        - Is not already claimed
        """
        pending_tasks = self.db.query(Task).filter(
            Task.status == TaskStatus.PENDING,
            or_(Task.owner == None, Task.owner == agent_id)
        ).order_by(Task.created_at).all()

        all_tasks = {t.id: t for t in self.list_tasks()}

        for task in pending_tasks:
            # Check if blocked
            is_blocked = False
            for blocker_id in task.blocked_by:
                if blocker_id in all_tasks:
                    blocker = all_tasks[blocker_id]
                    if blocker.status != TaskStatus.COMPLETED:
                        is_blocked = True
                        break

            if not is_blocked:
                return task

        return None
