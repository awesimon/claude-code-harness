"""
工具基类模块
定义所有工具的抽象基类和通用接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Generic, Optional, TypeVar, Dict, Protocol, runtime_checkable, get_args
from enum import Enum, auto
import asyncio


class ToolError(Exception):
    """工具执行错误基类"""

    def __init__(
        self,
        message: str,
        error_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}

    def __str__(self) -> str:
        if self.error_code:
            return f"[Error {self.error_code}] {self.message}"
        return self.message


class ToolNotFoundError(ToolError):
    """工具未找到错误"""

    def __init__(self, tool_name: str):
        super().__init__(
            message=f"Tool '{tool_name}' not found",
            error_code=404,
            details={"tool_name": tool_name},
        )


class ToolValidationError(ToolError):
    """工具输入验证错误"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            error_code=400,
            details=details,
        )


class ToolPermissionError(ToolError):
    """工具权限错误"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            error_code=403,
            details=details,
        )


class ToolExecutionError(ToolError):
    """工具执行错误"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            error_code=500,
            details=details,
        )


class ToolTimeoutError(ToolError):
    """工具执行超时错误"""

    def __init__(self, timeout_seconds: float):
        super().__init__(
            message=f"Tool execution timed out after {timeout_seconds} seconds",
            error_code=504,
            details={"timeout_seconds": timeout_seconds},
        )


@dataclass
class ToolResult:
    """工具执行结果"""

    success: bool
    data: Any
    message: str = ""
    error: Optional[ToolError] = None
    metadata: Optional[Dict[str, Any]] = None

    @classmethod
    def ok(
        cls,
        data: Any,
        message: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ToolResult":
        """创建成功的结果"""
        return cls(
            success=True,
            data=data,
            message=message,
            metadata=metadata,
        )

    @classmethod
    def error(
        cls,
        error: ToolError,
        message: str = "",
    ) -> "ToolResult":
        """创建错误的结果"""
        return cls(
            success=False,
            data=None,
            message=message or str(error),
            error=error,
        )


InputType = TypeVar("InputType")
OutputType = TypeVar("OutputType")


def _resolve_tool_input_type(tool_cls: type) -> Optional[type]:
    """
    解析工具 dict → 模型 时使用的输入类型。

    优先 ``input_model``（未写 ``Tool[Input, Output]`` 的工具常用）；
    否则从 ``Tool[Input, ...]`` 泛型实参取第一个。
    不能再用 ``__orig_bases__[0].__args__``：``Tool`` 定义为 ``(ABC, Generic[...])`` 时
    第一个基类是 ``ABC``，会触发 ``'ABC' has no attribute '__args__'``。
    """
    im = getattr(tool_cls, "input_model", None)
    if im is not None:
        return im
    for base in getattr(tool_cls, "__orig_bases__", ()) or ():
        args = get_args(base)
        if args:
            return args[0]
    return None


class Tool(ABC, Generic[InputType, OutputType]):
    """
    工具抽象基类

    所有工具必须继承此类并实现execute方法。
    提供统一的工具接口、输入验证和错误处理机制。
    """

    name: str = ""
    description: str = ""
    version: str = "1.0"

    def __init__(self):
        if not self.name:
            self.name = self.__class__.__name__

    @abstractmethod
    async def execute(self, input_data: InputType) -> ToolResult:
        """
        执行工具操作

        Args:
            input_data: 工具输入数据

        Returns:
            ToolResult: 执行结果
        """
        pass

    async def validate(self, input_data: InputType) -> Optional[ToolError]:
        """
        验证输入数据

        Args:
            input_data: 待验证的输入数据

        Returns:
            如果验证失败返回ToolError，否则返回None
        """
        return None

    async def run(self, input_data: InputType, context: Optional[Dict[str, Any]] = None) -> ToolResult:
        """
        运行工具（包含验证和执行）

        Args:
            input_data: 工具输入数据 (dataclass 或 dict)
            context: 可选的执行上下文，包含 session_id, current_mode 等

        Returns:
            ToolResult: 执行结果
        """
        try:
            # 如果输入是 dict，尝试转换为 dataclass 或 Pydantic model
            if isinstance(input_data, dict):
                import dataclasses
                input_type = _resolve_tool_input_type(self.__class__)
                if input_type is None:
                    return ToolResult.error(
                        ToolValidationError(
                            "Invalid input data: cannot resolve tool input type "
                            "(use Tool[Input, Output] or set class attribute input_model=...)"
                        )
                    )
                if dataclasses.is_dataclass(input_type):
                    input_data = input_type(**input_data)
                elif hasattr(input_type, 'model_validate'):
                    # Pydantic BaseModel
                    input_data = input_type.model_validate(input_data)
                elif hasattr(input_type, '__init__'):
                    # 其他类型，尝试直接实例化
                    input_data = input_type(**input_data)
        except Exception as e:
            return ToolResult.error(
                ToolValidationError(f"Invalid input data: {str(e)}")
            )

        # 验证输入
        validation_error = await self.validate(input_data)
        if validation_error:
            return ToolResult.error(validation_error)

        # 执行工具
        try:
            return await self.execute(input_data)
        except ToolError as e:
            return ToolResult.error(e)
        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(
                    message=f"Unexpected error: {str(e)}",
                    details={"exception_type": type(e).__name__},
                )
            )

    def get_schema(self) -> Dict[str, Any]:
        """
        获取工具的JSON Schema描述

        Returns:
            工具的JSON Schema描述
        """
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
        }

    def is_read_only(self) -> bool:
        """是否为只读工具（不修改系统状态）"""
        return False

    def is_destructive(self) -> bool:
        """是否为破坏性工具（删除、覆盖等操作）"""
        return False

    def requires_confirmation(self) -> bool:
        """是否需要用户确认"""
        return self.is_destructive()


class ToolRegistry:
    """工具注册表"""

    _tools: Dict[str, Tool] = {}

    @classmethod
    def register(cls, tool: Tool) -> None:
        """注册工具"""
        cls._tools[tool.name] = tool

    @classmethod
    def get(cls, name: str) -> Optional[Tool]:
        """获取工具"""
        return cls._tools.get(name)

    @classmethod
    def list_tools(cls) -> list[str]:
        """列出所有工具名称"""
        return list(cls._tools.keys())

    @classmethod
    def get_all_schemas(cls) -> Dict[str, Dict[str, Any]]:
        """获取所有工具的Schema"""
        return {name: tool.get_schema() for name, tool in cls._tools.items()}


# 装饰器用于自动注册工具
def register_tool(tool_class: type) -> type:
    """工具注册装饰器"""
    instance = tool_class()
    ToolRegistry.register(instance)
    return tool_class
