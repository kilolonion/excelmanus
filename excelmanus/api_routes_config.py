"""模型 / thinking / config / settings API。

从 api.py 抽出的独立路由模块。运行时状态只从 api_app_state 读取，
禁止 ``from excelmanus.api import _config`` 反向导入。
由 api.py 在 create_app 中 include_router 注册。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from excelmanus.api_app_state import (
    _get_probe_job_mgr,
    _list_available_model_names,
    _sync_config_profiles_from_db,
    _user_config_store,
    apply_profile_to_config,
    error_json_response as _error_json_response,
    get_config,
    get_config_store,
    get_session_manager,
    is_placeholder_model_profile,
    set_config_incomplete,
    set_restart_reason,
)
from excelmanus.api_sse import sse_format as _sse_format
from excelmanus.config import format_deprecated_model_message
from excelmanus.logger import get_logger

logger = get_logger("api.config")

router = APIRouter()


def _supports_vision_for(model: str, base_url: str) -> bool:
    """给模型列表带上与引擎一致的视觉推断，供新对话在 engine 创建前使用。"""
    from excelmanus.vision_capability import infer_vision_capable

    probe: bool | None = None
    config = get_config()
    override = getattr(config, "main_model_vision", "auto") if config is not None else "auto"
    sm = get_session_manager()
    db = sm.database if sm is not None else None
    if db is not None:
        try:
            from excelmanus.model_probe import load_capabilities
            caps = load_capabilities(db, model, base_url)
            if caps is not None:
                probe = caps.supports_vision
        except Exception:
            logger.debug("模型列表视觉推断加载 probe 失败", exc_info=True)
    return infer_vision_capable(model, override=override, probe=probe)


class ModelSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


@router.get("/api/v1/models")
async def list_models(request: Request) -> JSONResponse:
    """获取可用模型列表（含多模型配置档案）。

    注意：模型列表为只读信息，不需要管理员权限，所有已认证用户均可访问。
    """
    assert get_config() is not None, "服务未初始化"
    user_cfg = _user_config_store()
    active_name = user_cfg.get_active_model() if user_cfg is not None else None
    db_profiles = get_config_store().list_profiles() if get_config_store() else []

    models: list[dict] = []
    for p in db_profiles:
        if is_placeholder_model_profile(
            p.get("name", ""), p.get("model", ""), p.get("base_url", ""),
        ):
            continue
        models.append({
            "name": p["name"],
            "model": p["model"],
            "display_name": p.get("name", ""),
            "description": p.get("description", ""),
            "active": p["name"] == active_name,
            "base_url": p.get("base_url", ""),
            "supports_vision": _supports_vision_for(
                p["model"], p.get("base_url") or "",
            ),
        })
    if models and not any(m["active"] for m in models):
        models[0]["active"] = True

    return JSONResponse(content={"models": models})


async def _activate_named_profile(name: str) -> JSONResponse | None:
    """校验并激活档案。成功返回 None，失败返回错误响应。"""
    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider

    store = get_config_store()
    profile = store.get_profile(name) if store is not None else None
    if profile is None and OpenAICodexProvider.is_codex_profile_name(name):
        profile = {"name": name, "model": name}
    if profile is None:
        if store is None:
            return _error_json_response(503, "配置存储未初始化")
        available_names = _list_available_model_names()
        return _error_json_response(
            404,
            f"未找到模型 {name!r}。可用模型：{', '.join(available_names)}",
        )
    deprecated = _deprecated_model_error_response(
        profile.get("model", ""),
        prefix=f"模型 {name!r} 使用了已弃用 Model ID。",
    )
    if deprecated is not None:
        return deprecated

    user_cfg = _user_config_store()
    if user_cfg is not None:
        user_cfg.set_active_model(name)
    apply_profile_to_config(name)

    if get_session_manager() is None:
        return None
    sessions = await get_session_manager().list_sessions()
    for session_info in sessions:
        try:
            engine = get_session_manager().get_engine(session_info["id"])
            if engine is not None:
                engine.switch_model(name)
        except Exception:
            pass

    db = get_session_manager().database
    if db is not None:
        try:
            from excelmanus.model_probe import load_capabilities

            for session_info in sessions:
                engine = get_session_manager().get_engine(session_info["id"])
                if engine is not None:
                    caps = load_capabilities(
                        db, engine.current_model, engine.active_base_url,
                    )
                    if caps is not None:
                        engine.set_model_capabilities(caps)
        except Exception:
            logger.debug("模型切换后加载能力缓存失败", exc_info=True)
    return None


@router.put("/api/v1/models/active")
async def switch_model(request: ModelSwitchRequest, raw_request: Request) -> JSONResponse:
    """切换当前激活模型并持久化，同时同步所有活跃会话。"""
    assert get_config() is not None, "服务未初始化"

    name = request.name.strip()
    if not name or name.lower() == "default":
        return _error_json_response(400, "请指定模型档案名称。")
    if is_placeholder_model_profile(name):
        return _error_json_response(400, "不能激活测试占位模型。")

    err = await _activate_named_profile(name)
    if err is not None:
        return err
    return JSONResponse(content={"message": f"模型已切换为 {name}"})


# ── Thinking 配置 API ──────────────────────────────────


class ThinkingConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effort: str | None = None  # none|minimal|low|medium|high|xhigh|max
    budget: int | None = None  # 精确 token 预算（0 = 使用 effort 换算）


@router.get("/api/v1/thinking")
async def get_thinking_config(raw_request: Request) -> JSONResponse:
    """获取当前 thinking 配置（等级 + 预算）。"""
    if get_session_manager() is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    sessions = await get_session_manager().list_sessions()
    # 取第一个活跃 session 的 thinking_config
    for s in sessions:
        engine = get_session_manager().get_engine(s["id"])
        if engine is not None:
            tc = engine.thinking_config
            return JSONResponse(content={
                "effort": tc.effort,
                "budget": tc.budget_tokens,
                "effective_budget": tc.effective_budget(),
            })
    # 回退到全局配置
    assert get_config() is not None
    return JSONResponse(content={
        "effort": get_config().thinking_effort,
        "budget": get_config().thinking_budget,
        "effective_budget": 0,
    })


@router.put("/api/v1/thinking")
async def set_thinking_config(request: ThinkingConfigRequest, raw_request: Request) -> JSONResponse:
    """设置 thinking 等级和/或预算，同步到所有活跃会话。"""
    if get_session_manager() is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    from excelmanus.engine import _EFFORT_RATIOS
    if request.effort is not None and request.effort not in _EFFORT_RATIOS:
        return _error_json_response(400, f"无效的 effort 值: {request.effort!r}。可选: {', '.join(sorted(_EFFORT_RATIOS))}")
    sessions = await get_session_manager().list_sessions()
    updated = 0
    result_tc = None
    for s in sessions:
        engine = get_session_manager().get_engine(s["id"])
        if engine is not None:
            engine.set_thinking_config(effort=request.effort, budget=request.budget)
            result_tc = engine.thinking_config
            updated += 1

    if result_tc is None:
        return _error_json_response(404, "无活跃会话。")

    return JSONResponse(content={
        "effort": result_tc.effort,
        "budget": result_tc.budget_tokens,
        "effective_budget": result_tc.effective_budget(),
        "sessions_updated": updated,
    })


# ── 模型配置管理 API（正式仓 config.env 持久化） ──────────────────────

_MODEL_ENV_KEYS = {
    "embedding": {"api_key": "EXCELMANUS_EMBEDDING_API_KEY", "base_url": "EXCELMANUS_EMBEDDING_BASE_URL", "model": "EXCELMANUS_EMBEDDING_MODEL", "enabled": "EXCELMANUS_EMBEDDING_ENABLED"},
}


def _find_env_file() -> str:
    """定位可读的 .env：项目根优先，否则正式仓。保留给调用方探测路径。"""
    from excelmanus.data_home import get_config_env_path, get_project_env_path

    project = get_project_env_path()
    if project.is_file():
        return str(project)
    cwd = os.path.join(os.getcwd(), ".env")
    if os.path.isfile(cwd):
        return cwd
    return str(get_config_env_path())


def _read_env_file(path: str) -> list[str]:
    from excelmanus.data_home import read_env_lines

    return read_env_lines(path)


def _write_env_file(path: str, lines: list[str]) -> None:
    from excelmanus.data_home import _atomic_write_text

    _atomic_write_text(path, "".join(lines), private=path.endswith("config.env"))


def _update_env_var(lines: list[str], key: str, value: str) -> list[str]:
    from excelmanus.data_home import update_env_lines

    return update_env_lines(lines, key, value)


def _persist_env_updates(updates: dict[str, str]) -> None:
    from excelmanus.data_home import persist_env_updates

    persist_env_updates(updates)


class ModelConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    enabled: bool | None = None
    protocol: str | None = None


class ModelProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    model: str
    api_key: str = ""
    base_url: str = ""
    description: str = ""
    protocol: str = "auto"
    thinking_mode: str = "auto"
    model_family: str = ""
    custom_extra_body: str = ""
    custom_extra_headers: str = ""


def _deprecated_model_error_response(model: str, *, prefix: str = "") -> JSONResponse | None:
    """Return unified 422 response when model id is deprecated."""
    message = format_deprecated_model_message(model)
    if not message:
        return None
    content = f"{prefix}{message}" if prefix else message
    return _error_json_response(422, content)


@router.get("/api/v1/config/models")
async def get_model_config(request: Request) -> JSONResponse:
    """获取模型配置（embedding + profiles + 当前激活档案）。"""
    assert get_config() is not None, "服务未初始化"
    user_cfg = _user_config_store()
    result: dict = {
        "embedding": {
            "api_key": _mask_key(get_config().embedding_api_key or ""),
            "base_url": get_config().embedding_base_url or "",
            "model": get_config().embedding_model or "",
            "enabled": get_config().embedding_enabled,
        },
        "profiles": [
            {
                "name": p["name"],
                "model": p["model"],
                "api_key": _mask_key(p.get("api_key", "")),
                "base_url": p.get("base_url", ""),
                "description": p.get("description", ""),
                "protocol": p.get("protocol", "auto"),
                "thinking_mode": p.get("thinking_mode", "auto"),
                "model_family": p.get("model_family", ""),
                "custom_extra_body": p.get("custom_extra_body", ""),
                "custom_extra_headers": p.get("custom_extra_headers", ""),
            }
            for p in (get_config_store().list_profiles() if get_config_store() else [])
        ],
        "active": user_cfg.get_active_model() if user_cfg is not None else None,
    }
    return JSONResponse(content=result)


def _mask_key(key: str) -> str:
    """脱敏 API Key：保留前4后4位。"""
    if not key or len(key) <= 12:
        return "****" if key else ""
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"



@router.put("/api/v1/config/models/{section}")
async def update_model_config(
    section: str,
    request: ModelConfigUpdate,
    raw_request: Request,
) -> JSONResponse:
    """更新指定模型配置区块并持久化到正式仓。"""
    if section not in _MODEL_ENV_KEYS:
        return _error_json_response(400, f"未知配置区块: {section}")

    key_map = _MODEL_ENV_KEYS[section]

    updates: dict[str, str] = {}
    if request.api_key is not None and "api_key" in key_map:
        updates[key_map["api_key"]] = request.api_key
    if request.base_url is not None and "base_url" in key_map:
        updates[key_map["base_url"]] = request.base_url
    if request.model is not None and "model" in key_map:
        deprecated = _deprecated_model_error_response(
            request.model,
            prefix=f"{section} 模型配置不可用。",
        )
        if deprecated is not None:
            return deprecated
        updates[key_map["model"]] = request.model
    if request.enabled is not None and "enabled" in key_map:
        updates[key_map["enabled"]] = "true" if request.enabled else "false"
    if request.protocol is not None and "protocol" in key_map:
        updates[key_map["protocol"]] = request.protocol

    if not updates:
        return _error_json_response(400, "无有效更新字段")

    _persist_env_updates(updates)

    # 同步更新内存中的 _config 实例
    if get_config() is not None:
        _SECTION_CONFIG_FIELDS = {
            "embedding": {"api_key": "embedding_api_key", "base_url": "embedding_base_url", "model": "embedding_model", "enabled": "embedding_enabled"},
        }
        field_map = _SECTION_CONFIG_FIELDS.get(section, {})
        for req_field, config_attr in field_map.items():
            val = getattr(request, req_field, None)
            if val is not None:
                object.__setattr__(get_config(), config_attr, val)

    return JSONResponse(content={"status": "ok", "section": section, "updated": list(updates.keys())})


@router.post("/api/v1/config/models/profiles")
async def add_model_profile(request: ModelProfileCreate, raw_request: Request) -> JSONResponse:
    """新增多模型条目并持久化到数据库。"""
    if get_config_store() is None:
        return _error_json_response(503, "配置存储未初始化")

    if get_config_store().get_profile(request.name):
        return _error_json_response(409, f"模型名称已存在: {request.name}")
    if is_placeholder_model_profile(request.name, request.model, request.base_url or ""):
        return _error_json_response(400, "不能保存测试占位模型。")

    deprecated = _deprecated_model_error_response(
        request.model,
        prefix=f"模型档案 {request.name!r} 不可保存。",
    )
    if deprecated is not None:
        return deprecated

    get_config_store().add_profile(
        name=request.name,
        model=request.model,
        api_key=request.api_key or "",
        base_url=request.base_url or "",
        description=request.description or "",
        protocol=request.protocol or "auto",
        thinking_mode=request.thinking_mode or "auto",
        model_family=request.model_family or "",
        custom_extra_body=request.custom_extra_body or "",
        custom_extra_headers=request.custom_extra_headers or "",
    )
    _sync_config_profiles_from_db()
    if get_session_manager() is not None and get_config() is not None:
        await get_session_manager().broadcast_model_profiles(get_config().models)

    user_cfg = _user_config_store()
    if user_cfg is not None and not user_cfg.get_active_model():
        await _activate_named_profile(request.name)

    return JSONResponse(status_code=201, content={"status": "created", "name": request.name})


@router.delete("/api/v1/config/models/profiles/{name:path}")
async def delete_model_profile(name: str, request: Request) -> JSONResponse:
    """删除多模型条目。"""
    if get_config_store() is None:
        return _error_json_response(503, "配置存储未初始化")

    if not get_config_store().delete_profile(name):
        return _error_json_response(404, f"未找到模型: {name}")

    user_cfg = _user_config_store()
    was_active = user_cfg is not None and user_cfg.get_active_model() == name
    _sync_config_profiles_from_db()
    if get_session_manager() is not None and get_config() is not None:
        await get_session_manager().broadcast_model_profiles(get_config().models)
    if was_active:
        remaining = get_config_store().list_profiles()
        if remaining:
            await _activate_named_profile(remaining[0]["name"])
        elif user_cfg is not None:
            user_cfg.set_active_model(None)
            set_config_incomplete(True)
    return JSONResponse(content={"status": "deleted", "name": name})


@router.put("/api/v1/config/models/profiles/{name:path}")
async def update_model_profile(
    name: str,
    request: ModelProfileCreate,
    raw_request: Request,
) -> JSONResponse:
    """更新多模型条目。"""
    if get_config_store() is None:
        return _error_json_response(503, "配置存储未初始化")

    if not get_config_store().get_profile(name):
        return _error_json_response(404, f"未找到模型: {name}")

    deprecated = _deprecated_model_error_response(
        request.model,
        prefix=f"模型档案 {name!r} 不可更新。",
    )
    if deprecated is not None:
        return deprecated

    get_config_store().update_profile(
        name,
        new_name=request.name if request.name != name else None,
        model=request.model,
        api_key=request.api_key or None,
        base_url=request.base_url or None,
        description=request.description or None,
        protocol=request.protocol or None,
        thinking_mode=request.thinking_mode,
        model_family=request.model_family,
        custom_extra_body=request.custom_extra_body,
        custom_extra_headers=request.custom_extra_headers,
    )
    _sync_config_profiles_from_db()
    if get_session_manager() is not None and get_config() is not None:
        await get_session_manager().broadcast_model_profiles(get_config().models)

    user_cfg = _user_config_store()
    active_name = user_cfg.get_active_model() if user_cfg is not None else None
    if active_name == name or active_name == request.name:
        await _activate_named_profile(request.name)

    return JSONResponse(content={"status": "updated", "name": request.name})


# ── 模型配置导出/导入 API ──────────────────────────────


class ConfigExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: list[str] = ["embedding", "profiles"]
    mode: Literal["password", "simple"] = "password"
    password: str | None = None


class ConfigImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str
    password: str | None = None


def _collect_raw_sections(section_names: list[str]) -> dict[str, Any]:
    """收集指定区块的原始（未脱敏）配置数据。"""
    assert get_config() is not None
    result: dict[str, Any] = {}
    if "embedding" in section_names:
        result["embedding"] = {
            "api_key": get_config().embedding_api_key or "",
            "base_url": get_config().embedding_base_url or "",
            "model": get_config().embedding_model or "",
            "enabled": get_config().embedding_enabled,
        }
    if "profiles" in section_names and get_config_store() is not None:
        result["profiles"] = get_config_store().list_profiles()
    return result


@router.post("/api/v1/config/export")
async def export_model_config(
    request: ConfigExportRequest,
) -> JSONResponse:
    """将模型配置加密导出为令牌字符串。"""
    if get_config() is None:
        return _error_json_response(503, "服务未初始化")

    valid_sections = {"embedding", "profiles"}
    invalid = set(request.sections) - valid_sections
    if invalid:
        return _error_json_response(400, f"无效的配置区块: {', '.join(invalid)}")
    sections = _collect_raw_sections(request.sections)

    try:
        from excelmanus.config_transfer import export_config
        token = export_config(sections, password=request.password, mode=request.mode)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return JSONResponse(content={"token": token, "sections": list(sections.keys()), "mode": request.mode})


@router.post("/api/v1/config/import")
async def import_model_config(
    request: ConfigImportRequest,
) -> JSONResponse:
    """解密并导入模型配置令牌。进程内始终写入全局配置。"""
    if get_config() is None:
        return _error_json_response(503, "服务未初始化")

    try:
        from excelmanus.config_transfer import import_config
        payload = import_config(request.token, password=request.password)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    sections = payload.get("sections", {})
    imported: dict[str, Any] = {}
    env_updates: dict[str, str] = {}

    for section_key in ("embedding",):
        section_data = sections.get(section_key)
        if not isinstance(section_data, dict):
            continue
        key_map = _MODEL_ENV_KEYS.get(section_key)
        if not key_map:
            continue

        updated_fields: list[str] = []
        for field_name in ("api_key", "base_url", "model", "protocol"):
            val = section_data.get(field_name)
            if val is None or not isinstance(val, str):
                continue
            if field_name == "model":
                deprecated = _deprecated_model_error_response(
                    val,
                    prefix=f"导入的 {section_key} 模型配置不可用。",
                )
                if deprecated is not None:
                    return deprecated
            env_key = key_map.get(field_name)
            if not env_key:
                continue
            env_updates[env_key] = val
            updated_fields.append(field_name)

        # enabled 字段为 bool，单独处理
        if "enabled" in section_data and isinstance(section_data["enabled"], bool):
            env_key = key_map.get("enabled")
            if env_key:
                env_updates[env_key] = "true" if section_data["enabled"] else "false"
                updated_fields.append("enabled")

        if get_config() is not None and updated_fields:
            field_map = {
                "embedding": {"api_key": "embedding_api_key", "base_url": "embedding_base_url", "model": "embedding_model", "enabled": "embedding_enabled"},
            }.get(section_key, {})
            for f in updated_fields:
                config_attr = field_map.get(f)
                if config_attr:
                    object.__setattr__(get_config(), config_attr, section_data.get(f) if f == "enabled" else (section_data.get(f) or None))

        if updated_fields:
            imported[section_key] = updated_fields

    if env_updates:
        _persist_env_updates(env_updates)

    profiles_data = sections.get("profiles")
    if isinstance(profiles_data, list) and get_config_store() is not None:
        profile_names: list[str] = []
        for p in profiles_data:
            if not isinstance(p, dict):
                continue
            name = p.get("name", "").strip()
            model = p.get("model", "").strip()
            if not name or not model:
                continue
            deprecated = _deprecated_model_error_response(
                model,
                prefix=f"导入的模型档案 {name!r} 不可用。",
            )
            if deprecated is not None:
                return deprecated
            existing = get_config_store().get_profile(name)
            if existing:
                get_config_store().update_profile(
                    name,
                    model=model,
                    api_key=p.get("api_key", ""),
                    base_url=p.get("base_url", ""),
                    description=p.get("description", ""),
                    protocol=p.get("protocol", "auto"),
                    thinking_mode=p.get("thinking_mode", "auto"),
                    model_family=p.get("model_family", ""),
                    custom_extra_body=p.get("custom_extra_body", ""),
                    custom_extra_headers=p.get("custom_extra_headers", ""),
                )
            else:
                get_config_store().add_profile(
                    name=name,
                    model=model,
                    api_key=p.get("api_key", ""),
                    base_url=p.get("base_url", ""),
                    description=p.get("description", ""),
                    protocol=p.get("protocol", "auto"),
                    thinking_mode=p.get("thinking_mode", "auto"),
                    model_family=p.get("model_family", ""),
                    custom_extra_body=p.get("custom_extra_body", ""),
                    custom_extra_headers=p.get("custom_extra_headers", ""),
                )
            profile_names.append(name)
        if profile_names:
            imported["profiles"] = profile_names
            _sync_config_profiles_from_db()

    return JSONResponse(content={
        "status": "ok",
        "imported": imported,
        "exported_at": payload.get("ts", ""),
    })


@router.post("/api/v1/config/transfer/detect")
async def detect_config_token(request: Request) -> JSONResponse:
    """检测令牌的加密模式（用于前端判断是否需要密码输入框）。"""
    body = await request.json()
    token = body.get("token", "")
    if not token:
        return _error_json_response(400, "缺少 token 字段")

    from excelmanus.config_transfer import detect_token_mode
    mode = detect_token_mode(token)
    return JSONResponse(content={"mode": mode, "needs_password": mode == "password"})


# ── 模型能力探测 API ──────────────────────────────────


def _resolve_active_engine_info() -> tuple[str, str, str]:
    """返回当前激活模型配置（始终从 get_config() 读取）。"""
    assert get_config() is not None
    return get_config().model, get_config().base_url, get_config().api_key


def _profile_connection(
    profile: dict,
    *,
    default_protocol: str = "auto",
) -> tuple[str, str, str, str]:
    """读取档案自身的连接信息，不借用其它档案的凭证。"""
    return (
        str(profile.get("model") or ""),
        str(profile.get("base_url") or ""),
        str(profile.get("api_key") or ""),
        str(profile.get("protocol") or default_protocol),
    )


def _resolve_model_info(
    req_name: str | None,
    req_model: str | None,
    req_base_url: str | None,
) -> tuple[str, str, str, str]:
    """根据请求参数解析模型信息。

    优先级：profile name > model ID 匹配 profile > 直接使用 model+base_url。
    返回 (model, base_url, api_key, protocol)。
    """
    assert get_config() is not None
    _default_protocol = get_config().protocol or "auto"

    # 1) 无参数：返回激活模型配置
    if not req_name and not req_model:
        m, b, a = _resolve_active_engine_info()
        return m, b, a, _default_protocol

    # 2) 按 profile name 精确查找
    lookup_name = req_name or req_model

    # 2-1) DB profile 查找
    if get_config_store() is not None and lookup_name:
        profile = get_config_store().get_profile(lookup_name)
        if profile is not None:
            return _profile_connection(profile, default_protocol=_default_protocol)

    # 3) 按 model ID 在所有 profiles 中查找（处理前端传 model ID 而非 name 的情况）
    if get_config_store() is not None and req_model:
        for p in get_config_store().list_profiles():
            if p["model"] == req_model:
                model, base_url, api_key, protocol = _profile_connection(
                    p, default_protocol=_default_protocol,
                )
                if req_base_url and base_url != req_base_url:
                    continue
                return model, base_url, api_key, protocol

    # 4) 无匹配档案：使用请求参数，缺省项取当前激活快照
    model = req_model or get_config().model
    base_url = req_base_url or get_config().base_url
    return model, base_url, get_config().api_key, _default_protocol


@router.get("/api/v1/config/models/capabilities")
async def get_model_capabilities(request: Request) -> JSONResponse:
    """获取模型能力探测结果。支持 ?model=xxx 查询指定模型，否则返回当前活跃模型。"""
    if get_config() is None:
        return _error_json_response(503, "服务未初始化")

    from excelmanus.model_probe import load_capabilities

    db = get_session_manager().database if get_session_manager() else None
    if db is None:
        return JSONResponse(content={"capabilities": None, "reason": "数据库未启用"})

    req_name = request.query_params.get("name")
    req_model = request.query_params.get("model")
    req_base_url = request.query_params.get("base_url")
    model, base_url, _, _protocol = _resolve_model_info(req_name, req_model, req_base_url)

    caps = load_capabilities(db, model, base_url)
    return JSONResponse(content={
        "capabilities": caps.to_dict() if caps else None,
        "model": model,
        "base_url": base_url,
    })


@router.get("/api/v1/config/models/capabilities/all")
async def get_all_model_capabilities(request: Request) -> JSONResponse:
    """获取所有已配置模型档案的能力探测结果。"""
    if get_config() is None:
        return _error_json_response(503, "服务未初始化")

    from excelmanus.model_probe import load_capabilities

    db = get_session_manager().database if get_session_manager() else None
    if db is None:
        return JSONResponse(content={"items": []})

    result: list[dict] = []

    profiles = get_config_store().list_profiles() if get_config_store() else []
    for p in profiles:
        p_model, p_base_url, _, _ = _profile_connection(p)
        caps = load_capabilities(db, p_model, p_base_url)
        result.append({
            "name": p["name"],
            "model": p_model,
            "base_url": p_base_url,
            "capabilities": caps.to_dict() if caps else None,
        })

    return JSONResponse(content={"items": result})


@router.post("/api/v1/config/models/capabilities/probe")
async def probe_model_capabilities(request: Request) -> JSONResponse:
    """手动触发模型能力探测。支持 body 指定 model/base_url/api_key，否则探测当前活跃模型。"""
    if get_config() is None:
        return _error_json_response(503, "服务未初始化")

    from excelmanus.model_probe import delete_capabilities, run_full_probe, save_capabilities
    from excelmanus.providers import create_client

    db = get_session_manager().database if get_session_manager() else None

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    req_name = body.get("name")
    req_model = body.get("model")
    req_base_url = body.get("base_url")

    model, base_url, api_key, resolved_protocol = _resolve_model_info(req_name, req_model, req_base_url)

    # 保留 profile 级坐标用于缓存键（与 GET 端点一致），
    # 同时剥离 Codex 前缀得到 API 实际使用的模型 ID。
    cache_model, cache_base_url = model, base_url
    api_model = model
    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
    if OpenAICodexProvider.is_codex_profile_name(model):
        _real = OpenAICodexProvider.model_from_profile_name(model)
        api_model = _real if _real else model[len(OpenAICodexProvider.MODEL_NAME_PREFIX):]

    # 优先尝试当前用户的运行时凭据（如 Codex OAuth access token），
    # 以便能通过 backend-api 执行真实探测并写入缓存。
    _resolver = getattr(getattr(request, "app", None).state, "credential_resolver", None)
    if _resolver is not None:
        try:
            _resolved_cred = _resolver.resolve_sync(api_model)
            if _resolved_cred:
                api_key = _resolved_cred.api_key or api_key
                if _resolved_cred.base_url:
                    base_url = _resolved_cred.base_url
                if _resolved_cred.protocol:
                    resolved_protocol = _resolved_cred.protocol
        except Exception:
            logger.debug("能力探测解析运行时凭证失败", exc_info=True)

    if body.get("api_key"):
        api_key = body["api_key"]

    # 解析 thinking_mode（来自请求体或 profile 配置）
    req_thinking_mode = body.get("thinking_mode", "auto")
    if req_thinking_mode == "auto" and get_config_store() and req_name:
        _prof = get_config_store().get_profile(req_name)
        if _prof:
            req_thinking_mode = _prof.get("thinking_mode", "auto")

    if db is not None:
        delete_capabilities(db, cache_model, cache_base_url)

    # 非 ASCII 字符检测（httpx 用 ASCII 编码 HTTP header）
    for _fl, _fv in [("API Key", api_key), ("Base URL", base_url), ("Model", api_model)]:
        try:
            _fv.encode("ascii")
        except UnicodeEncodeError as _enc:
            _bc = _enc.object[_enc.start:_enc.end]
            return _error_json_response(
                400, f"{_fl} 包含非法字符 '{_bc}'（位置 {_enc.start}），请检查是否有多余的特殊字符"
            )

    req_protocol = body.get("protocol") or resolved_protocol
    client = create_client(api_key=api_key, base_url=base_url, protocol=req_protocol)

    try:
        caps = await run_full_probe(
            client=client,
            model=api_model,
            base_url=base_url,
            skip_if_cached=False,
            db=None,  # 先不自动存，下面用 profile 坐标手动存
            thinking_mode=req_thinking_mode,
        )
    except Exception as exc:
        return _error_json_response(500, f"探测失败: {exc}")

    # 用 profile 级坐标缓存（与 GET /capabilities/all 读取一致）
    if db is not None:
        caps.model = cache_model
        caps.base_url = cache_base_url
        save_capabilities(db, caps)

    # 同步到所有活跃会话的引擎
    if get_session_manager() is not None:
        await get_session_manager().broadcast_model_capabilities(api_model, caps)

    return JSONResponse(content={"capabilities": caps.to_dict(), "model": cache_model})


@router.post("/api/v1/config/models/capabilities/probe-all")
async def probe_all_model_capabilities(request: Request) -> JSONResponse:
    """一键探测所有已配置模型的能力。"""
    if get_config() is None:
        return _error_json_response(503, "服务未初始化")

    from excelmanus.model_probe import delete_capabilities, run_full_probe
    from excelmanus.providers import create_client

    db = get_session_manager().database if get_session_manager() else None

    # 收集所有需要探测的 (name, model, base_url, api_key, protocol) 元组
    targets: list[tuple[str, str, str, str, str]] = []

    profiles = get_config_store().list_profiles() if get_config_store() else []
    for p in profiles:
        # Codex OAuth 档案使用用户订阅凭据，不走通用 API Key 探测
        if p.get("model", "").startswith("openai-codex/"):
            continue
        p_model, p_base_url, p_api_key, p_protocol = _profile_connection(
            p, default_protocol=get_config().protocol or "auto",
        )
        targets.append((p["name"], p_model, p_base_url, p_api_key, p_protocol))

    results: list[dict] = []
    # 构建 name → thinking_mode 映射
    _thinking_mode_map: dict[str, str] = {}
    for p in profiles:
        _thinking_mode_map[p["name"]] = p.get("thinking_mode", "auto")

    for name, model, base_url, api_key, protocol in targets:
        if db is not None:
            delete_capabilities(db, model, base_url)
        client = create_client(api_key=api_key, base_url=base_url, protocol=protocol)
        _tm = _thinking_mode_map.get(name, "auto")
        try:
            caps = await run_full_probe(
                client=client, model=model, base_url=base_url,
                skip_if_cached=False, db=db, thinking_mode=_tm,
            )
            results.append({
                "name": name, "model": model,
                "capabilities": caps.to_dict(),
            })
            if get_session_manager() is not None:
                await get_session_manager().broadcast_model_capabilities(model, caps)
        except Exception as exc:
            results.append({
                "name": name, "model": model,
                "error": str(exc)[:200],
            })

    return JSONResponse(content={"results": results})


# ── 异步探测任务 API（Job-based） ──────────────────────────────


def _build_probe_targets(
    names: list[str] | None = None,
    *,
    probe_all: bool = False,
) -> list["ProbeTargetSpec"]:
    """将 profile 名列表或 all=True 解析为 ProbeTargetSpec 列表。"""
    from excelmanus.capability_probe_jobs import ProbeTargetSpec
    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider

    assert get_config() is not None
    default_protocol = get_config().protocol or "auto"
    profiles = get_config_store().list_profiles() if get_config_store() else []
    profile_map = {p["name"]: p for p in profiles}

    # thinking_mode 映射
    thinking_map: dict[str, str] = {}
    for p in profiles:
        thinking_map[p["name"]] = p.get("thinking_mode", "auto")

    want_names: list[str]
    if probe_all:
        want_names = [p["name"] for p in profiles]
    elif names:
        want_names = names
    else:
        user_cfg = _user_config_store()
        active = user_cfg.get_active_model() if user_cfg is not None else None
        want_names = [active] if active else [p["name"] for p in profiles[:1]]

    targets: list[ProbeTargetSpec] = []
    seen_keys: set[str] = set()

    for name in want_names:
        if name in profile_map:
            p = profile_map[name]
            model, base_url, api_key, protocol = _profile_connection(
                p, default_protocol=default_protocol,
            )
        else:
            continue

        if model.startswith("openai-codex/"):
            continue

        cache_model = model
        api_model = model
        if OpenAICodexProvider.is_codex_profile_name(model):
            real = OpenAICodexProvider.model_from_profile_name(model)
            api_model = real if real else model[len(OpenAICodexProvider.MODEL_NAME_PREFIX):]

        dedup_key = f"{cache_model}|{base_url}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        targets.append(ProbeTargetSpec(
            name=name,
            cache_model=cache_model,
            api_model=api_model,
            base_url=base_url,
            api_key=api_key,
            protocol=protocol,
            thinking_mode=thinking_map.get(name, "auto"),
        ))

    return targets


@router.post("/api/v1/config/models/capabilities/jobs")
async def create_probe_job(request: Request) -> JSONResponse:
    """创建异步能力探测任务，立即返回 job_id。"""
    if get_config() is None:
        return _error_json_response(503, "服务未初始化")

    mgr = _get_probe_job_mgr()

    from excelmanus.capability_probe_jobs import ProbeTargetSpec
    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass

    probe_all = body.get("all", False)
    req_name = body.get("name")
    req_model = body.get("model")

    targets: list[ProbeTargetSpec]

    if probe_all or req_name:
        names = [req_name] if req_name and not probe_all else None
        targets = _build_probe_targets(names, probe_all=probe_all)
    elif req_model:
        model, base_url, api_key, protocol = _resolve_model_info(None, req_model, body.get("base_url"))
        cache_model = model
        api_model = model
        if OpenAICodexProvider.is_codex_profile_name(model):
            real = OpenAICodexProvider.model_from_profile_name(model)
            api_model = real if real else model[len(OpenAICodexProvider.MODEL_NAME_PREFIX):]

        _resolver = getattr(getattr(request, "app", None).state, "credential_resolver", None)
        if _resolver is not None:
            try:
                _resolved_cred = _resolver.resolve_sync(api_model)
                if _resolved_cred:
                    api_key = _resolved_cred.api_key or api_key
                    if _resolved_cred.base_url:
                        base_url = _resolved_cred.base_url
                    if _resolved_cred.protocol:
                        protocol = _resolved_cred.protocol
            except Exception:
                logger.debug("probe job 解析运行时凭证失败", exc_info=True)

        targets = [ProbeTargetSpec(
            name=req_name or req_model or "active",
            cache_model=cache_model,
            api_model=api_model,
            base_url=base_url,
            api_key=api_key,
            protocol=protocol,
            thinking_mode=body.get("thinking_mode", "auto"),
        )]
    else:
        targets = _build_probe_targets(None, probe_all=False)

    if not targets:
        return _error_json_response(400, "无可探测的模型")

    db = get_session_manager().database if get_session_manager() else None

    from excelmanus.model_probe import delete_capabilities
    if db is not None:
        for t in targets:
            delete_capabilities(db, t.cache_model, t.base_url)

    result = await mgr.create_job(
        targets=targets,
        db=db,
        session_manager=get_session_manager(),
    )
    return JSONResponse(content=result, status_code=202)


@router.get("/api/v1/config/models/capabilities/jobs/{job_id}")
async def get_probe_job(request: Request, job_id: str) -> JSONResponse:
    """获取探测任务快照。"""
    mgr = _get_probe_job_mgr()
    snapshot = await mgr.get_job_snapshot(job_id)
    if snapshot is None:
        return _error_json_response(404, "探测任务不存在")
    return JSONResponse(content=snapshot)


@router.delete("/api/v1/config/models/capabilities/jobs/{job_id}")
async def cancel_probe_job(request: Request, job_id: str) -> JSONResponse:
    """取消探测任务。"""
    mgr = _get_probe_job_mgr()
    state = await mgr.cancel_job(job_id)
    if state is None:
        return _error_json_response(404, "探测任务不存在")
    return JSONResponse(content={"state": state})


@router.get("/api/v1/config/models/capabilities/jobs/{job_id}/events")
async def probe_job_events(request: Request, job_id: str) -> StreamingResponse:
    """SSE 事件流：实时推送探测任务进度。"""
    mgr = _get_probe_job_mgr()

    queue = await mgr.subscribe(job_id)
    if queue is None:
        return _error_json_response(404, "探测任务不存在")

    snapshot = await mgr.get_job_snapshot(job_id)

    async def _event_gen() -> AsyncIterator[str]:
        try:
            if snapshot is not None:
                yield _sse_format("job_update", snapshot)

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                yield _sse_format(event.get("event", "job_update"), event.get("data", {}))

                data = event.get("data") or {}
                state = data.get("state", "")
                if state in ("succeeded", "partial", "failed", "cancelled"):
                    break
        finally:
            await mgr.unsubscribe(job_id, queue)

    return StreamingResponse(
        _event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _diagnose_connection_error(error: str, base_url: str, model: str) -> str:
    """根据错误信息和配置，返回用户可操作的修复建议。"""
    err_lower = error.lower()

    # ── 请求被拦截（常见于 base_url 缺少 /v1） ──
    if "blocked" in err_lower or "request was blocked" in err_lower:
        if base_url and not base_url.rstrip("/").endswith("/v1"):
            return f"请求被拦截，通常是 Base URL 缺少 /v1 路径。请尝试将 Base URL 改为：{base_url.rstrip('/')}/v1"
        return "请求被 API 服务商拦截，请检查 Base URL 是否正确、账号是否有访问权限。"

    # ── 404：路径错误 ──
    if "404" in err_lower or "not found" in err_lower:
        if base_url and not base_url.rstrip("/").endswith("/v1"):
            return f"API 端点不存在 (404)。请尝试在 Base URL 末尾加上 /v1：{base_url.rstrip('/')}/v1"
        return "API 端点不存在 (404)。请检查 Base URL 和模型名称是否正确。"

    # ── 认证失败 ──
    if any(k in err_lower for k in ("401", "unauthorized", "invalid api key", "authentication", "invalid.*key")):
        return "API Key 认证失败，请检查 Key 是否正确、是否已过期。"

    # ── 权限不足 ──
    if any(k in err_lower for k in ("403", "forbidden", "permission")):
        return "权限不足 (403)。该 API Key 可能无权访问此模型，请确认账号权限。"

    # ── 额度/计费 ──
    if any(k in err_lower for k in ("402", "quota", "billing", "balance", "insufficient", "payment")):
        return "账号额度不足或计费问题，请检查 API 账号余额。"

    # ── 限流 ──
    if any(k in err_lower for k in ("429", "rate limit", "too many")):
        return "请求被限流 (429)，请稍后重试或降低请求频率。"

    # ── 模型不存在 ──
    if any(k in err_lower for k in ("model not found", "model_not_found", "does not exist", "no such model")):
        return f"模型 '{model}' 不存在。请检查模型名称是否拼写正确。"

    # ── 账号池耗尽 ──
    if any(k in err_lower for k in ("no.*account.*available", "no available", "pool.*exhaust")):
        return "API 代理的账号池暂无可用账号，请稍后重试。"

    # ── 超时 ──
    if any(k in err_lower for k in ("timeout", "timed out")):
        return "请求超时。请检查网络连通性，或 Base URL 是否可访问。"

    # ── 连接失败 ──
    if any(k in err_lower for k in ("connection refused", "connection error", "connect error", "name resolution", "getaddrinfo", "dns")):
        return "无法连接到 API 服务器。请检查 Base URL 是否正确、网络是否畅通。"

    # ── SSL 错误 ──
    if any(k in err_lower for k in ("ssl", "certificate", "cert")):
        return "SSL 证书验证失败。请检查 Base URL 的 HTTPS 证书是否有效。"

    return ""


@router.post("/api/v1/config/models/test-connection")
async def test_model_connection(request: Request) -> JSONResponse:
    """轻量级模型连通测试：仅发送 Hi 检查模型是否可达、鉴权是否正常。"""
    if get_config() is None:
        return _error_json_response(503, "服务未初始化")

    from excelmanus.model_probe import probe_health
    from excelmanus.providers import create_client

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    req_name = body.get("name")
    req_model = body.get("model")
    req_base_url = body.get("base_url")

    # Codex OAuth 档案使用用户订阅凭据，无法用通用 API Key 测试
    _test_model_id = req_model or ""
    if not _test_model_id and req_name and get_config_store():
        _tp = get_config_store().get_profile(req_name)
        if _tp:
            _test_model_id = _tp.get("model", "")
    if _test_model_id.startswith("openai-codex/"):
        return JSONResponse(content={
            "ok": True,
            "model": _test_model_id,
            "note": "Codex OAuth 模型使用用户订阅凭据，无需通用 API Key 测试",
        })

    model, base_url, api_key, resolved_protocol = _resolve_model_info(req_name, req_model, req_base_url)
    if body.get("api_key"):
        api_key = body["api_key"]

    # 格式校验
    if not model or not model.strip():
        return JSONResponse(content={"ok": False, "error": "模型标识符为空", "model": model})
    if not base_url or not base_url.strip():
        return JSONResponse(content={"ok": False, "error": "Base URL 为空", "model": model})
    if not api_key or not api_key.strip():
        return JSONResponse(content={"ok": False, "error": "API Key 为空", "model": model})

    # 占位符检测
    placeholder_patterns = [
        "sk-xxx", "your-api-key", "your_api_key", "api-key-here",
        "replace-with", "填入", "替换为", "请填写", "请输入",
    ]
    api_key_lower = api_key.strip().lower()
    for pat in placeholder_patterns:
        if pat in api_key_lower:
            return JSONResponse(content={
                "ok": False,
                "error": f"API Key 似乎是占位符（包含 '{pat}'），请填入真实的 API Key",
                "is_placeholder": True,
                "model": model,
            })

    # 非 ASCII 字符检测（httpx 用 ASCII 编码 HTTP header，非 ASCII 会导致 UnicodeEncodeError）
    for _field_label, _field_val in [("API Key", api_key), ("Base URL", base_url), ("Model", model)]:
        try:
            _field_val.encode("ascii")
        except UnicodeEncodeError as _enc_err:
            _bad_char = _enc_err.object[_enc_err.start:_enc_err.end]
            return JSONResponse(content={
                "ok": False,
                "error": f"{_field_label} 包含非法字符 '{_bad_char}'（位置 {_enc_err.start}），请检查是否有多余的特殊字符或空格",
                "model": model,
            })

    req_protocol = body.get("protocol") or resolved_protocol
    client = create_client(api_key=api_key, base_url=base_url, protocol=req_protocol)
    try:
        healthy, health_err = await probe_health(client, model, timeout=15.0)
    except Exception as exc:
        err_str = str(exc)[:200]
        hint = _diagnose_connection_error(err_str, base_url, model)
        return JSONResponse(content={"ok": False, "error": f"连通测试异常: {err_str}", "hint": hint, "model": model})

    result: dict = {
        "ok": healthy,
        "error": health_err if not healthy else "",
        "model": model,
        "base_url": base_url,
    }
    if not healthy and health_err:
        hint = _diagnose_connection_error(health_err, base_url, model)
        if hint:
            result["hint"] = hint
    return JSONResponse(content=result)


# Provider fallback model lists used when /models endpoint returns 404.
# Each entry: (base_url keyword, model list, user hint)
_PROVIDER_FALLBACK_MODELS: list[tuple[str, list[dict], str]] = [
    (
        "minimax",
        [
            {"id": "MiniMax-M3"},
            {"id": "MiniMax-M2.7"},
            {"id": "MiniMax-M2.7-highspeed"},
            {"id": "MiniMax-M2.5"},
            {"id": "MiniMax-M2.5-highspeed"},
            {"id": "MiniMax-M2.1"},
            {"id": "MiniMax-M2.1-highspeed"},
            {"id": "MiniMax-M2.1-lightning"},
            {"id": "MiniMax-M2"},
            {"id": "M2-her"},
        ],
        "MiniMax \u901a\u5e38\u4e0d\u652f\u6301 /models \u679a\u4e3e\uff0c\u5df2\u56de\u9000\u4e3a\u63a8\u8350\u6a21\u578b\u5217\u8868\u3002"
        "\u82e5\u4ecd\u5f02\u5e38\uff0c\u8bf7\u786e\u8ba4 Base URL\uff08\u5efa\u8bae https://api.minimax.io/v1\uff09\u548c API Key\u3002",
    ),
    (
        "generativelanguage.googleapis.com",
        [
            {"id": "gemini-3.8-flash"},
            {"id": "gemini-3.7-flash"},
            {"id": "gemini-3.6-flash"},
            {"id": "gemini-3.5-flash"},
            {"id": "gemini-3.5-flash-lite"},
            {"id": "gemini-3.1-pro-preview"},
            {"id": "gemini-2.5-pro"},
            {"id": "gemini-2.5-flash"},
            {"id": "gemini-2.5-flash-lite"},
        ],
        "Gemini OpenAI \u517c\u5bb9\u7aef\u70b9\u4e0d\u652f\u6301\u6807\u51c6 /models \u679a\u4e3e\uff0c\u5df2\u56de\u9000\u4e3a\u63a8\u8350\u6a21\u578b\u5217\u8868\u3002",
    ),
    (
        "bigmodel.cn",
        [
            {"id": "glm-5.3"},
            {"id": "glm-5.3-flash"},
            {"id": "glm-5-turbo"},
            {"id": "glm-5.2"},
            {"id": "glm-5.1"},
            {"id": "glm-5"},
            {"id": "glm-4.7"},
            {"id": "glm-4.6v"},
        ],
        "\u667a\u8c31 GLM /models \u7aef\u70b9\u8def\u5f84\u4e0e\u6807\u51c6 OpenAI \u4e0d\u540c\uff0c\u5df2\u56de\u9000\u4e3a\u63a8\u8350\u6a21\u578b\u5217\u8868\u3002",
    ),
    (
        "dashscope.aliyuncs.com",
        [
            {"id": "qwen3.8-max"},
            {"id": "qwen3.8-flash"},
            {"id": "qwen3.7-plus"},
            {"id": "qwen3.7-flash"},
            {"id": "qwen3.7-max"},
            {"id": "qwen-max"},
            {"id": "qwen-plus"},
            {"id": "qwen-flash"},
            {"id": "qwen-turbo"},
            {"id": "qwen-long"},
            {"id": "qwen3-coder-plus"},
            {"id": "qwen-coder-plus"},
        ],
        "\u963f\u91cc\u4e91\u767e\u70bc DashScope /models \u679a\u4e3e\u901a\u5e38\u9700\u8981\u7279\u5b9a\u6743\u9650\uff0c\u5df2\u56de\u9000\u4e3a\u63a8\u8350\u6a21\u578b\u5217\u8868\u3002",
    ),
    (
        "moonshot.cn",
        [
            {"id": "kimi-k3"},
            {"id": "kimi-k2.7-code"},
            {"id": "kimi-k2.7-code-highspeed"},
            {"id": "kimi-k2.6"},
        ],
        "Kimi (Moonshot) /models \u7aef\u70b9\u4e0d\u53ef\u7528\uff0c\u5df2\u56de\u9000\u4e3a\u63a8\u8350\u6a21\u578b\u5217\u8868\u3002",
    ),
    (
        "deepseek.com",
        [
            {"id": "deepseek-flash"},
            {"id": "deepseek-v4-pro"},
            {"id": "deepseek-v4-flash"},
        ],
        "DeepSeek /models \u7aef\u70b9\u4e0d\u53ef\u7528\uff0c\u5df2\u56de\u9000\u4e3a\u63a8\u8350\u6a21\u578b\u5217\u8868\u3002",
    ),
    (
        "volces.com",
        [
            {"id": "doubao-seed-2.1-pro"},
            {"id": "doubao-seed-2.1-turbo"},
            {"id": "doubao-seed-2.0-pro"},
            {"id": "doubao-seed-2.0-code"},
            {"id": "doubao-seed-2.0-lite"},
            {"id": "doubao-seed-2.0-mini"},
            {"id": "doubao-seed-evolving"},
        ],
        "\u706b\u5c71\u65b9\u821f /models \u679a\u4e3e\u4e0d\u53ef\u7528\uff0c\u5df2\u56de\u9000\u4e3a\u63a8\u8350\u6a21\u578b\u5217\u8868\u3002\u82e5\u4f7f\u7528\u63a5\u5165\u70b9\uff0c\u8bf7\u586b\u5199 ep- \u5f00\u5934\u7684 Model ID\u3002",
    ),
    (
        "api.x.ai",
        [
            {"id": "grok-4.6"},
            {"id": "grok-4.5"},
            {"id": "grok-4.3"},
            {"id": "grok-4-fast-reasoning"},
            {"id": "grok-code-fast-1"},
        ],
        "xAI /models \u679a\u4e3e\u4e0d\u53ef\u7528\uff0c\u5df2\u56de\u9000\u4e3a\u63a8\u8350\u6a21\u578b\u5217\u8868\u3002",
    ),
]


def _get_provider_fallback(base_url: str) -> tuple[list[dict], str] | None:
    """Return curated model list for a known provider when /models returns 404."""
    url_lower = base_url.lower()
    for pattern, models, hint in _PROVIDER_FALLBACK_MODELS:
        if pattern in url_lower:
            return models, hint
    return None


@router.post("/api/v1/config/models/list-remote")
async def list_remote_models(request: Request) -> JSONResponse:
    """调用远程 API 端点列出可用模型（用于添加模型时自动检测）。"""
    if get_config() is None:
        return _error_json_response(503, "服务未初始化")

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass

    base_url = (body.get("base_url") or "").strip() or get_config().base_url
    api_key = (body.get("api_key") or "").strip() or get_config().api_key
    protocol = (body.get("protocol") or "auto").strip().lower()

    if not base_url:
        return JSONResponse(content={"models": [], "error": "Base URL \u4e3a\u7a7a"})
    if not api_key:
        return JSONResponse(content={"models": [], "error": "API Key \u4e3a\u7a7a"})

    import httpx

    # Always append /models directly to base_url.
    # Never insert an extra /v1 segment — the caller already provides the versioned prefix.
    url = base_url.rstrip("/")
    if not url.endswith("/models"):
        url = url + "/models"

    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
    # Anthropic native API uses x-api-key header
    if protocol == "anthropic" or "anthropic" in base_url.lower():
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        url = base_url.rstrip("/").rstrip("/v1").rstrip("/") + "/v1/models"

    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return JSONResponse(content={"models": [], "error": "\u8bf7\u6c42\u8d85\u65f6\uff0c\u8bf7\u68c0\u67e5 Base URL \u662f\u5426\u53ef\u8bbf\u95ee"})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            fallback = _get_provider_fallback(base_url)
            if fallback is not None:
                models, hint = fallback
                return JSONResponse(content={"models": models, "hint": hint})
        hint = _diagnose_connection_error(str(exc)[:200], base_url, "")
        return JSONResponse(content={"models": [], "error": f"HTTP {exc.response.status_code}: {str(exc)[:150]}", "hint": hint})
    except Exception as exc:
        return JSONResponse(content={"models": [], "error": f"请求失败: {str(exc)[:200]}"})

    # 解析模型列表（兼容 OpenAI / Anthropic / 各类代理格式）
    models_raw: list = []
    if isinstance(data, dict):
        models_raw = data.get("data") or data.get("models") or []
    elif isinstance(data, list):
        models_raw = data

    models_out: list[dict] = []
    for m in models_raw:
        if isinstance(m, str):
            models_out.append({"id": m})
        elif isinstance(m, dict):
            mid = m.get("id") or m.get("name") or m.get("model") or ""
            if mid:
                models_out.append({"id": mid, "owned_by": m.get("owned_by", "")})

    # 按 id 排序
    models_out.sort(key=lambda x: x["id"])

    return JSONResponse(content={"models": models_out})


@router.get("/api/v1/config/models/check-placeholder")
async def check_model_placeholder(request: Request) -> JSONResponse:
    """检测当前模型配置是否使用了默认占位符值。"""
    if get_config() is None:
        return _error_json_response(503, "服务未初始化")

    placeholder_patterns = [
        "sk-xxx", "your-api-key", "your_api_key", "api-key-here",
        "replace-with", "填入", "替换为", "请填写", "请输入",
    ]

    def _is_placeholder(val: str) -> bool:
        if not val or not val.strip():
            return True
        lower = val.strip().lower()
        return any(p in lower for p in placeholder_patterns)

    results: list[dict] = []

    profiles = get_config_store().list_profiles() if get_config_store() else []
    if not profiles:
        results.append({"name": "active", "field": "model", "model": ""})
    for p in profiles:
        p_api_key = p.get("api_key") or ""
        if not p.get("model", "").startswith("openai-codex/") and _is_placeholder(p_api_key):
            results.append({"name": p["name"], "field": "api_key", "model": p["model"]})
        if not p.get("model") or not str(p.get("model")).strip():
            results.append({"name": p["name"], "field": "model", "model": ""})

    return JSONResponse(content={
        "has_placeholder": len(results) > 0,
        "items": results,
    })


@router.put("/api/v1/config/models/capabilities")
async def update_model_capabilities(request: Request) -> JSONResponse:
    """手动覆盖模型能力标记（前端设置用）。"""
    if get_config() is None:
        return _error_json_response(503, "服务未初始化")

    from excelmanus.model_probe import update_capabilities_override

    db = get_session_manager().database if get_session_manager() else None
    if db is None:
        return _error_json_response(503, "数据库未启用")

    body = await request.json()
    overrides = body.get("overrides", {})
    if not overrides:
        return _error_json_response(400, "缺少 overrides 字段")

    req_name = body.get("name")
    req_model = body.get("model")
    req_base_url = body.get("base_url")
    model, base_url, _, _protocol = _resolve_model_info(req_name, req_model, req_base_url)

    caps = update_capabilities_override(db, model, base_url, overrides)

    if caps is not None and get_session_manager() is not None:
        await get_session_manager().broadcast_model_capabilities(model, caps)

    return JSONResponse(content={
        "capabilities": caps.to_dict() if caps else None,
    })


# ── 运行时配置管理 API ──────────────────────────────────


def _mask_api_key(key: str | None) -> str:
    """将 API Key 遮掩为 sk-****xxxx 格式，前端仅需知道是否已配置。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:3] + "*" * (len(key) - 7) + key[-4:]


_RUNTIME_ENV_KEYS: dict[str, str] = {
    # ── 会话 ──
    "session_ttl_seconds": "EXCELMANUS_SESSION_TTL_SECONDS",
    "max_sessions": "EXCELMANUS_MAX_SESSIONS",
    "max_consecutive_failures": "EXCELMANUS_MAX_CONSECUTIVE_FAILURES",
    # ── 执行与安全 ──
    "subagent_enabled": "EXCELMANUS_SUBAGENT_ENABLED",
    "max_iterations": "EXCELMANUS_MAX_ITERATIONS",
    "friendly_error_messages": "EXCELMANUS_FRIENDLY_ERROR_MESSAGES",
    # ── 上下文与记忆 ──
    "max_context_tokens": "EXCELMANUS_MAX_CONTEXT_TOKENS",
    "memory_enabled": "EXCELMANUS_MEMORY_ENABLED",
    "memory_auto_extract_interval": "EXCELMANUS_MEMORY_AUTO_EXTRACT_INTERVAL",
    "memory_auto_load_lines": "EXCELMANUS_MEMORY_AUTO_LOAD_LINES",
    "memory_expire_days": "EXCELMANUS_MEMORY_EXPIRE_DAYS",
    "chat_history_enabled": "EXCELMANUS_CHAT_HISTORY_ENABLED",
    # ── 记忆维护 ──
    "memory_maintenance_enabled": "EXCELMANUS_MEMORY_MAINTENANCE_ENABLED",
    "memory_maintenance_min_entries": "EXCELMANUS_MEMORY_MAINTENANCE_MIN_ENTRIES",
    "memory_maintenance_new_threshold": "EXCELMANUS_MEMORY_MAINTENANCE_NEW_THRESHOLD",
    "memory_maintenance_interval_hours": "EXCELMANUS_MEMORY_MAINTENANCE_INTERVAL_HOURS",
    "memory_maintenance_model": "EXCELMANUS_MEMORY_MAINTENANCE_MODEL",
    # ── 摘要与压缩 ──
    "summarization_enabled": "EXCELMANUS_SUMMARIZATION_ENABLED",
    "summarization_threshold_ratio": "EXCELMANUS_SUMMARIZATION_THRESHOLD_RATIO",
    "summarization_keep_recent_turns": "EXCELMANUS_SUMMARIZATION_KEEP_RECENT_TURNS",
    "compaction_enabled": "EXCELMANUS_COMPACTION_ENABLED",
    "compaction_threshold_ratio": "EXCELMANUS_COMPACTION_THRESHOLD_RATIO",
    "compaction_keep_recent_turns": "EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS",
    "compaction_max_summary_tokens": "EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS",
    "prompt_cache_key_enabled": "EXCELMANUS_PROMPT_CACHE_KEY_ENABLED",
    # ── 推理配置 ──
    "thinking_effort": "EXCELMANUS_THINKING_EFFORT",
    "thinking_budget": "EXCELMANUS_THINKING_BUDGET",
    # ── 子代理 ──
    "subagent_max_iterations": "EXCELMANUS_SUBAGENT_MAX_ITERATIONS",
    "subagent_timeout_seconds": "EXCELMANUS_SUBAGENT_TIMEOUT_SECONDS",
    "subagent_max_consecutive_failures": "EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES",
    "parallel_subagent_max": "EXCELMANUS_PARALLEL_SUBAGENT_MAX",
    # ── LLM 重试 ──
    "llm_retry_max_attempts": "EXCELMANUS_LLM_RETRY_MAX_ATTEMPTS",
    "llm_retry_base_delay_seconds": "EXCELMANUS_LLM_RETRY_BASE_DELAY_SECONDS",
    "llm_retry_max_delay_seconds": "EXCELMANUS_LLM_RETRY_MAX_DELAY_SECONDS",
    # ── 视觉 ──
    "main_model_vision": "EXCELMANUS_MAIN_MODEL_VISION",
    "image_keep_rounds": "EXCELMANUS_IMAGE_KEEP_ROUNDS",
    "image_max_active": "EXCELMANUS_IMAGE_MAX_ACTIVE",
    "image_token_budget": "EXCELMANUS_IMAGE_TOKEN_BUDGET",
    # ── 系统消息与工具 ──
    "system_message_mode": "EXCELMANUS_SYSTEM_MESSAGE_MODE",
    "tool_result_hard_cap_chars": "EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS",
    "large_excel_threshold_bytes": "EXCELMANUS_LARGE_EXCEL_THRESHOLD_BYTES",
    "parallel_readonly_tools": "EXCELMANUS_PARALLEL_READONLY_TOOLS",
    "hooks_command_enabled": "EXCELMANUS_HOOKS_COMMAND_ENABLED",
    "hooks_command_timeout_seconds": "EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS",
    "hooks_output_max_chars": "EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS",
    "log_level": "EXCELMANUS_LOG_LEVEL",
    # ── 代码策略 ──
    "code_policy_enabled": "EXCELMANUS_CODE_POLICY_ENABLED",
    "code_policy_green_auto_approve": "EXCELMANUS_CODE_POLICY_GREEN_AUTO",
    "code_policy_yellow_auto_approve": "EXCELMANUS_CODE_POLICY_YELLOW_AUTO",
    "tool_schema_validation_mode": "EXCELMANUS_TOOL_SCHEMA_VALIDATION_MODE",
    "tool_schema_validation_canary_percent": "EXCELMANUS_TOOL_SCHEMA_VALIDATION_CANARY_PERCENT",
    "tool_schema_strict_path": "EXCELMANUS_TOOL_SCHEMA_STRICT_PATH",
    # ── 技能发现 ──
    "skills_context_char_budget": "EXCELMANUS_SKILLS_CONTEXT_CHAR_BUDGET",
    "skills_discovery_enabled": "EXCELMANUS_SKILLS_DISCOVERY_ENABLED",
    "skills_discovery_scan_workspace_ancestors": "EXCELMANUS_SKILLS_DISCOVERY_SCAN_WORKSPACE_ANCESTORS",
    "skills_discovery_include_agents": "EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_AGENTS",
    "skills_discovery_scan_external_tool_dirs": "EXCELMANUS_SKILLS_DISCOVERY_SCAN_EXTERNAL_TOOL_DIRS",
    # ── Embedding / 语义检索 ──
    "embedding_enabled": "EXCELMANUS_EMBEDDING_ENABLED",
    "embedding_model": "EXCELMANUS_EMBEDDING_MODEL",
    "embedding_dimensions": "EXCELMANUS_EMBEDDING_DIMENSIONS",
    "embedding_timeout_seconds": "EXCELMANUS_EMBEDDING_TIMEOUT_SECONDS",
    "memory_semantic_top_k": "EXCELMANUS_MEMORY_SEMANTIC_TOP_K",
    "memory_semantic_threshold": "EXCELMANUS_MEMORY_SEMANTIC_THRESHOLD",
    "memory_semantic_fallback_recent": "EXCELMANUS_MEMORY_SEMANTIC_FALLBACK_RECENT",
    # ── Playbook ──
    "playbook_enabled": "EXCELMANUS_PLAYBOOK_ENABLED",
    "playbook_max_bullets": "EXCELMANUS_PLAYBOOK_MAX_BULLETS",
    "registry_semantic_top_k": "EXCELMANUS_REGISTRY_SEMANTIC_TOP_K",
    "registry_semantic_threshold": "EXCELMANUS_REGISTRY_SEMANTIC_THRESHOLD",
    # ── 内置搜索引擎 ──
    "exa_search_enabled": "EXCELMANUS_EXA_SEARCH",
    "search_default_provider": "EXCELMANUS_SEARCH_DEFAULT",
    "exa_api_key": "EXCELMANUS_EXA_API_KEY",
    "tavily_api_key": "EXCELMANUS_TAVILY_API_KEY",
    "brave_api_key": "EXCELMANUS_BRAVE_API_KEY",
}


@router.get("/api/v1/config/runtime")
async def get_runtime_config(request: Request) -> JSONResponse:
    """读取运行时行为配置。"""
    assert get_config() is not None, "服务未初始化"
    return JSONResponse(content={
        # ── 会话 ──
        "session_ttl_seconds": get_config().session_ttl_seconds,
        "max_sessions": get_config().max_sessions,
        "max_consecutive_failures": get_config().max_consecutive_failures,
        # ── 执行与安全 ──
        "subagent_enabled": get_config().subagent_enabled,
        "max_iterations": get_config().max_iterations,
        "friendly_error_messages": get_config().friendly_error_messages,
        # ── 上下文与记忆 ──
        "max_context_tokens": get_config().max_context_tokens,
        "memory_enabled": get_config().memory_enabled,
        "memory_auto_extract_interval": get_config().memory_auto_extract_interval,
        "memory_auto_load_lines": get_config().memory_auto_load_lines,
        "memory_expire_days": get_config().memory_expire_days,
        "chat_history_enabled": get_config().chat_history_enabled,
        # ── 记忆维护 ──
        "memory_maintenance_enabled": get_config().memory_maintenance_enabled,
        "memory_maintenance_min_entries": get_config().memory_maintenance_min_entries,
        "memory_maintenance_new_threshold": get_config().memory_maintenance_new_threshold,
        "memory_maintenance_interval_hours": get_config().memory_maintenance_interval_hours,
        "memory_maintenance_model": get_config().memory_maintenance_model or "",
        # ── 摘要与压缩 ──
        "summarization_enabled": get_config().summarization_enabled,
        "summarization_threshold_ratio": get_config().summarization_threshold_ratio,
        "summarization_keep_recent_turns": get_config().summarization_keep_recent_turns,
        "compaction_enabled": get_config().compaction_enabled,
        "compaction_threshold_ratio": get_config().compaction_threshold_ratio,
        "compaction_keep_recent_turns": get_config().compaction_keep_recent_turns,
        "compaction_max_summary_tokens": get_config().compaction_max_summary_tokens,
        "prompt_cache_key_enabled": get_config().prompt_cache_key_enabled,
        # ── 推理配置 ──
        "thinking_effort": get_config().thinking_effort,
        "thinking_budget": get_config().thinking_budget,
        # ── 子代理 ──
        "subagent_max_iterations": get_config().subagent_max_iterations,
        "subagent_timeout_seconds": get_config().subagent_timeout_seconds,
        "subagent_max_consecutive_failures": get_config().subagent_max_consecutive_failures,
        "parallel_subagent_max": get_config().parallel_subagent_max,
        # ── LLM 重试 ──
        "llm_retry_max_attempts": get_config().llm_retry_max_attempts,
        "llm_retry_base_delay_seconds": get_config().llm_retry_base_delay_seconds,
        "llm_retry_max_delay_seconds": get_config().llm_retry_max_delay_seconds,
        # ── 视觉 ──
        "main_model_vision": get_config().main_model_vision,
        "image_keep_rounds": get_config().image_keep_rounds,
        "image_max_active": get_config().image_max_active,
        "image_token_budget": get_config().image_token_budget,
        # ── 系统消息与工具 ──
        "system_message_mode": get_config().system_message_mode,
        "tool_result_hard_cap_chars": get_config().tool_result_hard_cap_chars,
        "large_excel_threshold_bytes": get_config().large_excel_threshold_bytes,
        "parallel_readonly_tools": get_config().parallel_readonly_tools,
        "hooks_command_enabled": get_config().hooks_command_enabled,
        "hooks_command_timeout_seconds": get_config().hooks_command_timeout_seconds,
        "hooks_output_max_chars": get_config().hooks_output_max_chars,
        "log_level": get_config().log_level,
        # ── 代码策略 ──
        "code_policy_enabled": get_config().code_policy_enabled,
        "code_policy_green_auto_approve": get_config().code_policy_green_auto_approve,
        "code_policy_yellow_auto_approve": get_config().code_policy_yellow_auto_approve,
        "tool_schema_validation_mode": get_config().tool_schema_validation_mode,
        "tool_schema_validation_canary_percent": get_config().tool_schema_validation_canary_percent,
        "tool_schema_strict_path": get_config().tool_schema_strict_path,
        # ── 技能发现 ──
        "skills_context_char_budget": get_config().skills_context_char_budget,
        "skills_discovery_enabled": get_config().skills_discovery_enabled,
        "skills_discovery_scan_workspace_ancestors": get_config().skills_discovery_scan_workspace_ancestors,
        "skills_discovery_include_agents": get_config().skills_discovery_include_agents,
        "skills_discovery_scan_external_tool_dirs": get_config().skills_discovery_scan_external_tool_dirs,
        # ── Embedding / 语义检索 ──
        "embedding_enabled": get_config().embedding_enabled,
        "embedding_model": get_config().embedding_model,
        "embedding_dimensions": get_config().embedding_dimensions,
        "embedding_timeout_seconds": get_config().embedding_timeout_seconds,
        "memory_semantic_top_k": get_config().memory_semantic_top_k,
        "memory_semantic_threshold": get_config().memory_semantic_threshold,
        "memory_semantic_fallback_recent": get_config().memory_semantic_fallback_recent,
        # ── Playbook ──
        "playbook_enabled": get_config().playbook_enabled,
        "playbook_max_bullets": get_config().playbook_max_bullets,
        "registry_semantic_top_k": get_config().registry_semantic_top_k,
        "registry_semantic_threshold": get_config().registry_semantic_threshold,
        # ── 内置搜索引擎 ──
        "exa_search_enabled": get_config().exa_search_enabled,
        "search_default_provider": get_config().search_default_provider,
        "exa_api_key": _mask_api_key(get_config().exa_api_key),
        "tavily_api_key": _mask_api_key(get_config().tavily_api_key),
        "brave_api_key": _mask_api_key(get_config().brave_api_key),
    })


class RuntimeConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # ── 会话 ──
    session_ttl_seconds: int | None = Field(default=None, gt=0)
    max_sessions: int | None = Field(default=None, gt=0)
    max_consecutive_failures: int | None = Field(default=None, gt=0)
    # ── 执行与安全 ──
    subagent_enabled: bool | None = None
    max_iterations: int | None = None
    friendly_error_messages: bool | None = None
    # ── 上下文与记忆 ──
    max_context_tokens: int | None = Field(default=None, gt=0)
    memory_enabled: bool | None = None
    memory_auto_extract_interval: int | None = Field(default=None, ge=0)
    memory_auto_load_lines: int | None = Field(default=None, gt=0)
    memory_expire_days: int | None = Field(default=None, ge=0)
    chat_history_enabled: bool | None = None
    # ── 记忆维护 ──
    memory_maintenance_enabled: bool | None = None
    memory_maintenance_min_entries: int | None = Field(default=None, ge=1)
    memory_maintenance_new_threshold: int | None = Field(default=None, ge=1)
    memory_maintenance_interval_hours: float | None = Field(default=None, ge=0.5)
    memory_maintenance_model: str | None = None
    # ── 摘要与压缩 ──
    summarization_enabled: bool | None = None
    summarization_threshold_ratio: float | None = Field(default=None, gt=0, lt=1)
    summarization_keep_recent_turns: int | None = Field(default=None, gt=0)
    compaction_enabled: bool | None = None
    compaction_threshold_ratio: float | None = Field(default=None, gt=0, lt=1)
    compaction_keep_recent_turns: int | None = Field(default=None, gt=0)
    compaction_max_summary_tokens: int | None = Field(default=None, gt=0)
    prompt_cache_key_enabled: bool | None = None
    # ── 推理配置 ──
    thinking_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    thinking_budget: int | None = Field(default=None, ge=0)
    # ── 子代理 ──
    subagent_max_iterations: int | None = Field(default=None, gt=0)
    subagent_timeout_seconds: int | None = Field(default=None, gt=0)
    subagent_max_consecutive_failures: int | None = Field(default=None, gt=0)
    parallel_subagent_max: int | None = Field(default=None, gt=0)
    # ── LLM 重试 ──
    llm_retry_max_attempts: int | None = Field(default=None, ge=1)
    llm_retry_base_delay_seconds: float | None = Field(default=None, gt=0)
    llm_retry_max_delay_seconds: float | None = Field(default=None, gt=0)
    # ── 视觉 ──
    main_model_vision: Literal["auto", "true", "false"] | None = None
    image_keep_rounds: int | None = Field(default=None, gt=0)
    image_max_active: int | None = Field(default=None, gt=0)
    image_token_budget: int | None = Field(default=None, gt=0)
    # ── 系统消息与工具 ──
    system_message_mode: Literal["auto", "merge", "replace"] | None = None
    tool_result_hard_cap_chars: int | None = Field(default=None, ge=0)
    large_excel_threshold_bytes: int | None = Field(default=None, gt=0)
    parallel_readonly_tools: bool | None = None
    hooks_command_enabled: bool | None = None
    hooks_command_timeout_seconds: int | None = Field(default=None, gt=0)
    hooks_output_max_chars: int | None = Field(default=None, gt=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = None
    # ── 代码策略 ──
    code_policy_enabled: bool | None = None
    code_policy_green_auto_approve: bool | None = None
    code_policy_yellow_auto_approve: bool | None = None
    tool_schema_validation_mode: Literal["off", "shadow", "enforce"] | None = None
    tool_schema_validation_canary_percent: int | None = Field(default=None, ge=0, le=100)
    tool_schema_strict_path: bool | None = None
    # ── 技能发现 ──
    skills_context_char_budget: int | None = Field(default=None, ge=0)
    skills_discovery_enabled: bool | None = None
    skills_discovery_scan_workspace_ancestors: bool | None = None
    skills_discovery_include_agents: bool | None = None
    skills_discovery_scan_external_tool_dirs: bool | None = None
    # ── Embedding / 语义检索 ──
    embedding_enabled: bool | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = Field(default=None, gt=0)
    embedding_timeout_seconds: float | None = Field(default=None, gt=0)
    memory_semantic_top_k: int | None = Field(default=None, gt=0)
    memory_semantic_threshold: float | None = Field(default=None, ge=0, le=1)
    memory_semantic_fallback_recent: int | None = Field(default=None, gt=0)
    # ── Playbook ──
    playbook_enabled: bool | None = None
    playbook_max_bullets: int | None = Field(default=None, gt=0)
    registry_semantic_top_k: int | None = Field(default=None, gt=0)
    registry_semantic_threshold: float | None = Field(default=None, ge=0, le=1)
    # ── 内置搜索引擎 ──
    exa_search_enabled: bool | None = None
    search_default_provider: Literal["exa", "tavily", "brave"] | None = None
    exa_api_key: str | None = None
    tavily_api_key: str | None = None
    brave_api_key: str | None = None


@router.put("/api/v1/config/runtime")
async def update_runtime_config(request: RuntimeConfigUpdate, raw_request: Request) -> JSONResponse:
    """更新运行时行为配置并持久化到正式仓。"""
    assert get_config() is not None, "服务未初始化"
    updates: dict[str, str] = {}

    payload = request.model_dump(exclude_none=True)
    # 过滤掉前端回传的掩码 API Key（含 * 号），避免覆盖真实密钥
    _API_KEY_FIELDS = {"exa_api_key", "tavily_api_key", "brave_api_key"}
    for ak_field in _API_KEY_FIELDS:
        val = payload.get(ak_field)
        if isinstance(val, str) and ("*" in val or val == ""):
            payload.pop(ak_field, None)
    if not payload:
        return _error_json_response(400, "无有效更新字段")

    for field, value in payload.items():
        env_key = _RUNTIME_ENV_KEYS.get(field)
        if env_key is None:
            continue
        if isinstance(value, bool):
            str_val = "true" if value else "false"
        else:
            str_val = str(value)
        updates[env_key] = str_val

    if updates:
        _persist_env_updates(updates)

    # 同步更新内存中的 config 实例
    for field, value in payload.items():
        if hasattr(get_config(), field):
            object.__setattr__(get_config(), field, value)

    # 上下文窗口 / 压缩配置必须广播到已打开的对话。
    # 引擎持有 replace() 后的 config 副本，只改全局 get_config() 不会反映到对话页。
    _CONTEXT_OPT_KEYS = {
        "max_context_tokens",
        "compaction_enabled",
        "compaction_threshold_ratio",
    }
    if payload.keys() & _CONTEXT_OPT_KEYS and get_session_manager() is not None:
        await get_session_manager().broadcast_context_optimization(
            max_context_tokens=payload.get("max_context_tokens"),
            compaction_enabled=payload.get("compaction_enabled"),
            compaction_threshold_ratio=payload.get("compaction_threshold_ratio"),
        )

    # 需要重启才能生效的配置项集合
    _RESTART_REQUIRED_KEYS = {
        "embedding_enabled",
        "deploy_mode",
        "mcp_shared_manager",
    }
    # 人类可读的重启原因映射
    _RESTART_REASON_MAP: dict[str, str] = {
        "embedding_enabled": "语义检索配置已更新",
        "deploy_mode": "部署模式已更改",
        "mcp_shared_manager": "MCP 管理器配置已更改",
    }
    # 仅需 MCP 热重载的配置项（搜索引擎相关）
    _MCP_RELOAD_KEYS = {
        "exa_search_enabled",
        "search_default_provider",
        "exa_api_key",
        "tavily_api_key",
        "brave_api_key",
    }

    restart_keys = payload.keys() & _RESTART_REQUIRED_KEYS
    need_restart = len(restart_keys) > 0
    reload_keys = payload.keys() & _MCP_RELOAD_KEYS
    need_mcp_reload = len(reload_keys) > 0 and not need_restart

    # 构建重启原因描述
    restart_reason = ""
    if need_restart:
        reasons = [_RESTART_REASON_MAP.get(k, k) for k in restart_keys]
        restart_reason = "、".join(reasons)

    # MCP 热重载：搜索引擎配置变更时重建 MCP 连接
    mcp_reloaded = False
    mcp_reload_error = ""
    if need_mcp_reload:
        try:
            mcp_manager = (
                getattr(get_session_manager(), "_shared_mcp_manager", None)
                if get_session_manager() is not None
                else None
            )
            if mcp_manager is not None and getattr(get_session_manager(), "_registry", None) is not None:
                await mcp_manager.shutdown()
                mcp_manager._initialized = False
                if get_session_manager() is not None:
                    get_session_manager().reset_mcp_initialized()
                await mcp_manager.initialize(getattr(get_session_manager(), "_registry", None))
                mcp_reloaded = True
                logger.info("搜索引擎配置更新，MCP 热重载完成")
        except Exception as exc:
            mcp_reload_error = str(exc)
            logger.error("MCP 热重载失败: %s", exc, exc_info=True)

    resp = JSONResponse(content={
        "status": "ok",
        "updated": list(payload.keys()),
        "restarting": need_restart,
        "restart_reason": restart_reason,
        "mcp_reloaded": mcp_reloaded,
        "mcp_reload_error": mcp_reload_error,
    })
    if need_restart:
        set_restart_reason(restart_reason)
        from starlette.background import BackgroundTask
        from excelmanus.restart import schedule_restart
        resp.background = BackgroundTask(schedule_restart)
    return resp



