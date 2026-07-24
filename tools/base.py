"""
工具基类模块
定义所有工具的抽象基类和通用接口
"""

import dataclasses
import math
import re
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Dict, Generic, Mapping, Optional, TypeVar, cast, get_args

from pydantic import BaseModel

_ACTIVE_TOOL_CONTEXT: ContextVar[Dict[str, Any] | None] = ContextVar(
    "active_tool_context", default=None
)


def get_active_tool_context() -> Dict[str, Any]:
    return _ACTIVE_TOOL_CONTEXT.get() or {}


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


def to_json_value(value: Any, path: str = "$") -> Any:
    """Convert supported structured tool output into a detached JSON value."""

    if isinstance(value, Enum):
        return to_json_value(value.value, path)
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite JSON numbers")
        return value
    if isinstance(value, BaseModel):
        return to_json_value(value.model_dump(mode="json"), path)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: to_json_value(getattr(value, item.name), f"{path}.{item.name}")
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        converted: Dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{path} mapping keys must be strings")
            converted[key] = to_json_value(item, f"{path}.{key}")
        return converted
    if isinstance(value, (list, tuple)):
        return [
            to_json_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(f"{path} contains unsupported JSON value {type(value).__name__}")


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
    def fail(
        cls,
        error: Exception | str,
        message: str = "",
    ) -> "ToolResult":
        """创建错误的结果"""
        if isinstance(error, str):
            error = ToolExecutionError(error)
        elif not isinstance(error, ToolError):
            error = ToolExecutionError(str(error))
        return cls(
            success=False,
            data=None,
            message=message or str(error),
            error=error,
        )


InputType = TypeVar("InputType")
OutputType = TypeVar("OutputType")


_EXPLICIT_TOOL_ALIASES = {
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "edit_file",
    "Glob": "glob",
    "Grep": "grep",
    "Bash": "bash",
    "Agent": "agent",
    "EnterPlanMode": "enter_plan_mode",
    "ExitPlanMode": "exit_plan_mode",
    "AskUserQuestion": "ask_user_question",
}


def canonical_tool_name(name: str) -> str:
    """Return the stable lower-snake-case name used by the harness."""
    if name in _EXPLICIT_TOOL_ALIASES:
        return _EXPLICIT_TOOL_ALIASES[name]
    value = name.replace("-", "_").replace(" ", "_")
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"_+", "_", value).strip("_").lower()


def tool_flag(tool: Any, attribute: str, default: bool = False) -> bool:
    """Read a legacy tool trait implemented as either a method or a boolean."""
    value = getattr(tool, attribute, default)
    return bool(value() if callable(value) else value)


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
        return cast(type, im)
    input_type = getattr(tool_cls, "input_type", None)
    if input_type is not None:
        return cast(type, input_type)
    for base in getattr(tool_cls, "__orig_bases__", ()) or ():
        args = get_args(base)
        if args:
            return cast(type, args[0])
    return None


def _schema_from_input_type(tool_cls: type) -> Optional[Dict[str, Any]]:
    input_type = _resolve_tool_input_type(tool_cls)
    if input_type is None:
        return None
    try:
        from pydantic import TypeAdapter

        schema = TypeAdapter(input_type).json_schema()
        return schema if isinstance(schema, dict) else None
    except Exception:
        return None


def _normalize_object_schema(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = dict(schema) if isinstance(schema, dict) else {}
    normalized["type"] = "object"
    normalized.setdefault("properties", {})
    return normalized


@dataclass(frozen=True)
class ToolSpec:
    """Canonical, provider-neutral description of a registered tool."""

    name: str
    description: str
    parameters: Dict[str, Any]
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_tool(
        cls,
        tool: "Tool",
        *,
        name: Optional[str] = None,
        aliases: tuple[str, ...] = (),
    ) -> "ToolSpec":
        raw_schema = tool.get_schema() or {}
        parameters = raw_schema.get("parameters")
        if parameters is None:
            parameters = raw_schema.get("input_schema") or raw_schema.get("inputSchema")
        if parameters is None:
            getter = getattr(tool, "get_input_schema", None)
            if callable(getter):
                try:
                    parameters = getter()
                except TypeError:
                    parameters = None
        if parameters is None:
            parameters = getattr(tool, "input_schema", None)
        if parameters is None:
            parameters = _schema_from_input_type(tool.__class__)

        return cls(
            name=name or canonical_tool_name(tool.name),
            description=raw_schema.get("description", tool.description),
            parameters=_normalize_object_schema(parameters),
            aliases=aliases,
        )

    def to_openai(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


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
        prepared, error = await self.prepare_input(input_data)
        if error is not None:
            return ToolResult.fail(error)
        return await self.invoke_prepared(cast(InputType, prepared), context)

    def coerce_input(
        self, input_data: Any
    ) -> tuple[Optional[InputType], Optional[ToolError]]:
        """Parse raw input through the declared Pydantic-compatible type."""

        input_type = _resolve_tool_input_type(self.__class__)
        if input_type is None:
            return None, ToolValidationError(
                "Invalid input data: cannot resolve tool input type "
                "(use Tool[Input, Output] or set class attribute input_model=...)"
            )
        try:
            from pydantic import TypeAdapter

            if isinstance(input_type, type) and isinstance(input_data, input_type):
                parsed = input_data
            elif dataclasses.is_dataclass(input_type) and isinstance(input_data, Mapping):
                parsed = input_type(**dict(input_data))
            elif hasattr(input_type, "model_validate"):
                parsed = input_type.model_validate(input_data, strict=True)
            else:
                parsed = TypeAdapter(input_type).validate_python(input_data, strict=True)
            return cast(InputType, parsed), None
        except Exception as exc:
            return None, ToolValidationError(f"Invalid input data: {str(exc)}")

    async def prepare_input(
        self, input_data: Any
    ) -> tuple[Optional[InputType], Optional[ToolError]]:
        """Coerce and run the tool's semantic validation without executing it."""

        prepared, error = self.coerce_input(input_data)
        if error is not None:
            return None, error
        try:
            validation_error = await self.validate(cast(InputType, prepared))
        except ToolError as exc:
            return None, exc
        except Exception as exc:
            return None, ToolValidationError(f"Invalid input data: {str(exc)}")
        return (
            (None, validation_error)
            if validation_error is not None
            else (prepared, None)
        )

    async def invoke_prepared(
        self,
        input_data: InputType,
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """Execute already-validated input under the legacy active context."""

        context_token = _ACTIVE_TOOL_CONTEXT.set(context or {})
        try:
            return await self.execute(input_data)
        except ToolError as e:
            return ToolResult.fail(e)
        except Exception as e:
            return ToolResult.fail(
                ToolExecutionError(
                    message=f"Unexpected error: {str(e)}",
                    details={"exception_type": type(e).__name__},
                )
            )
        finally:
            _ACTIVE_TOOL_CONTEXT.reset(context_token)

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
    _aliases: Dict[str, str] = {}

    @classmethod
    def register(cls, tool: Tool) -> None:
        """注册工具"""
        canonical = canonical_tool_name(tool.name)
        cls._tools[canonical] = tool
        cls._aliases[canonical] = canonical
        cls._aliases[tool.name] = canonical
        cls._aliases[tool.name.lower()] = canonical
        for alias in getattr(tool, "aliases", ()):
            cls._aliases[alias] = canonical
            cls._aliases[alias.lower()] = canonical
        for alias, target in _EXPLICIT_TOOL_ALIASES.items():
            if target == canonical:
                cls._aliases[alias] = canonical

    @classmethod
    def get(cls, name: str) -> Optional[Tool]:
        """获取工具"""
        canonical = cls.resolve_name(name)
        return cls._tools.get(canonical) if canonical else None

    @classmethod
    def resolve_name(cls, name: str) -> Optional[str]:
        """Resolve canonical and legacy names without duplicate registrations."""
        if name in cls._aliases:
            return cls._aliases[name]
        normalized = canonical_tool_name(name)
        if normalized in cls._tools:
            return normalized
        explicit = _EXPLICIT_TOOL_ALIASES.get(name)
        if explicit in cls._tools:
            return explicit
        return None

    @classmethod
    def list_tools(cls) -> list[str]:
        """列出所有工具名称"""
        return list(cls._tools.keys())

    @classmethod
    def get_spec(cls, name: str) -> Optional[ToolSpec]:
        canonical = cls.resolve_name(name)
        if canonical is None:
            return None
        tool = cls._tools[canonical]
        aliases = tuple(
            sorted(alias for alias, target in cls._aliases.items() if target == canonical)
        )
        return ToolSpec.from_tool(tool, name=canonical, aliases=aliases)

    @classmethod
    def list_specs(cls) -> list[ToolSpec]:
        return [
            spec
            for name in cls.list_tools()
            if (spec := cls.get_spec(name)) is not None
        ]

    @classmethod
    def get_all_schemas(cls) -> Dict[str, Dict[str, Any]]:
        """获取所有工具的Schema"""
        return {spec.name: spec.to_openai()["function"] for spec in cls.list_specs()}


# 装饰器用于自动注册工具
def register_tool(tool_class: type) -> type:
    """工具注册装饰器"""
    instance = tool_class()
    ToolRegistry.register(instance)
    return tool_class
