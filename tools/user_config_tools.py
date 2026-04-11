"""
用户偏好设置工具
支持 theme 和 vim mode 等用户偏好配置
"""

from typing import Optional
from pydantic import BaseModel, Field
from pathlib import Path
import json
import os

from tools.base import Tool, ToolResult, register_tool


# 用户配置目录
USER_CONFIG_DIR = Path(os.path.expanduser("~/.claude_code"))
USER_CONFIG_FILE = USER_CONFIG_DIR / "user_config.json"

# 支持的 theme 列表
THEMES = [
    "dark",
    "light",
    "light-daltonized",
    "dark-daltonized",
    "light-ansi",
    "dark-ansi",
]

# 支持的编辑器模式
EDITOR_MODES = ["normal", "vim"]


class UserConfig:
    """用户配置管理器"""

    def __init__(self):
        self.config_file = USER_CONFIG_FILE
        self._config = self._load_config()

    def _load_config(self) -> dict:
        """加载配置"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "theme": "dark",
            "editor_mode": "normal",
        }

    def _save_config(self):
        """保存配置"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Failed to save user config: {e}")

    def get(self, key: str, default=None):
        """获取配置项"""
        return self._config.get(key, default)

    def set(self, key: str, value):
        """设置配置项"""
        self._config[key] = value
        self._save_config()

    def get_all(self) -> dict:
        """获取所有配置"""
        return self._config.copy()


# 全局配置实例
_user_config = UserConfig()


class ThemeGetInput(BaseModel):
    """获取 theme 输入"""
    pass


@register_tool
class ThemeGetTool(Tool[ThemeGetInput, ToolResult]):
    """获取当前 theme 设置"""

    name = "theme_get"
    description = "Get current theme setting"
    input_model = ThemeGetInput

    async def execute(self, input_data: ThemeGetInput) -> ToolResult:
        """执行获取 theme"""
        theme = _user_config.get("theme", "dark")

        theme_descriptions = {
            "dark": "Dark theme (default)",
            "light": "Light theme",
            "light-daltonized": "Light theme with color-blind friendly colors",
            "dark-daltonized": "Dark theme with color-blind friendly colors",
            "light-ansi": "Light theme using ANSI colors only",
            "dark-ansi": "Dark theme using ANSI colors only",
        }

        return ToolResult(
            success=True,
            data={
                "theme": theme,
                "description": theme_descriptions.get(theme, ""),
                "available_themes": THEMES,
            },
            message=f"Current theme: {theme}"
        )


class ThemeSetInput(BaseModel):
    """设置 theme 输入"""
    theme: str = Field(..., description=f"Theme name. One of: {', '.join(THEMES)}")


@register_tool
class ThemeSetTool(Tool[ThemeSetInput, ToolResult]):
    """设置 theme"""

    name = "theme_set"
    description = "Set the theme"
    input_model = ThemeSetInput

    async def execute(self, input_data: ThemeSetInput) -> ToolResult:
        """执行设置 theme"""
        if input_data.theme not in THEMES:
            return ToolResult(
                success=False,
                data=None,
                message=f"Invalid theme: {input_data.theme}",
                error=f"Theme must be one of: {', '.join(THEMES)}"
            )

        _user_config.set("theme", input_data.theme)

        return ToolResult(
            success=True,
            data={"theme": input_data.theme},
            message=f"Theme set to '{input_data.theme}'"
        )


class EditorModeGetInput(BaseModel):
    """获取编辑器模式输入"""
    pass


@register_tool
class EditorModeGetTool(Tool[EditorModeGetInput, ToolResult]):
    """获取当前编辑器模式 (normal/vim)"""

    name = "editor_mode_get"
    description = "Get current editor mode (normal or vim)"
    input_model = EditorModeGetInput

    async def execute(self, input_data: EditorModeGetInput) -> ToolResult:
        """执行获取编辑器模式"""
        mode = _user_config.get("editor_mode", "normal")

        mode_descriptions = {
            "normal": "Standard readline keyboard bindings",
            "vim": "Vim-style keybindings (Escape to toggle INSERT/NORMAL)",
        }

        return ToolResult(
            success=True,
            data={
                "mode": mode,
                "description": mode_descriptions.get(mode, ""),
                "available_modes": EDITOR_MODES,
            },
            message=f"Current editor mode: {mode}"
        )


class EditorModeSetInput(BaseModel):
    """设置编辑器模式输入"""
    mode: str = Field(..., description=f"Editor mode. One of: {', '.join(EDITOR_MODES)}")


@register_tool
class EditorModeSetTool(Tool[EditorModeSetInput, ToolResult]):
    """设置编辑器模式 (normal/vim)"""

    name = "editor_mode_set"
    description = "Set editor mode to normal or vim"
    input_model = EditorModeSetInput

    async def execute(self, input_data: EditorModeSetInput) -> ToolResult:
        """执行设置编辑器模式"""
        if input_data.mode not in EDITOR_MODES:
            return ToolResult(
                success=False,
                data=None,
                message=f"Invalid editor mode: {input_data.mode}",
                error=f"Mode must be one of: {', '.join(EDITOR_MODES)}"
            )

        _user_config.set("editor_mode", input_data.mode)

        hints = {
            "normal": "Using standard (readline) keyboard bindings.",
            "vim": "Use Escape key to toggle between INSERT and NORMAL modes.",
        }

        return ToolResult(
            success=True,
            data={"mode": input_data.mode},
            message=f"Editor mode set to '{input_data.mode}'. {hints.get(input_data.mode, '')}"
        )


class UserConfigGetInput(BaseModel):
    """获取用户配置输入"""
    pass


@register_tool
class UserConfigGetTool(Tool[UserConfigGetInput, ToolResult]):
    """获取所有用户配置"""

    name = "user_config_get"
    description = "Get all user preferences (theme, editor mode, etc.)"
    input_model = UserConfigGetInput

    async def execute(self, input_data: UserConfigGetInput) -> ToolResult:
        """执行获取用户配置"""
        config = _user_config.get_all()

        return ToolResult(
            success=True,
            data=config,
            message=f"User configuration: {len(config)} settings"
        )
