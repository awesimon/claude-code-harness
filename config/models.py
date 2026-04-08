"""
模型配置模块
定义所有内置模型及其配置
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum
import json
import os


class ModelProvider(Enum):
    """模型提供商"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    GLM = "glm"
    MINIMAX = "minimax"
    KIMI = "kimi"


@dataclass
class ModelConfig:
    """模型配置"""
    model_id: str
    name: str
    provider: ModelProvider
    max_tokens: int = 4096
    temperature: float = 1.0
    supports_streaming: bool = True
    supports_tools: bool = True
    description: str = ""
    icon: str = ""
    enabled: bool = True
    api_base: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "model_id": self.model_id,
            "name": self.name,
            "provider": self.provider.value,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "supports_streaming": self.supports_streaming,
            "supports_tools": self.supports_tools,
            "description": self.description,
            "icon": self.icon,
            "enabled": self.enabled,
            "api_base": self.api_base,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelConfig":
        """从字典创建"""
        data = data.copy()
        if "provider" in data and isinstance(data["provider"], str):
            data["provider"] = ModelProvider(data["provider"])
        return cls(**data)


# 内置模型配置
BUILTIN_MODELS: Dict[str, ModelConfig] = {
    # DeepSeek 模型
    "deepseek-v3.2": ModelConfig(
        model_id="deepseek-v3.2",
        name="DeepSeek V3.2",
        provider=ModelProvider.DEEPSEEK,
        max_tokens=8192,
        temperature=1.0,
        supports_streaming=True,
        supports_tools=True,
        description="DeepSeek V3.2 - 强大的代码生成和推理能力",
        icon="deepseek",
    ),
    "deepseek-v3.2-thinking": ModelConfig(
        model_id="deepseek-v3.2-thinking",
        name="DeepSeek V3.2 Thinking",
        provider=ModelProvider.DEEPSEEK,
        max_tokens=8192,
        temperature=1.0,
        supports_streaming=True,
        supports_tools=True,
        description="DeepSeek V3.2 Thinking - 增强推理模式",
        icon="deepseek",
    ),

    # GLM 模型
    "glm-4.7": ModelConfig(
        model_id="glm-4.7",
        name="GLM 4.7",
        provider=ModelProvider.GLM,
        max_tokens=4096,
        temperature=1.0,
        supports_streaming=True,
        supports_tools=True,
        description="GLM 4.7 - 智谱AI最新模型",
        icon="glm",
    ),
    "glm-5": ModelConfig(
        model_id="glm-5",
        name="GLM 5",
        provider=ModelProvider.GLM,
        max_tokens=8192,
        temperature=1.0,
        supports_streaming=True,
        supports_tools=True,
        description="GLM 5 - 智谱AI旗舰模型",
        icon="glm",
    ),

    # MiniMax 模型
    "minimax-m2.1": ModelConfig(
        model_id="minimax-m2.1",
        name="MiniMax M2.1",
        provider=ModelProvider.MINIMAX,
        max_tokens=4096,
        temperature=1.0,
        supports_streaming=True,
        supports_tools=True,
        description="MiniMax M2.1 - 高效的多模态模型",
        icon="minimax",
    ),
    "minimax-m2.5": ModelConfig(
        model_id="minimax-m2.5",
        name="MiniMax M2.5",
        provider=ModelProvider.MINIMAX,
        max_tokens=4096,
        temperature=1.0,
        supports_streaming=True,
        supports_tools=True,
        description="MiniMax M2.5 - 增强版多模态模型",
        icon="minimax",
    ),
    "minimax-m2.7": ModelConfig(
        model_id="minimax-m2.7",
        name="MiniMax M2.7",
        provider=ModelProvider.MINIMAX,
        max_tokens=8192,
        temperature=1.0,
        supports_streaming=True,
        supports_tools=True,
        description="MiniMax M2.7 - 最新旗舰模型",
        icon="minimax",
    ),

    # Kimi 模型
    "kimi-k2.5": ModelConfig(
        model_id="kimi-k2.5",
        name="Kimi K2.5",
        provider=ModelProvider.KIMI,
        max_tokens=4096,
        temperature=1.0,
        supports_streaming=True,
        supports_tools=True,
        description="Kimi K2.5 - Moonshot AI模型",
        icon="kimi",
    ),

    # OpenAI 模型
    "gpt-4o": ModelConfig(
        model_id="gpt-4o",
        name="GPT-4o",
        provider=ModelProvider.OPENAI,
        max_tokens=4096,
        temperature=1.0,
        supports_streaming=True,
        supports_tools=True,
        description="GPT-4o - OpenAI多模态旗舰模型",
        icon="openai",
    ),
    "gpt-4o-mini": ModelConfig(
        model_id="gpt-4o-mini",
        name="GPT-4o Mini",
        provider=ModelProvider.OPENAI,
        max_tokens=4096,
        temperature=1.0,
        supports_streaming=True,
        supports_tools=True,
        description="GPT-4o Mini - 轻量级高效模型",
        icon="openai",
    ),

    # Anthropic 模型
    "claude-3-5-sonnet": ModelConfig(
        model_id="claude-3-5-sonnet",
        name="Claude 3.5 Sonnet",
        provider=ModelProvider.ANTHROPIC,
        max_tokens=4096,
        temperature=1.0,
        supports_streaming=True,
        supports_tools=True,
        description="Claude 3.5 Sonnet - 平衡性能与效率",
        icon="anthropic",
    ),
    "claude-3-opus": ModelConfig(
        model_id="claude-3-opus",
        name="Claude 3 Opus",
        provider=ModelProvider.ANTHROPIC,
        max_tokens=4096,
        temperature=1.0,
        supports_streaming=True,
        supports_tools=True,
        description="Claude 3 Opus - 最强大的Claude模型",
        icon="anthropic",
    ),
}


class ModelManager:
    """模型管理器"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._models: Dict[str, ModelConfig] = {}
        self._default_model_id: str = "gpt-4o"
        self._config_file = os.path.expanduser("~/.claude_python_api/models.json")
        self._load_config()

    def _load_config(self):
        """加载配置"""
        # 首先加载内置模型
        self._models = BUILTIN_MODELS.copy()

        # 尝试从配置文件加载自定义配置
        if os.path.exists(self._config_file):
            try:
                with open(self._config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)

                # 加载默认模型设置
                if "default_model" in config:
                    self._default_model_id = config["default_model"]

                # 加载自定义模型配置（覆盖内置配置）
                if "models" in config:
                    for model_id, model_data in config["models"].items():
                        if model_id in self._models:
                            # 更新现有配置
                            existing = self._models[model_id]
                            for key, value in model_data.items():
                                if hasattr(existing, key):
                                    setattr(existing, key, value)
                        else:
                            # 添加新模型
                            self._models[model_id] = ModelConfig.from_dict(model_data)

            except Exception as e:
                print(f"Warning: Failed to load model config: {e}")

    def _save_config(self):
        """保存配置"""
        try:
            os.makedirs(os.path.dirname(self._config_file), exist_ok=True)
            config = {
                "default_model": self._default_model_id,
                "models": {
                    model_id: model.to_dict()
                    for model_id, model in self._models.items()
                }
            }
            with open(self._config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Warning: Failed to save model config: {e}")

    def get_all_models(self) -> List[ModelConfig]:
        """获取所有模型"""
        return list(self._models.values())

    def get_enabled_models(self) -> List[ModelConfig]:
        """获取启用的模型"""
        return [m for m in self._models.values() if m.enabled]

    def get_model(self, model_id: str) -> Optional[ModelConfig]:
        """获取特定模型"""
        return self._models.get(model_id)

    def get_default_model(self) -> ModelConfig:
        """获取默认模型"""
        if self._default_model_id in self._models:
            model = self._models[self._default_model_id]
            if model.enabled:
                return model
        # 返回第一个启用的模型
        enabled = self.get_enabled_models()
        if enabled:
            return enabled[0]
        # 返回第一个内置模型
        return list(BUILTIN_MODELS.values())[0]

    def set_default_model(self, model_id: str) -> bool:
        """设置默认模型"""
        if model_id not in self._models:
            return False
        self._default_model_id = model_id
        self._save_config()
        return True

    def add_custom_model(self, config: ModelConfig) -> bool:
        """添加自定义模型"""
        self._models[config.model_id] = config
        self._save_config()
        return True

    def update_model(self, model_id: str, **kwargs) -> bool:
        """更新模型配置"""
        if model_id not in self._models:
            return False
        model = self._models[model_id]
        for key, value in kwargs.items():
            if hasattr(model, key):
                setattr(model, key, value)
        self._save_config()
        return True

    def enable_model(self, model_id: str, enabled: bool = True) -> bool:
        """启用/禁用模型"""
        return self.update_model(model_id, enabled=enabled)

    def remove_custom_model(self, model_id: str) -> bool:
        """移除自定义模型（不能移除内置模型）"""
        if model_id in BUILTIN_MODELS:
            return False
        if model_id in self._models:
            del self._models[model_id]
            self._save_config()
            return True
        return False

    def get_models_by_provider(self, provider: ModelProvider) -> List[ModelConfig]:
        """按提供商获取模型"""
        return [m for m in self._models.values() if m.provider == provider and m.enabled]


# 全局模型管理器实例
model_manager = ModelManager()
