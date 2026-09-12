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
    load_config,
    load_cors_allow_origins,
)
from excelmanus.engine import ChatResult, ToolCallResult
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.logger import get_logger, setup_logging
from excelmanus.mentions import MentionParser, MentionResolver
from excelmanus.mentions.parser import ResolvedMention
from excelmanus.mcp.manager import MCPManager
from excelmanus.output_guard import (
    guard_public_reply,
    sanitize_external_data,
    sanitize_external_text,
)
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
from excelmanus.api_sse import (
    SessionStreamState as _SessionStreamState,
    inject_seq_into_sse as _inject_seq,
    sse_event_to_sse as _sse_event_to_sse_impl,
    sse_format as _sse_format,
)
from excelmanus.error_guidance import FailureGuidance, classify_failure

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine
    from excelmanus.skillpacks import SkillpackManager
    from excelmanus.workspace import IsolatedWorkspace

logger = get_logger("api")

# ── 请求 / 响应模型 ──────────────────────────────────────


class ErrorResponse(BaseModel):
    """错误响应体（不暴露内部堆栈）。"""

    error: str
    error_id: str


# ── 全局状态（由 lifespan 初始化） ────────────────────────

_session_manager: SessionManager | None = None
_tool_registry: ToolRegistry | None = None
_skillpack_loader: SkillpackLoader | None = None
_skill_router: SkillRouter | None = None
_skillpack_manager: "SkillpackManager | None" = None
_config: ExcelManusConfig | None = None
_config_incomplete: bool = False  # True when essential config (API key/base_url/model) is missing
_draining: bool = False  # True during graceful shutdown, health returns "draining"
_restart_reason: str = ""  # 重启原因，draining 期间通过 health 传递给前端
_cap_probe_job_manager: Any = None  # 类型：CapabilityProbeJobManager | None
_rules_manager: Any = None  # 类型：RulesManager | None
_api_persistent_memory: Any = None  # 类型：PersistentMemory | None（API 层共享）
_database: Any = None  # 类型：Database | None
_config_store: Any = None  # 类型：GlobalConfigStore | None


def _get_file_registry(workspace_root: str) -> Any:
    """获取或懒创建指定工作区的 FileRegistry，与引擎共用主库。"""
    from excelmanus.api_app_state import get_file_registry, set_database

    set_database(_database)
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
            cors_allow_origins=tuple(load_cors_allow_origins()),
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
    if _session_manager is None:
        return False
    try:
        await _session_manager.get_session_detail(session_id)
    except SessionNotFoundError:
        return False
    return True


from excelmanus.api_app_state import (  # noqa: F401
    _active_chat_tasks,
    _session_stream_states,
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



# ── Lifespan ──────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：初始化配置、注册 Skill、启动清理任务。"""
    global _session_manager, _tool_registry, _skillpack_loader, _skill_router, _skillpack_manager, _config, _database

    # create_app 已在构建应用时确定启动配置；lifespan 不再二次加载。
    bootstrap_error: ConfigError | None = app.state.bootstrap_config_error
    if bootstrap_error is not None:
        set_config_incomplete(True)
        _config_incomplete = True
        logger.warning(
            "\n"
            "╔══════════════════════════════════════════════════════════════╗\n"
            "║  ⚠️  模型配置缺失，服务以降级模式启动                      ║\n"
            "║                                                            ║\n"
            "║  %s\n"
            "║                                                            ║\n"
            "║  你可以通过以下任一方式完成配置：                          ║\n"
            "║    1. 打开浏览器访问前端页面，按引导填写 API Key           ║\n"
            "║    2. 编辑项目根目录下的 .env 文件，设置以下必填项：       ║\n"
            "║       EXCELMANUS_API_KEY=sk-xxx                            ║\n"
            "║       EXCELMANUS_BASE_URL=https://api.openai.com/v1        ║\n"
            "║       EXCELMANUS_MODEL=gpt-6-astra                         ║\n"
            "║    3. 使用 EXCELMANUS_MODELS 环境变量配置多模型            ║\n"
            "║                                                            ║\n"
            "║  配置完成后，通过前端设置页保存即可生效（无需重启）。      ║\n"
            "╚══════════════════════════════════════════════════════════════╝",
            str(bootstrap_error).ljust(58)[:58] + "║",
        )

    _config = app.state.bootstrap_config
    from excelmanus.api_app_state import bind_app_state, set_config_store, set_database
    bind_app_state(app, config=_config)
    setup_logging(_config.log_level)

    # ── 集中数据管理：注册安装 + 首次迁移 ──────────────
    from excelmanus.data_home import (
        register_installation,
        migrate_project_env,
        ensure_data_dirs,
        has_project_local_data,
        migrate_data_from_project,
        is_data_centralized,
        scan_once,
    )
    project_root = Path(__file__).resolve().parent.parent
    logger.info("部署模式: %s", _config.deploy_mode)
    try:
        register_installation(project_root)
        scan_once(skip_desktop_scan=_config.is_server)
        migrate_project_env(project_root)
        if _config.data_root:
            ensure_data_dirs()
            # 仅 standalone 模式执行自动迁移；服务器模式由管理员手动触发
            if _config.is_standalone:
                if not is_data_centralized() and has_project_local_data(project_root):
                    stats = migrate_data_from_project(project_root)
                    if stats:
                        logger.info("首次运行数据迁移完成: %s", stats)
    except Exception:
        logger.debug("集中数据管理初始化失败（非致命）", exc_info=True)

    # 初始化工具层
    _tool_registry = ToolRegistry()
    _tool_registry.register_builtin_tools(_config.workspace_root)
    set_tool_registry(_tool_registry)

    # 初始化 Skillpack 层
    _skillpack_loader = SkillpackLoader(_config, _tool_registry)
    _skillpack_loader.load_all()
    _skill_router = SkillRouter(_config, _skillpack_loader)
    from excelmanus.skillpacks import SkillpackManager
    _skillpack_manager = SkillpackManager(_config, _skillpack_loader)

    set_skillpack_loader(_skillpack_loader)
    set_skillpack_manager(_skillpack_manager)

    # 初始化统一数据库（配置档案必须落库，不能绑在聊天记录开关上）
    from excelmanus.database import Database

    _database = None
    chat_history = None
    resolved_db_path = os.path.expanduser(
        _config.chat_history_db_path or _config.db_path
    )
    _database = Database(resolved_db_path)
    logger.info("统一数据库已启用: %s", resolved_db_path)
    if _config.chat_history_enabled:
        from excelmanus.chat_history import ChatHistoryStore
        chat_history = ChatHistoryStore(_database)

    # 初始化 GlobalConfigStore 并从 .env 迁移已有 profiles
    global _config_store
    if _database is not None:
        from excelmanus.stores.config_store import GlobalConfigStore
        _config_store = GlobalConfigStore(_database)
        set_database(_database)
        set_config_store(_config_store)
        app.state.config_store = _config_store
        existing = _config_store.list_profiles()
        if not existing:
            env_models_raw = os.environ.get("EXCELMANUS_MODELS", "")
            if env_models_raw:
                n = _config_store.import_profiles_from_env(
                    env_models_raw, _config.api_key, _config.base_url,
                )
                if n:
                    logger.info("已从 EXCELMANUS_MODELS 迁移 %d 个模型 profile 到数据库", n)
                    try:
                        from excelmanus.data_home import delete_env_keys

                        delete_env_keys(["EXCELMANUS_MODELS"])
                        logger.info("已从正式仓清除 EXCELMANUS_MODELS（数据库为唯一来源）")
                    except Exception:
                        logger.debug("清除 EXCELMANUS_MODELS 失败", exc_info=True)
                    os.environ.pop("EXCELMANUS_MODELS", None)
        _sync_config_profiles_from_db()
        ensure_active_model()
        logger.info("GlobalConfigStore 已初始化")
    set_database(_database)
    set_config_store(_config_store)

    # 初始化异步能力探测任务管理器
    global _cap_probe_job_manager
    from excelmanus.capability_probe_jobs import CapabilityProbeJobManager
    _cap_probe_job_manager = CapabilityProbeJobManager()
    set_cap_probe_job_manager(_cap_probe_job_manager)

    # 初始化会话管理器
    # 始终创建共享 MCP 管理器并在启动时自动连接
    shared_mcp_manager = MCPManager(_config.workspace_root, app_config=_config)
    try:
        await shared_mcp_manager.initialize(_tool_registry)
        mcp_info = shared_mcp_manager.get_server_info()
        mcp_ready = sum(1 for s in mcp_info if s["status"] == "ready")
        if mcp_info:
            logger.info(
                "MCP 启动自动连接完成: %d/%d 个 Server 就绪",
                mcp_ready, len(mcp_info),
            )
    except Exception:
        logger.warning("MCP 启动自动连接失败", exc_info=True)

    _session_manager = SessionManager(
        max_sessions=_config.max_sessions,
        ttl_seconds=_config.session_ttl_seconds,
        config=_config,
        registry=_tool_registry,
        skill_router=_skill_router,
        shared_mcp_manager=shared_mcp_manager,
        chat_history=chat_history,
        database=_database,
        config_store=_config_store,
    )
    bind_app_state(app, session_manager=_session_manager)
    try:
        _session_manager.ensure_default_workspace()
    except Exception:
        logger.warning("默认工作区登记失败", exc_info=True)
    await _session_manager.start_background_cleanup()

    # 初始化全局 RulesManager + 共享 PersistentMemory（供 API 层直接使用）
    global _rules_manager, _api_persistent_memory
    try:
        from excelmanus.rules import RulesManager as _RM
        from excelmanus.stores.rules_store import RulesStore as _RS
        _rules_db_store = _RS(_database) if _database is not None else None
        _rules_manager = _RM(db_store=_rules_db_store)
    except Exception:
        logger.debug("RulesManager 初始化失败", exc_info=True)

    if _config.memory_enabled:
        try:
            from excelmanus.persistent_memory import PersistentMemory as _PM
            if _database is not None:
                from excelmanus.stores.memory_store import MemoryStore as _MS
                _mem_backend = _MS(_database)
            else:
                from excelmanus.stores.file_memory_backend import FileMemoryBackend as _FMB
                _mem_backend = _FMB(
                    memory_dir=_config.memory_dir,
                    auto_load_lines=_config.memory_auto_load_lines,
                )
            _api_persistent_memory = _PM(
                backend=_mem_backend,
                auto_load_lines=_config.memory_auto_load_lines,
            )
        except Exception:
            logger.debug("API PersistentMemory 初始化失败", exc_info=True)

    loaded_skillpacks = (
        sorted(_skillpack_loader.get_skillpacks().keys())
        if _skillpack_loader is not None
        else []
    )
    tool_names = (
        sorted(_tool_registry.get_tool_names())
        if _tool_registry is not None
        else []
    )
    app.state.workspace_root = _config.workspace_root
    app.state.data_root = _config.data_root
    if _database is not None:
        try:
            from excelmanus.auth.providers.credential_store import CredentialStore as _CredStore
            _cred_store = _CredStore(_database.conn)
            app.state.credential_store = _cred_store
            if _session_manager is not None:
                _session_manager.set_credential_store(_cred_store)
            try:
                from excelmanus.auth.providers.resolver import CredentialResolver as _CredResolver
                _cred_resolver = _CredResolver(credential_store=_cred_store)
                app.state.credential_resolver = _cred_resolver
                if _session_manager is not None:
                    _session_manager.set_credential_resolver(_cred_resolver)
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
                conn=_database.conn,
                credential_store=getattr(app.state, "credential_store", None),
            )
            app.state.pool_service = _pool_service
            _cr = getattr(app.state, "credential_resolver", None)
            if _cr is not None:
                _cr._pool_service = _pool_service
                _cr._pool_enabled = getattr(_config, "pool_enabled", False)
            logger.info("PoolService 已初始化")
            if getattr(_config, "pool_auto_enabled", False) and getattr(_config, "pool_enabled", False):
                try:
                    from excelmanus.pool.breaker import BreakerManager as _BreakerMgr
                    from excelmanus.pool.metrics import MetricsAggregator as _MetricsAgg
                    from excelmanus.pool.auto_rotate import PoolAutoRotateService as _AutoRotateSvc
                    _breaker_mgr = _BreakerMgr(
                        conn=_database.conn,
                        failure_threshold=getattr(
                            _config, "pool_auto_breaker_threshold", 5,
                        ),
                        open_seconds=getattr(
                            _config, "pool_auto_breaker_open_seconds", 120,
                        ),
                    )
                    app.state.pool_breaker_manager = _breaker_mgr
                    _metrics_agg = _MetricsAgg(conn=_database.conn)
                    app.state.pool_metrics_aggregator = _metrics_agg
                    _auto_rotate_svc = _AutoRotateSvc(
                        conn=_database.conn,
                        pool_service=_pool_service,
                        default_cooldown_seconds=getattr(
                            _config, "pool_auto_default_cooldown_seconds", 300,
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

    _fire_and_forget(_background_update_check(), name="update_check")

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
        _auto_interval = getattr(_config, "pool_auto_interval_seconds", 60)

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

    yield

    # ── Graceful Shutdown ──────────────────────────────────────
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

    if _session_manager is not None:
        drain_timeout = 30  # 最长等待 30 秒
        for _drain_i in range(drain_timeout):
            active = await _session_manager.get_active_count()
            if active == 0:
                logger.info("所有活跃连接已排空")
                break
            if _drain_i % 5 == 0:
                logger.info("等待 %d 个活跃连接排空... (%d/%ds)", active, _drain_i, drain_timeout)
            await asyncio.sleep(1)
        else:
            active = await _session_manager.get_active_count()
            if active > 0:
                logger.warning("排空超时，仍有 %d 个活跃连接，强制关闭", active)

    # 停止异步探测任务
    mgr = get_cap_probe_job_manager() or _cap_probe_job_manager
    if mgr is not None:
        await mgr.shutdown()
        set_cap_probe_job_manager(None)

    # 关闭所有会话与 MCP 连接
    if _session_manager is not None:
        await _session_manager.shutdown()

    # 关闭统一数据库
    if _database is not None:
        _database.close()

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
    "internal_error": "服务内部错误，请联系管理员。",
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
    return "服务处理出现异常，请稍后重试。如问题持续，请联系管理员。"



async def _handle_session_not_found(
    request: Request, exc: SessionNotFoundError
) -> JSONResponse:
    """会话不存在 → 404。"""
    error_id = str(uuid.uuid4())
    friendly_enabled = _config.friendly_error_messages if _config else False
    error_msg = _get_friendly_error_message(exc, friendly_enabled)
    body = ErrorResponse(error=error_msg, error_id=error_id)
    return JSONResponse(status_code=404, content=body.model_dump())


async def _handle_session_limit(
    request: Request, exc: SessionLimitExceededError
) -> JSONResponse:
    """会话数量超限 → 429。"""
    error_id = str(uuid.uuid4())
    friendly_enabled = _config.friendly_error_messages if _config else False
    error_msg = _get_friendly_error_message(exc, friendly_enabled)
    body = ErrorResponse(error=error_msg, error_id=error_id)
    return JSONResponse(status_code=429, content=body.model_dump())


async def _handle_session_busy(
    request: Request, exc: SessionBusyError
) -> JSONResponse:
    """会话正在处理中 → 409。"""
    error_id = str(uuid.uuid4())
    friendly_enabled = _config.friendly_error_messages if _config else False
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
    friendly_enabled = _config.friendly_error_messages if _config else False
    error_msg = _get_friendly_error_message(exc, friendly_enabled)
    body = ErrorResponse(error=error_msg, error_id=error_id)
    return JSONResponse(status_code=500, content=body.model_dump())


def _register_exception_handlers(application: FastAPI) -> None:
    """注册全局异常处理器。"""
    application.add_exception_handler(SessionNotFoundError, _handle_session_not_found)
    application.add_exception_handler(SessionLimitExceededError, _handle_session_limit)
    application.add_exception_handler(SessionBusyError, _handle_session_busy)
    application.add_exception_handler(Exception, _handle_unexpected)


def create_app(
    config: ExcelManusConfig | None = None,
) -> FastAPI:
    """创建 FastAPI 应用，CORS 与运行期配置共享同一来源。

    Args:
        config: 预构建的配置对象；为 None 时自动从环境加载。
    """
    bootstrap_error: ConfigError | None = None
    bootstrap_config = config
    if bootstrap_config is None:
        bootstrap_config, bootstrap_error = _build_bootstrap_config()
    assert bootstrap_config is not None

    application = FastAPI(
        title="ExcelManus API",
        version=excelmanus.__version__,
        lifespan=lifespan,
    )
    application.state.bootstrap_config = bootstrap_config
    application.state.bootstrap_config_error = bootstrap_error

    # 构建 CORS 允许来源列表：除了显式配置的来源外，自动添加本机 LAN IP
    # 的前端端口来源，以便浏览器直连后端的 SSE 流式请求不被 CORS 拦截。
    cors_origins = set(bootstrap_config.cors_allow_origins)
    lan_ips: set[str] = set()
    # 方法1: gethostname + getaddrinfo（部分系统可用）
    try:
        import socket
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                lan_ips.add(ip)
    except Exception:
        pass
    # 方法2: UDP connect trick（不实际发送数据，最可靠的主 IP 获取方式）
    try:
        import socket as _sock
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(("10.254.254.254", 1))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            lan_ips.add(ip)
    except Exception:
        pass
    # 从环境变量读取前端端口，支持多端口（逗号分隔）和自定义端口部署（默认 3000）
    _frontend_ports_raw = os.environ.get("EXCELMANUS_FRONTEND_PORT", "3000").strip()
    _frontend_ports = [p.strip() for p in _frontend_ports_raw.split(",") if p.strip()]
    for ip in lan_ips:
        for _fp in _frontend_ports:
            cors_origins.add(f"http://{ip}:{_fp}")

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
        expose_headers=["X-Request-Id"],
    )

    _register_exception_handlers(application)

    # 注册认证路由
    from excelmanus.auth.router import router as auth_router
    application.include_router(auth_router)

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
    ExcelWriteRequest,
    WordWriteRequest,
    create_file_group,
    delete_file_group,
    download_file,
    get_excel_compare,
    get_excel_file,
    get_excel_snapshot,
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
    toggle_present_as,
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
    _MODEL_ENV_KEYS,
    _build_probe_targets,
    _collect_raw_sections,
    _deprecated_model_error_response,
    _diagnose_connection_error,
    _find_env_file,
    _get_provider_fallback,
    _mask_api_key,
    _mask_key,
    _read_env_file,
    _resolve_active_engine_info,
    _resolve_model_info,
    _update_env_var,
    _write_env_file,
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
    ClawHubInstallRequest,
    ClawHubUpdateRequest,
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
    clawhub_check_updates,
    clawhub_install,
    clawhub_list_installed,
    clawhub_search,
    clawhub_skill_detail,
    clawhub_update,
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
    args, _ = parser.parse_known_args()

    os.environ["EXCELMANUS_API_PORT"] = str(args.port)
    os.environ["EXCELMANUS_API_HOST"] = args.host

    from excelmanus.auth.manage_token import require_manage_token_for_bind

    require_manage_token_for_bind(args.host)

    uvicorn.run(
        "excelmanus.api:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )

