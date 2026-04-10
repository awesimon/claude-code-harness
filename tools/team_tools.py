"""
团队管理工具模块
提供团队创建、删除、成员管理和状态查询等功能
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List
import json
import asyncio

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool


@dataclass
class TeamCreateInput:
    """团队创建工具的输入参数"""
    team_name: str
    lead_agent_id: Optional[str] = None


@dataclass
class TeamDeleteInput:
    """团队删除工具的输入参数"""
    team_id: str
    force: bool = False


@dataclass
class TeamAddMemberInput:
    """添加团队成员工具的输入参数"""
    team_id: str
    agent_id: str
    name: str
    agent_type: str = "worker"


@dataclass
class TeamRemoveMemberInput:
    """移除团队成员工具的输入参数"""
    team_id: str
    agent_id: str


@dataclass
class TeamGetInput:
    """获取团队详情工具的输入参数"""
    team_id: str


@dataclass
class TeamListInput:
    """列出团队工具的输入参数"""
    pass


@dataclass
class TeamGetStatusInput:
    """获取团队成员状态工具的输入参数"""
    team_id: str


@dataclass
class TeamConfig:
    """团队配置数据结构"""
    id: str
    name: str
    lead_agent_id: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class TeamMemberConfig:
    """团队成员配置数据结构"""
    agent_id: str
    name: str
    agent_type: str
    status: str
    joined_at: str


# ============================================================================
# Team Create Tool
# ============================================================================

@register_tool
class TeamCreateTool(Tool[TeamCreateInput, Dict[str, Any]]):
    """
    创建团队工具

    在数据库中创建新团队，并创建团队配置文件在 ~/.claude/teams/{team-name}/config.json
    """

    name = "team_create"
    description = "创建新团队，可选择指定团队领导Agent"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self.claude_dir = Path.home() / ".claude"
        self.teams_dir = self.claude_dir / "teams"
        self.tasks_dir = self.claude_dir / "tasks"

    async def validate(self, input_data: TeamCreateInput) -> Optional[ToolError]:
        if not input_data.team_name:
            return ToolValidationError("team_name 不能为空")

        # 验证团队名称格式
        if "/" in input_data.team_name or "\\" in input_data.team_name:
            return ToolValidationError("team_name 不能包含路径分隔符")

        return None

    async def execute(self, input_data: TeamCreateInput) -> ToolResult:
        import datetime
        from models import SessionLocal, Team

        db = SessionLocal()
        try:
            # Check if team name already exists
            existing = db.query(Team).filter(Team.name == input_data.team_name).first()
            if existing:
                return ToolResult.error(
                    ToolValidationError(f"Team '{input_data.team_name}' already exists")
                )

            # Create team in database
            team = Team(
                name=input_data.team_name,
                lead_agent_id=input_data.lead_agent_id
            )
            db.add(team)
            db.commit()
            db.refresh(team)

            # Create filesystem directories for compatibility
            team_dir = self.teams_dir / input_data.team_name
            task_dir = self.tasks_dir / input_data.team_name

            await asyncio.to_thread(team_dir.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(task_dir.mkdir, parents=True, exist_ok=True)

            # Save config file
            config = {
                "id": team.id,
                "name": team.name,
                "lead_agent_id": team.lead_agent_id,
                "created_at": team.created_at.isoformat() if team.created_at else None,
                "updated_at": team.updated_at.isoformat() if team.updated_at else None,
                "members": [],
                "metadata": {
                    "version": "1.0",
                    "task_count": 0,
                }
            }

            config_path = team_dir / "config.json"
            await asyncio.to_thread(
                config_path.write_text,
                json.dumps(config, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )

            return ToolResult.ok(
                data={
                    "team_id": team.id,
                    "team_name": team.name,
                    "lead_agent_id": team.lead_agent_id,
                    "config_path": str(config_path),
                },
                message=f"成功创建团队 '{input_data.team_name}'",
                metadata={
                    "team_id": team.id,
                    "team_name": input_data.team_name,
                }
            )

        except Exception as e:
            db.rollback()
            return ToolResult.error(
                ToolExecutionError(f"创建团队失败: {str(e)}")
            )
        finally:
            db.close()

    def is_destructive(self) -> bool:
        return False


# ============================================================================
# Team Delete Tool
# ============================================================================

@register_tool
class TeamDeleteTool(Tool[TeamDeleteInput, Dict[str, Any]]):
    """
    删除团队工具

    从数据库中删除团队，并清理团队目录 ~/.claude/teams/{team-name}/
    """

    name = "team_delete"
    description = "删除团队及其所有成员和任务"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self.claude_dir = Path.home() / ".claude"
        self.teams_dir = self.claude_dir / "teams"
        self.tasks_dir = self.claude_dir / "tasks"

    async def validate(self, input_data: TeamDeleteInput) -> Optional[ToolError]:
        if not input_data.team_id:
            return ToolValidationError("team_id 不能为空")
        return None

    async def execute(self, input_data: TeamDeleteInput) -> ToolResult:
        from models import SessionLocal, Team, TeamMember, Task, TaskStatus

        db = SessionLocal()
        try:
            # Get team from database
            team = db.query(Team).filter(Team.id == input_data.team_id).first()
            if not team:
                return ToolResult.error(
                    ToolValidationError(f"Team '{input_data.team_id}' not found")
                )

            team_name = team.name

            # Check for incomplete tasks if not forcing
            if not input_data.force:
                incomplete_tasks = db.query(Task).filter(
                    Task.team_id == input_data.team_id,
                    Task.status != TaskStatus.COMPLETED
                ).all()
                if incomplete_tasks:
                    return ToolResult.error(
                        ToolValidationError(
                            f"团队有 {len(incomplete_tasks)} 个未完成任务，设置 force=True 以强制删除"
                        )
                    )

            # Delete team (cascade will handle members)
            db.delete(team)
            db.commit()

            # Clean up filesystem
            deleted_items = []
            team_dir = self.teams_dir / team_name
            task_dir = self.tasks_dir / team_name

            if team_dir.exists():
                await asyncio.to_thread(self._remove_directory, team_dir)
                deleted_items.append(str(team_dir))

            if task_dir.exists():
                await asyncio.to_thread(self._remove_directory, task_dir)
                deleted_items.append(str(task_dir))

            return ToolResult.ok(
                data={
                    "team_id": input_data.team_id,
                    "team_name": team_name,
                    "deleted_items": deleted_items,
                },
                message=f"成功删除团队 '{team_name}'",
            )

        except Exception as e:
            db.rollback()
            return ToolResult.error(
                ToolExecutionError(f"删除团队失败: {str(e)}")
            )
        finally:
            db.close()

    def _remove_directory(self, path: Path) -> None:
        """递归删除目录"""
        import shutil
        shutil.rmtree(path)

    def is_destructive(self) -> bool:
        return True

    def requires_confirmation(self) -> bool:
        return True


# ============================================================================
# Team Add Member Tool
# ============================================================================

@register_tool
class TeamAddMemberTool(Tool[TeamAddMemberInput, Dict[str, Any]]):
    """
    添加团队成员工具

    将Agent添加到团队中，Agent加入后可以访问团队的任务列表
    """

    name = "team_add_member"
    description = "添加Agent到团队，Agent可以访问团队任务"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self.claude_dir = Path.home() / ".claude"
        self.teams_dir = self.claude_dir / "teams"

    async def validate(self, input_data: TeamAddMemberInput) -> Optional[ToolError]:
        if not input_data.team_id:
            return ToolValidationError("team_id 不能为空")
        if not input_data.agent_id:
            return ToolValidationError("agent_id 不能为空")
        if not input_data.name:
            return ToolValidationError("name 不能为空")
        return None

    async def execute(self, input_data: TeamAddMemberInput) -> ToolResult:
        from models import SessionLocal, Team, TeamMember

        db = SessionLocal()
        try:
            # Check if team exists
            team = db.query(Team).filter(Team.id == input_data.team_id).first()
            if not team:
                return ToolResult.error(
                    ToolValidationError(f"Team '{input_data.team_id}' not found")
                )

            # Check if agent is already in team
            existing = db.query(TeamMember).filter(
                TeamMember.team_id == input_data.team_id,
                TeamMember.agent_id == input_data.agent_id
            ).first()
            if existing:
                return ToolResult.error(
                    ToolValidationError("Agent is already a member of this team")
                )

            # Add member
            member = TeamMember(
                team_id=input_data.team_id,
                agent_id=input_data.agent_id,
                name=input_data.name,
                agent_type=input_data.agent_type
            )
            db.add(member)
            db.commit()
            db.refresh(member)

            # Update config file
            await self._update_team_config(team)

            return ToolResult.ok(
                data={
                    "team_id": input_data.team_id,
                    "agent_id": input_data.agent_id,
                    "name": input_data.name,
                    "agent_type": input_data.agent_type,
                    "joined_at": member.joined_at.isoformat() if member.joined_at else None,
                },
                message=f"成功添加成员 '{input_data.name}' 到团队",
            )

        except Exception as e:
            db.rollback()
            return ToolResult.error(
                ToolExecutionError(f"添加成员失败: {str(e)}")
            )
        finally:
            db.close()

    async def _update_team_config(self, team):
        """Update the team config file with current members"""
        from models import SessionLocal, TeamMember

        db = SessionLocal()
        try:
            members = db.query(TeamMember).filter(TeamMember.team_id == team.id).all()

            config_path = self.teams_dir / team.name / "config.json"
            if config_path.exists():
                config = json.loads(await asyncio.to_thread(config_path.read_text, encoding='utf-8'))
                config["members"] = [
                    {
                        "agent_id": m.agent_id,
                        "name": m.name,
                        "agent_type": m.agent_type,
                        "status": m.status.value if m.status else "idle",
                        "joined_at": m.joined_at.isoformat() if m.joined_at else None,
                    }
                    for m in members
                ]
                await asyncio.to_thread(
                    config_path.write_text,
                    json.dumps(config, indent=2, ensure_ascii=False),
                    encoding='utf-8'
                )
        finally:
            db.close()

    def is_destructive(self) -> bool:
        return False


# ============================================================================
# Team Remove Member Tool
# ============================================================================

@register_tool
class TeamRemoveMemberTool(Tool[TeamRemoveMemberInput, Dict[str, Any]]):
    """
    移除团队成员工具

    从团队中移除Agent
    """

    name = "team_remove_member"
    description = "从团队中移除Agent"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self.claude_dir = Path.home() / ".claude"
        self.teams_dir = self.claude_dir / "teams"

    async def validate(self, input_data: TeamRemoveMemberInput) -> Optional[ToolError]:
        if not input_data.team_id:
            return ToolValidationError("team_id 不能为空")
        if not input_data.agent_id:
            return ToolValidationError("agent_id 不能为空")
        return None

    async def execute(self, input_data: TeamRemoveMemberInput) -> ToolResult:
        from models import SessionLocal, Team, TeamMember

        db = SessionLocal()
        try:
            # Check if team exists
            team = db.query(Team).filter(Team.id == input_data.team_id).first()
            if not team:
                return ToolResult.error(
                    ToolValidationError(f"Team '{input_data.team_id}' not found")
                )

            # Find and remove member
            member = db.query(TeamMember).filter(
                TeamMember.team_id == input_data.team_id,
                TeamMember.agent_id == input_data.agent_id
            ).first()

            if not member:
                return ToolResult.error(
                    ToolValidationError("Agent is not a member of this team")
                )

            member_name = member.name
            db.delete(member)
            db.commit()

            # Update config file
            await self._update_team_config(team)

            return ToolResult.ok(
                data={
                    "team_id": input_data.team_id,
                    "agent_id": input_data.agent_id,
                    "name": member_name,
                },
                message=f"成功从团队移除成员 '{member_name}'",
            )

        except Exception as e:
            db.rollback()
            return ToolResult.error(
                ToolExecutionError(f"移除成员失败: {str(e)}")
            )
        finally:
            db.close()

    async def _update_team_config(self, team):
        """Update the team config file with current members"""
        from models import SessionLocal, TeamMember

        db = SessionLocal()
        try:
            members = db.query(TeamMember).filter(TeamMember.team_id == team.id).all()

            config_path = self.teams_dir / team.name / "config.json"
            if config_path.exists():
                config = json.loads(await asyncio.to_thread(config_path.read_text, encoding='utf-8'))
                config["members"] = [
                    {
                        "agent_id": m.agent_id,
                        "name": m.name,
                        "agent_type": m.agent_type,
                        "status": m.status.value if m.status else "idle",
                        "joined_at": m.joined_at.isoformat() if m.joined_at else None,
                    }
                    for m in members
                ]
                await asyncio.to_thread(
                    config_path.write_text,
                    json.dumps(config, indent=2, ensure_ascii=False),
                    encoding='utf-8'
                )
        finally:
            db.close()

    def is_destructive(self) -> bool:
        return True

    def requires_confirmation(self) -> bool:
        return True


# ============================================================================
# Team List Tool
# ============================================================================

@register_tool
class TeamListTool(Tool[TeamListInput, List[Dict[str, Any]]]):
    """
    列出团队工具

    获取所有团队的列表及其成员信息
    """

    name = "team_list"
    description = "列出所有团队及其成员"
    version = "1.0"

    async def execute(self, input_data: TeamListInput) -> ToolResult:
        from models import SessionLocal, Team, TeamMember

        db = SessionLocal()
        try:
            teams = db.query(Team).order_by(Team.created_at.desc()).all()

            result = []
            for team in teams:
                members = db.query(TeamMember).filter(TeamMember.team_id == team.id).all()
                result.append({
                    "id": team.id,
                    "name": team.name,
                    "lead_agent_id": team.lead_agent_id,
                    "created_at": team.created_at.isoformat() if team.created_at else None,
                    "updated_at": team.updated_at.isoformat() if team.updated_at else None,
                    "member_count": len(members),
                    "members": [
                        {
                            "agent_id": m.agent_id,
                            "name": m.name,
                            "agent_type": m.agent_type,
                            "status": m.status.value if m.status else "idle",
                            "joined_at": m.joined_at.isoformat() if m.joined_at else None,
                        }
                        for m in members
                    ]
                })

            return ToolResult.ok(
                data=result,
                message=f"找到 {len(result)} 个团队",
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"获取团队列表失败: {str(e)}")
            )
        finally:
            db.close()

    def is_read_only(self) -> bool:
        return True


# ============================================================================
# Team Get Tool
# ============================================================================

@register_tool
class TeamGetTool(Tool[TeamGetInput, Dict[str, Any]]):
    """
    获取团队详情工具

    获取指定团队的详细信息，包括所有成员
    """

    name = "team_get"
    description = "获取团队详情和成员列表"
    version = "1.0"

    async def validate(self, input_data: TeamGetInput) -> Optional[ToolError]:
        if not input_data.team_id:
            return ToolValidationError("team_id 不能为空")
        return None

    async def execute(self, input_data: TeamGetInput) -> ToolResult:
        from models import SessionLocal, Team, TeamMember

        db = SessionLocal()
        try:
            team = db.query(Team).filter(Team.id == input_data.team_id).first()
            if not team:
                return ToolResult.error(
                    ToolValidationError(f"Team '{input_data.team_id}' not found")
                )

            members = db.query(TeamMember).filter(TeamMember.team_id == team.id).all()

            result = {
                "id": team.id,
                "name": team.name,
                "lead_agent_id": team.lead_agent_id,
                "created_at": team.created_at.isoformat() if team.created_at else None,
                "updated_at": team.updated_at.isoformat() if team.updated_at else None,
                "members": [
                    {
                        "agent_id": m.agent_id,
                        "name": m.name,
                        "agent_type": m.agent_type,
                        "status": m.status.value if m.status else "idle",
                        "joined_at": m.joined_at.isoformat() if m.joined_at else None,
                    }
                    for m in members
                ]
            }

            return ToolResult.ok(
                data=result,
                message=f"获取团队 '{team.name}' 详情成功",
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"获取团队详情失败: {str(e)}")
            )
        finally:
            db.close()

    def is_read_only(self) -> bool:
        return True


# ============================================================================
# Team Get Status Tool
# ============================================================================

@register_tool
class TeamGetStatusTool(Tool[TeamGetStatusInput, List[Dict[str, Any]]]):
    """
    获取团队成员状态工具

    获取团队成员的状态信息，包括当前任务状态（idle/busy）
    基于任务所有权确定agent是idle还是busy
    """

    name = "team_get_status"
    description = "获取团队成员状态，基于任务所有权确定idle/busy状态"
    version = "1.0"

    async def validate(self, input_data: TeamGetStatusInput) -> Optional[ToolError]:
        if not input_data.team_id:
            return ToolValidationError("team_id 不能为空")
        return None

    async def execute(self, input_data: TeamGetStatusInput) -> ToolResult:
        from models import SessionLocal, Team, TeamMember, Task, TaskStatus

        db = SessionLocal()
        try:
            team = db.query(Team).filter(Team.id == input_data.team_id).first()
            if not team:
                return ToolResult.error(
                    ToolValidationError(f"Team '{input_data.team_id}' not found")
                )

            members = db.query(TeamMember).filter(TeamMember.team_id == team.id).all()

            agent_statuses = []
            for member in members:
                # Count incomplete tasks for this agent in this team
                incomplete_tasks = db.query(Task).filter(
                    Task.team_id == input_data.team_id,
                    Task.owner == member.agent_id,
                    Task.status != TaskStatus.COMPLETED
                ).all()

                is_busy = len(incomplete_tasks) > 0

                agent_statuses.append({
                    "agent_id": member.agent_id,
                    "name": member.name,
                    "agent_type": member.agent_type,
                    "status": "busy" if is_busy else "idle",
                    "joined_at": member.joined_at.isoformat() if member.joined_at else None,
                    "current_tasks": [t.id for t in incomplete_tasks]
                })

            return ToolResult.ok(
                data={
                    "team_id": input_data.team_id,
                    "team_name": team.name,
                    "agents": agent_statuses
                },
                message=f"获取团队 '{team.name}' 成员状态成功",
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"获取团队成员状态失败: {str(e)}")
            )
        finally:
            db.close()

    def is_read_only(self) -> bool:
        return True
