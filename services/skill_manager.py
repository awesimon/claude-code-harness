"""
Skill 管理器
支持动态安装、卸载和管理技能
"""

import os
import json
import shutil
import subprocess
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime

from tools.base import Tool, ToolResult, register_tool


# Skill 目录
SKILLS_DIR = Path(os.path.expanduser("~/.claude_code/skills"))
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

SKILL_INDEX_FILE = SKILLS_DIR / "index.json"


@dataclass
class SkillInfo:
    """技能信息"""
    name: str
    version: str
    description: str
    author: str
    source_url: str
    install_path: str
    installed_at: str
    tools: List[str]
    enabled: bool = True


class SkillManager:
    """技能管理器"""

    def __init__(self):
        self.skills_dir = SKILLS_DIR
        self.index_file = SKILL_INDEX_FILE
        self._skills: Dict[str, SkillInfo] = {}
        self._load_index()

    def _load_index(self):
        """加载技能索引"""
        if self.index_file.exists():
            try:
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for name, info in data.items():
                        self._skills[name] = SkillInfo(**info)
            except Exception as e:
                print(f"Failed to load skill index: {e}")

    def _save_index(self):
        """保存技能索引"""
        try:
            data = {name: asdict(info) for name, info in self._skills.items()}
            with open(self.index_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Failed to save skill index: {e}")

    def install_from_git(self, git_url: str, name: Optional[str] = None) -> ToolResult:
        """从 Git 仓库安装技能"""
        try:
            # 从 URL 提取技能名称
            if not name:
                name = git_url.split('/')[-1].replace('.git', '')

            # 检查是否已存在
            if name in self._skills:
                return ToolResult(
                    success=False,
                    message=f"Skill '{name}' already installed",
                    error="Skill already exists"
                )

            # 创建安装目录
            install_path = self.skills_dir / name
            if install_path.exists():
                shutil.rmtree(install_path)

            # 克隆仓库
            result = subprocess.run(
                ["git", "clone", "--depth", "1", git_url, str(install_path)],
                capture_output=True,
                text=True,
                check=True
            )

            # 读取 skill.json
            skill_json = install_path / "skill.json"
            if not skill_json.exists():
                # 清理
                shutil.rmtree(install_path)
                return ToolResult(
                    success=False,
                    message="Invalid skill: skill.json not found",
                    error="Missing skill.json"
                )

            with open(skill_json, 'r', encoding='utf-8') as f:
                manifest = json.load(f)

            # 验证 manifest
            required_fields = ["name", "version", "description"]
            for field in required_fields:
                if field not in manifest:
                    shutil.rmtree(install_path)
                    return ToolResult(
                        success=False,
                        message=f"Invalid skill.json: missing '{field}'",
                        error=f"Missing field: {field}"
                    )

            # 安装依赖
            requirements = install_path / "requirements.txt"
            if requirements.exists():
                subprocess.run(
                    ["pip", "install", "-r", str(requirements), "--quiet"],
                    capture_output=True,
                    check=True
                )

            # 加载并注册工具
            tools = self._load_skill_tools(install_path, manifest)

            # 保存技能信息
            skill_info = SkillInfo(
                name=manifest["name"],
                version=manifest.get("version", "0.1.0"),
                description=manifest.get("description", ""),
                author=manifest.get("author", "Unknown"),
                source_url=git_url,
                install_path=str(install_path),
                installed_at=datetime.now().isoformat(),
                tools=tools
            )

            self._skills[name] = skill_info
            self._save_index()

            return ToolResult(
                success=True,
                data={
                    "name": skill_info.name,
                    "version": skill_info.version,
                    "tools": tools
                },
                message=f"Skill '{name}' installed successfully with {len(tools)} tools"
            )

        except subprocess.CalledProcessError as e:
            return ToolResult(
                success=False,
                message="Failed to clone repository",
                error=str(e)
            )
        except Exception as e:
            return ToolResult(
                success=False,
                message="Installation failed",
                error=str(e)
            )

    def install_from_local(self, local_path: str, name: Optional[str] = None) -> ToolResult:
        """从本地路径安装技能"""
        try:
            source_path = Path(local_path)
            if not source_path.exists():
                return ToolResult(
                    success=False,
                    message=f"Path not found: {local_path}",
                    error="Path does not exist"
                )

            # 从路径提取名称
            if not name:
                name = source_path.name

            # 检查是否已存在
            if name in self._skills:
                return ToolResult(
                    success=False,
                    message=f"Skill '{name}' already installed",
                    error="Skill already exists"
                )

            # 读取 skill.json
            skill_json = source_path / "skill.json"
            if not skill_json.exists():
                return ToolResult(
                    success=False,
                    message="Invalid skill: skill.json not found",
                    error="Missing skill.json"
                )

            with open(skill_json, 'r', encoding='utf-8') as f:
                manifest = json.load(f)

            # 复制到技能目录
            install_path = self.skills_dir / name
            if install_path.exists():
                shutil.rmtree(install_path)

            shutil.copytree(source_path, install_path)

            # 加载并注册工具
            tools = self._load_skill_tools(install_path, manifest)

            # 保存技能信息
            skill_info = SkillInfo(
                name=manifest.get("name", name),
                version=manifest.get("version", "0.1.0"),
                description=manifest.get("description", ""),
                author=manifest.get("author", "Unknown"),
                source_url=str(source_path),
                install_path=str(install_path),
                installed_at=datetime.now().isoformat(),
                tools=tools
            )

            self._skills[name] = skill_info
            self._save_index()

            return ToolResult(
                success=True,
                data={
                    "name": skill_info.name,
                    "version": skill_info.version,
                    "tools": tools
                },
                message=f"Skill '{name}' installed successfully with {len(tools)} tools"
            )

        except Exception as e:
            return ToolResult(
                success=False,
                message="Installation failed",
                error=str(e)
            )

    def _load_skill_tools(self, install_path: Path, manifest: dict) -> List[str]:
        """加载技能中的工具并注册"""
        tools = []

        # 获取入口文件
        entry_point = manifest.get("entry_point", "skill.py")
        entry_file = install_path / entry_point

        if not entry_file.exists():
            return tools

        try:
            # 动态加载模块
            spec = importlib.util.spec_from_file_location(
                f"skill_{manifest['name']}",
                entry_file
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # 查找并注册工具
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and
                    issubclass(attr, Tool) and
                    attr is not Tool and
                    hasattr(attr, 'name')):
                    # 工具类，创建实例并注册
                    try:
                        instance = attr()
                        from tools.base import ToolRegistry
                        ToolRegistry.register(instance)
                        tools.append(instance.name)
                    except Exception as e:
                        print(f"Failed to register tool {attr_name}: {e}")

        except Exception as e:
            print(f"Failed to load skill from {entry_file}: {e}")

        return tools

    def uninstall(self, name: str) -> ToolResult:
        """卸载技能"""
        try:
            if name not in self._skills:
                return ToolResult(
                    success=False,
                    message=f"Skill '{name}' not found",
                    error="Skill not installed"
                )

            skill_info = self._skills[name]

            # 删除安装目录
            install_path = Path(skill_info.install_path)
            if install_path.exists():
                shutil.rmtree(install_path)

            # 从索引中移除
            del self._skills[name]
            self._save_index()

            return ToolResult(
                success=True,
                data={"name": name},
                message=f"Skill '{name}' uninstalled successfully"
            )

        except Exception as e:
            return ToolResult(
                success=False,
                message="Uninstall failed",
                error=str(e)
            )

    def list_skills(self) -> ToolResult:
        """列出所有已安装的技能"""
        try:
            skills = []
            for name, info in self._skills.items():
                skills.append({
                    "name": info.name,
                    "version": info.version,
                    "description": info.description,
                    "author": info.author,
                    "tools": info.tools,
                    "enabled": info.enabled,
                    "installed_at": info.installed_at
                })

            return ToolResult(
                success=True,
                data={"skills": skills, "count": len(skills)},
                message=f"{len(skills)} skill(s) installed"
            )

        except Exception as e:
            return ToolResult(
                success=False,
                message="Failed to list skills",
                error=str(e)
            )

    def get_skill(self, name: str) -> Optional[SkillInfo]:
        """获取技能信息"""
        return self._skills.get(name)

    def enable_skill(self, name: str) -> ToolResult:
        """启用技能"""
        try:
            if name not in self._skills:
                return ToolResult(
                    success=False,
                    message=f"Skill '{name}' not found",
                    error="Skill not installed"
                )

            self._skills[name].enabled = True
            self._save_index()

            return ToolResult(
                success=True,
                message=f"Skill '{name}' enabled"
            )

        except Exception as e:
            return ToolResult(
                success=False,
                message="Failed to enable skill",
                error=str(e)
            )

    def disable_skill(self, name: str) -> ToolResult:
        """禁用技能"""
        try:
            if name not in self._skills:
                return ToolResult(
                    success=False,
                    message=f"Skill '{name}' not found",
                    error="Skill not installed"
                )

            self._skills[name].enabled = False
            self._save_index()

            return ToolResult(
                success=True,
                message=f"Skill '{name}' disabled"
            )

        except Exception as e:
            return ToolResult(
                success=False,
                message="Failed to disable skill",
                error=str(e)
            )

    def load_all_skills(self):
        """启动时加载所有已启用的技能"""
        for name, info in self._skills.items():
            if info.enabled:
                try:
                    install_path = Path(info.install_path)
                    skill_json = install_path / "skill.json"
                    if skill_json.exists():
                        with open(skill_json, 'r', encoding='utf-8') as f:
                            manifest = json.load(f)
                        self._load_skill_tools(install_path, manifest)
                except Exception as e:
                    print(f"Failed to load skill {name}: {e}")


# 全局技能管理器实例
skill_manager = SkillManager()
