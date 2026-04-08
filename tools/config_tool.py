"""
配置管理工具模块
提供配置读取、写入和管理功能
支持settings.json管理和环境变量配置
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List, Union
import json
import os
import asyncio
from datetime import datetime

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool


@dataclass
class ConfigGetInput:
    """获取配置的输入参数"""
    key: str  # 配置键，支持点号分隔的路径如 "server.host"
    scope: str = "settings"  # 配置范围: settings, env, all


@dataclass
class ConfigSetInput:
    """设置配置的输入参数"""
    key: str  # 配置键
    value: Any  # 配置值
    scope: str = "settings"  # 配置范围: settings, env
    persist: bool = True  # 是否持久化到文件


@dataclass
class ConfigDeleteInput:
    """删除配置的输入参数"""
    key: str  # 配置键
    scope: str = "settings"  # 配置范围: settings, env


@dataclass
class ConfigListInput:
    """列出配置的输入参数"""
    scope: str = "all"  # 配置范围: settings, env, all
    prefix: Optional[str] = None  # 键前缀过滤


@dataclass
class ConfigItem:
    """配置项信息"""
    key: str
    value: Any
    scope: str  # settings 或 env
    source: str  # 配置来源描述
    modified_at: Optional[str] = None


@dataclass
class ConfigGetOutput:
    """获取配置的输出结果"""
    key: str
    value: Any
    scope: str
    found: bool


@dataclass
class ConfigSetOutput:
    """设置配置的输出结果"""
    success: bool
    key: str
    value: Any
    scope: str
    persisted: bool


@dataclass
class ConfigDeleteOutput:
    """删除配置的输出结果"""
    success: bool
    key: str
    scope: str
    existed: bool


@dataclass
class ConfigListOutput:
    """列出配置的输出结果"""
    items: List[ConfigItem]
    total: int
    scope: str


class ConfigManager:
    """配置管理器 - 管理settings.json和环境变量"""

    def __init__(self):
        self.claude_dir = Path.home() / ".claude"
        self.config_file = self.claude_dir / "settings.json"
        self._settings_cache: Optional[Dict[str, Any]] = None
        self._cache_timestamp: Optional[float] = None

    def ensure_config_dir(self) -> None:
        """确保配置目录存在"""
        self.claude_dir.mkdir(parents=True, exist_ok=True)

    async def load_settings(self, use_cache: bool = True) -> Dict[str, Any]:
        """
        加载settings.json配置

        Args:
            use_cache: 是否使用缓存

        Returns:
            配置字典
        """
        # 检查缓存有效性
        if use_cache and self._settings_cache is not None:
            if self.config_file.exists():
                mtime = self.config_file.stat().st_mtime
                if self._cache_timestamp == mtime:
                    return self._settings_cache.copy()

        # 从文件加载
        if not self.config_file.exists():
            return {}

        try:
            content = await asyncio.to_thread(
                self.config_file.read_text,
                encoding='utf-8'
            )
            settings = json.loads(content)

            # 更新缓存
            self._settings_cache = settings.copy()
            if self.config_file.exists():
                self._cache_timestamp = self.config_file.stat().st_mtime

            return settings
        except json.JSONDecodeError as e:
            raise ToolExecutionError(f"settings.json格式错误: {str(e)}")
        except Exception as e:
            raise ToolExecutionError(f"读取settings.json失败: {str(e)}")

    async def save_settings(self, settings: Dict[str, Any]) -> None:
        """
        保存配置到settings.json

        Args:
            settings: 配置字典
        """
        self.ensure_config_dir()

        try:
            content = json.dumps(settings, indent=2, ensure_ascii=False, default=str)
            await asyncio.to_thread(
                self.config_file.write_text,
                content,
                encoding='utf-8'
            )

            # 更新缓存
            self._settings_cache = settings.copy()
            self._cache_timestamp = self.config_file.stat().st_mtime

        except Exception as e:
            raise ToolExecutionError(f"保存settings.json失败: {str(e)}")

    def _get_nested_value(self, data: Dict[str, Any], key: str) -> tuple[bool, Any]:
        """
        获取嵌套字典中的值

        Returns:
            (是否找到, 值)
        """
        keys = key.split('.')
        current = data

        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return False, None

        return True, current

    def _set_nested_value(self, data: Dict[str, Any], key: str, value: Any) -> None:
        """设置嵌套字典中的值"""
        keys = key.split('.')
        current = data

        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]

        current[keys[-1]] = value

    def _delete_nested_value(self, data: Dict[str, Any], key: str) -> bool:
        """
        删除嵌套字典中的值

        Returns:
            是否成功删除
        """
        keys = key.split('.')
        current = data

        for k in keys[:-1]:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return False

        if isinstance(current, dict) and keys[-1] in current:
            del current[keys[-1]]
            return True
        return False

    async def get_config(
        self,
        key: str,
        scope: str = "settings"
    ) -> Optional[ConfigItem]:
        """
        获取配置项

        Args:
            key: 配置键
            scope: 配置范围

        Returns:
            配置项或None
        """
        # 环境变量优先
        if scope in ("env", "all"):
            env_key = key.upper().replace('.', '_')
            env_value = os.environ.get(env_key)
            if env_value is not None:
                return ConfigItem(
                    key=key,
                    value=env_value,
                    scope="env",
                    source=f"环境变量 {env_key}"
                )

            # 检查带有CLAUDE_前缀的环境变量
            prefixed_key = f"CLAUDE_{env_key}"
            env_value = os.environ.get(prefixed_key)
            if env_value is not None:
                return ConfigItem(
                    key=key,
                    value=env_value,
                    scope="env",
                    source=f"环境变量 {prefixed_key}"
                )

        # 从settings.json读取
        if scope in ("settings", "all"):
            settings = await self.load_settings()
            found, value = self._get_nested_value(settings, key)
            if found:
                return ConfigItem(
                    key=key,
                    value=value,
                    scope="settings",
                    source=f"settings.json"
                )

        return None

    async def set_config(
        self,
        key: str,
        value: Any,
        scope: str = "settings",
        persist: bool = True
    ) -> bool:
        """
        设置配置项

        Args:
            key: 配置键
            value: 配置值
            scope: 配置范围
            persist: 是否持久化

        Returns:
            是否成功
        """
        if scope == "env":
            # 设置环境变量（仅当前进程）
            env_key = key.upper().replace('.', '_')
            os.environ[env_key] = str(value)
            return True

        elif scope == "settings":
            settings = await self.load_settings()
            self._set_nested_value(settings, key, value)

            if persist:
                await self.save_settings(settings)

            return True

        return False

    async def delete_config(self, key: str, scope: str = "settings") -> bool:
        """
        删除配置项

        Args:
            key: 配置键
            scope: 配置范围

        Returns:
            是否成功删除
        """
        if scope == "env":
            env_key = key.upper().replace('.', '_')
            if env_key in os.environ:
                del os.environ[env_key]
                return True
            return False

        elif scope == "settings":
            settings = await self.load_settings()
            existed = self._delete_nested_value(settings, key)

            if existed:
                await self.save_settings(settings)

            return existed

        return False

    async def list_configs(
        self,
        scope: str = "all",
        prefix: Optional[str] = None
    ) -> List[ConfigItem]:
        """
        列出配置项

        Args:
            scope: 配置范围
            prefix: 键前缀过滤

        Returns:
            配置项列表
        """
        items = []

        # 从settings.json读取
        if scope in ("settings", "all"):
            settings = await self.load_settings()
            settings_items = self._flatten_dict(settings, prefix=prefix)
            for key, value in settings_items:
                items.append(ConfigItem(
                    key=key,
                    value=value,
                    scope="settings",
                    source="settings.json"
                ))

        # 从环境变量读取
        if scope in ("env", "all"):
            for env_key, value in os.environ.items():
                # 转换环境变量名为配置键格式
                if env_key.startswith("CLAUDE_"):
                    key = env_key[7:].lower().replace('_', '.')
                else:
                    key = env_key.lower().replace('_', '.')

                if prefix is None or key.startswith(prefix):
                    items.append(ConfigItem(
                        key=key,
                        value=value,
                        scope="env",
                        source=f"环境变量 {env_key}"
                    ))

        return items

    def _flatten_dict(
        self,
        data: Dict[str, Any],
        parent_key: str = "",
        prefix: Optional[str] = None
    ) -> List[tuple[str, Any]]:
        """
        扁平化嵌套字典

        Returns:
            [(键, 值), ...]
        """
        items = []

        for key, value in data.items():
            full_key = f"{parent_key}.{key}" if parent_key else key

            if prefix is not None and not full_key.startswith(prefix):
                continue

            if isinstance(value, dict):
                items.extend(self._flatten_dict(value, full_key, prefix))
            else:
                items.append((full_key, value))

        return items


# 全局配置管理器实例
_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """获取全局配置管理器实例"""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def set_config_manager(manager: ConfigManager) -> None:
    """设置全局配置管理器实例"""
    global _config_manager
    _config_manager = manager


@register_tool
class ConfigGetTool(Tool[ConfigGetInput, ConfigGetOutput]):
    """
    获取配置工具

    从settings.json或环境变量中读取配置值。
    支持点号分隔的键路径（如 "server.host"）。

    配置优先级：
    1. 环境变量（CLaude_前缀或大写键名）
    2. settings.json中的配置

    使用场景：
    - 读取API密钥等敏感配置
    - 获取服务端点地址
    - 查询模型默认参数
    """

    name = "config_get"
    description = "获取配置值，支持settings.json和环境变量"
    version = "1.0"

    async def validate(self, input_data: ConfigGetInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.key or not input_data.key.strip():
            return ToolValidationError("key 不能为空")

        valid_scopes = {"settings", "env", "all"}
        if input_data.scope not in valid_scopes:
            return ToolValidationError(
                f"无效的scope: {input_data.scope}，可选值: {', '.join(valid_scopes)}"
            )

        return None

    async def execute(self, input_data: ConfigGetInput) -> ToolResult:
        """执行获取配置"""
        key = input_data.key.strip()
        scope = input_data.scope

        try:
            manager = get_config_manager()
            item = await manager.get_config(key, scope)

            if item:
                return ToolResult.ok(
                    data=ConfigGetOutput(
                        key=key,
                        value=item.value,
                        scope=item.scope,
                        found=True
                    ),
                    message=f"配置项 '{key}' 的值: {item.value}",
                    metadata={
                        "key": key,
                        "scope": item.scope,
                        "source": item.source
                    }
                )
            else:
                return ToolResult.ok(
                    data=ConfigGetOutput(
                        key=key,
                        value=None,
                        scope=scope,
                        found=False
                    ),
                    message=f"配置项 '{key}' 不存在",
                    metadata={"key": key, "found": False}
                )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"获取配置失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        """是否为只读工具"""
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "配置键，支持点号分隔的路径如 'server.host'"
                },
                "scope": {
                    "type": "string",
                    "enum": ["settings", "env", "all"],
                    "description": "配置范围",
                    "default": "settings"
                }
            },
            "required": ["key"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "配置键"},
                "value": {"description": "配置值"},
                "scope": {"type": "string", "description": "配置来源范围"},
                "found": {"type": "boolean", "description": "是否找到配置"}
            }
        }
        return schema


@register_tool
class ConfigSetTool(Tool[ConfigSetInput, ConfigSetOutput]):
    """
    设置配置工具

    将配置值写入settings.json或环境变量。
    支持点号分隔的键路径创建嵌套配置。

    使用场景：
    - 更新API密钥
    - 修改服务端点设置
    - 设置模型默认参数

    注意：
    - settings.json保存在 ~/.claude/settings.json
    - 环境变量设置仅影响当前进程
    """

    name = "config_set"
    description = "设置配置值，支持settings.json和环境变量"
    version = "1.0"

    async def validate(self, input_data: ConfigSetInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.key or not input_data.key.strip():
            return ToolValidationError("key 不能为空")

        valid_scopes = {"settings", "env"}
        if input_data.scope not in valid_scopes:
            return ToolValidationError(
                f"无效的scope: {input_data.scope}，可选值: {', '.join(valid_scopes)}"
            )

        return None

    async def execute(self, input_data: ConfigSetInput) -> ToolResult:
        """执行设置配置"""
        key = input_data.key.strip()
        value = input_data.value
        scope = input_data.scope
        persist = input_data.persist

        try:
            manager = get_config_manager()
            success = await manager.set_config(key, value, scope, persist)

            if success:
                return ToolResult.ok(
                    data=ConfigSetOutput(
                        success=True,
                        key=key,
                        value=value,
                        scope=scope,
                        persisted=persist if scope == "settings" else False
                    ),
                    message=f"配置项 '{key}' 设置成功",
                    metadata={
                        "key": key,
                        "scope": scope,
                        "persisted": persist if scope == "settings" else False
                    }
                )
            else:
                return ToolResult.error(
                    ToolExecutionError(f"配置项 '{key}' 设置失败")
                )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"设置配置失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        """是否为只读工具"""
        return False

    def is_destructive(self) -> bool:
        """是否为破坏性工具"""
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "配置键，支持点号分隔的路径"
                },
                "value": {
                    "description": "配置值（任意类型）"
                },
                "scope": {
                    "type": "string",
                    "enum": ["settings", "env"],
                    "description": "配置范围",
                    "default": "settings"
                },
                "persist": {
                    "type": "boolean",
                    "description": "是否持久化到settings.json",
                    "default": True
                }
            },
            "required": ["key", "value"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "key": {"type": "string"},
                "value": {"description": "配置值"},
                "scope": {"type": "string"},
                "persisted": {"type": "boolean"}
            }
        }
        return schema


@register_tool
class ConfigDeleteTool(Tool[ConfigDeleteInput, ConfigDeleteOutput]):
    """
    删除配置工具

    从settings.json或环境变量中删除配置项。

    使用场景：
    - 清理过期配置
    - 重置为默认值的准备步骤
    """

    name = "config_delete"
    description = "删除配置项，支持settings.json和环境变量"
    version = "1.0"

    async def validate(self, input_data: ConfigDeleteInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.key or not input_data.key.strip():
            return ToolValidationError("key 不能为空")

        valid_scopes = {"settings", "env"}
        if input_data.scope not in valid_scopes:
            return ToolValidationError(
                f"无效的scope: {input_data.scope}，可选值: {', '.join(valid_scopes)}"
            )

        return None

    async def execute(self, input_data: ConfigDeleteInput) -> ToolResult:
        """执行删除配置"""
        key = input_data.key.strip()
        scope = input_data.scope

        try:
            manager = get_config_manager()
            existed = await manager.delete_config(key, scope)

            if existed:
                return ToolResult.ok(
                    data=ConfigDeleteOutput(
                        success=True,
                        key=key,
                        scope=scope,
                        existed=True
                    ),
                    message=f"配置项 '{key}' 已删除",
                    metadata={"key": key, "scope": scope}
                )
            else:
                return ToolResult.ok(
                    data=ConfigDeleteOutput(
                        success=True,
                        key=key,
                        scope=scope,
                        existed=False
                    ),
                    message=f"配置项 '{key}' 不存在，无需删除",
                    metadata={"key": key, "scope": scope, "existed": False}
                )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"删除配置失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        """是否为只读工具"""
        return False

    def is_destructive(self) -> bool:
        """是否为破坏性工具"""
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "要删除的配置键"
                },
                "scope": {
                    "type": "string",
                    "enum": ["settings", "env"],
                    "description": "配置范围",
                    "default": "settings"
                }
            },
            "required": ["key"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "key": {"type": "string"},
                "scope": {"type": "string"},
                "existed": {"type": "boolean"}
            }
        }
        return schema


@register_tool
class ConfigListTool(Tool[ConfigListInput, ConfigListOutput]):
    """
    列出配置工具

    列出所有配置项，支持按范围过滤和键前缀过滤。

    使用场景：
    - 查看当前所有配置
    - 查找特定前缀的配置
    - 配置备份和迁移
    """

    name = "config_list"
    description = "列出所有配置项，支持按范围过滤和键前缀过滤"
    version = "1.0"

    async def validate(self, input_data: ConfigListInput) -> Optional[ToolError]:
        """验证输入参数"""
        valid_scopes = {"settings", "env", "all"}
        if input_data.scope not in valid_scopes:
            return ToolValidationError(
                f"无效的scope: {input_data.scope}，可选值: {', '.join(valid_scopes)}"
            )

        return None

    async def execute(self, input_data: ConfigListInput) -> ToolResult:
        """执行列出配置"""
        scope = input_data.scope
        prefix = input_data.prefix

        try:
            manager = get_config_manager()
            items = await manager.list_configs(scope, prefix)

            # 转换为字典列表
            item_dicts = [
                {
                    "key": item.key,
                    "value": item.value,
                    "scope": item.scope,
                    "source": item.source
                }
                for item in items
            ]

            return ToolResult.ok(
                data=ConfigListOutput(
                    items=items,
                    total=len(items),
                    scope=scope
                ),
                message=f"共找到 {len(items)} 个配置项",
                metadata={
                    "total": len(items),
                    "scope": scope,
                    "prefix": prefix,
                    "items": item_dicts
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"列出配置失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        """是否为只读工具"""
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["settings", "env", "all"],
                    "description": "配置范围",
                    "default": "all"
                },
                "prefix": {
                    "type": "string",
                    "description": "键前缀过滤（可选）"
                }
            }
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "value": {},
                            "scope": {"type": "string"},
                            "source": {"type": "string"}
                        }
                    }
                },
                "total": {"type": "integer"},
                "scope": {"type": "string"}
            }
        }
        return schema
