"""PoolService：号池账号管理、预算计算、健康信号、人工激活。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from excelmanus.pool.models import (
    PoolAccount,
    PoolAccountSummary,
    PoolBudgetSnapshot,
    PoolManualActive,
)

if TYPE_CHECKING:
    from excelmanus.auth.providers.credential_store import CredentialStore
    from excelmanus.db_adapter import ConnectionAdapter

logger = logging.getLogger(__name__)

POOL_USER_ID = "__pool_service__"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _row_to_dict(row: Any) -> dict[str, Any]:
    """将数据库行转换为 dict（兼容 sqlite3.Row 和 dict）。"""
    if isinstance(row, dict):
        return row
    if hasattr(row, "keys"):
        return dict(row)
    return {}


def _week_start_utc(tz_name: str, now_utc: datetime | None = None) -> datetime:
    """计算指定时区下本周一 00:00 的 UTC 时间。"""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Asia/Shanghai")

    if now_utc is None:
        now_utc = datetime.now(tz=timezone.utc)
    local_now = now_utc.astimezone(tz)
    days_since_monday = local_now.weekday()  # 0=Monday
    monday_local = local_now.replace(
        hour=0, minute=0, second=0, microsecond=0,
    ) - timedelta(days=days_since_monday)
    return monday_local.astimezone(timezone.utc)


def _day_start_utc(tz_name: str, now_utc: datetime | None = None) -> datetime:
    """计算指定时区下当天 00:00 的 UTC 时间。"""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Asia/Shanghai")

    if now_utc is None:
        now_utc = datetime.now(tz=timezone.utc)
    local_now = now_utc.astimezone(tz)
    day_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start_local.astimezone(timezone.utc)


class PoolService:
    """号池核心服务。"""

    def __init__(
        self,
        conn: "ConnectionAdapter",
        credential_store: "CredentialStore | None" = None,
    ) -> None:
        self._conn = conn
        self._cred_store = credential_store

    # ── 账号 CRUD ─────────────────────────────────────────────

    def create_account(
        self,
        *,
        label: str = "",
        provider: str = "openai-codex",
        account_id: str = "",
        plan_type: str = "",
        daily_budget_tokens: int = 0,
        weekly_budget_tokens: int = 0,
        timezone_str: str = "Asia/Shanghai",
    ) -> PoolAccount:
        """创建池账号。"""
        aid = str(uuid.uuid4())
        now = _now_iso()
        self._conn.execute(
            """INSERT INTO pool_accounts
               (id, label, provider, account_id, plan_type, status,
                daily_budget_tokens, weekly_budget_tokens, timezone,
                health_signal, health_confidence, health_updated_at,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                aid, label, provider, account_id, plan_type, "active",
                daily_budget_tokens, weekly_budget_tokens, timezone_str,
                "ok", 0.0, "",
                now, now,
            ),
        )
        self._conn.commit()
        return PoolAccount(
            id=aid, label=label, provider=provider,
            account_id=account_id, plan_type=plan_type,
            status="active",
            daily_budget_tokens=daily_budget_tokens,
            weekly_budget_tokens=weekly_budget_tokens,
            timezone=timezone_str,
            health_signal="ok", health_confidence=0.0,
            health_updated_at="",
            created_at=now, updated_at=now,
        )

    def get_account(self, account_id: str) -> PoolAccount | None:
        """获取单个池账号。"""
        row = self._conn.execute(
            "SELECT * FROM pool_accounts WHERE id = ?", (account_id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_account(row)

    def list_accounts(self) -> list[PoolAccount]:
        """列出所有池账号。"""
        rows = self._conn.execute(
            "SELECT * FROM pool_accounts ORDER BY created_at DESC",
        ).fetchall()
        return [self._row_to_account(r) for r in rows]

    def update_account(
        self,
        account_id: str,
        **kwargs: Any,
    ) -> PoolAccount | None:
        """更新池账号字段。支持: label, status, daily_budget_tokens, weekly_budget_tokens, timezone。"""
        allowed_fields = {
            "label", "status", "daily_budget_tokens", "weekly_budget_tokens", "timezone",
        }
        updates: dict[str, Any] = {
            k: v for k, v in kwargs.items() if k in allowed_fields
        }
        if not updates:
            return self.get_account(account_id)

        updates["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [account_id]
        self._conn.execute(
            f"UPDATE pool_accounts SET {set_clause} WHERE id = ?",
            params,
        )
        self._conn.commit()
        return self.get_account(account_id)

    # ── OAuth 凭证管理 ────────────────────────────────────────

    def store_oauth_credential(
        self,
        account_id: str,
        credential: Any,
    ) -> None:
        """将 OAuth 凭证存入 CredentialStore（池专用 user_id + profile_name）。"""
        if self._cred_store is None:
            logger.warning("CredentialStore 未初始化，无法存储池凭证")
            return
        profile_name = f"pool/{account_id}"
        account = self.get_account(account_id)
        provider = account.provider if account else "openai-codex"
        self._cred_store.upsert_profile(
            user_id=POOL_USER_ID,
            provider=provider,
            profile_name=profile_name,
            credential=credential,
        )

    def get_pool_profile_name(self, account_id: str) -> str:
        """返回池账号的 profile_name。"""
        return f"pool/{account_id}"

    # ── 台账写入 ──────────────────────────────────────────────

    def log_usage(
        self,
        *,
        pool_account_id: str,
        session_id: str = "",
        user_id: str = "",
        model: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        outcome: str = "success",
        error_code: str = "",
    ) -> None:
        """写入用量台账。"""
        try:
            self._conn.execute(
                """INSERT INTO pool_usage_ledger
                   (pool_account_id, session_id, user_id, model,
                    prompt_tokens, completion_tokens, total_tokens,
                    outcome, error_code, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    pool_account_id, session_id, user_id, model,
                    prompt_tokens, completion_tokens,
                    total_tokens if total_tokens else prompt_tokens + completion_tokens,
                    outcome, error_code, _now_iso(),
                ),
            )
            self._conn.commit()
        except Exception:
            logger.debug("写入池台账失败", exc_info=True)

    # ── 健康信号 ──────────────────────────────────────────────

    def update_health_signal(
        self,
        account_id: str,
        signal: str,
        confidence: float = 1.0,
    ) -> None:
        """更新池账号健康信号。

        signal: ok / depleted / rate_limited / transient
        """
        now = _now_iso()
        self._conn.execute(
            """UPDATE pool_accounts
               SET health_signal = ?, health_confidence = ?,
                   health_updated_at = ?, updated_at = ?
               WHERE id = ?""",
            (signal, min(confidence, 1.0), now, now, account_id),
        )
        # depleted 信号同时更新 status
        if signal == "depleted" and confidence >= 0.8:
            self._conn.execute(
                "UPDATE pool_accounts SET status = 'depleted' WHERE id = ? AND status = 'active'",
                (account_id,),
            )
        self._conn.commit()

    # ── 人工激活映射 ──────────────────────────────────────────

    def set_manual_active(
        self,
        provider: str,
        model_pattern: str,
        pool_account_id: str,
        activated_by: str = "",
    ) -> PoolManualActive:
        """设置人工激活映射。

        使用 DELETE + INSERT 保证 SQLite/PostgreSQL 复合主键 UPSERT 兼容。
        """
        now = _now_iso()
        self._conn.execute(
            "DELETE FROM pool_manual_active WHERE provider = ? AND model_pattern = ?",
            (provider, model_pattern),
        )
        self._conn.execute(
            """INSERT INTO pool_manual_active
               (provider, model_pattern, pool_account_id, activated_by, activated_at)
               VALUES (?,?,?,?,?)""",
            (provider, model_pattern, pool_account_id, activated_by, now),
        )
        self._conn.commit()
        return PoolManualActive(
            provider=provider, model_pattern=model_pattern,
            pool_account_id=pool_account_id,
            activated_by=activated_by, activated_at=now,
        )

    def get_manual_active(
        self, provider: str, model_pattern: str = "*",
    ) -> PoolManualActive | None:
        """获取激活映射。"""
        row = self._conn.execute(
            "SELECT * FROM pool_manual_active WHERE provider = ? AND model_pattern = ?",
            (provider, model_pattern),
        ).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        return PoolManualActive(
            provider=d["provider"],
            model_pattern=d["model_pattern"],
            pool_account_id=d["pool_account_id"],
            activated_by=d.get("activated_by", ""),
            activated_at=d.get("activated_at", ""),
        )

    def list_manual_active(self) -> list[PoolManualActive]:
        """列出所有激活映射。"""
        rows = self._conn.execute(
            "SELECT * FROM pool_manual_active ORDER BY activated_at DESC",
        ).fetchall()
        result: list[PoolManualActive] = []
        for row in rows:
            d = _row_to_dict(row)
            result.append(PoolManualActive(
                provider=d["provider"],
                model_pattern=d["model_pattern"],
                pool_account_id=d["pool_account_id"],
                activated_by=d.get("activated_by", ""),
                activated_at=d.get("activated_at", ""),
            ))
        return result

    def resolve_active_account(
        self, provider: str, model: str,
    ) -> PoolAccount | None:
        """解析当前激活的池账号。

        查找顺序：精确 model_pattern → 通配 '*'。
        仅返回 status=active 的账号。
        """
        for pattern in (model, "*"):
            mapping = self.get_manual_active(provider, pattern)
            if mapping:
                account = self.get_account(mapping.pool_account_id)
                if account and account.status == "active":
                    return account
        return None

    # ── 预算快照 ──────────────────────────────────────────────

    def refresh_snapshots(self) -> int:
        """刷新所有池账号的预算快照。返回刷新数量。"""
        accounts = self.list_accounts()
        now_utc = datetime.now(tz=timezone.utc)
        count = 0
        for account in accounts:
            if account.status == "disabled":
                continue
            try:
                self._refresh_single_snapshot(account, now_utc)
                count += 1
            except Exception:
                logger.debug("刷新快照失败: %s", account.id, exc_info=True)
        return count

    def _refresh_single_snapshot(
        self, account: PoolAccount, now_utc: datetime,
    ) -> None:
        """刷新单个账号的预算快照。"""
        day_start = _day_start_utc(account.timezone, now_utc)
        week_start = _week_start_utc(account.timezone, now_utc)

        day_tokens = self._sum_tokens_since(account.id, day_start)
        week_tokens = self._sum_tokens_since(account.id, week_start)

        daily_remaining = max(0, account.daily_budget_tokens - day_tokens)
        weekly_remaining = max(0, account.weekly_budget_tokens - week_tokens)

        now = _now_iso()
        self._conn.execute(
            """INSERT OR REPLACE INTO pool_budget_snapshots
               (pool_account_id, day_window_tokens, week_window_tokens,
                daily_remaining, weekly_remaining, snapshot_at)
               VALUES (?,?,?,?,?,?)""",
            (account.id, day_tokens, week_tokens, daily_remaining, weekly_remaining, now),
        )
        self._conn.commit()

    def _sum_tokens_since(self, account_id: str, since: datetime) -> int:
        """聚合指定时间之后的 token 用量。"""
        since_iso = since.isoformat()
        row = self._conn.execute(
            "SELECT COALESCE(SUM(total_tokens), 0) as total "
            "FROM pool_usage_ledger "
            "WHERE pool_account_id = ? AND created_at >= ? AND outcome = 'success'",
            (account_id, since_iso),
        ).fetchone()
        if row is None:
            return 0
        d = _row_to_dict(row)
        return int(d.get("total", 0) or 0)

    def get_snapshot(self, account_id: str) -> PoolBudgetSnapshot | None:
        """获取单个账号的预算快照。"""
        row = self._conn.execute(
            "SELECT * FROM pool_budget_snapshots WHERE pool_account_id = ?",
            (account_id,),
        ).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        return PoolBudgetSnapshot(
            pool_account_id=d["pool_account_id"],
            day_window_tokens=int(d.get("day_window_tokens", 0) or 0),
            week_window_tokens=int(d.get("week_window_tokens", 0) or 0),
            daily_remaining=int(d.get("daily_remaining", 0) or 0),
            weekly_remaining=int(d.get("weekly_remaining", 0) or 0),
            snapshot_at=d.get("snapshot_at", ""),
        )

    # ── 号池总览 ──────────────────────────────────────────────

    def get_summary(self) -> list[PoolAccountSummary]:
        """号池总览：所有账号 + 快照 + 激活状态。"""
        accounts = self.list_accounts()
        active_mappings = self.list_manual_active()
        active_ids = {m.pool_account_id for m in active_mappings}

        summaries: list[PoolAccountSummary] = []
        for account in accounts:
            snapshot = self.get_snapshot(account.id)
            summaries.append(PoolAccountSummary(
                account=account,
                snapshot=snapshot,
                is_active=account.id in active_ids,
            ))
        return summaries

    # ── 探测 ──────────────────────────────────────────────────

    async def probe_account(self, account_id: str) -> dict[str, Any]:
        """探测池账号连通性（发送最小请求）。"""
        account = self.get_account(account_id)
        if not account:
            return {"status": "error", "message": "账号不存在"}

        if self._cred_store is None:
            return {"status": "error", "message": "CredentialStore 未初始化"}

        profile_name = self.get_pool_profile_name(account_id)
        # 按 profile_name 精确获取凭证（多池账号安全）
        _get_by_name = getattr(self._cred_store, "get_profile_by_name", None)
        if _get_by_name is not None:
            profile = _get_by_name(POOL_USER_ID, account.provider, profile_name)
        else:
            profile = self._cred_store.get_active_profile(POOL_USER_ID, account.provider)
            if profile is not None and profile.profile_name != profile_name:
                profile = None
        if profile is None:
            return {"status": "error", "message": "未找到池账号凭证，请先导入 OAuth token"}

        if not profile.access_token:
            return {"status": "error", "message": "池账号 access_token 为空"}

        from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
        provider = OpenAICodexProvider()
        api_key, base_url = provider.get_api_credential(profile.access_token)

        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{base_url}/responses",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-5.1-codex-mini",
                        "input": "Say OK",
                        "max_output_tokens": 5,
                    },
                )
            if resp.status_code == 200:
                self.update_health_signal(account_id, "ok", 1.0)
                return {"status": "ok", "message": "连通性正常", "http_status": 200}
            else:
                body = resp.text[:200]
                return {
                    "status": "error",
                    "message": f"HTTP {resp.status_code}",
                    "http_status": resp.status_code,
                    "detail": body,
                }
        except Exception as e:
            return {"status": "error", "message": str(e)[:200]}

    # ── 内部工具 ──────────────────────────────────────────────

    @staticmethod
    def _row_to_account(row: Any) -> PoolAccount:
        d = _row_to_dict(row)
        return PoolAccount(
            id=d["id"],
            label=d.get("label", ""),
            provider=d.get("provider", "openai-codex"),
            account_id=d.get("account_id", ""),
            plan_type=d.get("plan_type", ""),
            status=d.get("status", "active"),
            daily_budget_tokens=int(d.get("daily_budget_tokens", 0) or 0),
            weekly_budget_tokens=int(d.get("weekly_budget_tokens", 0) or 0),
            timezone=d.get("timezone", "Asia/Shanghai"),
            health_signal=d.get("health_signal", "ok"),
            health_confidence=float(d.get("health_confidence", 0.0) or 0.0),
            health_updated_at=d.get("health_updated_at", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
