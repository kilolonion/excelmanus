"""安全相关工具。"""

from excelmanus.security.guard import FileAccessGuard, SecurityViolationError
from excelmanus.security.policy import (
    ApprovalPolicy,
    ExecutionPolicy,
    is_plan_active,
    resolve_approval_policy,
    resolve_execution_policy,
    writes_denied,
)
from excelmanus.security.sanitizer import sanitize_sensitive_text

__all__ = [
    "ApprovalPolicy",
    "ExecutionPolicy",
    "FileAccessGuard",
    "SecurityViolationError",
    "is_plan_active",
    "resolve_approval_policy",
    "resolve_execution_policy",
    "sanitize_sensitive_text",
    "writes_denied",
]
