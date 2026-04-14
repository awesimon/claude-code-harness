"""
Agent Skills 加载器
按照 agentskills.io 标准协议加载 skill
"""
import os
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime
import asyncio

from models.skill import SkillDefinition, SkillMetadata
from utils.frontmatter_parser import parse_frontmatter, extract_frontmatter_field


class SkillLoader:
    """
    Skill 加载器

    从 .claude/skills/ 目录加载符合标准协议的 skill
    目录结构:
        skill-name/
        ├── SKILL.md          # 必需
        ├── scripts/          # 可选
        ├── references/       # 可选
        └── assets/           # 可选
    """

    def __init__(self, skills_dir: Optional[str] = None):
        """
        初始化加载器

        Args:
            skills_dir: Skill 目录路径，默认 ~/.claude/skills
        """
        if skills_dir is None:
            skills_dir = os.path.expanduser("~/.claude/skills")
        self.skills_dir = Path(skills_dir)

    def ensure_skills_dir(self) -> None:
        """确保 skills 目录存在"""
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    async def load_all_skills(self) -> Dict[str, SkillDefinition]:
        """
        加载所有 skill

        Returns:
            Dict[skill_name, SkillDefinition]
        """
        skills = {}

        if not self.skills_dir.exists():
            return skills

        # 遍历所有子目录
        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            try:
                skill = await self.load_skill_from_dir(skill_dir)
                if skill:
                    skills[skill.name] = skill
            except Exception as e:
                print(f"Failed to load skill from {skill_dir}: {e}")

        return skills

    async def load_skill_from_dir(self, skill_dir: Path) -> Optional[SkillDefinition]:
        """
        从目录加载单个 skill

        Args:
            skill_dir: Skill 目录路径

        Returns:
            SkillDefinition 或 None（如果不是有效的 skill 目录）
        """
        skill_md_path = skill_dir / "SKILL.md"

        if not skill_md_path.exists():
            return None

        # 异步读取文件
        content = await asyncio.to_thread(skill_md_path.read_text, encoding='utf-8')

        return self._parse_skill(skill_dir, content)

    def _parse_skill(self, skill_dir: Path, content: str) -> SkillDefinition:
        """
        解析 skill 内容

        Args:
            skill_dir: Skill 目录
            content: SKILL.md 文件内容

        Returns:
            SkillDefinition
        """
        # 解析 frontmatter
        frontmatter, markdown_content = parse_frontmatter(content)

        # 提取必需字段
        name = frontmatter.get('name')
        if not name:
            raise ValueError(f"Missing required 'name' field in {skill_dir}")

        description = frontmatter.get('description')
        if not description:
            raise ValueError(f"Missing required 'description' field in {skill_dir}")

        # 验证目录名匹配 name 字段
        if skill_dir.name != name:
            raise ValueError(f"Skill directory name '{skill_dir.name}' does not match 'name' field '{name}'")

        # 提取可选字段
        license_field = frontmatter.get('license')
        compatibility = frontmatter.get('compatibility')

        # 解析 metadata
        metadata_raw = frontmatter.get('metadata', {})
        metadata = SkillMetadata(**metadata_raw) if metadata_raw else None

        # 解析 allowed-tools（支持空格分隔的字符串或列表）
        allowed_tools_raw = extract_frontmatter_field(frontmatter, 'allowed_tools')
        allowed_tools = None
        if allowed_tools_raw:
            if isinstance(allowed_tools_raw, str):
                allowed_tools = allowed_tools_raw.split()
            elif isinstance(allowed_tools_raw, list):
                allowed_tools = allowed_tools_raw

        # 检查可选子目录
        has_scripts = (skill_dir / "scripts").is_dir()
        has_references = (skill_dir / "references").is_dir()
        has_assets = (skill_dir / "assets").is_dir()

        return SkillDefinition(
            name=name,
            description=description,
            license=license_field,
            compatibility=compatibility,
            metadata=metadata,
            allowed_tools=allowed_tools,
            content=markdown_content,
            base_dir=str(skill_dir.absolute()),
            has_scripts=has_scripts,
            has_references=has_references,
            has_assets=has_assets,
            loaded_at=datetime.now()
        )

    def get_skill_path(self, skill_name: str) -> Optional[Path]:
        """获取 skill 目录路径"""
        skill_dir = self.skills_dir / skill_name
        if skill_dir.exists() and skill_dir.is_dir():
            return skill_dir
        return None

    async def install_skill(self, source_path: str, skill_name: Optional[str] = None) -> str:
        """
        安装 skill（从本地路径复制）

        Args:
            source_path: 源 skill 目录路径
            skill_name: 目标 skill 名称（默认使用源目录名）

        Returns:
            安装的 skill 名称
        """
        source = Path(source_path)
        if not source.exists():
            raise ValueError(f"Source path does not exist: {source_path}")

        # 验证源目录包含 SKILL.md
        if not (source / "SKILL.md").exists():
            raise ValueError(f"Source is not a valid skill directory (missing SKILL.md): {source_path}")

        # 确定目标名称
        if skill_name is None:
            skill_name = source.name

        # 验证名称格式
        if not self._is_valid_skill_name(skill_name):
            raise ValueError(f"Invalid skill name: {skill_name}")

        # 复制目录
        target = self.skills_dir / skill_name
        if target.exists():
            raise ValueError(f"Skill already exists: {skill_name}")

        await asyncio.to_thread(self._copy_tree, source, target)

        return skill_name

    def _is_valid_skill_name(self, name: str) -> bool:
        """验证 skill 名称格式"""
        if not name:
            return False
        if len(name) > 64:
            return False
        if name.startswith('-') or name.endswith('-'):
            return False
        if '--' in name:
            return False
        # 只允许小写字母、数字和连字符
        return all(c.islower() or c.isdigit() or c == '-' for c in name)

    def _copy_tree(self, src: Path, dst: Path) -> None:
        """递归复制目录"""
        import shutil
        shutil.copytree(src, dst)

    async def uninstall_skill(self, skill_name: str) -> bool:
        """
        卸载 skill

        Args:
            skill_name: Skill 名称

        Returns:
            是否成功卸载
        """
        skill_dir = self.skills_dir / skill_name
        if not skill_dir.exists():
            return False

        await asyncio.to_thread(self._remove_tree, skill_dir)
        return True

    def _remove_tree(self, path: Path) -> None:
        """递归删除目录"""
        import shutil
        shutil.rmtree(path)
