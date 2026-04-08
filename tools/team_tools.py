"""
团队管理工具模块
提供团队创建、删除等功能
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
    description: str
    agent_type: Optional[str] = None  # 团队领导类型（可选）


@dataclass
class TeamDeleteInput:
    """团队删除工具的输入参数"""
    team_name: str
    force: bool = False  # 是否强制删除（忽略未完成的成员）


@dataclass
class TeamConfig:
    """团队配置数据结构"""
    name: str
    description: str
    created_at: str
    agent_type: Optional[str] = None
    members: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@register_tool
class TeamCreateTool(Tool[TeamCreateInput, Dict[str, Any]]):
    """
    创建团队工具

    创建团队配置文件在 ~/.claude/teams/{team-name}/config.json
    创建任务列表目录 ~/.claude/tasks/{team-name}/
    """

    name = "team_create"
    description = "创建新团队，包括配置文件和任务目录"
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

        # 检查团队是否已存在
        team_config_path = self.teams_dir / input_data.team_name / "config.json"
        if team_config_path.exists():
            return ToolValidationError(f"团队 '{input_data.team_name}' 已存在")

        return None

    async def execute(self, input_data: TeamCreateInput) -> ToolResult:
        import datetime

        team_name = input_data.team_name
        team_dir = self.teams_dir / team_name
        task_dir = self.tasks_dir / team_name

        try:
            # 创建团队目录
            await asyncio.to_thread(team_dir.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(task_dir.mkdir, parents=True, exist_ok=True)

            # 创建团队配置
            config = TeamConfig(
                name=team_name,
                description=input_data.description,
                created_at=datetime.datetime.now().isoformat(),
                agent_type=input_data.agent_type,
                members=[],
                metadata={
                    "version": "1.0",
                    "task_count": 0,
                }
            )

            # 保存配置文件
            config_path = team_dir / "config.json"
            config_dict = {
                "name": config.name,
                "description": config.description,
                "created_at": config.created_at,
                "agent_type": config.agent_type,
                "members": config.members,
                "metadata": config.metadata,
            }

            await asyncio.to_thread(
                config_path.write_text,
                json.dumps(config_dict, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )

            return ToolResult.ok(
                data={
                    "team_name": team_name,
                    "config_path": str(config_path),
                    "task_dir": str(task_dir),
                    "config": config_dict,
                },
                message=f"成功创建团队 '{team_name}'",
                metadata={
                    "team_name": team_name,
                    "config_path": str(config_path),
                    "task_dir": str(task_dir),
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"创建团队失败: {str(e)}")
            )

    def is_destructive(self) -> bool:
        return False


@register_tool
class TeamDeleteTool(Tool[TeamDeleteInput, Dict[str, Any]]):
    """
    删除团队工具

    清理团队目录 ~/.claude/teams/{team-name}/
    清理任务列表目录 ~/.claude/tasks/{team-name}/
    """

    name = "team_delete"
    description = "删除团队及其所有相关数据"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self.claude_dir = Path.home() / ".claude"
        self.teams_dir = self.claude_dir / "teams"
        self.tasks_dir = self.claude_dir / "tasks"

    async def validate(self, input_data: TeamDeleteInput) -> Optional[ToolError]:
        if not input_data.team_name:
            return ToolValidationError("team_name 不能为空")

        # 检查团队是否存在
        team_dir = self.teams_dir / input_data.team_name
        if not team_dir.exists():
            return ToolValidationError(f"团队 '{input_data.team_name}' 不存在")

        # 检查是否有活跃成员（非强制模式下）
        if not input_data.force:
            config_path = team_dir / "config.json"
            if config_path.exists():
                try:
                    config_content = await asyncio.to_thread(config_path.read_text, encoding='utf-8')
                    config = json.loads(config_content)
                    members = config.get("members", [])
                    # 检查是否有非空闲成员
                    active_members = [m for m in members if m.get("status") != "idle"]
                    if active_members:
                        return ToolValidationError(
                            f"团队 '{input_data.team_name}' 仍有 {len(active_members)} 个活跃成员，设置 force=True 以强制删除"
                        )
                except Exception:
                    # 如果配置文件读取失败，继续执行删除
                    pass

        return None

    async def execute(self, input_data: TeamDeleteInput) -> ToolResult:
        team_name = input_data.team_name
        team_dir = self.teams_dir / team_name
        task_dir = self.tasks_dir / team_name

        try:
            deleted_items = []

            # 删除团队目录
            if team_dir.exists():
                await asyncio.to_thread(self._remove_directory, team_dir)
                deleted_items.append(str(team_dir))

            # 删除任务目录
            if task_dir.exists():
                await asyncio.to_thread(self._remove_directory, task_dir)
                deleted_items.append(str(task_dir))

            return ToolResult.ok(
                data={
                    "team_name": team_name,
                    "deleted_items": deleted_items,
                },
                message=f"成功删除团队 '{team_name}'",
                metadata={
                    "team_name": team_name,
                    "deleted_count": len(deleted_items),
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"删除团队失败: {str(e)}")
            )

    def _remove_directory(self, path: Path) -> None:
        """递归删除目录"""
        import shutil
        shutil.rmtree(path)

    def is_destructive(self) -> bool:
        return True

    def requires_confirmation(self) -> bool:
        return True
