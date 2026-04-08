"""
配置服务模块
管理环境变量和应用配置
"""

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


@dataclass
class AppConfig:
    """应用配置"""
    # API Keys
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    anthropic_base_url: Optional[str] = None

    # 默认模型配置
    default_model: str = "gpt-4o"
    default_max_tokens: int = 4096
    default_temperature: float = 0.7

    # 服务配置
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    @classmethod
    def from_env(cls) -> "AppConfig":
        """从环境变量加载配置"""
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_base_url=os.getenv("OPENAI_BASE_URL"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL"),
            default_model=os.getenv("DEFAULT_MODEL", "gpt-4o"),
            default_max_tokens=int(os.getenv("DEFAULT_MAX_TOKENS", "4096")),
            default_temperature=float(os.getenv("DEFAULT_TEMPERATURE", "0.7")),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            debug=os.getenv("DEBUG", "false").lower() == "true",
        )


class ConfigService:
    """配置服务"""

    _instance: Optional["ConfigService"] = None
    _config: Optional[AppConfig] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._config = AppConfig.from_env()
        return cls._instance

    @property
    def config(self) -> AppConfig:
        """获取配置"""
        if self._config is None:
            self._config = AppConfig.from_env()
        return self._config

    def reload(self):
        """重新加载配置"""
        self._config = AppConfig.from_env()


# 全局配置服务实例
config_service = ConfigService()