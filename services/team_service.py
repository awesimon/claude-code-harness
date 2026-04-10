"""
Team Service - Core business logic for team management
Implements team CRUD, member management, and agent status tracking
"""
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_
from fastapi import HTTPException

from models import Team, TeamMember, TeamMemberStatus, Task, TaskStatus


class TeamService:
    def __init__(self, db: Session):
        self.db = db

    def create_team(self, name: str, lead_agent_id: Optional[str] = None) -> Team:
        """Create a new team"""
        # Check if team name already exists
        existing = self.db.query(Team).filter(Team.name == name).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Team with name '{name}' already exists")

        team = Team(
            name=name,
            lead_agent_id=lead_agent_id
        )
        self.db.add(team)
        self.db.commit()
        self.db.refresh(team)
        return team

    def delete_team(self, team_id: str) -> bool:
        """Delete a team and all its members"""
        team = self.db.query(Team).filter(Team.id == team_id).first()
        if not team:
            return False

        self.db.delete(team)
        self.db.commit()
        return True

    def get_team(self, team_id: str) -> Optional[Team]:
        """Get a team by ID with its members"""
        return self.db.query(Team).filter(Team.id == team_id).first()

    def get_team_by_name(self, name: str) -> Optional[Team]:
        """Get a team by name"""
        return self.db.query(Team).filter(Team.name == name).first()

    def list_teams(self) -> List[Team]:
        """List all teams"""
        return self.db.query(Team).order_by(Team.created_at.desc()).all()

    def add_member(
        self,
        team_id: str,
        agent_id: str,
        name: str,
        agent_type: str = "worker"
    ) -> TeamMember:
        """Add a member to a team"""
        # Check if team exists
        team = self.get_team(team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

        # Check if agent is already in the team
        existing = self.db.query(TeamMember).filter(
            and_(TeamMember.team_id == team_id, TeamMember.agent_id == agent_id)
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Agent is already a member of this team")

        member = TeamMember(
            team_id=team_id,
            agent_id=agent_id,
            name=name,
            agent_type=agent_type,
            status=TeamMemberStatus.IDLE
        )
        self.db.add(member)
        self.db.commit()
        self.db.refresh(member)
        return member

    def remove_member(self, team_id: str, agent_id: str) -> bool:
        """Remove a member from a team"""
        member = self.db.query(TeamMember).filter(
            and_(TeamMember.team_id == team_id, TeamMember.agent_id == agent_id)
        ).first()

        if not member:
            return False

        self.db.delete(member)
        self.db.commit()
        return True

    def get_team_members(self, team_id: str) -> List[TeamMember]:
        """Get all members of a team"""
        return self.db.query(TeamMember).filter(TeamMember.team_id == team_id).all()

    def update_member_status(
        self,
        team_id: str,
        agent_id: str,
        status: TeamMemberStatus
    ) -> Optional[TeamMember]:
        """Update a member's status"""
        member = self.db.query(TeamMember).filter(
            and_(TeamMember.team_id == team_id, TeamMember.agent_id == agent_id)
        ).first()

        if not member:
            return None

        member.status = status
        self.db.commit()
        self.db.refresh(member)
        return member

    def get_agent_statuses(self, team_id: str) -> List[dict]:
        """
        Get statuses of all agents in a team based on task ownership.
        Returns idle/busy status determined by incomplete tasks.
        """
        members = self.get_team_members(team_id)

        agent_statuses = []
        for member in members:
            # Count incomplete tasks for this agent in this team
            incomplete_tasks = self.db.query(Task).filter(
                Task.team_id == team_id,
                Task.owner == member.agent_id,
                Task.status != TaskStatus.COMPLETED
            ).all()

            # Determine status based on tasks
            is_busy = len(incomplete_tasks) > 0
            computed_status = TeamMemberStatus.BUSY if is_busy else TeamMemberStatus.IDLE

            # Update member status if it changed
            if member.status != TeamMemberStatus.OFFLINE and member.status != computed_status:
                member.status = computed_status
                self.db.commit()

            agent_statuses.append({
                "agent_id": member.agent_id,
                "name": member.name,
                "agent_type": member.agent_type,
                "status": computed_status.value,
                "joined_at": member.joined_at.isoformat() if member.joined_at else None,
                "current_tasks": [t.id for t in incomplete_tasks]
            })

        return agent_statuses

    def get_agent_team_tasks(self, team_id: str, agent_id: str) -> List[Task]:
        """Get all tasks for an agent within a specific team"""
        return self.db.query(Task).filter(
            Task.team_id == team_id,
            Task.owner == agent_id
        ).all()

    def is_agent_in_team(self, team_id: str, agent_id: str) -> bool:
        """Check if an agent is a member of a team"""
        member = self.db.query(TeamMember).filter(
            and_(TeamMember.team_id == team_id, TeamMember.agent_id == agent_id)
        ).first()
        return member is not None

    def get_team_tasks(self, team_id: str) -> List[Task]:
        """Get all tasks associated with a team"""
        return self.db.query(Task).filter(Task.team_id == team_id).all()
