"""号池管理 API 路由。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from excelmanus.auth.dependencies import require_admin
from excelmanus.auth.store import UserRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/pool", tags=["pool"])


def _get_pool_service(request: Request):
    """从 app.state 获取 PoolService 实例。"""
    svc = getattr(request.app.state, "pool_service", None)
    if svc is None:
        raise HTTPException(503, "号池服务未初始化或未启用")
    return svc


def _get_credential_store(request: Request):
    """从 app.state 获取 CredentialStore 实例。"""
    store = getattr(request.app.state, "credential_store", None)
    if store is None:
        raise HTTPException(503, "凭证存储未初始化")
    return store


# ── POST /accounts/oauth — 导入池账号 ────────────────────────


@router.post("/accounts/oauth")
async def create_pool_account_oauth(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """导入池账号（粘贴 OAuth token JSON）。

    Body:
    {
        "token_data": { ... },  // OpenAI Codex auth.json 格式
        "label": "账号A",
        "daily_budget_tokens": 500000,
        "weekly_budget_tokens": 3000000,
        "timezone": "Asia/Shanghai"
    }
    """
    svc = _get_pool_service(request)
    body = await request.json()

    token_data = body.get("token_data")
    if not token_data or not isinstance(token_data, dict):
        raise HTTPException(400, "缺少 token_data 字段（OAuth token JSON）")

    # 验证 token
    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
    provider = OpenAICodexProvider()
    try:
        credential = provider.validate_token_data(token_data)
    except ValueError as e:
        raise HTTPException(400, f"Token 验证失败: {e}")

    # 创建池账号
    account = svc.create_account(
        label=body.get("label", ""),
        provider="openai-codex",
        account_id=credential.account_id,
        plan_type=credential.plan_type,
        daily_budget_tokens=int(body.get("daily_budget_tokens", 0)),
        weekly_budget_tokens=int(body.get("weekly_budget_tokens", 0)),
        timezone_str=body.get("timezone", "Asia/Shanghai"),
    )

    # 存储 OAuth 凭证
    svc.store_oauth_credential(account.id, credential)

    logger.info(
        "管理员 %s 导入池账号: id=%s, account_id=%s, plan=%s",
        _admin.id, account.id, credential.account_id, credential.plan_type,
    )
    return {"status": "ok", "account": account.to_dict()}


# ── GET /accounts — 列出所有池账号 ────────────────────────────


@router.get("/accounts")
async def list_pool_accounts(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """列出所有池账号。"""
    svc = _get_pool_service(request)
    accounts = svc.list_accounts()
    return {"accounts": [a.to_dict() for a in accounts], "total": len(accounts)}


# ── PATCH /accounts/{id} — 更新池账号 ────────────────────────


@router.patch("/accounts/{account_id}")
async def update_pool_account(
    account_id: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """更新池账号字段（label/status/budget/timezone）。"""
    svc = _get_pool_service(request)
    body = await request.json()

    existing = svc.get_account(account_id)
    if not existing:
        raise HTTPException(404, "池账号不存在")

    updated = svc.update_account(account_id, **body)
    if updated is None:
        raise HTTPException(404, "池账号不存在")

    logger.info("管理员 %s 更新池账号 %s: %s", _admin.id, account_id, body)
    return {"status": "ok", "account": updated.to_dict()}


# ── POST /accounts/{id}/probe — 探测账号连通性 ────────────────


@router.post("/accounts/{account_id}/probe")
async def probe_pool_account(
    account_id: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """探测池账号连通性。"""
    svc = _get_pool_service(request)

    existing = svc.get_account(account_id)
    if not existing:
        raise HTTPException(404, "池账号不存在")

    result = await svc.probe_account(account_id)
    return result


# ── POST /manual-active — 设置人工激活映射 ────────────────────


@router.post("/manual-active")
async def set_manual_active(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """设置人工激活映射。

    Body:
    {
        "provider": "openai-codex",
        "model_pattern": "*",
        "pool_account_id": "..."
    }
    """
    svc = _get_pool_service(request)
    body = await request.json()

    provider = body.get("provider", "openai-codex")
    model_pattern = body.get("model_pattern", "*")
    pool_account_id = body.get("pool_account_id")

    if not pool_account_id:
        raise HTTPException(400, "缺少 pool_account_id")

    account = svc.get_account(pool_account_id)
    if not account:
        raise HTTPException(404, "池账号不存在")
    if account.status != "active":
        raise HTTPException(400, f"池账号状态为 {account.status}，无法激活")

    mapping = svc.set_manual_active(
        provider=provider,
        model_pattern=model_pattern,
        pool_account_id=pool_account_id,
        activated_by=_admin.id,
    )

    logger.info(
        "管理员 %s 设置人工激活: provider=%s, pattern=%s, account=%s",
        _admin.id, provider, model_pattern, pool_account_id,
    )
    return {"status": "ok", "mapping": mapping.to_dict()}


# ── GET /manual-active — 查看当前激活映射 ────────────────────


@router.get("/manual-active")
async def list_manual_active(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """查看所有人工激活映射。"""
    svc = _get_pool_service(request)
    mappings = svc.list_manual_active()
    return {"mappings": [m.to_dict() for m in mappings], "total": len(mappings)}


# ── GET /summary — 号池总览 ──────────────────────────────────


@router.get("/summary")
async def pool_summary(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """号池总览（所有账号 + 预算快照 + 激活状态）。"""
    svc = _get_pool_service(request)
    summaries = svc.get_summary()
    return {
        "accounts": [s.to_dict() for s in summaries],
        "total": len(summaries),
    }


# ══════════════════════════════════════════════════════════════
# 自动轮换管理 API
# ══════════════════════════════════════════════════════════════


def _get_auto_rotate_service(request: Request):
    """从 app.state 获取 PoolAutoRotateService 实例。"""
    svc = getattr(request.app.state, "pool_auto_rotate_service", None)
    if svc is None:
        raise HTTPException(503, "自动轮换服务未初始化或未启用")
    return svc


# ── POST /auto/policies — 创建/更新策略 ──────────────────────


@router.post("/auto/policies")
async def upsert_auto_policy(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """创建或更新自动轮换策略。

    Body:
    {
        "provider": "openai-codex",
        "model_pattern": "*",
        "enabled": true,
        "low_watermark": 0.15,
        "rate_limit_threshold": 3,
        "transient_threshold": 5,
        "error_window_minutes": 5,
        "cooldown_seconds": 300,
        "fallback_to_default": true
    }
    """
    auto_svc = _get_auto_rotate_service(request)
    body = await request.json()

    # 从 app config 读取 P3 默认值（未配置时回退硬编码值）
    _cfg = getattr(request.app.state, "config", None)
    _def_hysteresis = getattr(_cfg, "pool_auto_hysteresis_delta", 0.12) if _cfg else 0.12
    _def_dwell = getattr(_cfg, "pool_auto_min_dwell_seconds", 180) if _cfg else 180
    _def_breaker_open = getattr(_cfg, "pool_auto_breaker_open_seconds", 120) if _cfg else 120

    policy = auto_svc.upsert_policy(
        provider=body.get("provider", "openai-codex"),
        model_pattern=body.get("model_pattern", "*"),
        enabled=body.get("enabled", True),
        low_watermark=float(body.get("low_watermark", 0.15)),
        rate_limit_threshold=int(body.get("rate_limit_threshold", 3)),
        transient_threshold=int(body.get("transient_threshold", 5)),
        error_window_minutes=int(body.get("error_window_minutes", 5)),
        cooldown_seconds=int(body.get("cooldown_seconds", 300)),
        fallback_to_default=body.get("fallback_to_default", True),
        hysteresis_delta=float(body.get("hysteresis_delta", _def_hysteresis)),
        min_dwell_seconds=int(body.get("min_dwell_seconds", _def_dwell)),
        breaker_open_seconds=int(body.get("breaker_open_seconds", _def_breaker_open)),
    )

    logger.info(
        "管理员 %s 更新自动轮换策略: provider=%s, pattern=%s",
        _admin.id, policy.provider, policy.model_pattern,
    )
    return {"status": "ok", "policy": policy.to_dict()}


# ── GET /auto/policies — 列出所有策略 ────────────────────────


@router.get("/auto/policies")
async def list_auto_policies(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """列出所有自动轮换策略。"""
    auto_svc = _get_auto_rotate_service(request)
    policies = auto_svc.list_policies()
    return {"policies": [p.to_dict() for p in policies], "total": len(policies)}


# ── POST /auto/run — 手动触发一次评估 ────────────────────────


@router.post("/auto/run")
async def run_auto_evaluate(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """手动触发一次自动轮换评估。"""
    auto_svc = _get_auto_rotate_service(request)
    results = await auto_svc.evaluate_all_policies()
    logger.info("管理员 %s 手动触发自动轮换评估: %d 个策略", _admin.id, len(results))
    return {"status": "ok", "results": results}


# ── GET /auto/events — 查看轮换审计 ──────────────────────────


@router.get("/auto/events")
async def list_rotation_events(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
    limit: int = 50,
    provider: str | None = None,
    model_pattern: str | None = None,
) -> Any:
    """查看轮换审计事件。"""
    auto_svc = _get_auto_rotate_service(request)
    events = auto_svc.list_events(
        limit=min(limit, 200),
        provider=provider,
        model_pattern=model_pattern,
    )
    return {"events": [e.to_dict() for e in events], "total": len(events)}


# ══════════════════════════════════════════════════════════════
# P3：稳态治理 API
# ══════════════════════════════════════════════════════════════


# ── PATCH /auto/scopes/{provider}/{model_pattern}/mode ──────


@router.patch("/auto/scopes/{provider}/{model_pattern}/mode")
async def set_scope_mode(
    provider: str,
    model_pattern: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """切换 scope 运行模式。

    Body: {"mode": "auto"|"manual_locked"|"frozen"}
    """
    auto_svc = _get_auto_rotate_service(request)
    body = await request.json()
    mode = body.get("mode", "auto")
    if mode not in ("auto", "manual_locked", "frozen"):
        raise HTTPException(400, f"无效的 mode: {mode}")
    state = auto_svc.set_scope_mode(provider, model_pattern, mode)
    logger.info(
        "管理员 %s 切换 scope 模式: %s/%s → %s",
        _admin.id, provider, model_pattern, mode,
    )
    return {"status": "ok", "state": state.to_dict()}


# ── GET /auto/scopes/{provider}/{model_pattern}/state ───────


@router.get("/auto/scopes/{provider}/{model_pattern}/state")
async def get_scope_state(
    provider: str,
    model_pattern: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """查看 scope 当前运行状态。"""
    auto_svc = _get_auto_rotate_service(request)
    state = auto_svc.get_scope_state(provider, model_pattern)
    if state is None:
        return {"state": None}
    return {"state": state.to_dict()}


# ── POST /auto/scopes/{provider}/{model_pattern}/dry-run ────


@router.post("/auto/scopes/{provider}/{model_pattern}/dry-run")
async def dry_run_evaluate(
    provider: str,
    model_pattern: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """执行评估但不执行，返回 would-be 决策结果。"""
    auto_svc = _get_auto_rotate_service(request)
    result = await auto_svc.evaluate_scope(
        provider, model_pattern, trigger="dry_run", dry_run=True,
    )
    return {"status": "ok", "result": result}


# ── GET /auto/metrics ─────────────────────────────────────


def _get_metrics_aggregator(request: Request):
    """从 app.state 获取 MetricsAggregator 实例。"""
    agg = getattr(request.app.state, "pool_metrics_aggregator", None)
    if agg is None:
        raise HTTPException(503, "指标聚合器未初始化")
    return agg


@router.get("/auto/metrics")
async def list_rotation_metrics(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
    provider: str | None = None,
    model_pattern: str | None = None,
    minutes: int = 60,
) -> Any:
    """查询分钟级运行指标。"""
    agg = _get_metrics_aggregator(request)
    metrics = agg.query(
        provider=provider,
        model_pattern=model_pattern,
        minutes=min(minutes, 1440),
    )
    return {"metrics": [m.to_dict() for m in metrics], "total": len(metrics)}


# ── GET /auto/breakers ────────────────────────────────────


def _get_breaker_manager(request: Request):
    """从 app.state 获取 BreakerManager 实例。"""
    mgr = getattr(request.app.state, "pool_breaker_manager", None)
    if mgr is None:
        raise HTTPException(503, "熔断器管理器未初始化")
    return mgr


@router.get("/auto/breakers")
async def list_breakers(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """列出所有非 closed 的熔断器。"""
    mgr = _get_breaker_manager(request)
    breakers = mgr.list_breakers()
    return {"breakers": [b.to_dict() for b in breakers], "total": len(breakers)}
