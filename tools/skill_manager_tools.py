"""
Skill 管理工具
提供 skill 安装、卸载、列表功能
"""

from typing import Optional
from pydantic import BaseModel, Field

from .base import Tool, ToolResult, register_tool


class SkillInstallInput(BaseModel):
    """Skill 安装输入"""
    source: str = Field(..., description="Skill 来源（Git URL 或本地路径）")
    name: Optional[str] = Field(None, description="Skill 名称（可选，默认从 URL/路径提取）")


@register_tool
class SkillInstallTool(Tool):
    """安装 Skill"""

    name = "skill_install"
    description = """从 Git 仓库或本地路径安装 Skill，例如：
- 从 Git: source="https://github.com/user/my-skill.git"
- 从本地: source="/path/to/skill"
安装后会自动注册 skill 中的工具
使用场景：用户说"安装 skill"、"添加技能"、"从 github 安装"等"""
    input_model = SkillInstallInput

    async def execute(self, input_data: SkillInstallInput) -> ToolResult:
        """执行安装"""
        # 延迟导入避免循环
        from services.skill_manager import skill_manager

        source = input_data.source

        # 判断是 Git URL 还是本地路径
        if source.startswith("http://") or source.startswith("https://") or source.startswith("git@"):
            # Git URL
            return skill_manager.install_from_git(source, input_data.name)
        else:
            # 本地路径
            return skill_manager.install_from_local(source, input_data.name)


class SkillUninstallInput(BaseModel):
    """Skill 卸载输入"""
    name: str = Field(..., description="要卸载的 Skill 名称")


@register_tool
class SkillUninstallTool(Tool):
    """卸载 Skill"""

    name = "skill_uninstall"
    description = """卸载已安装的 Skill
使用场景：用户说"卸载 skill"、"删除技能"、"移除 skill"等"""
    input_model = SkillUninstallInput

    async def execute(self, input_data: SkillUninstallInput) -> ToolResult:
        """执行卸载"""
        from services.skill_manager import skill_manager
        return skill_manager.uninstall(input_data.name)


class SkillListInput(BaseModel):
    """Skill 列表输入"""
    pass


@register_tool
class SkillListTool(Tool):
    """列出所有已安装的 Skills"""

    name = "skill_list"
    description = """列出所有已安装的 Skills 及其信息
使用场景：用户说"列出 skills"、"查看已安装的技能"、"有哪些 skill"等"""
    input_model = SkillListInput

    async def execute(self, input_data: SkillListInput) -> ToolResult:
        """执行列表"""
        from services.skill_manager import skill_manager
        return skill_manager.list_skills()


class SkillEnableInput(BaseModel):
    """Skill 启用输入"""
    name: str = Field(..., description="要启用的 Skill 名称")


@register_tool
class SkillEnableTool(Tool):
    """启用 Skill"""

    name = "skill_enable"
    description = """启用已禁用的 Skill
使用场景：用户说"启用 skill"、"开启技能"等"""
    input_model = SkillEnableInput

    async def execute(self, input_data: SkillEnableInput) -> ToolResult:
        """执行启用"""
        from services.skill_manager import skill_manager
        return skill_manager.enable_skill(input_data.name)


class SkillDisableInput(BaseModel):
    """Skill 禁用输入"""
    name: str = Field(..., description="要禁用的 Skill 名称")


@register_tool
class SkillDisableTool(Tool):
    """禁用 Skill"""

    name = "skill_disable"
    description = """禁用已安装的 Skill（不会删除，只是暂时停用）
使用场景：用户说"禁用 skill"、"关闭技能"等"""
    input_model = SkillDisableInput

    async def execute(self, input_data: SkillDisableInput) -> ToolResult:
        """执行禁用"""
        from services.skill_manager import skill_manager
        return skill_manager.disable_skill(input_data.name)
