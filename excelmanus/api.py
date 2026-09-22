"""API 服务模块：基于 FastAPI 的 REST API 服务。

端点：
- POST   /api/v1/chat*                     对话 / SSE / 回滚（见 api_routes_chat.py）
- GET    /api/v1/skills                      列出 Skillpack 摘要
- GET    /api/v1/skills/{name}               查询 Skillpack 详情
- POST   /api/v1/skills                      创建 project Skillpack
- PATCH  /api/v1/skills/{name}               更新 project Skillpack
- DELETE /api/v1/skills/{name}               软删除 project Skillpack
- POST   /api/v1/skills/import               从本地路径或 GitHub URL 导入 Skillpack
- GET    /api/v1/mcp/servers                 列出 MCP Server 配置+状态
- POST   /api/v1/mcp/servers                 新增 MCP Server
- PUT    /api/v1/mcp/servers/{name}          更新 MCP Server 配置
- DELETE /api/v1/mcp/servers/{name}          删除 MCP Server
- POST   /api/v1/mcp/reload                  热重载所有 MCP 连接
- POST   /api/v1/mcp/servers/{name}/test     测试单个 MCP Server 连接
- GET    /api/v1/files/excel                  返回 xlsx 文件二进制流（Univer 加载）
- GET    /api/v1/files/excel/snapshot         返回 Excel 轻量 JSON 快照（聊天内嵌预览）
- POST   /api/v1/files/excel/write            侧边面板编辑回写单元格
- DELETE /api/v1/sessions/{session_id}        删除会话
- GET    /api/v1/sessions/{sid}/operations     操作历史时间线列表
- GET    /api/v1/sessions/{sid}/operations/{id} 操作详情（含 diff）
- POST   /api/v1/sessions/{sid}/operations/{id}/undo 回滚指定操作
- GET    /api/v1/health                       健康检查
- PUT    /api/v1/onboarding                   持久化新手引导进度
"""

from __future__ import annotations

# ── Web 依赖守卫 ─────────────────────────────────────────
_web_missing: list[str] = []
try:
    import fastapi as _fastapi_check  # noqa: F401
except ImportError:
    _web_missing.append("fastapi")
try:
    import uvicorn as _uvicorn_check  # noqa: F401
except ImportError:
    _web_missing.append("uvicorn")
if _web_missing:
    raise ImportError(
        f"Web API 模式缺少依赖: {', '.join(_web_missing)}。"
        f"\n请运行: pip install excelmanus[web]"
    )
del _web_missing
# ─────────────────────────────────────────────────────────

import asyncio
import json
import os
import re
import unicodedata
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, AsyncIterator, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

try:
    import orjson
except ModuleNotFoundError:
    orjson = None


class CustomJSONResponse(JSONResponse):
    """自定义JSON响应类，支持中文字符（ensure_ascii=False）"""

    def render(self, content: Any) -> bytes:
        if orjson is not None:
            # 使用orjson序列化，设置ensure_ascii=False支持中文
            try:
                return orjson.dumps(
                    content,
                    option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS,
                )
            except (TypeError, ValueError):
                pass

        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")

import excelmanus
from excelmanus.config import (
    ConfigError,
    ExcelManusConfig,
    expand_cors_origins,
    load_config,
    load_cors_allow_origins,
    parse_frontend_ports,
)
from excelmanus.logger import get_logger, setup_logging
from excelmanus.mcp.manager import MCPManager
from excelmanus.session import (
    SessionBusyError,
    SessionLimitExceededError,
    SessionManager,
    SessionNotFoundError,
)
from excelmanus.skillpacks import (
    SkillpackLoader,
    SkillRouter,
)
from excelmanus.tools import ToolRegistry

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine
    from excelmanus.skillpacks import SkillpackManager
    from excelmanus.workspace import IsolatedWorkspace

logger = get_logger("api")

# 解释器后台预热每进程只做一次（lifespan 可能随测试/重启反复进入）
_interpreter_warmup_started = False

# ── 请求 / 响应模型 ──────────────────────────────────────


class ErrorResponse(BaseModel):
    """错误响应体（不暴露内部堆栈）。"""

    error: str
    error_id: str


from excelmanus.api_app_state import AppRuntime, RuntimeMiddleware, get_runtime, bind_runtime, reset_runtime


def _get_file_registry(workspace_root: str) -> Any:
    """获取或懒创建指定工作区的 FileRegistry，与引擎共用主库。"""
    from excelmanus.api_app_state import get_file_registry, set_database

    set_database(get_runtime().database)
    return get_file_registry(workspace_root)


def _build_bootstrap_config() -> tuple[ExcelManusConfig, ConfigError | None]:
    """构建应用启动配置：优先完整配置，失败时保留错误并回退到仅供导入期使用的占位配置。"""
    try:
        return load_config(), None
    except ConfigError as exc:
        fallback = ExcelManusConfig(
            api_key="",
            base_url="https://example.invalid/v1",
            model="",
            deploy_mode="server" if os.environ.get("EXCELMANUS_DEPLOY_MODE", "").strip().lower() == "server" else "standalone",
            cors_allow_origins=tuple(load_cors_allow_origins()),
            # Fresh profiles lack model credentials but still need packaged skills.
            skills_system_dir=str(Path(__file__).resolve().parent / "skillpacks" / "system"),
        )
        return fallback, exc


def _resolve_workspace(request: Request, session_id: str | None = None) -> "IsolatedWorkspace":
    from excelmanus.api_app_state import resolve_workspace
    return resolve_workspace(request, session_id=session_id)


def _resolve_workspace_root(request: Request) -> str:
    """返回当前请求对应的工作区根目录路径字符串。"""
    return str(_resolve_workspace(request).root_dir)


async def _has_session_access(session_id: str, request: Request) -> bool:
    """会话存在时返回 True。"""
    if get_runtime().session_manager is None:
        return False
    try:
        await get_runtime().session_manager.get_session_detail(session_id)
    except SessionNotFoundError:
        return False
    return True


from excelmanus.api_app_state import (  # noqa: F401
    _get_probe_job_mgr,
    _list_available_model_names,
    _sync_config_profiles_from_db,
    ensure_active_model,
    get_cap_probe_job_manager,
    get_config_incomplete,
    get_draining,
    get_restart_reason,
    get_skillpack_loader,
    get_skillpack_manager,
    get_tool_registry,
    set_cap_probe_job_manager,
    set_config_incomplete,
    set_draining,
    set_restart_reason,
    set_skillpack_loader,
    set_skillpack_manager,
    set_tool_registry,
)


def _fire_and_forget(coro: Any, *, name: str = "bridge_notify") -> None:
    """安全地 fire-and-forget 一个协程，捕获异常避免 'Task exception was never retrieved' 警告。"""
    from excelmanus.engine_utils import fire_and_forget
    fire_and_forget(coro, name=name)


def _error_json_response(status_code: int, message: str) -> CustomJSONResponse:
    """构建统一错误响应。"""
    error_id = str(uuid.uuid4())
    body = ErrorResponse(error=message, error_id=error_id)
    return CustomJSONResponse(status_code=status_code, content=body.model_dump())


def _make_content_disposition(filename: str) -> str:
    """构建兼容非 ASCII 文件名的 Content-Disposition 头部值（RFC 5987）。"""
    from urllib.parse import quote

    try:
        filename.encode("ascii")
        return f'attachment; filename="{filename}"'
    except UnicodeEncodeError:
        ascii_fallback = filename.encode("ascii", "replace").decode("ascii")
        encoded = quote(filename)
        return (
            f'attachment; filename="{ascii_fallback}"; '
            f"filename*=UTF-8''{encoded}"
        )


def _terminal_display_width(text: str) -> int:
    """计算文本在终端中的显示列宽。

    CJK/全角字符占 2 列；组合符、变体选择符（如 U+FE0F）、
    控制符等零宽字符占 0 列；紧随 U+FE0F 的字符按 emoji 呈现计 2 列。
    """
    width = 0
    for idx, ch in enumerate(text):
        if unicodedata.category(ch) in ("Mn", "Me", "Cf", "Cc"):
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F") or (
            idx + 1 < len(text) and text[idx + 1] == "\ufe0f"
        ):
            width += 2
        else:
            width += 1
    return width


def _render_banner_box(lines: list[str]) -> str:
    """将多行文本渲染为终端等宽边框框（按显示宽度对齐）。"""
    inner = max((_terminal_display_width(line) for line in lines), default=0)
    border = "═" * (inner + 4)
    body = [
        "║  " + line + " " * (inner - _terminal_display_width(line)) + "  ║"
        for line in lines
    ]
    return "\n".join([f"╔{border}╗", *body, f"╚{border}╝"])


# ── Lifespan ──────────────────────────────────────────────


@asynccontextmanager
async def _lifespan_bound(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：初始化配置、注册 Skill、启动清理任务。"""

    set_draining(False)

    # create_app 已在构建应用时确定启动配置；lifespan 不再二次加载。
    bootstrap_error: ConfigError | None = app.state.bootstrap_config_error
    if bootstrap_error is not None:
        set_config_incomplete(True)
        get_runtime().config_incomplete = True
        logger.warning(
            "\n%s",
            _render_banner_box(
                [
                    "⚠️  模型配置缺失，服务以降级模式启动",
                    "",
                    str(bootstrap_error),
                    "",
                    "你可以通过以下任一方式完成配置：",
                    "  1. 打开浏览器访问前端页面，按引导填写 API Key",
                    "  2. 在设置页添加模型档案（保存在主数据库）",
                    "",
                    "配置完成后，通过前端设置页保存即可生效（无需重启）。",
                ]
            ),
        )

    get_runtime().config = app.state.bootstrap_config
    from excelmanus.api_app_state import bind_app_state, set_config_store, set_database
    bind_app_state(app, config=get_runtime().config)
    setup_logging(get_runtime().config.log_level)
    from excelmanus.prompt.cache_restore import log_multi_worker_cache_risk

    log_multi_worker_cache_risk()

    # ── 集中数据管理：注册安装 + 首次迁移 ──────────────
    from excelmanus.data_home import (
        register_installation,
        ensure_data_dirs,
        has_project_local_data,
        migrate_data_from_project,
        is_data_centralized,
        scan_once,
    )
    project_root = Path(__file__).resolve().parent.parent
    logger.info("部署模式: %s", get_runtime().config.deploy_mode)
    try:
        register_installation(project_root)
        if get_runtime().config.data_root:
            ensure_data_dirs()
            # 仅 standalone 模式执行自动迁移；服务器模式由管理员手动触发
            if get_runtime().config.is_standalone:
                if not is_data_centralized() and has_project_local_data(project_root):
                    stats = migrate_data_from_project(project_root)
                    if stats:
                        logger.info("首次运行数据迁移完成: %s", stats)
    except Exception:
        logger.debug("集中数据管理初始化失败（非致命）", exc_info=True)

    # 初始化工具层
    get_runtime().tool_registry = ToolRegistry()
    get_runtime().tool_registry.register_builtin_tools(get_runtime().config.workspace_root)
    set_tool_registry(get_runtime().tool_registry)

    # 初始化 Skillpack 层
    get_runtime().skillpack_loader = SkillpackLoader(get_runtime().config, get_runtime().tool_registry)
    get_runtime().skillpack_loader.load_all()
    get_runtime().skill_router = SkillRouter(get_runtime().config, get_runtime().skillpack_loader)
    from excelmanus.skillpacks import SkillpackManager
    get_runtime().skillpack_manager = SkillpackManager(get_runtime().config, get_runtime().skillpack_loader)

    set_skillpack_loader(get_runtime().skillpack_loader)
    set_skillpack_manager(get_runtime().skillpack_manager)

    # 初始化统一数据库（配置档案必须落库，不能绑在聊天记录开关上）
    from excelmanus.data_home import resolve_db_path
    from excelmanus.database import Database
    from excelmanus.settings_persist import bind_settings_store, refresh_config_in_place

    get_runtime().database = None
    chat_history = None
    resolved_db_path = resolve_db_path()
    get_runtime().database = Database(resolved_db_path)
    logger.info("统一数据库已启用: %s", resolved_db_path)
    if get_runtime().config.chat_history_enabled:
        from excelmanus.chat_history import ChatHistoryStore
        chat_history = ChatHistoryStore(get_runtime().database)

    if get_runtime().database is not None:
        store = bind_settings_store(get_runtime().database)
        get_runtime().config_store = store
        if getattr(app.state, "bootstrap_from_settings", True):
            refresh_config_in_place(get_runtime().config)
            if get_runtime().config.api_key and get_runtime().config.base_url and get_runtime().config.model:
                set_config_incomplete(False)
                app.state.bootstrap_config_error = None
        _sync_config_profiles_from_db()
        ensure_active_model()
        logger.info("GlobalConfigStore 已初始化")
    set_database(get_runtime().database)
    set_config_store(get_runtime().config_store)

    # 初始化异步能力探测任务管理器
    from excelmanus.capability_probe_jobs import CapabilityProbeJobManager
    get_runtime().cap_probe_job_manager = CapabilityProbeJobManager()
    set_cap_probe_job_manager(get_runtime().cap_probe_job_manager)

    # 初始化会话管理器。MCP 连接放到 yield 之后的后台任务，避免卡住 health。
    shared_mcp_manager = MCPManager(get_runtime().config.workspace_root, app_config=get_runtime().config)

    get_runtime().session_manager = SessionManager(
        max_sessions=get_runtime().config.max_sessions,
        ttl_seconds=get_runtime().config.session_ttl_seconds,
        config=get_runtime().config,
        registry=get_runtime().tool_registry,
        skill_router=get_runtime().skill_router,
        shared_mcp_manager=shared_mcp_manager,
        chat_history=chat_history,
        database=get_runtime().database,
        config_store=get_runtime().config_store,
    )
    bind_app_state(app, session_manager=get_runtime().session_manager)
    try:
        get_runtime().session_manager.ensure_default_workspace()
    except Exception:
        logger.warning("默认工作区登记失败", exc_info=True)
    await get_runtime().session_manager.start_background_cleanup()

    # 初始化全局 RulesManager + 共享 PersistentMemory（供 API 层直接使用）
    try:
        from excelmanus.rules import RulesManager as _RM
        from excelmanus.stores.rules_store import RulesStore as _RS
        _rules_db_store = _RS(get_runtime().database) if get_runtime().database is not None else None
        get_runtime().rules_manager = _RM(db_store=_rules_db_store)
    except Exception:
        logger.debug("RulesManager 初始化失败", exc_info=True)

    if get_runtime().config.memory_enabled:
        try:
            from excelmanus.persistent_memory import PersistentMemory as _PM
            if get_runtime().database is not None:
                from excelmanus.stores.memory_store import MemoryStore as _MS
                _mem_backend = _MS(get_runtime().database)
            else:
                from excelmanus.stores.file_memory_backend import FileMemoryBackend as _FMB
                _mem_backend = _FMB(
                    memory_dir=get_runtime().config.memory_dir,
                    auto_load_lines=get_runtime().config.memory_auto_load_lines,
                )
            get_runtime().persistent_memory = _PM(
                backend=_mem_backend,
                auto_load_lines=get_runtime().config.memory_auto_load_lines,
            )
        except Exception:
            logger.debug("API PersistentMemory 初始化失败", exc_info=True)

    loaded_skillpacks = (
        sorted(get_runtime().skillpack_loader.get_skillpacks().keys())
        if get_runtime().skillpack_loader is not None
        else []
    )
    tool_names = (
        sorted(get_runtime().tool_registry.get_tool_names())
        if get_runtime().tool_registry is not None
        else []
    )
    app.state.workspace_root = get_runtime().config.workspace_root
    app.state.data_root = get_runtime().config.data_root
    if get_runtime().database is not None:
        try:
            from excelmanus.auth.providers.credential_store import CredentialStore as _CredStore
            _cred_store = _CredStore(get_runtime().database.conn)
            try:
                from excelmanus.auth.providers.workbuddy import migrate_legacy_workbuddy
                from excelmanus.api_app_state import get_config_store as _get_cs
                migrate_legacy_workbuddy(_cred_store, _get_cs())
            except Exception:
                logger.debug("旧版 workbuddy 数据迁移失败", exc_info=True)
            app.state.credential_store = _cred_store
            if get_runtime().session_manager is not None:
                get_runtime().session_manager.set_credential_store(_cred_store)
            try:
                from excelmanus.auth.providers.resolver import CredentialResolver as _CredResolver
                _cred_resolver = _CredResolver(credential_store=_cred_store)
                app.state.credential_resolver = _cred_resolver
                if get_runtime().session_manager is not None:
                    get_runtime().session_manager.set_credential_resolver(_cred_resolver)
                logger.info("CredentialResolver 已初始化")
            except Exception:
                logger.debug("CredentialResolver 初始化失败", exc_info=True)
                app.state.credential_resolver = None
        except Exception:
            logger.debug("CredentialStore 初始化失败", exc_info=True)
            app.state.credential_store = None
            app.state.credential_resolver = None
        try:
            from excelmanus.pool.service import PoolService as _PoolService
            _pool_service = _PoolService(
                conn=get_runtime().database.conn,
                credential_store=getattr(app.state, "credential_store", None),
            )
            app.state.pool_service = _pool_service
            _cr = getattr(app.state, "credential_resolver", None)
            if _cr is not None:
                _cr._pool_service = _pool_service
                _cr._pool_enabled = getattr(get_runtime().config, "pool_enabled", False)
            logger.info("PoolService 已初始化")
            if getattr(get_runtime().config, "pool_auto_enabled", False) and getattr(get_runtime().config, "pool_enabled", False):
                try:
                    from excelmanus.pool.breaker import BreakerManager as _BreakerMgr
                    from excelmanus.pool.metrics import MetricsAggregator as _MetricsAgg
                    from excelmanus.pool.auto_rotate import PoolAutoRotateService as _AutoRotateSvc
                    _breaker_mgr = _BreakerMgr(
                        conn=get_runtime().database.conn,
                        failure_threshold=getattr(
                            get_runtime().config, "pool_auto_breaker_threshold", 5,
                        ),
                        open_seconds=getattr(
                            get_runtime().config, "pool_auto_breaker_open_seconds", 120,
                        ),
                    )
                    app.state.pool_breaker_manager = _breaker_mgr
                    _metrics_agg = _MetricsAgg(conn=get_runtime().database.conn)
                    app.state.pool_metrics_aggregator = _metrics_agg
                    _auto_rotate_svc = _AutoRotateSvc(
                        conn=get_runtime().database.conn,
                        pool_service=_pool_service,
                        default_cooldown_seconds=getattr(
                            get_runtime().config, "pool_auto_default_cooldown_seconds", 300,
                        ),
                        breaker=_breaker_mgr,
                    )
                    app.state.pool_auto_rotate_service = _auto_rotate_svc
                    logger.info("PoolAutoRotateService + BreakerManager + MetricsAggregator 已初始化")
                except Exception:
                    logger.debug("PoolAutoRotateService 初始化失败", exc_info=True)
                    app.state.pool_auto_rotate_service = None
                    app.state.pool_breaker_manager = None
                    app.state.pool_metrics_aggregator = None
            else:
                app.state.pool_auto_rotate_service = None
                app.state.pool_breaker_manager = None
                app.state.pool_metrics_aggregator = None
        except Exception:
            logger.debug("PoolService 初始化失败", exc_info=True)
            app.state.pool_service = None
            app.state.pool_auto_rotate_service = None

    logger.info(
        "API 服务启动完成，已加载 %d 个工具、%d 个 Skillpack",
        len(tool_names),
        len(loaded_skillpacks),
    )

    # ── 后台静默检查更新（非阻塞） ──────────────────────
    async def _background_update_check() -> None:
        try:
            import asyncio
            from functools import partial
            from excelmanus.updater import check_for_updates, get_current_version
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(None, partial(check_for_updates, project_root, force=False))
            if info.has_update:
                logger.info(
                    "发现新版本: %s → %s（%d 个新提交）。"
                    "可在设置页「版本管理」中执行更新。",
                    get_current_version(project_root), info.latest, info.commits_behind,
                )
        except Exception:
            logger.debug("启动时后台版本检查失败（非致命）", exc_info=True)

    if os.environ.get("EXCELMANUS_DESKTOP") != "1":
        _fire_and_forget(_background_update_check(), name="update_check")

    # ── 附件派生缓存清理（非阻塞，只动可再生数据） ─────────
    async def _background_attachment_sweep() -> None:
        try:
            from excelmanus.attachments.store import sweep_stale_caches

            removed = sweep_stale_caches()
            if any(removed.values()):
                logger.info("附件缓存清理完成: %s", removed)
        except Exception:
            logger.debug("启动时附件缓存清理失败（非致命）", exc_info=True)

    _fire_and_forget(_background_attachment_sweep(), name="attachment_sweep")

    # ── 号池快照聚合后台任务（每 5 分钟） ──────────────────
    _pool_snapshot_task: asyncio.Task | None = None
    _pool_svc_bg = getattr(app.state, "pool_service", None)
    if _pool_svc_bg is not None:
        async def _pool_snapshot_loop() -> None:
            while True:
                await asyncio.sleep(300)  # 5 分钟
                try:
                    count = _pool_svc_bg.refresh_snapshots()
                    if count:
                        logger.debug("号池快照刷新: %d 个账号", count)
                except Exception:
                    logger.debug("号池快照刷新失败", exc_info=True)

        _pool_snapshot_task = asyncio.create_task(_pool_snapshot_loop())
        logger.info("号池快照聚合后台任务已启动（间隔 5 分钟）")

    # ── 号池自动轮换后台任务 ──────────────────────────────────
    _pool_auto_rotate_task: asyncio.Task | None = None
    _auto_rotate_svc_bg = getattr(app.state, "pool_auto_rotate_service", None)
    if _auto_rotate_svc_bg is not None:
        _auto_interval = getattr(get_runtime().config, "pool_auto_interval_seconds", 60)

        async def _pool_auto_rotate_loop() -> None:
            while True:
                await asyncio.sleep(_auto_interval)
                try:
                    results = await _auto_rotate_svc_bg.evaluate_all_policies()
                    actions = [r for r in results if r.get("action") != "none"]
                    if actions:
                        logger.info("号池自动轮换评估完成: %d 个操作", len(actions))
                except Exception:
                    logger.debug("号池自动轮换评估失败", exc_info=True)

        _pool_auto_rotate_task = asyncio.create_task(_pool_auto_rotate_loop())
        logger.info("号池自动轮换后台任务已启动（间隔 %ds）", _auto_interval)

    # ── 号池指标聚合后台任务（60s）───────────────────
    _pool_metrics_task: asyncio.Task | None = None
    _metrics_agg_bg = getattr(app.state, "pool_metrics_aggregator", None)
    if _metrics_agg_bg is not None and _auto_rotate_svc_bg is not None:
        async def _pool_metrics_loop() -> None:
            while True:
                await asyncio.sleep(60)
                try:
                    policies = _auto_rotate_svc_bg.list_policies()
                    count = _metrics_agg_bg.aggregate_all_scopes(policies)
                    if count:
                        logger.debug("号池指标聚合完成: %d 个 scope", count)
                except Exception:
                    logger.debug("号池指标聚合失败", exc_info=True)

        _pool_metrics_task = asyncio.create_task(_pool_metrics_loop())
        logger.info("号池指标聚合后台任务已启动（间隔 60s）")

    async def _background_mcp_init() -> None:
        try:
            await shared_mcp_manager.initialize(get_runtime().tool_registry)
            mcp_info = shared_mcp_manager.get_server_info()
            mcp_ready = sum(1 for s in mcp_info if s["status"] == "ready")
            if mcp_info:
                logger.info(
                    "MCP 启动自动连接完成: %d/%d 个 Server 就绪",
                    mcp_ready, len(mcp_info),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("MCP 启动自动连接失败", exc_info=True)

    mcp_init_task = asyncio.create_task(_background_mcp_init(), name="mcp_initialize")

    def _background_install_scan() -> None:
        try:
            scan_once(skip_desktop_scan=get_runtime().config.is_server)
        except Exception:
            logger.debug("主动扫描失败（非致命）", exc_info=True)

    scan_task = asyncio.get_running_loop().run_in_executor(
        None, _background_install_scan,
    )

    if os.environ.get("EXCELMANUS_DESKTOP") == "1":
        global _interpreter_warmup_started
        if not _interpreter_warmup_started:
            _interpreter_warmup_started = True
            # 随包运行时不带 .pyc，首个 run_code 的冷探测可能撞超时；
            # 启动即后台预热，把冷加载成本移出用户首个代码执行。
            import threading

            def _background_interpreter_warmup() -> None:
                try:
                    from excelmanus.tools.code_tools import warmup_interpreter

                    warmup_interpreter()
                except Exception:
                    logger.debug("解释器预热失败（首个 run_code 会重新探测）", exc_info=True)

            threading.Thread(
                target=_background_interpreter_warmup,
                name="interpreter-warmup",
                daemon=True,
            ).start()

    if os.environ.get("EXCELMANUS_DESKTOP_CONTROL_STDIN") == "1":
        import threading
        from excelmanus import restart

        def read_parent_control() -> None:
          try:
            import sys

            for line in sys.stdin:
              if line.strip() == "shutdown":
                break
          except Exception:
            pass
          if restart._desktop_server is not None:
            restart._desktop_server.should_exit = True

        threading.Thread(
            target=read_parent_control, name="desktop-control", daemon=True
        ).start()

    yield

    # ── Graceful Shutdown ──────────────────────────────────────
    if not mcp_init_task.done():
        mcp_init_task.cancel()
        try:
            await mcp_init_task
        except (asyncio.CancelledError, Exception):
            pass
    if not scan_task.done():
        scan_task.cancel()
        try:
            await scan_task
        except (asyncio.CancelledError, Exception):
            pass
    # 取消号池指标聚合后台任务
    if _pool_metrics_task is not None and not _pool_metrics_task.done():
        _pool_metrics_task.cancel()
        try:
            await _pool_metrics_task
        except (asyncio.CancelledError, Exception):
            pass
        logger.debug("号池指标聚合后台任务已取消")
    # 取消号池自动轮换后台任务
    if _pool_auto_rotate_task is not None and not _pool_auto_rotate_task.done():
        _pool_auto_rotate_task.cancel()
        try:
            await _pool_auto_rotate_task
        except (asyncio.CancelledError, Exception):
            pass
        logger.debug("号池自动轮换后台任务已取消")
    # 取消号池快照后台任务
    if _pool_snapshot_task is not None and not _pool_snapshot_task.done():
        _pool_snapshot_task.cancel()
        try:
            await _pool_snapshot_task
        except (asyncio.CancelledError, Exception):
            pass
        logger.debug("号池快照后台任务已取消")

    set_draining(True)
    logger.info("API 服务进入 draining 状态，等待活跃连接排空...")

    if get_runtime().session_manager is not None:
        drain_timeout = 30  # 最长等待 30 秒
        for _drain_i in range(drain_timeout):
            active = await get_runtime().session_manager.get_active_count()
            if active == 0:
                logger.info("所有活跃连接已排空")
                break
            if _drain_i % 5 == 0:
                logger.info("等待 %d 个活跃连接排空... (%d/%ds)", active, _drain_i, drain_timeout)
            await asyncio.sleep(1)
        else:
            active = await get_runtime().session_manager.get_active_count()
            if active > 0:
                logger.warning("排空超时，仍有 %d 个活跃连接，强制关闭", active)

    # 停止异步探测任务
    mgr = get_cap_probe_job_manager() or get_runtime().cap_probe_job_manager
    if mgr is not None:
        await mgr.shutdown()
        set_cap_probe_job_manager(None)

    # 关闭所有会话与 MCP 连接
    if get_runtime().session_manager is not None:
        await get_runtime().session_manager.shutdown()

    # 关闭统一数据库
    if get_runtime().database is not None:
        get_runtime().database.close()

    logger.info("API 服务已关闭")


# ── 全局异常处理 ──────────────────────────────────────────


# 错误消息映射表：将内部错误类型/消息映射为友好用户消息
_ERROR_MESSAGE_MAP: dict[str, str] = {
    # 会话相关错误
    "SessionNotFoundError": "会话已过期或不存在，请刷新页面重新开始。",
    "SessionLimitExceededError": "系统繁忙，会话数量已达上限，请稍后再试。",
    "SessionBusyError": "会话正在处理中，请稍等片刻再提交新请求。",
    # 配置相关错误
    "ConfigError": "配置错误，请检查设置后重试。",
    # 工具执行错误
    "ToolNotFoundError": "请求的工具不可用，请刷新页面后重试。",
    "ToolExecutionError": "工具执行失败，请稍后重试。",
    "ToolNotAllowedError": "当前操作不被允许，请尝试其他方式。",
    # Skillpack 错误
    "SkillpackNotFoundError": "技能包不存在，请刷新页面后重试。",
    "SkillpackManagerError": "技能包加载失败，请稍后重试。",
    # 安全相关错误
    "SecurityViolationError": "安全检查未通过，请检查输入后重试。",
    # 数据库错误
    "database": "数据库操作失败，请稍后重试。",
    "sqlite": "数据存储失败，请稍后重试。",
    # 网络/API 错误
    "timeout": "请求超时，请稍后重试。",
    "TimeoutError": "请求超时，请稍后重试。",
    "ConnectionError": "网络连接失败，请检查网络后重试。",
    "ConnectionRefusedError": "服务暂不可用，请稍后重试。",
    # 认证错误
    "AuthenticationError": "认证失败，请重新登录。",
    "UnauthorizedError": "登录已过期，请重新登录。",
    "PermissionError": "权限不足，无法执行此操作。",
    # 文件相关错误
    "FileNotFoundError": "文件不存在，请检查路径后重试。",
    "PermissionError": "没有文件访问权限，请检查权限设置。",
    # 内存/资源错误
    "MemoryError": "内存不足，请减少操作范围后重试。",
    "out of memory": "内存不足，请减少操作范围后重试。",
    # 默认内部错误（当无法映射时）
    "internal_error": "服务处理出现异常，请稍后重试。",
}


def _get_friendly_error_message(
    exc: Exception, friendly_enabled: bool
) -> str:
    """获取友好的错误消息。

    如果 friendly_error_messages 功能开启，则尝试将内部错误映射为友好消息；
    否则返回原始错误消息或通用内部错误消息。
    """
    if not friendly_enabled:
        # 功能关闭时，返回原始错误消息
        return str(exc)

    # 尝试匹配错误类型名
    exc_type_name = type(exc).__name__
    if exc_type_name in _ERROR_MESSAGE_MAP:
        return _ERROR_MESSAGE_MAP[exc_type_name]

    # 尝试匹配错误消息中的关键词
    exc_message = str(exc).lower()
    for key, friendly_msg in _ERROR_MESSAGE_MAP.items():
        if key in exc_message:
            return friendly_msg

    # 无法映射时返回通用友好消息
    return "服务处理出现异常，请稍后重试。"



async def _handle_session_not_found(
    request: Request, exc: SessionNotFoundError
) -> JSONResponse:
    """会话不存在 → 404。"""
    error_id = str(uuid.uuid4())
    friendly_enabled = get_runtime().config.friendly_error_messages if get_runtime().config else False
    error_msg = _get_friendly_error_message(exc, friendly_enabled)
    body = ErrorResponse(error=error_msg, error_id=error_id)
    return JSONResponse(status_code=404, content=body.model_dump())


async def _handle_session_limit(
    request: Request, exc: SessionLimitExceededError
) -> JSONResponse:
    """会话数量超限 → 429。"""
    error_id = str(uuid.uuid4())
    friendly_enabled = get_runtime().config.friendly_error_messages if get_runtime().config else False
    error_msg = _get_friendly_error_message(exc, friendly_enabled)
    body = ErrorResponse(error=error_msg, error_id=error_id)
    return JSONResponse(status_code=429, content=body.model_dump())


async def _handle_session_busy(
    request: Request, exc: SessionBusyError
) -> JSONResponse:
    """会话正在处理中 → 409。"""
    error_id = str(uuid.uuid4())
    friendly_enabled = get_runtime().config.friendly_error_messages if get_runtime().config else False
    error_msg = _get_friendly_error_message(exc, friendly_enabled)
    body = ErrorResponse(error=error_msg, error_id=error_id)
    return JSONResponse(status_code=409, content=body.model_dump())


async def _handle_unexpected(
    request: Request, exc: Exception
) -> JSONResponse:
    """未预期异常 → 500，返回 error_id，不暴露堆栈。"""
    error_id = str(uuid.uuid4())
    logger.error(
        "未预期异常 [error_id=%s]: %s", error_id, exc, exc_info=True
    )
    friendly_enabled = get_runtime().config.friendly_error_messages if get_runtime().config else False
    error_msg = _get_friendly_error_message(exc, friendly_enabled)
    body = ErrorResponse(error=error_msg, error_id=error_id)
    return JSONResponse(status_code=500, content=body.model_dump())


def _register_exception_handlers(application: FastAPI) -> None:
    """注册全局异常处理器。"""
    application.add_exception_handler(SessionNotFoundError, _handle_session_not_found)
    application.add_exception_handler(SessionLimitExceededError, _handle_session_limit)
    application.add_exception_handler(SessionBusyError, _handle_session_busy)
    application.add_exception_handler(Exception, _handle_unexpected)


def _discover_lan_ipv4_hosts() -> tuple[str, ...]:
    """探测本机局域网 IPv4。UDP connect 不发包，避免 Windows 上 gethostname 卡住。"""
    try:
        import socket as _sock

        sock = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        sock.settimeout(0)
        sock.connect(("10.254.254.254", 1))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            return (ip,)
    except Exception:
        return ()
    return ()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    token = bind_runtime(app.state.runtime)
    try:
        async with _lifespan_bound(app):
            yield
    finally:
        reset_runtime(token)


def create_app(
    config: ExcelManusConfig | None = None,
) -> FastAPI:
    """创建 FastAPI 应用，CORS 与运行期配置共享同一来源。

    Args:
        config: 预构建的配置对象；为 None 时从主库设置加载。
    """
    bootstrap_error: ConfigError | None = None
    bootstrap_from_settings = config is None
    bootstrap_config = config
    if bootstrap_config is None:
        bootstrap_config, bootstrap_error = _build_bootstrap_config()
    assert bootstrap_config is not None

    from excelmanus.auth.access import validate_access_config
    validate_access_config(server=bootstrap_config.deploy_mode == "server")

    application = FastAPI(
        title="ExcelManus API",
        version=excelmanus.__version__,
        lifespan=lifespan,
    )
    application.state.runtime = AppRuntime(config=bootstrap_config)
    application.add_middleware(RuntimeMiddleware)
    application.state.bootstrap_config = bootstrap_config
    application.state.bootstrap_config_error = bootstrap_error
    application.state.bootstrap_from_settings = bootstrap_from_settings

    # 显式配置 + loopback（localhost/127.0.0.1/::1）+ 本机 LAN IP。
    # 浏览器视 loopback 别名为不同源；缺 127 时直连 health/SSE 会被拦。
    cors_origins = expand_cors_origins(
        bootstrap_config.cors_allow_origins,
        frontend_ports=parse_frontend_ports(os.environ.get("EXCELMANUS_FRONTEND_PORT")),
        extra_hosts=_discover_lan_ipv4_hosts(),
    )

    from excelmanus.auth.manage_token import ManageTokenMiddleware
    application.add_middleware(ManageTokenMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "Accept",
            "X-Requested-With",
            "Cache-Control",
            "X-ExcelManus-Token",
        ],
        expose_headers=["X-Request-Id", "Content-Disposition", "X-ExcelManus-Auth", "Retry-After"],
    )

    _register_exception_handlers(application)

    # 注册认证路由
    from excelmanus.auth.router import router as auth_router
    application.include_router(auth_router)
    from excelmanus.auth.access import router as access_router
    application.include_router(access_router)

    # 注册子路由模块
    from excelmanus.api_routes_mcp import router as mcp_router
    application.include_router(mcp_router)
    from excelmanus.api_routes_rules import router as rules_router
    application.include_router(rules_router)
    from excelmanus.api_routes_version import router as version_router
    application.include_router(version_router)
    from excelmanus.pool.router import router as pool_router
    application.include_router(pool_router)
    from excelmanus.api_routes_files import router as files_router
    application.include_router(files_router)
    from excelmanus.api_routes_sessions import router as sessions_router
    application.include_router(sessions_router)
    from excelmanus.api_routes_workspaces import router as workspaces_router
    application.include_router(workspaces_router)
    from excelmanus.api_routes_workspace import router as workspace_router
    application.include_router(workspace_router)
    from excelmanus.api_routes_config import router as config_router
    application.include_router(config_router)
    from excelmanus.api_routes_skills import router as skills_router
    application.include_router(skills_router)
    from excelmanus.api_routes_system import router as system_router
    application.include_router(system_router)

    from excelmanus.api_routes_chat import router as chat_router
    application.include_router(chat_router)

    return application


def _resolve_excel_path(
    path: str,
    session_id: str | None = None,
    *,
    workspace_root: str | None = None,
) -> str | None:
    """将相对/绝对路径解析为安全的绝对路径。测试可 patch 此函数。"""
    from excelmanus.api_app_state import resolve_excel_path

    return resolve_excel_path(
        path, session_id, workspace_root=workspace_root
    )


def _safe_uploads_path(uploads_dir: "Path", relative: str) -> "Path | None":
    """在 uploads_dir 下解析 relative 路径并确保不越界。"""
    from excelmanus.api_app_state import safe_uploads_path

    return safe_uploads_path(uploads_dir, relative)


# ── Upload / mentions / command / health（已提取到 api_routes_system.py）──────


# ── Settings API（已提取到 api_routes_config.py）──────



# 默认 ASGI app（供 `uvicorn excelmanus.api:app` 与测试直接导入）
from excelmanus.api_routes_chat import (  # noqa: F401
    AbortRequest,
    AnswerQuestionRequest,
    ApproveRequest,
    ChatRequest,
    ChatResponse,
    GuideRequest,
    ImageAttachment,
    RollbackPreviewRequest,
    RollbackRequest,
    _SubscribeRequest,
    _build_reply_sse,
    _generate_session_title_background,
    _generate_session_title_with_timeout,
    _handle_save_command,
    _is_save_command,
    _persist_excel_event,
    _persist_failure_guidance_message,
    _public_excel_path,
    _public_tool_calls,
    _resolve_mentions,
    _serialize_images,
    _sse_event_to_sse,
    _truncate_user_message_as_title,
    chat,
    chat_abort,
    chat_answer,
    chat_approve,
    chat_guide,
    chat_rollback,
    chat_rollback_preview,
    chat_stream,
    chat_subscribe,
    chat_turns,
)
from excelmanus.api_routes_files import (  # noqa: F401
    AdmitAttachmentRequest,
    ExcelWriteRequest,
    WordWriteRequest,
    admit_attachment,
    create_file_group,
    delete_file_group,
    download_file,
    get_excel_compare,
    get_excel_file,
    get_excel_snapshot,
    get_excel_view,
    get_file_registry,
    get_file_relationships,
    get_image_file,
    get_spec_file,
    get_word_file,
    get_word_snapshot,
    list_excel_files,
    list_file_groups,
    list_workspace_files,
    read_text_file,
    reveal_file,
    update_file_group,
    update_file_group_members,
    workspace_create_file,
    workspace_delete_item,
    workspace_mkdir,
    workspace_rename_item,
    write_excel_cells,
    write_word_content,
)
from excelmanus.api_routes_sessions import (  # noqa: F401
    _change_type,
    clear_all_sessions,
    compact_session_context,
    delete_session,
    export_session,
    extract_session_memory,
    get_operation_detail,
    get_session,
    get_session_excel_events,
    get_session_messages,
    get_session_status,
    list_approvals,
    list_operations,
    list_sessions,
    scan_session_registry,
    toggle_full_access,
    undo_approval,
    undo_operation,
    update_session_title_api,
)
from excelmanus.api_routes_workspace import (  # noqa: F401
    list_revisions,
    restore_revision,
)
from excelmanus.api_routes_config import (  # noqa: F401
    ConfigExportRequest,
    ConfigImportRequest,
    ModelConfigUpdate,
    ModelProfileCreate,
    ModelSwitchRequest,
    RuntimeConfigUpdate,
    ThinkingConfigRequest,
    _LEGACY_MODEL_SECTIONS,
    _build_probe_targets,
    _collect_raw_sections,
    _deprecated_model_error_response,
    _diagnose_connection_error,
    _get_provider_fallback,
    _mask_api_key,
    _mask_key,
    _resolve_active_engine_info,
    _resolve_model_info,
    add_model_profile,
    cancel_probe_job,
    check_model_placeholder,
    create_probe_job,
    delete_model_profile,
    detect_config_token,
    export_model_config,
    get_all_model_capabilities,
    get_model_capabilities,
    get_model_config,
    get_probe_job,
    get_runtime_config,
    get_thinking_config,
    import_model_config,
    list_models,
    list_remote_models,
    probe_all_model_capabilities,
    probe_job_events,
    probe_model_capabilities,
    set_thinking_config,
    switch_model,
    test_model_connection,
    update_model_capabilities,
    update_model_config,
    update_model_profile,
    update_runtime_config,
)
from excelmanus.api_routes_skills import (  # noqa: F401
    SkillpackCreateRequest,
    SkillpackDetailResponse,
    SkillpackImportRequest,
    SkillpackMutationResponse,
    SkillpackPatchRequest,
    SkillpackSummaryResponse,
    _require_skillpack_manager,
    _to_skill_detail,
    _to_skill_summary,
    _to_standard_skill_detail_dict,
    create_skill,
    delete_skill,
    get_skill,
    import_skill,
    list_skills,
    patch_skill,
)
from excelmanus.api_routes_system import (  # noqa: F401
    execute_command,
    health,
    list_mentions,
    put_onboarding,
    upload_file,
    upload_file_from_url,
)

app = create_app()


# ── 入口函数 ──────────────────────────────────────────────


def main() -> None:
    """API 服务入口函数（pyproject.toml 入口点）。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="excelmanus-api",
        description="ExcelManus API Server",
    )
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=1)
    args, _ = parser.parse_known_args()
    if args.workers < 1:
        parser.error("--workers 必须至少为 1")

    os.environ["EXCELMANUS_API_PORT"] = str(args.port)
    os.environ["EXCELMANUS_API_HOST"] = args.host

    from excelmanus.auth.manage_token import require_manage_token_for_bind

    require_manage_token_for_bind(args.host)

    if os.environ.get("EXCELMANUS_DESKTOP") == "1":
        from excelmanus import restart
        server = uvicorn.Server(uvicorn.Config(
            "excelmanus.api:app", host=args.host, port=args.port, log_level="info",
            # Cancel long-lived SSE requests before lifespan drains agent state.
            timeout_graceful_shutdown=10,
        ))
        restart._desktop_server = server
        try:
            server.run()
        finally:
            restart._desktop_server = None
        if restart._desktop_restart_requested:
            raise SystemExit(75)
        return

    uvicorn.run(
        "excelmanus.api:app",
        host=args.host,
        port=args.port,
        log_level="info",
        workers=args.workers,
    )
