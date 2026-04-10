"""
Team Router - API endpoints for team management
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from models import get_db
from schemas import APIResponse
from services.team_service import TeamService

# Create router
teams_router = APIRouter(prefix="/teams", tags=["teams"])


@teams_router.post("")
async def create_team(
    name: str,
    lead_agent_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Create a new team"""
    service = TeamService(db)
    try:
        team = service.create_team(name, lead_agent_id)
        return APIResponse(
            success=True,
            data={
                "id": team.id,
                "name": team.name,
                "lead_agent_id": team.lead_agent_id,
                "created_at": team.created_at.isoformat() if team.created_at else None,
            },
            message=f"Team '{name}' created successfully"
        )
    except HTTPException as e:
        return APIResponse(success=False, error=e.detail)


@teams_router.get("")
async def list_teams(db: Session = Depends(get_db)):
    """List all teams"""
    service = TeamService(db)
    teams = service.list_teams()
    return APIResponse(
        success=True,
        data=[
            {
                "id": team.id,
                "name": team.name,
                "lead_agent_id": team.lead_agent_id,
                "member_count": len(team.members),
                "created_at": team.created_at.isoformat() if team.created_at else None,
            }
            for team in teams
        ],
        message=f"Found {len(teams)} teams"
    )


@teams_router.get("/{team_id}")
async def get_team(team_id: str, db: Session = Depends(get_db)):
    """Get team details with members"""
    service = TeamService(db)
    team = service.get_team(team_id)

    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    return APIResponse(
        success=True,
        data={
            "id": team.id,
            "name": team.name,
            "lead_agent_id": team.lead_agent_id,
            "created_at": team.created_at.isoformat() if team.created_at else None,
            "members": [
                {
                    "id": member.id,
                    "agent_id": member.agent_id,
                    "name": member.name,
                    "agent_type": member.agent_type,
                    "status": member.status.value if member.status else None,
                    "joined_at": member.joined_at.isoformat() if member.joined_at else None,
                }
                for member in team.members
            ]
        }
    )


@teams_router.delete("/{team_id}")
async def delete_team(team_id: str, db: Session = Depends(get_db)):
    """Delete a team"""
    service = TeamService(db)
    success = service.delete_team(team_id)

    if not success:
        raise HTTPException(status_code=404, detail="Team not found")

    return APIResponse(success=True, message="Team deleted successfully")


@teams_router.post("/{team_id}/members")
async def add_member(
    team_id: str,
    agent_id: str,
    name: str,
    agent_type: str = "worker",
    db: Session = Depends(get_db)
):
    """Add a member to a team"""
    service = TeamService(db)
    try:
        member = service.add_member(team_id, agent_id, name, agent_type)
        return APIResponse(
            success=True,
            data={
                "id": member.id,
                "agent_id": member.agent_id,
                "name": member.name,
                "agent_type": member.agent_type,
                "status": member.status.value if member.status else None,
            },
            message=f"Member '{name}' added to team"
        )
    except HTTPException as e:
        return APIResponse(success=False, error=e.detail)


@teams_router.delete("/{team_id}/members/{agent_id}")
async def remove_member(team_id: str, agent_id: str, db: Session = Depends(get_db)):
    """Remove a member from a team"""
    service = TeamService(db)
    try:
        service.remove_member(team_id, agent_id)
        return APIResponse(success=True, message="Member removed from team")
    except HTTPException as e:
        return APIResponse(success=False, error=e.detail)


@teams_router.get("/{team_id}/status")
async def get_team_status(team_id: str, db: Session = Depends(get_db)):
    """Get agent statuses for a team"""
    service = TeamService(db)
    try:
        statuses = service.get_agent_statuses(team_id)
        return APIResponse(
            success=True,
            data=statuses,
            message=f"Retrieved status for {len(statuses)} agents"
        )
    except HTTPException as e:
        return APIResponse(success=False, error=e.detail)
