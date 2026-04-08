"""
模型管理路由
提供模型相关的API端点
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.models import model_manager, ModelConfig, ModelProvider

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelResponse(BaseModel):
    """模型响应"""
    model_id: str
    name: str
    provider: str
    max_tokens: int
    temperature: float
    supports_streaming: bool
    supports_tools: bool
    description: str
    icon: str
    enabled: bool


class ModelsListResponse(BaseModel):
    """模型列表响应"""
    models: List[ModelResponse]
    default_model: str
    count: int


class DefaultModelResponse(BaseModel):
    """默认模型响应"""
    model_id: str
    model: ModelResponse


class SelectModelRequest(BaseModel):
    """选择模型请求"""
    model_id: str


class SelectModelResponse(BaseModel):
    """选择模型响应"""
    success: bool
    message: str
    model_id: Optional[str] = None


def model_to_response(model: ModelConfig) -> ModelResponse:
    """将模型配置转换为响应"""
    return ModelResponse(
        model_id=model.model_id,
        name=model.name,
        provider=model.provider.value,
        max_tokens=model.max_tokens,
        temperature=model.temperature,
        supports_streaming=model.supports_streaming,
        supports_tools=model.supports_tools,
        description=model.description,
        icon=model.icon,
        enabled=model.enabled,
    )


@router.get("", response_model=ModelsListResponse)
async def get_all_models():
    """
    获取所有可用模型列表
    """
    models = model_manager.get_enabled_models()
    return ModelsListResponse(
        models=[model_to_response(m) for m in models],
        default_model=model_manager.get_default_model().model_id,
        count=len(models),
    )


@router.get("/{model_id}", response_model=ModelResponse)
async def get_model(model_id: str):
    """
    获取特定模型详情
    """
    model = model_manager.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
    return model_to_response(model)


@router.post("/{model_id}/select", response_model=SelectModelResponse)
async def select_model(model_id: str):
    """
    选择默认模型
    """
    success = model_manager.set_default_model(model_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
    return SelectModelResponse(
        success=True,
        message=f"Default model set to {model_id}",
        model_id=model_id,
    )


@router.get("/default", response_model=DefaultModelResponse)
async def get_default_model():
    """
    获取当前默认模型
    """
    model = model_manager.get_default_model()
    return DefaultModelResponse(
        model_id=model.model_id,
        model=model_to_response(model),
    )


@router.get("/by-provider/{provider}", response_model=List[ModelResponse])
async def get_models_by_provider(provider: str):
    """
    按提供商获取模型
    """
    try:
        provider_enum = ModelProvider(provider)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid provider: {provider}")

    models = model_manager.get_models_by_provider(provider_enum)
    return [model_to_response(m) for m in models]
