"""号池健康信号：从 LLM API 错误中提取池账号健康状态。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from excelmanus.pool.service import PoolService

logger = logging.getLogger(__name__)


def classify_pool_health_signal(
    status_code: int | None,
    error_message: str = "",
) -> tuple[str, float] | None:
    """从 HTTP 状态码和错误消息推断池账号健康信号。

    返回 (signal, confidence) 或 None（不属于池相关错误）。
    """
    msg_lower = (error_message or "").lower()

    # 402 / quota / billing → depleted
    if status_code == 402:
        return ("depleted", 0.9)
    if any(kw in msg_lower for kw in (
        "insufficient quota", "quota exceeded", "billing",
        "payment_required", "balance",
    )):
        return ("depleted", 0.85)

    # 429 → rate_limited
    if status_code == 429:
        return ("rate_limited", 0.7)

    # 5xx / network → transient
    if status_code is not None and 500 <= status_code < 600:
        return ("transient", 0.5)
    if any(kw in msg_lower for kw in (
        "connection refused", "network", "timeout", "service unavailable",
    )):
        return ("transient", 0.4)

    return None


def update_pool_health_from_error(
    pool_service: "PoolService",
    pool_account_id: str,
    status_code: int | None = None,
    error_message: str = "",
    session_id: str = "",
    user_id: str = "",
    model: str = "",
    breaker: "Any | None" = None,
) -> None:
    """从 LLM 错误更新池账号健康信号并记录失败台账。"""
    signal_result = classify_pool_health_signal(status_code, error_message)
    if signal_result is None:
        return

    signal, confidence = signal_result
    try:
        pool_service.update_health_signal(pool_account_id, signal, confidence)
        # 记录失败台账
        error_code = ""
        if status_code:
            error_code = str(status_code)
        elif signal:
            error_code = signal
        pool_service.log_usage(
            pool_account_id=pool_account_id,
            session_id=session_id,
            user_id=user_id,
            model=model,
            outcome="error",
            error_code=error_code,
        )
        # 熔断器记录失败
        if breaker is not None:
            try:
                breaker.record_failure(pool_account_id)
            except Exception:
                logger.debug("熔断器记录失败异常", exc_info=True)
        logger.info(
            "池账号 %s 健康信号更新: %s (confidence=%.2f, status=%s)",
            pool_account_id, signal, confidence, status_code,
        )
    except Exception:
        logger.debug("更新池健康信号失败", exc_info=True)


def update_pool_health_on_success(
    pool_service: "PoolService",
    pool_account_id: str,
    breaker: "Any | None" = None,
) -> None:
    """成功请求后重置池账号健康信号。"""
    try:
        pool_service.update_health_signal(pool_account_id, "ok", 1.0)
        # 熔断器记录成功
        if breaker is not None:
            try:
                breaker.record_success(pool_account_id)
            except Exception:
                logger.debug("熔断器记录成功异常", exc_info=True)
    except Exception:
        logger.debug("重置池健康信号失败", exc_info=True)
