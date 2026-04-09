"""
错误类型定义和分类

区分可恢复和不可恢复错误，提供详细的错误信息
"""

from enum import Enum, auto
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass
import re


class ErrorCategory(Enum):
    """错误分类"""
    # 可恢复错误
    RATE_LIMIT = auto()          # 速率限制
    SERVER_ERROR = auto()        # 服务器错误 (5xx)
    TOKEN_LIMIT = auto()         # Token超限 (max_output_tokens)
    TIMEOUT = auto()             # 超时
    NETWORK = auto()             # 网络错误

    # 不可恢复错误
    AUTHENTICATION = auto()      # 认证错误
    PERMISSION = auto()          # 权限错误
    INVALID_REQUEST = auto()     # 无效请求
    PROMPT_TOO_LONG = auto()     # 提示太长 (超过模型最大限制)
    CONTENT_FILTER = auto()      # 内容过滤
    NOT_FOUND = auto()           # 资源不存在
    UNKNOWN = auto()             # 未知错误


@dataclass
class APIError(Exception):
    """基础API错误"""
    message: str
    category: ErrorCategory = ErrorCategory.UNKNOWN
    original_error: Optional[Exception] = None
    error_code: Optional[str] = None
    retryable: bool = False
    context: Dict[str, Any] = None

    def __post_init__(self):
        if self.context is None:
            self.context = {}

    def __str__(self) -> str:
        return f"[{self.category.name}] {self.message}"


class RecoverableError(APIError):
    """可恢复错误 - 可以通过重试或调整参数解决"""
    def __init__(
        self,
        message: str,
        category: ErrorCategory,
        original_error: Optional[Exception] = None,
        error_code: Optional[str] = None,
        suggested_action: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            category=category,
            original_error=original_error,
            error_code=error_code,
            retryable=True,
            context=context or {},
        )
        self.suggested_action = suggested_action


class NonRecoverableError(APIError):
    """不可恢复错误 - 需要用户干预或修改请求"""
    def __init__(
        self,
        message: str,
        category: ErrorCategory,
        original_error: Optional[Exception] = None,
        error_code: Optional[str] = None,
        user_message: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            category=category,
            original_error=original_error,
            error_code=error_code,
            retryable=False,
            context=context or {},
        )
        self.user_message = user_message or message


class TokenLimitError(RecoverableError):
    """Token限制错误 - 输出token超限"""
    def __init__(
        self,
        message: str,
        original_error: Optional[Exception] = None,
        current_max_tokens: Optional[int] = None,
        suggested_max_tokens: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            category=ErrorCategory.TOKEN_LIMIT,
            original_error=original_error,
            suggested_action="increase_max_tokens",
            context=context,
        )
        self.current_max_tokens = current_max_tokens
        self.suggested_max_tokens = suggested_max_tokens or (
            current_max_tokens * 2 if current_max_tokens else None
        )


class PromptTooLongError(NonRecoverableError):
    """提示太长错误 - 输入超过模型最大限制"""
    def __init__(
        self,
        message: str,
        original_error: Optional[Exception] = None,
        prompt_tokens: Optional[int] = None,
        max_allowed_tokens: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            category=ErrorCategory.PROMPT_TOO_LONG,
            original_error=original_error,
            user_message="The conversation is too long. Please start a new conversation or clear some messages.",
            context=context,
        )
        self.prompt_tokens = prompt_tokens
        self.max_allowed_tokens = max_allowed_tokens


class RateLimitError(RecoverableError):
    """速率限制错误"""
    def __init__(
        self,
        message: str,
        original_error: Optional[Exception] = None,
        retry_after: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            category=ErrorCategory.RATE_LIMIT,
            original_error=original_error,
            suggested_action="wait_and_retry",
            context=context,
        )
        self.retry_after = retry_after


class ServerError(RecoverableError):
    """服务器错误"""
    def __init__(
        self,
        message: str,
        status_code: int,
        original_error: Optional[Exception] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            category=ErrorCategory.SERVER_ERROR,
            original_error=original_error,
            suggested_action="retry_with_backoff",
            context=context,
        )
        self.status_code = status_code


class TimeoutError(RecoverableError):
    """超时错误"""
    def __init__(
        self,
        message: str,
        original_error: Optional[Exception] = None,
        timeout_seconds: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            category=ErrorCategory.TIMEOUT,
            original_error=original_error,
            suggested_action="retry_with_backoff",
            context=context,
        )
        self.timeout_seconds = timeout_seconds


class NetworkError(RecoverableError):
    """网络错误"""
    def __init__(
        self,
        message: str,
        original_error: Optional[Exception] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            category=ErrorCategory.NETWORK,
            original_error=original_error,
            suggested_action="check_network_and_retry",
            context=context,
        )


class AuthenticationError(NonRecoverableError):
    """认证错误"""
    def __init__(
        self,
        message: str,
        original_error: Optional[Exception] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            category=ErrorCategory.AUTHENTICATION,
            original_error=original_error,
            user_message="Authentication failed. Please check your API key configuration.",
            context=context,
        )


class PermissionError(NonRecoverableError):
    """权限错误"""
    def __init__(
        self,
        message: str,
        original_error: Optional[Exception] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            category=ErrorCategory.PERMISSION,
            original_error=original_error,
            user_message="You don't have permission to perform this action.",
            context=context,
        )


class ContentFilterError(NonRecoverableError):
    """内容过滤错误"""
    def __init__(
        self,
        message: str,
        original_error: Optional[Exception] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            category=ErrorCategory.CONTENT_FILTER,
            original_error=original_error,
            user_message="The content was flagged by the safety filter. Please try a different request.",
            context=context,
        )


# 错误分类规则
ERROR_PATTERNS: Dict[ErrorCategory, list] = {
    ErrorCategory.RATE_LIMIT: [
        r"rate.?limit",
        r"too.?many.?requests",
        r"429",
    ],
    ErrorCategory.TOKEN_LIMIT: [
        r"max.?output.?tokens",
        r"output.?token.?limit",
        r"maximum.?context.?length",
    ],
    ErrorCategory.PROMPT_TOO_LONG: [
        r"prompt.?too.?long",
        r"context.?length.?exceeded",
        r"input.?too.?long",
        r"maximum.?context",
        r"token.?limit.?exceeded",
    ],
    ErrorCategory.AUTHENTICATION: [
        r"authentication",
        r"unauthorized",
        r"invalid.?api.?key",
        r"401",
    ],
    ErrorCategory.PERMISSION: [
        r"permission",
        r"forbidden",
        r"403",
    ],
    ErrorCategory.SERVER_ERROR: [
        r"server.?error",
        r"internal.?server",
        r"bad.?gateway",
        r"gateway.?timeout",
        r"5\d{2}",
    ],
    ErrorCategory.TIMEOUT: [
        r"timeout",
        r"timed.?out",
    ],
    ErrorCategory.NETWORK: [
        r"connection",
        r"network",
        r"unreachable",
    ],
    ErrorCategory.CONTENT_FILTER: [
        r"content.?filter",
        r"safety",
        r"moderation",
        r"inappropriate",
    ],
}


def classify_error(
    error: Exception,
    status_code: Optional[int] = None,
    context: Optional[Dict[str, Any]] = None
) -> APIError:
    """
    分类错误并返回结构化的APIError

    Args:
        error: 原始异常
        status_code: HTTP状态码（如果有）
        context: 额外的上下文信息

    Returns:
        结构化的APIError
    """
    error_str = str(error).lower()
    error_type = type(error).__name__.lower()

    # 检查状态码
    if status_code:
        if status_code == 429:
            return RateLimitError(
                message=str(error),
                original_error=error,
                context=context,
            )
        elif status_code == 401:
            return AuthenticationError(
                message=str(error),
                original_error=error,
                context=context,
            )
        elif status_code == 403:
            return PermissionError(
                message=str(error),
                original_error=error,
                context=context,
            )
        elif status_code >= 500:
            return ServerError(
                message=str(error),
                status_code=status_code,
                original_error=error,
                context=context,
            )

    # 根据错误内容匹配
    for category, patterns in ERROR_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, error_str, re.IGNORECASE):
                if category == ErrorCategory.TOKEN_LIMIT:
                    return TokenLimitError(
                        message=str(error),
                        original_error=error,
                        context=context,
                    )
                elif category == ErrorCategory.PROMPT_TOO_LONG:
                    return PromptTooLongError(
                        message=str(error),
                        original_error=error,
                        context=context,
                    )
                elif category == ErrorCategory.RATE_LIMIT:
                    return RateLimitError(
                        message=str(error),
                        original_error=error,
                        context=context,
                    )
                elif category == ErrorCategory.SERVER_ERROR:
                    return ServerError(
                        message=str(error),
                        status_code=0,
                        original_error=error,
                        context=context,
                    )
                elif category == ErrorCategory.TIMEOUT:
                    return TimeoutError(
                        message=str(error),
                        original_error=error,
                        context=context,
                    )
                elif category == ErrorCategory.NETWORK:
                    return NetworkError(
                        message=str(error),
                        original_error=error,
                        context=context,
                    )
                elif category == ErrorCategory.AUTHENTICATION:
                    return AuthenticationError(
                        message=str(error),
                        original_error=error,
                        context=context,
                    )
                elif category == ErrorCategory.PERMISSION:
                    return PermissionError(
                        message=str(error),
                        original_error=error,
                        context=context,
                    )
                elif category == ErrorCategory.CONTENT_FILTER:
                    return ContentFilterError(
                        message=str(error),
                        original_error=error,
                        context=context,
                    )

    # 检查特定错误类型
    if "timeout" in error_type:
        return TimeoutError(
            message=str(error),
            original_error=error,
            context=context,
        )

    # 默认为不可恢复错误
    return NonRecoverableError(
        message=str(error),
        category=ErrorCategory.UNKNOWN,
        original_error=error,
        context=context,
    )


def is_retryable(error: Exception) -> bool:
    """检查错误是否可重试"""
    classified = classify_error(error)
    return classified.retryable
