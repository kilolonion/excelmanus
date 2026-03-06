"""号池分钟级指标聚合。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from excelmanus.pool.models import PoolRotationMetric

if TYPE_CHECKING:
    from excelmanus.db_adapter import ConnectionAdapter

logger = logging.getLogger(__name__)


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if hasattr(row, "keys"):
        return dict(row)
    return {}


def _current_minute_bucket() -> str:
    """当前分钟的 ISO 时间戳（秒归零）。"""
    now = datetime.now(tz=timezone.utc)
    return now.replace(second=0, microsecond=0).isoformat()


def _prev_minute_bucket() -> str:
    """上一分钟的 ISO 时间戳（已完结，数据完整）。"""
    from datetime import timedelta
    now = datetime.now(tz=timezone.utc)
    prev = (now - timedelta(minutes=1)).replace(second=0, microsecond=0)
    return prev.isoformat()


class MetricsAggregator:
    """分钟级号池运行指标聚合器。

    从 pool_usage_ledger + pool_rotation_events 聚合数据，
    写入 pool_rotation_metrics_minute 表。
    """

    def __init__(self, conn: "ConnectionAdapter") -> None:
        self._conn = conn

    def aggregate_minute(
        self,
        provider: str,
        model_pattern: str = "*",
        minute_bucket: str | None = None,
    ) -> PoolRotationMetric:
        """聚合指定分钟的数据。默认为上一分钟（已完结，数据完整）。"""
        if minute_bucket is None:
            minute_bucket = _prev_minute_bucket()

        # 下一分钟上界
        try:
            dt = datetime.fromisoformat(minute_bucket)
            from datetime import timedelta
            next_minute = (dt + timedelta(minutes=1)).isoformat()
        except (ValueError, TypeError):
            next_minute = minute_bucket

        # 从 pool_usage_ledger JOIN pool_accounts 聚合请求数据（按 provider 过滤）
        total = 0
        success = 0
        err_429 = 0
        err_5xx = 0

        row = self._conn.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN l.outcome = 'success' THEN 1 ELSE 0 END) as ok,
                SUM(CASE WHEN l.outcome = 'error' AND l.error_code = '429' THEN 1 ELSE 0 END) as e429,
                SUM(CASE WHEN l.outcome = 'error' AND l.error_code IN ('500','502','503','504') THEN 1 ELSE 0 END) as e5xx
            FROM pool_usage_ledger l
            JOIN pool_accounts a ON l.pool_account_id = a.id
            WHERE l.created_at >= ? AND l.created_at < ?
              AND a.provider = ?""",
            (minute_bucket, next_minute, provider),
        ).fetchone()
        if row is not None:
            d = _row_to_dict(row)
            total = int(d.get("total", 0) or 0)
            success = int(d.get("ok", 0) or 0)
            err_429 = int(d.get("e429", 0) or 0)
            err_5xx = int(d.get("e5xx", 0) or 0)

        # 从 pool_rotation_events 聚合轮换数据（按 provider + model_pattern 过滤）
        rotations = 0
        fallbacks = 0

        row2 = self._conn.execute(
            """SELECT
                SUM(CASE WHEN fallback_used = 0 THEN 1 ELSE 0 END) as rots,
                SUM(CASE WHEN fallback_used = 1 THEN 1 ELSE 0 END) as fbs
            FROM pool_rotation_events
            WHERE created_at >= ? AND created_at < ?
              AND provider = ? AND model_pattern = ?""",
            (minute_bucket, next_minute, provider, model_pattern),
        ).fetchone()
        if row2 is not None:
            d2 = _row_to_dict(row2)
            rotations = int(d2.get("rots", 0) or 0)
            fallbacks = int(d2.get("fbs", 0) or 0)

        metric = PoolRotationMetric(
            minute_bucket=minute_bucket,
            provider=provider,
            model_pattern=model_pattern,
            total_requests=total,
            success_count=success,
            error_429=err_429,
            error_5xx=err_5xx,
            rotations=rotations,
            fallbacks=fallbacks,
        )

        # INSERT OR REPLACE
        self._conn.execute(
            "DELETE FROM pool_rotation_metrics_minute "
            "WHERE minute_bucket = ? AND provider = ? AND model_pattern = ?",
            (minute_bucket, provider, model_pattern),
        )
        self._conn.execute(
            """INSERT INTO pool_rotation_metrics_minute
               (minute_bucket, provider, model_pattern,
                total_requests, success_count, error_429, error_5xx,
                rotations, fallbacks)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                metric.minute_bucket, metric.provider, metric.model_pattern,
                metric.total_requests, metric.success_count,
                metric.error_429, metric.error_5xx,
                metric.rotations, metric.fallbacks,
            ),
        )
        self._conn.commit()
        return metric

    def query(
        self,
        provider: str | None = None,
        model_pattern: str | None = None,
        minutes: int = 60,
    ) -> list[PoolRotationMetric]:
        """查询最近 N 分钟的指标。"""
        from datetime import timedelta
        since = (
            datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)
        ).replace(second=0, microsecond=0).isoformat()

        sql = "SELECT * FROM pool_rotation_metrics_minute WHERE minute_bucket >= ?"
        params: list[Any] = [since]
        if provider is not None:
            sql += " AND provider = ?"
            params.append(provider)
        if model_pattern is not None:
            sql += " AND model_pattern = ?"
            params.append(model_pattern)
        sql += " ORDER BY minute_bucket DESC"

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_metric(_row_to_dict(r)) for r in rows]

    def aggregate_all_scopes(
        self,
        policies: list[Any] | None = None,
    ) -> int:
        """遍历所有策略 scope 执行聚合。

        默认聚合上一分钟（已完结的完整窗口）。
        返回聚合的 scope 数量。
        """
        if policies is None:
            return 0
        bucket = _prev_minute_bucket()
        count = 0
        for policy in policies:
            try:
                self.aggregate_minute(
                    policy.provider, policy.model_pattern,
                    minute_bucket=bucket,
                )
                count += 1
            except Exception:
                logger.debug(
                    "指标聚合失败: %s/%s", policy.provider, policy.model_pattern,
                    exc_info=True,
                )
        return count

    @staticmethod
    def _row_to_metric(d: dict[str, Any]) -> PoolRotationMetric:
        return PoolRotationMetric(
            minute_bucket=d.get("minute_bucket", ""),
            provider=d.get("provider", ""),
            model_pattern=d.get("model_pattern", "*"),
            total_requests=int(d.get("total_requests", 0) or 0),
            success_count=int(d.get("success_count", 0) or 0),
            error_429=int(d.get("error_429", 0) or 0),
            error_5xx=int(d.get("error_5xx", 0) or 0),
            rotations=int(d.get("rotations", 0) or 0),
            fallbacks=int(d.get("fallbacks", 0) or 0),
        )
