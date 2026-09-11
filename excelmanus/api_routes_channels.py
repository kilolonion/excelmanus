"""渠道协同启动、配置、热启停与飞书 webhook API。

从 api.py 抽出的独立路由模块。运行时状态只从 api_app_state 读取，
禁止 ``from excelmanus.api import _config`` 反向导入。
由 api.py 在 create_app 中 include_router 注册。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from excelmanus.api_app_state import (
    error_json_response as _error_json_response,
    get_channel_launcher,
    get_config_store,
    set_channel_launcher,
)
from excelmanus.logger import get_logger

if TYPE_CHECKING:
    from excelmanus.channels.rate_limit import RateLimitConfig

logger = get_logger("api.channels")

router = APIRouter()


def _fire_and_forget(coro: Any, *, name: str = "bridge_notify") -> None:
    """安全地 fire-and-forget 一个协程，捕获异常避免 'Task exception was never retrieved' 警告。"""
    from excelmanus.engine_utils import fire_and_forget
    fire_and_forget(coro, name=name)


@router.get("/api/v1/channels")
async def channels_status() -> dict:
    """查询渠道协同启动状态（含每个渠道的详细状态和配置信息）。"""
    from excelmanus.channels.config_store import CHANNEL_CREDENTIAL_FIELDS
    from excelmanus.channels.launcher import ChannelLauncher, _CHANNEL_BUILDERS

    _channel_launcher = get_channel_launcher()
    _config_store = get_config_store()

    statuses: dict[str, str] = {}
    if _channel_launcher is not None:
        statuses = _channel_launcher.all_channel_status()

    # 加载持久化配置
    saved_configs: dict = {}
    if _config_store is not None:
        try:
            from excelmanus.channels.config_store import ChannelConfigStore
            ccs = ChannelConfigStore(_config_store)
            for name, cfg in ccs.load_all().items():
                # 脱敏：secret 字段仅返回是否已填
                masked_creds: dict[str, str] = {}
                fields = CHANNEL_CREDENTIAL_FIELDS.get(name, [])
                secret_keys = {f["key"] for f in fields if f.get("secret")}
                for k, v in cfg.credentials.items():
                    if k in secret_keys and v:
                        masked_creds[k] = (v[:4] + "••••••••" + v[-4:]) if len(v) > 12 else "••••••••"
                    else:
                        masked_creds[k] = v
                saved_configs[name] = {
                    "enabled": cfg.enabled,
                    "credentials": masked_creds,
                    "has_required": cfg.has_required_credentials(),
                    "missing_fields": cfg.get_missing_fields(),
                    "updated_at": cfg.updated_at,
                }
        except Exception:
            logger.debug("加载渠道配置失败", exc_info=True)

    # 构建每个渠道的完整信息
    all_channels = sorted(set(list(_CHANNEL_BUILDERS.keys()) + list(saved_configs.keys())))
    channel_details = []
    for ch in all_channels:
        dep_ok, dep_hint = ChannelLauncher.check_dependency(ch)
        detail: dict = {
            "name": ch,
            "status": statuses.get(ch, "stopped"),
            "supported": ch in _CHANNEL_BUILDERS,
            "fields": CHANNEL_CREDENTIAL_FIELDS.get(ch, []),
            "dep_installed": dep_ok,
            "install_hint": dep_hint,
        }
        if ch in saved_configs:
            detail.update(saved_configs[ch])
        else:
            detail["enabled"] = False
            detail["credentials"] = {}
            detail["has_required"] = False
            detail["missing_fields"] = [
                f["key"] for f in CHANNEL_CREDENTIAL_FIELDS.get(ch, []) if f.get("required")
            ]
        channel_details.append(detail)

    # 读取 require_bind 状态（env var > config_kv > false）
    env_rb = os.environ.get("EXCELMANUS_CHANNEL_REQUIRE_BIND", "").strip().lower()
    if env_rb:
        require_bind = env_rb in ("1", "true", "yes")
        require_bind_source = "env"
    elif _config_store is not None:
        db_rb = _config_store.get("channel_require_bind", "")
        require_bind = db_rb.strip().lower() in ("1", "true", "yes") if db_rb else False
        require_bind_source = "config"
    else:
        require_bind = False
        require_bind_source = "default"

    # 读取速率限制配置（env > DB > defaults）
    from excelmanus.channels.rate_limit import RateLimitConfig
    rl_cfg = RateLimitConfig.from_store(_config_store)
    rl_env_overrides = RateLimitConfig.env_overrides()

    # ── 读取扩展渠道设置 ──

    def _read_setting(db_key: str, env_key: str, default: str = "") -> tuple[str, str]:
        """返回 (value, source)。source: 'env' | 'config' | 'default'。"""
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            return env_val, "env"
        if _config_store is not None:
            db_val = _config_store.get(db_key, "")
            if db_val:
                return db_val.strip(), "config"
        return default, "default"

    # 访问控制
    admin_users_val, admin_users_src = _read_setting(
        "channel_admin_users", "EXCELMANUS_CHANNEL_ADMINS",
    )
    group_policy_val, group_policy_src = _read_setting(
        "channel_group_policy", "EXCELMANUS_CHANNEL_GROUP_POLICY", "auto",
    )
    group_whitelist_val, _ = _read_setting(
        "channel_group_whitelist", "", "",
    )
    group_blacklist_val, _ = _read_setting(
        "channel_group_blacklist", "", "",
    )
    allowed_users_val, _ = _read_setting(
        "channel_allowed_users", "", "",
    )

    # 行为设置
    default_concurrency_val, default_concurrency_src = _read_setting(
        "channel_default_concurrency", "EXCELMANUS_CHANNEL_DEFAULT_CONCURRENCY", "queue",
    )
    default_chat_mode_val, default_chat_mode_src = _read_setting(
        "channel_default_chat_mode", "EXCELMANUS_CHANNEL_DEFAULT_CHAT_MODE", "write",
    )
    public_url_val, public_url_src = _read_setting(
        "channel_public_url", "EXCELMANUS_PUBLIC_URL", "",
    )

    # 输出调优
    tg_edit_interval_min_val, _ = _read_setting("channel_tg_edit_interval_min", "", "1.5")
    tg_edit_interval_max_val, _ = _read_setting("channel_tg_edit_interval_max", "", "3.0")
    qq_progressive_chars_val, _ = _read_setting("channel_qq_progressive_chars", "", "200")
    qq_progressive_interval_val, _ = _read_setting("channel_qq_progressive_interval", "", "3.0")
    feishu_update_interval_val, _ = _read_setting("channel_feishu_update_interval", "", "0.5")

    # 构建 env_overrides 映射（前端用于判断锁定状态）
    settings_env_overrides: dict[str, str] = {}
    if admin_users_src == "env":
        settings_env_overrides["admin_users"] = "EXCELMANUS_CHANNEL_ADMINS"
    if group_policy_src == "env":
        settings_env_overrides["group_policy"] = "EXCELMANUS_CHANNEL_GROUP_POLICY"
    if default_concurrency_src == "env":
        settings_env_overrides["default_concurrency"] = "EXCELMANUS_CHANNEL_DEFAULT_CONCURRENCY"
    if default_chat_mode_src == "env":
        settings_env_overrides["default_chat_mode"] = "EXCELMANUS_CHANNEL_DEFAULT_CHAT_MODE"
    if public_url_src == "env":
        settings_env_overrides["public_url"] = "EXCELMANUS_PUBLIC_URL"

    return {
        "enabled": _channel_launcher is not None,
        "channels": _channel_launcher.active_channels if _channel_launcher is not None else [],
        "details": channel_details,
        "require_bind": require_bind,
        "require_bind_source": require_bind_source,
        "rate_limit": rl_cfg.to_dict(),
        "rate_limit_env_overrides": rl_env_overrides,
        # 扩展设置
        "settings": {
            "admin_users": admin_users_val,
            "group_policy": group_policy_val,
            "group_whitelist": group_whitelist_val,
            "group_blacklist": group_blacklist_val,
            "allowed_users": allowed_users_val,
            "default_concurrency": default_concurrency_val,
            "default_chat_mode": default_chat_mode_val,
            "public_url": public_url_val,
            "tg_edit_interval_min": tg_edit_interval_min_val,
            "tg_edit_interval_max": tg_edit_interval_max_val,
            "qq_progressive_chars": qq_progressive_chars_val,
            "qq_progressive_interval": qq_progressive_interval_val,
            "feishu_update_interval": feishu_update_interval_val,
        },
        "settings_env_overrides": settings_env_overrides,
    }


@router.put("/api/v1/channels/settings")
async def update_channel_settings(request: Request) -> JSONResponse:
    """更新渠道全局设置（管理员）。

    请求体支持以下字段（均可选，仅传入需修改的）：
    - require_bind: bool — 强制绑定前端账号
    - admin_users: str — 管理员用户 ID（逗号分隔）
    - group_policy: str — 群聊策略 deny/allow/whitelist/blacklist/auto
    - group_whitelist: str — 群白名单 JSON 数组
    - group_blacklist: str — 群黑名单 JSON 数组
    - allowed_users: str — 允许用户 JSON 数组
    - default_concurrency: str — 默认并发模式 queue/steer/guide
    - default_chat_mode: str — 默认聊天模式 write/read/plan
    - public_url: str — 公开访问 URL
    - tg_edit_interval_min: str — Telegram 编辑间隔最小值
    - tg_edit_interval_max: str — Telegram 编辑间隔最大值
    - qq_progressive_chars: str — QQ 渐进发送字符阈值
    - qq_progressive_interval: str — QQ 渐进发送间隔
    - feishu_update_interval: str — 飞书卡片更新间隔
    """
    _config_store = get_config_store()
    _channel_launcher = get_channel_launcher()

    if _config_store is None:
        return _error_json_response(503, "数据库未初始化。")

    body = await request.json()
    updated: list[str] = []
    locked: list[str] = []

    # ── require_bind ──
    if "require_bind" in body:
        env_rb = os.environ.get("EXCELMANUS_CHANNEL_REQUIRE_BIND", "").strip()
        if env_rb:
            locked.append("require_bind")
        else:
            val = "true" if body["require_bind"] else "false"
            _config_store.set("channel_require_bind", val)
            updated.append("require_bind")
            logger.info("渠道强制绑定设置已更新: %s", val)

    # ── 访问控制字段 ──

    # 检查 env 锁定的辅助函数
    _ENV_LOCK_MAP: dict[str, str] = {
        "admin_users": "EXCELMANUS_CHANNEL_ADMINS",
        "group_policy": "EXCELMANUS_CHANNEL_GROUP_POLICY",
        "default_concurrency": "EXCELMANUS_CHANNEL_DEFAULT_CONCURRENCY",
        "default_chat_mode": "EXCELMANUS_CHANNEL_DEFAULT_CHAT_MODE",
        "public_url": "EXCELMANUS_PUBLIC_URL",
    }
    _DB_KEY_MAP: dict[str, str] = {
        "admin_users": "channel_admin_users",
        "group_policy": "channel_group_policy",
        "group_whitelist": "channel_group_whitelist",
        "group_blacklist": "channel_group_blacklist",
        "allowed_users": "channel_allowed_users",
        "default_concurrency": "channel_default_concurrency",
        "default_chat_mode": "channel_default_chat_mode",
        "public_url": "channel_public_url",
        "tg_edit_interval_min": "channel_tg_edit_interval_min",
        "tg_edit_interval_max": "channel_tg_edit_interval_max",
        "qq_progressive_chars": "channel_qq_progressive_chars",
        "qq_progressive_interval": "channel_qq_progressive_interval",
        "feishu_update_interval": "channel_feishu_update_interval",
    }

    # 验证枚举值
    _VALID_GROUP_POLICIES = {"deny", "allow", "whitelist", "blacklist", "auto"}
    _VALID_CONCURRENCY = {"queue", "steer", "guide"}
    _VALID_CHAT_MODES = {"write", "read", "plan"}

    for field, db_key in _DB_KEY_MAP.items():
        if field not in body:
            continue

        # 检查 env 锁定
        env_var = _ENV_LOCK_MAP.get(field, "")
        if env_var and os.environ.get(env_var, "").strip():
            locked.append(field)
            continue

        value = body[field]

        # 枚举验证
        if field == "group_policy" and value not in _VALID_GROUP_POLICIES:
            return _error_json_response(400, f"无效的群聊策略: {value}，可选值: {', '.join(_VALID_GROUP_POLICIES)}")
        if field == "default_concurrency" and value not in _VALID_CONCURRENCY:
            return _error_json_response(400, f"无效的并发模式: {value}，可选值: {', '.join(_VALID_CONCURRENCY)}")
        if field == "default_chat_mode" and value not in _VALID_CHAT_MODES:
            return _error_json_response(400, f"无效的聊天模式: {value}，可选值: {', '.join(_VALID_CHAT_MODES)}")

        # 数值验证
        if field in ("tg_edit_interval_min", "tg_edit_interval_max", "qq_progressive_interval", "feishu_update_interval"):
            try:
                fval = float(value)
                if fval < 0.1 or fval > 60.0:
                    return _error_json_response(400, f"字段 {field} 超出合理范围 (0.1-60.0): {value}")
                value = str(fval)
            except (ValueError, TypeError):
                return _error_json_response(400, f"字段 {field} 必须为数字: {value}")
        if field == "qq_progressive_chars":
            try:
                ival = int(value)
                if ival < 50 or ival > 5000:
                    return _error_json_response(400, f"字段 {field} 超出合理范围 (50-5000): {value}")
                value = str(ival)
            except (ValueError, TypeError):
                return _error_json_response(400, f"字段 {field} 必须为整数: {value}")

        _config_store.set(db_key, str(value))
        updated.append(field)

    if updated:
        logger.info("渠道设置已更新: %s", updated)

    # 热更新运行中的 handler
    if _channel_launcher is not None:
        _propagate_channel_settings()

    result: dict = {"status": "ok", "updated_fields": updated}
    if locked:
        result["locked_fields"] = locked
        result["message"] = f"以下字段被环境变量锁定: {', '.join(locked)}"
    return JSONResponse(result)


@router.put("/api/v1/channels/rate-limit")
async def update_rate_limit_settings(request: Request) -> JSONResponse:
    """更新渠道速率限制配置（管理员）。

    请求体: {"chat_per_minute": 5, "chat_per_hour": 30, ...}
    仅传入需要修改的字段，未传入的保持不变。
    环境变量锁定的字段不可通过此接口修改。
    """
    _config_store = get_config_store()
    _channel_launcher = get_channel_launcher()

    if _config_store is None:
        return _error_json_response(503, "数据库未初始化。")

    from excelmanus.channels.rate_limit import RateLimitConfig

    body = await request.json()

    # 加载当前持久化配置作为 base
    current = RateLimitConfig.from_store(_config_store)
    current_dict = current.to_dict()

    # 环境变量锁定的字段不允许修改
    env_overrides = RateLimitConfig.env_overrides()
    locked_fields = [k for k in body if k in env_overrides]

    # 合并：仅更新传入的非锁定字段
    valid_fields = set(current_dict.keys())
    updated_fields: list[str] = []
    for key, value in body.items():
        if key not in valid_fields:
            continue
        if key in env_overrides:
            continue  # 被环境变量锁定，跳过
        try:
            if key in ("reject_cooldown_seconds", "auto_ban_duration_seconds"):
                current_dict[key] = float(value)
            else:
                current_dict[key] = int(value)
            updated_fields.append(key)
        except (ValueError, TypeError):
            return _error_json_response(400, f"字段 {key} 的值无效: {value}")

    # 保存到 DB
    new_cfg = RateLimitConfig.from_dict(current_dict)
    RateLimitConfig.save_to_store(new_cfg, _config_store)
    logger.info("速率限制配置已更新: %s", updated_fields)

    # 动态更新运行中的渠道 Bot 的限流器
    if _channel_launcher is not None:
        _propagate_rate_limit_config(new_cfg)

    result: dict = {"status": "ok", "updated_fields": updated_fields}
    if locked_fields:
        result["locked_fields"] = locked_fields
        result["message"] = f"以下字段被环境变量锁定，未修改: {', '.join(locked_fields)}"
    return JSONResponse(content=result)


def _propagate_rate_limit_config(cfg: "RateLimitConfig") -> None:
    """将新的速率限制配置传播到所有运行中的渠道 Bot。"""
    _channel_launcher = get_channel_launcher()
    if _channel_launcher is None:
        return
    for name, handler in _channel_launcher._handlers.items():
        try:
            if hasattr(handler, "_rate_limiter"):
                handler._rate_limiter.config = cfg
                logger.debug("渠道 %s 速率限制配置已热更新", name)
        except Exception:
            logger.debug("渠道 %s 速率限制配置热更新失败", name, exc_info=True)


def _propagate_channel_settings() -> None:
    """将渠道扩展设置传播到所有运行中的 MessageHandler。

    读取 config_store 中的最新值并更新 handler 内部状态。
    仅处理可安全热更新的字段（default_concurrency 等）。
    """
    _channel_launcher = get_channel_launcher()
    _config_store = get_config_store()
    if _channel_launcher is None or _config_store is None:
        return
    for name, handler in _channel_launcher._handlers.items():
        try:
            # 默认并发模式
            dc = _config_store.get("channel_default_concurrency", "")
            if dc and dc in ("queue", "steer", "guide"):
                handler._default_concurrency = dc

            logger.debug("渠道 %s 扩展设置已热更新", name)
        except Exception:
            logger.debug("渠道 %s 扩展设置热更新失败", name, exc_info=True)


@router.put("/api/v1/channels/{channel_name}/config")
async def save_channel_config(channel_name: str, request: Request) -> JSONResponse:
    """保存单个渠道的配置（凭证 + 启用状态）。

    请求体: {"credentials": {"token": "xxx", ...}, "enabled": true}
    """
    _config_store = get_config_store()
    if _config_store is None:
        return _error_json_response(503, "数据库未初始化，无法保存渠道配置。")

    from excelmanus.channels.config_store import (
        CHANNEL_CREDENTIAL_FIELDS,
        ChannelConfig,
        ChannelConfigStore,
    )

    if channel_name not in CHANNEL_CREDENTIAL_FIELDS:
        return _error_json_response(400, f"不支持的渠道: {channel_name}")

    body = await request.json()
    credentials = body.get("credentials", {})
    enabled = body.get("enabled", False)

    # 合并：如果前端传来 "••••••••" 占位符，保留原值
    ccs = ChannelConfigStore(_config_store)
    existing = ccs.get(channel_name)
    if existing:
        fields = CHANNEL_CREDENTIAL_FIELDS.get(channel_name, [])
        secret_keys = {f["key"] for f in fields if f.get("secret")}
        for k in secret_keys:
            if "••••••••" in (credentials.get(k) or "") and existing.credentials.get(k):
                credentials[k] = existing.credentials[k]

    cfg = ChannelConfig(
        name=channel_name,
        enabled=bool(enabled),
        credentials=credentials,
    )
    ccs.save(cfg)

    logger.info("渠道 %s 配置已保存 (enabled=%s)", channel_name, enabled)
    return JSONResponse(content={
        "status": "ok",
        "channel": channel_name,
        "enabled": enabled,
        "has_required": cfg.has_required_credentials(),
        "missing_fields": cfg.get_missing_fields(),
    })


@router.delete("/api/v1/channels/{channel_name}/config")
async def delete_channel_config(channel_name: str, request: Request) -> JSONResponse:
    """删除单个渠道的持久化配置。"""
    _config_store = get_config_store()
    if _config_store is None:
        return _error_json_response(503, "数据库未初始化。")

    from excelmanus.channels.config_store import ChannelConfigStore
    ccs = ChannelConfigStore(_config_store)
    deleted = ccs.delete(channel_name)
    if not deleted:
        return _error_json_response(404, f"渠道 {channel_name} 无保存的配置。")

    logger.info("渠道 %s 配置已删除", channel_name)
    return JSONResponse(content={"status": "ok", "channel": channel_name})


@router.post("/api/v1/channels/{channel_name}/start")
async def start_channel(channel_name: str, request: Request) -> JSONResponse:
    """热启动单个渠道 Bot。

    使用已保存的持久化配置凭证启动，环境变量作为 fallback。
    """
    _channel_launcher = get_channel_launcher()
    _config_store = get_config_store()

    # 确保 launcher 存在
    if _channel_launcher is None:
        from excelmanus.channels.launcher import ChannelLauncher
        api_port = int(os.environ.get("EXCELMANUS_API_PORT", "8000"))
        _evt_bridge = getattr(request.app.state, "event_bridge", None)
        _channel_launcher = ChannelLauncher(
            [],
            api_port=api_port,
            event_bridge=_evt_bridge,
            config_store=_config_store,
        )
        set_channel_launcher(_channel_launcher)

    # 从持久化配置加载凭证
    credentials: dict[str, str] | None = None
    if _config_store is not None:
        try:
            from excelmanus.channels.config_store import ChannelConfigStore
            ccs = ChannelConfigStore(_config_store)
            cfg = ccs.get(channel_name)
            if cfg and cfg.credentials:
                credentials = cfg.credentials
                logger.debug(
                    "渠道 %s 加载到持久化凭证，字段: %s",
                    channel_name, list(credentials.keys()),
                )
            else:
                logger.warning(
                    "渠道 %s 无持久化凭证（cfg=%s），将依赖环境变量",
                    channel_name, cfg is not None,
                )
        except Exception:
            logger.debug("加载渠道 %s 持久化凭证失败", channel_name, exc_info=True)
    else:
        logger.warning("config_store 未初始化，渠道 %s 将仅使用环境变量凭证", channel_name)

    ok, msg = await _channel_launcher.start_channel(channel_name, credentials=credentials)
    if ok:
        return JSONResponse(content={"status": "ok", "message": msg})
    return _error_json_response(400, msg)


@router.post("/api/v1/channels/{channel_name}/stop")
async def stop_channel(channel_name: str, request: Request) -> JSONResponse:
    """热停止单个渠道 Bot。"""
    _channel_launcher = get_channel_launcher()
    if _channel_launcher is None:
        return _error_json_response(400, f"渠道 {channel_name} 未启动")

    ok, msg = await _channel_launcher.stop_channel(channel_name)
    if ok:
        return JSONResponse(content={"status": "ok", "message": msg})
    return _error_json_response(400, msg)


@router.post("/api/v1/channels/{channel_name}/test")
async def test_channel_config(channel_name: str, request: Request) -> JSONResponse:
    """测试渠道凭证是否有效（不启动 Bot）。

    请求体（可选）: {"credentials": {"token": "xxx"}}
    如不传则使用已保存的配置。
    """
    from excelmanus.channels.config_store import CHANNEL_CREDENTIAL_FIELDS

    if channel_name not in CHANNEL_CREDENTIAL_FIELDS:
        return _error_json_response(400, f"不支持的渠道: {channel_name}")

    _config_store = get_config_store()

    # 获取凭证（请求体 > 持久化配置 > 环境变量）
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    credentials = body.get("credentials", {})

    # 如果请求未传凭证，从持久化配置加载
    if not credentials and _config_store is not None:
        try:
            from excelmanus.channels.config_store import ChannelConfigStore
            ccs = ChannelConfigStore(_config_store)
            cfg = ccs.get(channel_name)
            if cfg:
                credentials = cfg.credentials
        except Exception:
            pass

    # 合并：如果前端传来 "••••••••" 占位符，从持久化配置还原原值
    if credentials and _config_store is not None:
        try:
            from excelmanus.channels.config_store import ChannelConfigStore
            ccs = ChannelConfigStore(_config_store)
            existing = ccs.get(channel_name)
            if existing:
                fields = CHANNEL_CREDENTIAL_FIELDS.get(channel_name, [])
                secret_keys = {f["key"] for f in fields if f.get("secret")}
                for k in secret_keys:
                    if "••••••••" in (credentials.get(k) or "") and existing.credentials.get(k):
                        credentials[k] = existing.credentials[k]
        except Exception:
            pass

    # 执行平台特定的凭证验证
    if channel_name == "telegram":
        token = credentials.get("token") or os.environ.get("EXCELMANUS_TG_TOKEN", "")
        if not token or "••••••••" in token:
            return _error_json_response(400, "未提供 Telegram Bot Token。")
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10, trust_env=True) as client:
                resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
                data = resp.json()
                if data.get("ok"):
                    bot_info = data.get("result", {})
                    return JSONResponse(content={
                        "status": "ok",
                        "message": f"Token 有效！Bot: @{bot_info.get('username', '?')} ({bot_info.get('first_name', '')})",
                        "bot_info": {
                            "username": bot_info.get("username"),
                            "name": bot_info.get("first_name"),
                        },
                    })
                return _error_json_response(400, f"Token 无效: {data.get('description', '未知错误')}")
        except Exception as e:
            hint = "（提示：如需代理访问 Telegram，请设置 HTTPS_PROXY 环境变量）"
            return _error_json_response(502, f"连接 Telegram API 失败: {e} {hint}")

    elif channel_name == "qq":
        app_id = credentials.get("app_id") or os.environ.get("EXCELMANUS_QQ_APPID", "")
        secret = credentials.get("secret") or os.environ.get("EXCELMANUS_QQ_SECRET", "")
        if not app_id or not secret or "••••••••" in secret:
            return _error_json_response(400, "未提供 QQ Bot AppID 或 AppSecret。")
        # QQ Bot 的鉴权较复杂（WebSocket），这里只做基础格式验证
        try:
            int(app_id)
        except ValueError:
            return _error_json_response(400, "AppID 格式无效（应为数字）。")
        return JSONResponse(content={
            "status": "ok",
            "message": f"凭证格式验证通过 (AppID: {app_id})。完整验证需启动 Bot。",
        })

    elif channel_name == "feishu":
        app_id = credentials.get("app_id") or os.environ.get("EXCELMANUS_FEISHU_APP_ID", "")
        app_secret = credentials.get("app_secret") or os.environ.get("EXCELMANUS_FEISHU_APP_SECRET", "")
        if not app_id or not app_secret or "••••••••" in (app_secret or ""):
            return _error_json_response(400, "未提供飞书 App ID 或 App Secret。")
        try:
            import httpx
            # 使用飞书 API 获取 tenant_access_token 验证凭证
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": app_id, "app_secret": app_secret},
                )
                data = resp.json()
                if data.get("code") == 0:
                    return JSONResponse(content={
                        "status": "ok",
                        "message": f"凭证有效！已获取 tenant_access_token (AppID: {app_id})",
                        "bot_info": {"app_id": app_id},
                    })
                return _error_json_response(
                    400,
                    f"凭证无效: {data.get('msg', '未知错误')} (code={data.get('code')})",
                )
        except Exception as e:
            return _error_json_response(502, f"连接飞书 API 失败: {e}")

    else:
        return JSONResponse(content={
            "status": "ok",
            "message": f"渠道 {channel_name} 的凭证验证尚未实现，请直接启动测试。",
        })


# ── 飞书 Webhook 回调 ─────────────────────────────────────

@router.post("/api/v1/channels/feishu/webhook")
async def feishu_webhook(request: Request) -> JSONResponse:
    """飞书事件订阅回调端点。

    支持：
    1. URL 验证（challenge-response）
    2. im.message.receive_v1 消息事件
    3. 卡片按钮回调（card.action.trigger）
    """
    try:
        body = await request.json()
    except Exception:
        return _error_json_response(400, "无效的请求体")

    # 1) URL 验证（飞书事件订阅配置时的 challenge 验证）
    if body.get("type") == "url_verification":
        challenge = body.get("challenge", "")
        return JSONResponse(content={"challenge": challenge})

    # 2) 获取 launcher 中的飞书 adapter 和 handler
    launcher = getattr(request.app.state, "channel_launcher", None)
    if launcher is None:
        return _error_json_response(503, "渠道启动器未初始化")

    feishu_adapter = launcher._apps.get("feishu")
    feishu_handler = launcher._handlers.get("feishu")
    if feishu_adapter is None or feishu_handler is None:
        return _error_json_response(503, "飞书渠道未启动，请先在设置中启动飞书渠道")

    # 3) 处理事件（v2.0 事件格式）
    header = body.get("header", {})
    event_type = header.get("event_type", "")
    event = body.get("event", {})

    if event_type == "im.message.receive_v1":
        from excelmanus.channels.feishu.handlers import handle_feishu_event
        # 异步处理，不阻塞 webhook 响应
        _fire_and_forget(
            handle_feishu_event(feishu_adapter, feishu_handler, event),
            name="feishu_message",
        )
        return JSONResponse(content={"code": 0, "msg": "ok"})

    # 4) 卡片按钮回调
    if event_type == "card.action.trigger":
        from excelmanus.channels.feishu.handlers import handle_feishu_card_action
        _fire_and_forget(
            handle_feishu_card_action(feishu_adapter, feishu_handler, event),
            name="feishu_card_action",
        )
        return JSONResponse(content={"code": 0, "msg": "ok"})

    # 5) v1.0 事件格式兼容（部分老版本飞书使用）
    if body.get("event") and not header:
        v1_event = body.get("event", {})
        v1_type = v1_event.get("type", "")
        if v1_type == "message":
            from excelmanus.channels.feishu.handlers import handle_feishu_event
            _fire_and_forget(
                handle_feishu_event(feishu_adapter, feishu_handler, v1_event),
                name="feishu_v1_message",
            )
            return JSONResponse(content={"code": 0, "msg": "ok"})

    logger.debug("飞书未处理的事件类型: %s", event_type or body.get("type", "unknown"))
    return JSONResponse(content={"code": 0, "msg": "ok"})
