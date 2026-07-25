"""
统一的配置路径管理模块

所有配置都存储在当前工作目录下的 .claude 目录中，
不再依赖用户主目录的 ~/.claude 或 ~/.claude_code
"""

import os
from pathlib import Path
from typing import Optional


# 配置目录名称
CONFIG_DIR_NAME = ".claude"


def get_project_root() -> Path:
    """
    获取项目根目录

    优先使用环境变量 CLAUDE_PROJECT_ROOT，
    否则使用当前工作目录
    """
    env_root = os.environ.get("CLAUDE_PROJECT_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path.cwd().resolve()


def get_config_dir() -> Path:
    """
    获取配置目录路径

    返回: {项目根目录}/.claude
    """
    config_dir = get_project_root() / CONFIG_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_subdir(subdir: str) -> Path:
    """
    获取配置子目录

    Args:
        subdir: 子目录名称

    Returns:
        Path: 子目录路径
    """
    path = get_config_dir() / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_config_file(filename: str) -> Path:
    """
    获取配置文件路径

    Args:
        filename: 配置文件名

    Returns:
        Path: 配置文件路径
    """
    return get_config_dir() / filename


# 预定义的配置路径
class ConfigPaths:
    """常用配置路径"""

    @staticmethod
    def skills_dir() -> Path:
        """技能目录: .claude/skills/"""
        return get_config_subdir("skills")

    @staticmethod
    def teams_dir() -> Path:
        """团队目录: .claude/teams/"""
        return get_config_subdir("teams")

    @staticmethod
    def tasks_dir() -> Path:
        """任务目录: .claude/tasks/"""
        return get_config_subdir("tasks")

    @staticmethod
    def plans_dir() -> Path:
        """计划目录: .claude/plans/"""
        return get_config_subdir("plans")

    @staticmethod
    def sessions_dir() -> Path:
        """会话目录: .claude/sessions/"""
        return get_config_subdir("sessions")

    @staticmethod
    def hooks_dir() -> Path:
        """Hooks 目录: .claude/hooks/"""
        return get_config_subdir("hooks")

    @staticmethod
    def user_config_dir() -> Path:
        """用户配置目录: .claude/user/"""
        return get_config_subdir("user")

    @staticmethod
    def settings_file() -> Path:
        """设置文件: .claude/settings.json"""
        return get_config_file("settings.json")

    @staticmethod
    def user_config_file() -> Path:
        """用户配置文件: .claude/user/config.json"""
        return get_config_subdir("user") / "config.json"

    @staticmethod
    def hooks_config_file() -> Path:
        """Hooks 配置文件: .claude/hooks/config.json"""
        return get_config_subdir("hooks") / "config.json"

    @staticmethod
    def models_config_file() -> Path:
        """模型配置文件: .claude/models.json"""
        return get_config_file("models.json")


# 向后兼容的别名
get_claude_dir = get_config_dir
