"""号池账号熔断器管理。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from excelmanus.pool.models import PoolAccountBreaker

if TYPE_CHECKING:
    from excelmanus.db_adapter import ConnectionAdapter

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if hasattr(row, "keys"):
        return dict(row)
    return {}


class BreakerManager:
    """账号熔断器：连续失败超阈值时暂时排除账号，过期后自动进入半开探测。"""

    def __init__(
        self,
        conn: "ConnectionAdapter",
        failure_threshold: int = 5,
        open_seconds: int = 120,
    ) -> None:
        self._conn = conn
        self._failure_threshold = failure_threshold
        self._open_seconds = open_seconds

    def record_failure(
        self, account_id: str, *, open_seconds: int | None = None,
    ) -> PoolAccountBreaker:
        """记录一次失败。连续失败超阈值则打开熔断器。

        Args:
            open_seconds: 可选，覆盖全局 open_seconds（用于按策略配置）。
        """
        now = _now_iso()
        state = self._get_raw(account_id)
        if state is None:
            # 首次记录
            state = PoolAccountBreaker(
                pool_account_id=account_id,
                consecutive_failures=1,
                breaker_state="closed",
                last_failure_at=now,
                updated_at=now,
            )
        else:
            state.consecutive_failures += 1
            state.last_failure_at = now
            state.updated_at = now

        # 超阈值 → 打开熔断器
        if state.consecutive_failures >= self._failure_threshold:
            state.breaker_state = "open"
            _effective_open = open_seconds if open_seconds is not None else self._open_seconds
            open_until = datetime.now(tz=timezone.utc) + timedelta(
                seconds=_effective_open,
            )
            state.open_until = open_until.isoformat()

        self._upsert(state)
        return state

    def record_success(self, account_id: str) -> PoolAccountBreaker:
        """记录一次成功。half_open → closed；closed → 重置计数。"""
        state = self._get_raw(account_id)
        if state is None:
            return PoolAccountBreaker(
                pool_account_id=account_id,
                breaker_state="closed",
                updated_at=_now_iso(),
            )

        now = _now_iso()
        if state.breaker_state == "half_open":
            state.breaker_state = "closed"
        state.consecutive_failures = 0
        state.updated_at = now
        self._upsert(state)
        return state

    def get_state(self, account_id: str) -> PoolAccountBreaker:
        """获取熔断状态。open 且过期 → 自动转 half_open。"""
        state = self._get_raw(account_id)
        if state is None:
            return PoolAccountBreaker(
                pool_account_id=account_id,
                breaker_state="closed",
            )

        if state.breaker_state == "open" and state.open_until:
            try:
                open_until = datetime.fromisoformat(state.open_until)
                if open_until.tzinfo is None:
                    open_until = open_until.replace(tzinfo=timezone.utc)
                if datetime.now(tz=timezone.utc) >= open_until:
                    state.breaker_state = "half_open"
                    state.updated_at = _now_iso()
                    self._upsert(state)
            except (ValueError, TypeError):
                pass

        return state

    def is_available(self, account_id: str) -> bool:
        """closed 或 half_open 返回 True，open 返回 False。"""
        state = self.get_state(account_id)
        return state.breaker_state != "open"

    def list_breakers(self) -> list[PoolAccountBreaker]:
        """列出所有非 closed 的熔断器。"""
        rows = self._conn.execute(
            "SELECT * FROM pool_account_breakers WHERE breaker_state != 'closed'",
        ).fetchall()
        result = []
        for row in rows:
            d = _row_to_dict(row)
            b = self._row_to_breaker(d)
            # 检查 open 是否过期 → half_open
            if b.breaker_state == "open" and b.open_until:
                try:
                    open_until = datetime.fromisoformat(b.open_until)
                    if open_until.tzinfo is None:
                        open_until = open_until.replace(tzinfo=timezone.utc)
                    if datetime.now(tz=timezone.utc) >= open_until:
                        b.breaker_state = "half_open"
                        b.updated_at = _now_iso()
                        self._upsert(b)
                except (ValueError, TypeError):
                    pass
            result.append(b)
        return result

    # ── 内部方法 ──────────────────────────────────────────────

    def _get_raw(self, account_id: str) -> PoolAccountBreaker | None:
        """从数据库读取原始熔断状态（不自动转换 open→half_open）。"""
        row = self._conn.execute(
            "SELECT * FROM pool_account_breakers WHERE pool_account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_breaker(_row_to_dict(row))

    def _upsert(self, state: PoolAccountBreaker) -> None:
        """写入或更新熔断状态（DELETE+INSERT 保证兼容性）。"""
        self._conn.execute(
            "DELETE FROM pool_account_breakers WHERE pool_account_id = ?",
            (state.pool_account_id,),
        )
        self._conn.execute(
            """INSERT INTO pool_account_breakers
               (pool_account_id, consecutive_failures, breaker_state,
                open_until, last_failure_at, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (
                state.pool_account_id,
                state.consecutive_failures,
                state.breaker_state,
                state.open_until,
                state.last_failure_at,
                state.updated_at,
            ),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_breaker(d: dict[str, Any]) -> PoolAccountBreaker:
        return PoolAccountBreaker(
            pool_account_id=d.get("pool_account_id", ""),
            consecutive_failures=int(d.get("consecutive_failures", 0) or 0),
            breaker_state=d.get("breaker_state", "closed") or "closed",
            open_until=d.get("open_until", "") or "",
            last_failure_at=d.get("last_failure_at", "") or "",
            updated_at=d.get("updated_at", "") or "",
        )
