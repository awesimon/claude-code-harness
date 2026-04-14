"""
Agent Skills 工具（标准协议版）
按照 agentskills.io 规范实现
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from datetime import datetime

from tools.base import Tool, ToolResult, ToolError, ToolValidationError, ToolExecutionError, register_tool
from services.skill_registry import get_skill_registry, SkillExecutionContext
from models.skill import SkillExecutionResult


@dataclass
class SkillExecuteInput:
    """执行 skill 的输入"""
    skill: str  # skill 名称
    args: Optional[str] = None  # 可选参数


@dataclass
class SkillListInput:
    """列出 skills 的输入"""
    include_details: bool = False  # 是否包含详细信息


@dataclass
class SkillInstallInput:
    """安装 skill 的输入"""
    source: str  # 源路径（本地目录）
    name: Optional[str] = None  # 可选的目标名称


@dataclass
class SkillUninstallInput:
    """卸载 skill 的输入"""
    skill: str  # skill 名称


@register_tool
class SkillExecuteToolV2(Tool[SkillExecuteInput, Dict[str, Any]]):
    """
    执行 Agent Skill

    按照 agentskills.io 标准协议执行指定的 skill。
    Skill 必须已安装在 .claude/skills/ 目录中。
    """

    name = "skill_execute"
    description = """Execute an installed Agent Skill.

Skills are packages of instructions, scripts, and resources that give agents
capabilities and expertise. They follow the agentskills.io standard protocol.

Example usage:
- skill: pdf-processing, args: extract text from report.pdf
- skill: data-analysis, args: analyze sales_data.csv
"""
    version = "2.0"

    def __init__(self):
        super().__init__()
        self._registry = get_skill_registry()

    async def validate(self, input_data: SkillExecuteInput) -> Optional[ToolError]:
        """验证输入"""
        if not input_data.skill or not input_data.skill.strip():
            return ToolValidationError("skill name is required")

        skill_name = input_data.skill.strip()

        # 检查 skill 是否存在
        if not self._registry.get(skill_name):
            available = ", ".join(self._registry.list_skills()[:10])
            return ToolValidationError(
                f"Skill '{skill_name}' not found. "
                f"Available skills: {available}"
            )

        return None

    async def execute(self, input_data: SkillExecuteInput) -> ToolResult:
        """执行 skill"""
        skill_name = input_data.skill.strip()

        try:
            # 执行 skill
            result = await self._registry.execute_skill(
                skill_name=skill_name,
                args=input_data.args,
                context=SkillExecutionContext()
            )

            if result.success:
                return ToolResult.ok(
                    data=result.data,
                    message=result.message or f"Successfully executed skill: {skill_name}",
                    metadata={
                        "skill": skill_name,
                        "args": input_data.args,
                        "executed_at": result.executed_at.isoformat()
                    }
                )
            else:
                return ToolResult.error(
                    ToolExecutionError(result.error or "Unknown error")
                )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"Failed to execute skill: {str(e)}")
            )

    def get_schema(self) -> Dict[str, Any]:
        """获取 JSON Schema"""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "Name of the skill to execute"
                    },
                    "args": {
                        "type": "string",
                        "description": "Optional arguments to pass to the skill"
                    }
                },
                "required": ["skill"]
            }
        }


@register_tool
class SkillListToolV2(Tool[SkillListInput, List[Dict[str, Any]]]):
    """
    列出所有已安装的 Agent Skills

    返回符合 agentskills.io 标准协议的 skill 列表。
    """

    name = "skill_list"
    description = "List all installed Agent Skills with their metadata"
    version = "2.0"

    def __init__(self):
        super().__init__()
        self._registry = get_skill_registry()

    async def execute(self, input_data: SkillListInput) -> ToolResult:
        """列出所有 skills"""
        try:
            skills = self._registry.get_all_skills()

            result = []
            for name, skill in skills.items():
                item = {
                    "name": skill.name,
                    "description": skill.description,
                }

                if input_data.include_details:
                    item.update({
                        "license": skill.license,
                        "compatibility": skill.compatibility,
                        "metadata": skill.metadata.dict() if skill.metadata else None,
                        "allowed_tools": skill.allowed_tools,
                        "has_scripts": skill.has_scripts,
                        "has_references": skill.has_references,
                        "has_assets": skill.has_assets,
                        "loaded_at": skill.loaded_at.isoformat() if skill.loaded_at else None
                    })

                result.append(item)

            # 按名称排序
            result.sort(key=lambda x: x["name"])

            return ToolResult.ok(
                data=result,
                message=f"Found {len(result)} installed skills",
                metadata={"count": len(result)}
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"Failed to list skills: {str(e)}")
            )

    def is_read_only(self) -> bool:
        return True

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "include_details": {
                        "type": "boolean",
                        "description": "Include detailed metadata for each skill",
                        "default": False
                    }
                }
            }
        }


@register_tool
class SkillInstallToolV2(Tool[SkillInstallInput, str]):
    """
    安装 Agent Skill

    从本地路径安装 skill 到 .claude/skills/ 目录。
    """

    name = "skill_install"
    description = """Install an Agent Skill from a local directory.

The source directory must contain a valid SKILL.md file following
the agentskills.io standard protocol.
"""
    version = "2.0"

    def __init__(self):
        super().__init__()
        self._registry = get_skill_registry()

    async def validate(self, input_data: SkillInstallInput) -> Optional[ToolError]:
        """验证输入"""
        if not input_data.source or not input_data.source.strip():
            return ToolValidationError("source path is required")

        if input_data.name:
            # 验证名称格式
            name = input_data.name.strip()
            if not self._is_valid_name(name):
                return ToolValidationError(
                    f"Invalid skill name: {name}. "
                    "Must be 1-64 chars, lowercase letters/numbers/hyphens only."
                )

        return None

    def _is_valid_name(self, name: str) -> bool:
        """验证 skill 名称"""
        if not name or len(name) > 64:
            return False
        if name.startswith('-') or name.endswith('-') or '--' in name:
            return False
        return all(c.islower() or c.isdigit() or c == '-' for c in name)

    async def execute(self, input_data: SkillInstallInput) -> ToolResult:
        """安装 skill"""
        try:
            skill_name = await self._registry.install_skill(
                source_path=input_data.source.strip(),
                skill_name=input_data.name.strip() if input_data.name else None
            )

            return ToolResult.ok(
                data={"skill": skill_name},
                message=f"Successfully installed skill: {skill_name}",
                metadata={
                    "skill": skill_name,
                    "source": input_data.source,
                    "installed_at": datetime.now().isoformat()
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"Failed to install skill: {str(e)}")
            )

    def is_destructive(self) -> bool:
        return True

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Path to the skill directory to install"
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional name for the installed skill (defaults to directory name)"
                    }
                },
                "required": ["source"]
            }
        }


@register_tool
class SkillUninstallToolV2(Tool[SkillUninstallInput, bool]):
    """
    卸载 Agent Skill
    """

    name = "skill_uninstall"
    description = "Uninstall an Agent Skill"
    version = "2.0"

    def __init__(self):
        super().__init__()
        self._registry = get_skill_registry()

    async def validate(self, input_data: SkillUninstallInput) -> Optional[ToolError]:
        """验证输入"""
        if not input_data.skill or not input_data.skill.strip():
            return ToolValidationError("skill name is required")

        skill_name = input_data.skill.strip()

        # 检查 skill 是否存在
        if not self._registry.get(skill_name):
            return ToolValidationError(f"Skill '{skill_name}' not found")

        return None

    async def execute(self, input_data: SkillUninstallInput) -> ToolResult:
        """卸载 skill"""
        try:
            skill_name = input_data.skill.strip()

            result = await self._registry.uninstall_skill(skill_name)

            if result:
                return ToolResult.ok(
                    data=True,
                    message=f"Successfully uninstalled skill: {skill_name}",
                    metadata={"skill": skill_name}
                )
            else:
                return ToolResult.error(
                    ToolExecutionError(f"Failed to uninstall skill: {skill_name}")
                )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"Failed to uninstall skill: {str(e)}")
            )

    def is_destructive(self) -> bool:
        return True

    def requires_confirmation(self) -> bool:
        return True

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "Name of the skill to uninstall"
                    }
                },
                "required": ["skill"]
            }
        }
