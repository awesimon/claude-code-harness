"""
Agent Skills 加载器
按照 agentskills.io 标准协议加载 skill
"""
import os
import re
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Awaitable, Callable, Dict, Iterator, Optional
from datetime import datetime
import asyncio

from models.skill import SkillDefinition, SkillMetadata
from utils.frontmatter_parser import parse_frontmatter, extract_frontmatter_field
from utils.skill_paths import (
    is_valid_skill_name,
    resolve_skill_path,
    validate_skill_name,
)


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
        self.skills_dir = Path(skills_dir).expanduser().resolve(strict=False)
        self._root_identity: tuple[int, int] | None = None
        self.ensure_skills_dir()

    def ensure_skills_dir(self) -> None:
        """确保 skills 目录存在"""
        if self._root_identity is not None:
            self._verify_root()
            return
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        info = self.skills_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or self.skills_dir.resolve(strict=True) != self.skills_dir:
            raise ValueError("skills root must be a canonical directory")
        self._root_identity = (info.st_dev, info.st_ino)

    def _verify_root(self) -> Path:
        try:
            info = self.skills_dir.lstat()
        except OSError as exc:
            raise ValueError("skills root changed after loader initialization") from exc
        identity = (info.st_dev, info.st_ino)
        if (
            self._root_identity is None
            or identity != self._root_identity
            or not stat.S_ISDIR(info.st_mode)
            or self.skills_dir.resolve(strict=True) != self.skills_dir
        ):
            raise ValueError("skills root changed after loader initialization")
        return self.skills_dir

    @contextmanager
    def _open_root(self) -> Iterator[int]:
        root = self._verify_root()
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(root, flags)
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != self._root_identity:
                raise ValueError("skills root changed after loader initialization")
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _entry_exists(root_fd: int, name: str) -> bool:
        try:
            os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    async def load_all_skills(self) -> Dict[str, SkillDefinition]:
        """
        加载所有 skill

        Returns:
            Dict[skill_name, SkillDefinition]
        """
        skills = {}

        root = self._verify_root()

        # 遍历所有子目录
        for skill_dir in root.iterdir():
            if skill_dir.is_symlink() or not skill_dir.is_dir():
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
        root = self._verify_root()
        canonical = skill_dir.resolve(strict=True)
        try:
            relative = canonical.relative_to(root)
        except ValueError as exc:
            raise ValueError("skill directory escapes skills root") from exc
        if len(relative.parts) != 1 or canonical.name != skill_dir.name:
            raise ValueError("skill directory escapes skills root")
        skill_md_entry = canonical / "SKILL.md"
        try:
            skill_md_path = skill_md_entry.resolve(strict=True)
        except FileNotFoundError:
            return None
        try:
            skill_md_path.relative_to(canonical)
            skill_md_path.relative_to(root)
        except ValueError as exc:
            raise ValueError("SKILL.md escapes skill directory") from exc
        file_info = skill_md_path.stat(follow_symlinks=False)
        if not stat.S_ISREG(file_info.st_mode):
            raise ValueError("SKILL.md must be a regular file")
        self._verify_root()

        # 异步读取文件
        content = await asyncio.to_thread(skill_md_path.read_text, encoding='utf-8')

        return self._parse_skill(canonical, content)

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
        validate_skill_name(name)

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
        skill_dir = resolve_skill_path(self._verify_root(), skill_name)
        if skill_dir.exists() and skill_dir.is_dir():
            return skill_dir
        return None

    async def install_skill(self, source_path: str, skill_name: Optional[str] = None) -> str:
        """
        安装 skill（从本地路径或 GitHub URL）

        Args:
            source_path: 源路径（本地目录或 GitHub URL）
                       GitHub URL 格式:
                       - https://github.com/user/repo/tree/main/path/to/skill
                       - https://github.com/user/repo/blob/main/path/to/skill/SKILL.md
                       - github.com/user/repo/path/to/skill
            skill_name: 目标 skill 名称（默认使用源目录名或从 URL 推断）

        Returns:
            安装的 skill 名称
        """
        # 检查是否为 GitHub URL
        if self._is_github_url(source_path):
            return await self._install_from_github(source_path, skill_name)

        # 本地路径安装
        return await self._install_from_local(source_path, skill_name)

    def _is_github_url(self, url: str) -> bool:
        """检查是否为 GitHub URL"""
        return 'github.com' in url.lower()

    async def _install_from_github(self, github_url: str, skill_name: Optional[str] = None) -> str:
        """
        从 GitHub 安装 skill

        支持的 URL 格式:
        - https://github.com/user/repo/tree/branch/path/to/skill
        - https://github.com/user/repo/blob/branch/path/to/skill/SKILL.md
        - github.com/user/repo/path/to/skill
        """
        # 解析 GitHub URL
        parsed = self._parse_github_url(github_url)
        if not parsed:
            raise ValueError(f"Invalid GitHub URL: {github_url}")

        user, repo, branch, path = parsed

        # 如果没有指定 skill_name，从路径推断
        if skill_name is None:
            skill_name = Path(path).name

        # 下载前先验证 URL/调用者给出的名称
        try:
            skill_name = validate_skill_name(skill_name)
        except ValueError as exc:
            raise ValueError(f"Invalid skill name derived from URL: {skill_name}") from exc

        raw_base = f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}"
        skill_md_content = await self._download_file(f"{raw_base}/SKILL.md")
        if not skill_md_content:
            raise ValueError(f"Could not download SKILL.md from {raw_base}")
        try:
            frontmatter, _ = parse_frontmatter(skill_md_content)
            actual_name = frontmatter.get("name")
            if actual_name is not None:
                skill_name = validate_skill_name(actual_name)
        except Exception as exc:
            raise ValueError(f"Invalid SKILL.md content: {exc}") from exc

        async def populate(staged_skill: Path) -> None:
            staged_skill.mkdir()
            (staged_skill / "SKILL.md").write_text(
                skill_md_content, encoding="utf-8"
            )
            await self._download_github_directory(raw_base, staged_skill)

        return await self._stage_and_publish(skill_name, populate)

    def _parse_github_url(self, url: str) -> Optional[tuple]:
        """
        解析 GitHub URL

        Returns:
            (user, repo, branch, path) 或 None
        """
        # 标准化 URL
        url = url.strip()
        if url.startswith('http://'):
            url = url[7:]
        if url.startswith('https://'):
            url = url[8:]
        if url.startswith('github.com/'):
            url = url[11:]

        # 匹配模式: user/repo/tree/branch/path
        tree_pattern = r'^([^/]+)/([^/]+)/tree/([^/]+)/(.+)$'
        match = re.match(tree_pattern, url)
        if match:
            return match.group(1), match.group(2), match.group(3), match.group(4)

        # 匹配模式: user/repo/blob/branch/path/SKILL.md
        blob_pattern = r'^([^/]+)/([^/]+)/blob/([^/]+)/(.+)/SKILL\.md$'
        match = re.match(blob_pattern, url)
        if match:
            return match.group(1), match.group(2), match.group(3), match.group(4)

        # 匹配模式: user/repo/path（默认 main 分支）
        simple_pattern = r'^([^/]+)/([^/]+)/(.+)$'
        match = re.match(simple_pattern, url)
        if match:
            return match.group(1), match.group(2), "main", match.group(3)

        return None

    async def _download_file(self, url: str) -> Optional[str]:
        """下载文件内容"""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as response:
                    if response.status == 200:
                        return await response.text()
                    return None
        except Exception:
            # 如果没有 aiohttp，尝试使用 urllib
            try:
                import urllib.request
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None,
                    lambda: urllib.request.urlopen(url, timeout=30).read().decode('utf-8')
                )
            except Exception:
                return None

    async def _download_github_directory(self, raw_base: str, target_dir: Path) -> None:
        """
        尝试下载 GitHub 目录中的文件

        注意：GitHub raw content API 不支持目录列表，
        所以我们尝试下载常见的 skill 文件
        """
        # 尝试下载 scripts/ 目录中的文件
        scripts_dir = target_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)

        # 常见的脚本文件
        common_scripts = ['script.py', 'run.py', 'main.py', 'index.js', 'script.sh']
        for script in common_scripts:
            content = await self._download_file(f"{raw_base}/scripts/{script}")
            if content:
                (scripts_dir / script).write_text(content, encoding='utf-8')

        # 尝试下载 references/ 目录中的 README.md
        refs_dir = target_dir / "references"
        refs_dir.mkdir(exist_ok=True)

        readme_content = await self._download_file(f"{raw_base}/references/README.md")
        if readme_content:
            (refs_dir / "README.md").write_text(readme_content, encoding='utf-8')

    async def _install_from_local(self, source_path: str, skill_name: Optional[str] = None) -> str:
        """从本地路径安装 skill"""
        source_candidate = Path(source_path).expanduser()
        if not source_candidate.exists():
            raise ValueError(f"Source path does not exist: {source_path}")
        source = source_candidate.resolve(strict=True)
        if not source.is_dir():
            raise ValueError(f"Source is not a directory: {source_path}")

        # 验证源目录包含 SKILL.md
        if not (source / "SKILL.md").exists():
            raise ValueError(f"Source is not a valid skill directory (missing SKILL.md): {source_path}")

        # 确定目标名称
        if skill_name is None:
            skill_name = source.name

        name = validate_skill_name(skill_name)

        async def populate(staged_skill: Path) -> None:
            await asyncio.to_thread(self._copy_tree, source, staged_skill)

        return await self._stage_and_publish(name, populate)

    async def _stage_and_publish(
        self,
        skill_name: str,
        populate: Callable[[Path], Awaitable[None]],
    ) -> str:
        name = validate_skill_name(skill_name)
        with self._open_root() as root_fd:
            target = resolve_skill_path(self.skills_dir, name)
            if self._entry_exists(root_fd, name) or target.is_symlink():
                raise ValueError(f"Skill already exists: {name}")
            with tempfile.TemporaryDirectory(prefix="skill-install-") as tmpdir:
                staging_root = Path(tmpdir).resolve(strict=True)
                staged_skill = resolve_skill_path(staging_root, name)
                await populate(staged_skill)
                self._validate_staged_skill(staging_root, name)
                self._verify_root()
                if self._entry_exists(root_fd, name):
                    raise ValueError(f"Skill already exists: {name}")
                os.rename(staged_skill, name, dst_dir_fd=root_fd)
        return name

    @staticmethod
    def _validate_staged_skill(staging_root: Path, skill_name: str) -> None:
        from harness.skills import SkillResolver

        try:
            SkillResolver(staging_root).resolve(skill_name)
        except (OSError, ValueError) as exc:
            raise ValueError(f"Invalid skill source: {exc}") from exc

    def _is_valid_skill_name(self, name: str) -> bool:
        """验证 skill 名称格式"""
        return is_valid_skill_name(name)

    def _copy_tree(self, src: Path, dst: Path) -> None:
        """递归复制目录"""
        shutil.copytree(src, dst, symlinks=True)

    async def uninstall_skill(self, skill_name: str) -> bool:
        """
        卸载 skill

        Args:
            skill_name: Skill 名称

        Returns:
            是否成功卸载
        """
        name = validate_skill_name(skill_name)
        resolve_skill_path(self._verify_root(), name)
        with self._open_root() as root_fd:
            if not self._entry_exists(root_fd, name):
                return False
            info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"Skill path is not a directory: {name}")
            await asyncio.to_thread(shutil.rmtree, name, dir_fd=root_fd)
        return True
