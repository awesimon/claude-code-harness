"""
Skill 注册表
管理所有已加载的 skill，提供查询和执行接口
"""
from typing import Dict, Optional, List, Any, Callable
import asyncio
from dataclasses import dataclass

from models.skill import SkillDefinition, SkillExecutionResult
from services.skill_loader import SkillLoader


@dataclass
class SkillExecutionContext:
    """Skill 执行上下文"""
    session_id: Optional[str] = None
    agent_context: Optional[Any] = None
    tool_context: Optional[Dict[str, Any]] = None


class SkillRegistry:
    """
    Skill 注册表

    单例模式管理所有已加载的 skill
    """

    _instance: Optional['SkillRegistry'] = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if SkillRegistry._initialized:
            return

        self._skills: Dict[str, SkillDefinition] = {}
        self._loader: Optional[SkillLoader] = None
        self._executors: Dict[str, Callable] = {}
        SkillRegistry._initialized = True

    def initialize(self, skills_dir: Optional[str] = None) -> None:
        """
        初始化注册表

        Args:
            skills_dir: Skill 目录路径
        """
        self._loader = SkillLoader(skills_dir)

    async def load_all_skills(self) -> int:
        """
        加载所有 skill

        Returns:
            加载的 skill 数量
        """
        if not self._loader:
            raise RuntimeError("SkillRegistry not initialized. Call initialize() first.")

        skills = await self._loader.load_all_skills()
        self._skills = skills
        return len(skills)

    def get(self, name: str) -> Optional[SkillDefinition]:
        """
        获取 skill 定义

        Args:
            name: Skill 名称

        Returns:
            SkillDefinition 或 None
        """
        return self._skills.get(name)

    def list_skills(self) -> List[str]:
        """列出所有已加载的 skill 名称"""
        return list(self._skills.keys())

    def get_all_skills(self) -> Dict[str, SkillDefinition]:
        """获取所有 skill 定义"""
        return self._skills.copy()

    def register_executor(self, skill_name: str, executor: Callable) -> None:
        """
        为 skill 注册执行器

        Args:
            skill_name: Skill 名称
            executor: 执行函数
        """
        self._executors[skill_name] = executor

    async def execute_skill(
        self,
        skill_name: str,
        args: Optional[str] = None,
        context: Optional[SkillExecutionContext] = None
    ) -> SkillExecutionResult:
        """
        执行 skill

        Args:
            skill_name: Skill 名称
            args: 执行参数
            context: 执行上下文

        Returns:
            SkillExecutionResult
        """
        skill = self.get(skill_name)
        if not skill:
            return SkillExecutionResult(
                success=False,
                error=f"Skill not found: {skill_name}"
            )

        # 如果有注册的执行器，使用它
        if skill_name in self._executors:
            try:
                result = await self._executors[skill_name](skill, args, context)
                return SkillExecutionResult(success=True, data=result)
            except Exception as e:
                return SkillExecutionResult(success=False, error=str(e))

        # 默认执行：返回 skill 内容
        return SkillExecutionResult(
            success=True,
            data={
                "skill": skill_name,
                "description": skill.description,
                "content": skill.content,
                "args": args
            },
            message=f"Skill '{skill_name}' loaded successfully. Use this content to guide your actions."
        )

    async def install_skill(self, source_path: str, skill_name: Optional[str] = None) -> str:
        """
        安装 skill

        Args:
            source_path: 源路径
            skill_name: 目标名称

        Returns:
            安装的 skill 名称
        """
        if not self._loader:
            raise RuntimeError("SkillRegistry not initialized")

        name = await self._loader.install_skill(source_path, skill_name)

        # 重新加载该 skill
        skill_dir = self._loader.get_skill_path(name)
        if skill_dir:
            skill = await self._loader.load_skill_from_dir(skill_dir)
            if skill:
                self._skills[name] = skill

        return name

    async def uninstall_skill(self, skill_name: str) -> bool:
        """
        卸载 skill

        Args:
            skill_name: Skill 名称

        Returns:
            是否成功
        """
        if not self._loader:
            raise RuntimeError("SkillRegistry not initialized")

        result = await self._loader.uninstall_skill(skill_name)

        if result and skill_name in self._skills:
            del self._skills[skill_name]

        return result

    def clear(self) -> None:
        """清空注册表（用于测试）"""
        self._skills.clear()
        self._executors.clear()


# 全局注册表实例
def get_skill_registry() -> SkillRegistry:
    """获取全局 SkillRegistry 实例"""
    return SkillRegistry()
