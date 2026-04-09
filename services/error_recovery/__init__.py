"""
错误恢复模块

提供API错误分类、自动重试、token超限恢复等功能
"""

from .error_types import (
    ErrorCategory,
    RecoverableError,
    NonRecoverableError,
    TokenLimitError,
    PromptTooLongError,
    RateLimitError,
    ServerError,
    AuthenticationError,
    TimeoutError,
    NetworkError,
    classify_error,
)

from .retry_handler import (
    RetryConfig,
    ExponentialBackoff,
    with_retry,
    RetryHandler,
    retry_with_backoff,
)

from .token_recovery import (
    TokenRecoveryStrategy,
    MaxOutputTokensRecovery,
    TokenRecoveryManager,
    TokenRecoveryResult,
    RecoveryAction,
    TruncateHistoryRecovery,
    PromptCompressionRecovery,
)

from .recovery_manager import (
    RecoveryManager,
    RecoveryResult,
    RecoveryConfig,
    RecoveryPhase,
    with_recovery,
    classify_for_user,
)

__all__ = [
    # Error Types
    "ErrorCategory",
    "RecoverableError",
    "NonRecoverableError",
    "TokenLimitError",
    "PromptTooLongError",
    "RateLimitError",
    "ServerError",
    "AuthenticationError",
    "TimeoutError",
    "NetworkError",
    "classify_error",
    # Retry Handler
    "RetryConfig",
    "ExponentialBackoff",
    "with_retry",
    "RetryHandler",
    "retry_with_backoff",
    # Token Recovery
    "TokenRecoveryStrategy",
    "MaxOutputTokensRecovery",
    "TokenRecoveryManager",
    "TokenRecoveryResult",
    "RecoveryAction",
    "TruncateHistoryRecovery",
    "PromptCompressionRecovery",
    # Recovery Manager
    "RecoveryManager",
    "RecoveryResult",
    "RecoveryConfig",
    "RecoveryPhase",
    "with_recovery",
    "classify_for_user",
]
