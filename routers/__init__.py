"""
路由模块初始化
"""

from .models import router as models_router
from .plan import router as plan_router

__all__ = ["models_router", "plan_router"]