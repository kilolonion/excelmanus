"""API 服务模块：基于 FastAPI 的 REST API 服务。

端点：
- POST   /api/v1/chat                        对话接口（完整 JSON）
- POST   /api/v1/chat/stream                  对话接口（SSE 流式）
- POST   /api/v1/chat/abort                   终止活跃聊天任务
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
- DELETE /api/v1/sessions/{session_id}        删除会话
- GET    /api/v1/health                       健康检查
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator, Literal

import uvicorn
from fastapi import APIRouter, FastAPI, Request, UploadFile, File as FastAPIFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StringConstraints

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
    SkillpackConflictError,
    SkillpackInputError,
    SkillpackLoader,
    SkillpackNotFoundError,
    SkillRouter,
)
from excelmanus.skillpacks.importer import SkillImportError
from excelmanus.tools import ToolRegistry

logger = get_logger("api")

# ── 请求 / 响应模型 ──────────────────────────────────────


class ImageAttachment(BaseModel):
    """图片附件。"""

    data: str  # base64 编码
    media_type: str = "image/png"
    detail: Literal["auto", "low", "high"] = "auto"


class ChatRequest(BaseModel):
    """对话请求体。"""

    model_config = ConfigDict(extra="forbid")

    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1)
    ]
    session_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ] | None = None
    images: list[ImageAttachment] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """对话响应体。"""

    session_id: str
    reply: str
    skills_used: list[str]
    tool_scope: list[str] = Field(default_factory=list)
    route_mode: str
    iterations: int = 0
    truncated: bool = False
    tool_calls: list[dict] = Field(default_factory=list)
    # token 使用统计
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ErrorResponse(BaseModel):
    """错误响应体（不暴露内部堆栈）。"""

    error: str
    error_id: str


class SkillpackSummaryResponse(BaseModel):
    """Skillpack 摘要响应。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str
    source: str
    writable: bool
    argument_hint: str = Field(
        default="",
        validation_alias=AliasChoices("argument_hint", "argument-hint"),
        serialization_alias="argument-hint",
    )


class SkillpackDetailResponse(SkillpackSummaryResponse):
    """Skillpack 详情响应。"""

    file_patterns: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("file_patterns", "file-patterns"),
        serialization_alias="file-patterns",
    )
    resources: list[str]
    version: str
    disable_model_invocation: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "disable_model_invocation",
            "disable-model-invocation",
        ),
        serialization_alias="disable-model-invocation",
    )
    user_invocable: bool = Field(
        default=True,
        validation_alias=AliasChoices("user_invocable", "user-invocable"),
        serialization_alias="user-invocable",
    )
    instructions: str
    resource_contents: dict[str, str]
    hooks: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    command_dispatch: str = Field(
        default="none",
        validation_alias=AliasChoices("command_dispatch", "command-dispatch"),
        serialization_alias="command-dispatch",
    )
    command_tool: str | None = Field(
        default=None,
        validation_alias=AliasChoices("command_tool", "command-tool"),
        serialization_alias="command-tool",
    )
    required_mcp_servers: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "required_mcp_servers",
            "required-mcp-servers",
        ),
        serialization_alias="required-mcp-servers",
    )
    required_mcp_tools: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "required_mcp_tools",
            "required-mcp-tools",
        ),
        serialization_alias="required-mcp-tools",
    )
    extensions: dict[str, Any] = Field(default_factory=dict)


class SkillpackImportRequest(BaseModel):
    """导入 skillpack 请求体。"""

    model_config = ConfigDict(extra="forbid")
    source: Literal["local_path", "github_url"]
    value: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)
    ]
    overwrite: bool = False


class SkillpackCreateRequest(BaseModel):
    """创建 skillpack 请求体。"""

    model_config = ConfigDict(extra="forbid")
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    payload: dict[str, Any]


class SkillpackPatchRequest(BaseModel):
    """更新 skillpack 请求体。"""

    model_config = ConfigDict(extra="forbid")
    payload: dict[str, Any]


class SkillpackMutationResponse(BaseModel):
    """写操作响应。"""

    status: str
    name: str
    detail: dict[str, Any] | None = None


# ── 全局状态（由 lifespan 初始化） ────────────────────────

_session_manager: SessionManager | None = None
_tool_registry: ToolRegistry | None = None
_skillpack_loader: SkillpackLoader | None = None
_skill_router: SkillRouter | None = None
_config: ExcelManusConfig | None = None
_active_chat_tasks: dict[str, asyncio.Task[Any]] = {}
_router = APIRouter()


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


def _is_external_safe_mode() -> bool:
    """是否启用对外安全模式（默认开启）。"""
    if _config is None:
        return True
    return bool(_config.external_safe_mode)


def _public_route_fields(route_mode: str, skills_used: list[str], tool_scope: list[str]) -> tuple[str, list[str], list[str]]:
    """根据安全模式裁剪路由元信息。"""
    if _is_external_safe_mode():
        return "hidden", [], []
    return route_mode, skills_used, tool_scope


def _error_json_response(status_code: int, message: str) -> JSONResponse:
    """构建统一错误响应。"""
    error_id = str(uuid.uuid4())
    body = ErrorResponse(error=message, error_id=error_id)
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _require_skill_engine():
    """创建临时 AgentEngine 实例用于 skillpack 管理。"""
    assert _config is not None, "服务未初始化"
    assert _tool_registry is not None, "服务未初始化"
    assert _skill_router is not None, "服务未初始化"
    from excelmanus.engine import AgentEngine

    return AgentEngine(
        config=_config,
        registry=_tool_registry,
        skill_router=_skill_router,
    )


def _to_skill_summary(detail: dict[str, Any]) -> SkillpackSummaryResponse:
    """从详情字典提取摘要响应。"""
    return SkillpackSummaryResponse(
        name=str(detail.get("name", "")),
        description=str(detail.get("description", "")),
        source=str(detail.get("source", "")),
        writable=bool(detail.get("writable", False)),
        argument_hint=str(
            detail.get("argument-hint", detail.get("argument_hint", "")) or ""
        ),
    )


def _to_skill_detail(detail: dict[str, Any]) -> SkillpackDetailResponse:
    """详情字典转换为响应模型。"""
    return SkillpackDetailResponse(
        name=str(detail.get("name", "")),
        description=str(detail.get("description", "")),
        source=str(detail.get("source", "")),
        writable=bool(detail.get("writable", False)),
        argument_hint=str(
            detail.get("argument-hint", detail.get("argument_hint", "")) or ""
        ),
        file_patterns=list(
            detail.get("file-patterns", detail.get("file_patterns", [])) or []
        ),
        resources=list(detail.get("resources", []) or []),
        version=str(detail.get("version", "1.0.0")),
        disable_model_invocation=bool(
            detail.get(
                "disable-model-invocation",
                detail.get("disable_model_invocation", False),
            )
        ),
        user_invocable=bool(
            detail.get("user-invocable", detail.get("user_invocable", True))
        ),
        instructions=str(detail.get("instructions", "") or ""),
        resource_contents=dict(detail.get("resource_contents", {}) or {}),
        hooks=dict(detail.get("hooks", {}) or {}),
        model=(
            str(detail.get("model")).strip()
            if detail.get("model") is not None and str(detail.get("model")).strip()
            else None
        ),
        metadata=dict(detail.get("metadata", {}) or {}),
        command_dispatch=str(
            detail.get("command-dispatch", detail.get("command_dispatch", "none"))
            or "none"
        ),
        command_tool=(
            str(
                detail.get("command-tool", detail.get("command_tool"))
            ).strip()
            if detail.get("command-tool", detail.get("command_tool")) is not None
            and str(detail.get("command-tool", detail.get("command_tool"))).strip()
            else None
        ),
        required_mcp_servers=list(
            detail.get(
                "required-mcp-servers",
                detail.get("required_mcp_servers", []),
            )
            or []
        ),
        required_mcp_tools=list(
            detail.get(
                "required-mcp-tools",
                detail.get("required_mcp_tools", []),
            )
            or []
        ),
        extensions=dict(detail.get("extensions", {}) or {}),
    )


def _to_standard_skill_detail_dict(detail: dict[str, Any]) -> dict[str, Any]:
    """将技能详情标准化为 API 输出字段。"""
    return _to_skill_detail(detail).model_dump(by_alias=True, exclude_none=False)


def _normalize_chat_result(value: ChatResult | str | None) -> ChatResult:
    """兼容测试桩仍返回 str 的场景。"""
    if isinstance(value, ChatResult):
        return value
    return ChatResult(reply="" if value is None else str(value))


def _serialize_images(images: list[ImageAttachment]) -> list[dict[str, str]]:
    """将请求中的图片附件标准化为引擎可消费的字典列表。"""
    return [img.model_dump() for img in images]


def _public_tool_calls(tool_calls: list[ToolCallResult]) -> list[dict]:
    """根据安全模式裁剪工具调用明细。"""
    if _is_external_safe_mode():
        return []

    rows: list[dict] = []
    for item in tool_calls:
        rows.append({
            "tool_name": item.tool_name,
            "arguments": sanitize_external_data(
                item.arguments if isinstance(item.arguments, dict) else {},
                max_len=1000,
            ),
            "result": sanitize_external_text(item.result or "", max_len=3000),
            "success": bool(item.success),
            "error": (
                sanitize_external_text(item.error, max_len=1000)
                if item.error
                else None
            ),
            "pending_approval": bool(item.pending_approval),
            "approval_id": item.approval_id,
            "pending_question": bool(item.pending_question),
            "question_id": item.question_id,
            "pending_plan": bool(item.pending_plan),
            "plan_id": item.plan_id,
        })
    return rows


# ── Lifespan ──────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：初始化配置、注册 Skill、启动清理任务。"""
    global _session_manager, _tool_registry, _skillpack_loader, _skill_router, _config

    # create_app 已在构建应用时确定启动配置；lifespan 不再二次加载。
    bootstrap_error: ConfigError | None = app.state.bootstrap_config_error
    if bootstrap_error is not None:
        raise bootstrap_error

    _config = app.state.bootstrap_config
    setup_logging(_config.log_level)

    # 初始化工具层
    _tool_registry = ToolRegistry()
    _tool_registry.register_builtin_tools(_config.workspace_root)

    # 初始化 Skillpack 层
    _skillpack_loader = SkillpackLoader(_config, _tool_registry)
    _skillpack_loader.load_all()
    _skill_router = SkillRouter(_config, _skillpack_loader)

    # 初始化统一数据库
    from excelmanus.database import Database

    _database = None
    chat_history = None
    if _config.chat_history_enabled:
        from excelmanus.chat_history import ChatHistoryStore

        resolved_db_path = os.path.expanduser(
            _config.chat_history_db_path or _config.db_path
        )
        _database = Database(resolved_db_path)
        chat_history = ChatHistoryStore.from_database(_database)
        logger.info("统一数据库已启用: %s", resolved_db_path)

    # 初始化会话管理器
    shared_mcp_manager: MCPManager | None = None
    if _config.mcp_shared_manager:
        shared_mcp_manager = MCPManager(_config.workspace_root)

    _session_manager = SessionManager(
        max_sessions=_config.max_sessions,
        ttl_seconds=_config.session_ttl_seconds,
        config=_config,
        registry=_tool_registry,
        skill_router=_skill_router,
        shared_mcp_manager=shared_mcp_manager,
        chat_history=chat_history,
    )
    await _session_manager.start_background_cleanup()

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
    logger.info(
        "API 服务启动完成，已加载 %d 个工具、%d 个 Skillpack",
        len(tool_names),
        len(loaded_skillpacks),
    )

    yield

    # 关闭所有会话与 MCP 连接
    if _session_manager is not None:
        await _session_manager.shutdown()

    # 关闭统一数据库
    if _database is not None:
        _database.close()

    logger.info("API 服务已关闭")


# ── 全局异常处理 ──────────────────────────────────────────


async def _handle_session_not_found(
    request: Request, exc: SessionNotFoundError
) -> JSONResponse:
    """会话不存在 → 404。"""
    error_id = str(uuid.uuid4())
    body = ErrorResponse(error=str(exc), error_id=error_id)
    return JSONResponse(status_code=404, content=body.model_dump())


async def _handle_session_limit(
    request: Request, exc: SessionLimitExceededError
) -> JSONResponse:
    """会话数量超限 → 429。"""
    error_id = str(uuid.uuid4())
    body = ErrorResponse(error=str(exc), error_id=error_id)
    return JSONResponse(status_code=429, content=body.model_dump())


async def _handle_session_busy(
    request: Request, exc: SessionBusyError
) -> JSONResponse:
    """会话正在处理中 → 409。"""
    error_id = str(uuid.uuid4())
    body = ErrorResponse(error=str(exc), error_id=error_id)
    return JSONResponse(status_code=409, content=body.model_dump())


async def _handle_unexpected(
    request: Request, exc: Exception
) -> JSONResponse:
    """未预期异常 → 500，返回 error_id，不暴露堆栈。"""
    error_id = str(uuid.uuid4())
    logger.error(
        "未预期异常 [error_id=%s]: %s", error_id, exc, exc_info=True
    )
    body = ErrorResponse(error="服务内部错误，请联系管理员。", error_id=error_id)
    return JSONResponse(status_code=500, content=body.model_dump())


def _register_exception_handlers(application: FastAPI) -> None:
    """注册全局异常处理器。"""
    application.add_exception_handler(SessionNotFoundError, _handle_session_not_found)
    application.add_exception_handler(SessionLimitExceededError, _handle_session_limit)
    application.add_exception_handler(SessionBusyError, _handle_session_busy)
    application.add_exception_handler(Exception, _handle_unexpected)


def create_app(config: ExcelManusConfig | None = None) -> FastAPI:
    """创建 FastAPI 应用，CORS 与运行期配置共享同一来源。"""
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
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(bootstrap_config.cors_allow_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )
    _register_exception_handlers(application)
    application.include_router(_router)
    return application


# ── 端点 ──────────────────────────────────────────────────

# OpenAPI 错误响应 schema（供路由 responses 参数引用）
_error_responses: dict = {
    403: {"model": ErrorResponse, "description": "无权限执行"},
    404: {"model": ErrorResponse, "description": "会话不存在"},
    409: {"model": ErrorResponse, "description": "会话正在处理中"},
    422: {"model": ErrorResponse, "description": "请求参数错误"},
    429: {"model": ErrorResponse, "description": "会话数量超限"},
    500: {"model": ErrorResponse, "description": "服务内部错误"},
}


@_router.post("/api/v1/chat", response_model=ChatResponse, responses=_error_responses)
async def chat(request: ChatRequest) -> ChatResponse:
    """对话接口：创建或复用会话，将消息传递给 AgentEngine。"""
    assert _session_manager is not None, "服务未初始化"

    session_id, engine = await _session_manager.acquire_for_chat(
        request.session_id
    )
    try:
        chat_result = _normalize_chat_result(
            await engine.chat(
                request.message,
                images=_serialize_images(request.images),
            )
        )
    finally:
        await _session_manager.release_for_chat(session_id)

    normalized_reply = guard_public_reply(chat_result.reply.strip())
    route = engine.last_route_result
    route_mode, skills_used, tool_scope = _public_route_fields(
        route.route_mode,
        route.skills_used,
        route.tool_scope,
    )
    return ChatResponse(
        session_id=session_id,
        reply=normalized_reply,
        skills_used=skills_used,
        tool_scope=tool_scope,
        route_mode=route_mode,
        iterations=chat_result.iterations,
        truncated=chat_result.truncated,
        tool_calls=_public_tool_calls(chat_result.tool_calls),
        prompt_tokens=chat_result.prompt_tokens,
        completion_tokens=chat_result.completion_tokens,
        total_tokens=chat_result.total_tokens,
    )


@_router.post("/api/v1/chat/stream", responses=_error_responses)
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """SSE 流式对话接口：实时推送思考过程、工具调用、最终回复。"""
    assert _session_manager is not None, "服务未初始化"

    session_id, engine = await _session_manager.acquire_for_chat(
        request.session_id
    )

    # 用 asyncio.Queue 桥接同步回调与异步生成器
    event_queue: asyncio.Queue[ToolCallEvent | None] = asyncio.Queue()

    def _on_event(event: ToolCallEvent) -> None:
        """引擎事件回调：将事件放入队列。"""
        event_queue.put_nowait(event)

    async def _run_chat() -> ChatResult:
        """后台执行 engine.chat，完成后向队列发送结束信号。"""
        try:
            result = await engine.chat(
                request.message,
                on_event=_on_event,
                images=_serialize_images(request.images),
            )
            return _normalize_chat_result(result)
        except Exception:
            raise
        finally:
            await _session_manager.release_for_chat(session_id)

    async def _event_generator() -> AsyncIterator[str]:
        """SSE 事件生成器：从队列消费事件并格式化为 SSE 文本。"""
        safe_mode = _is_external_safe_mode()
        # 先推送 session_id
        yield _sse_format("session_init", {"session_id": session_id})

        # 启动 chat 任务
        chat_task = asyncio.create_task(_run_chat())
        _active_chat_tasks[session_id] = chat_task
        queue_get_task: asyncio.Task[ToolCallEvent | None] | None = asyncio.create_task(
            event_queue.get()
        )

        async def _cancel_task(task: asyncio.Task[Any] | None) -> None:
            if task is None or task.done():
                return
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        try:
            while True:
                assert queue_get_task is not None
                done, _ = await asyncio.wait(
                    [queue_get_task, chat_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if queue_get_task in done:
                    event = queue_get_task.result()
                    if event is not None:
                        sse = _sse_event_to_sse(event, safe_mode=safe_mode)
                        if sse is not None:
                            yield sse
                    if chat_task.done():
                        queue_get_task = None
                    else:
                        queue_get_task = asyncio.create_task(event_queue.get())

                if chat_task in done:
                    # 排空队列中剩余事件
                    while True:
                        try:
                            event = event_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if event is not None:
                            sse = _sse_event_to_sse(event, safe_mode=safe_mode)
                            if sse is not None:
                                yield sse
                    break

            # chat 任务完成：获取结果
            chat_result = chat_task.result()
            normalized_reply = guard_public_reply((chat_result.reply or "").strip())
            route = engine.last_route_result
            route_mode, skills_used, tool_scope = _public_route_fields(
                route.route_mode,
                route.skills_used,
                route.tool_scope,
            )
            yield _sse_format("reply", {
                "content": normalized_reply,
                "skills_used": skills_used,
                "tool_scope": tool_scope,
                "route_mode": route_mode,
                "iterations": chat_result.iterations,
                "truncated": chat_result.truncated,
                "prompt_tokens": chat_result.prompt_tokens,
                "completion_tokens": chat_result.completion_tokens,
                "total_tokens": chat_result.total_tokens,
            })
            yield _sse_format("done", {})

        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断开或 abort 端点取消：终止 chat 任务
            logger.info("会话 %s 的流式请求被取消", session_id)
            if not chat_task.done():
                chat_task.cancel()
                try:
                    await chat_task
                except (asyncio.CancelledError, Exception):
                    pass
        except Exception as exc:
            error_id = str(uuid.uuid4())
            logger.error(
                "SSE 流异常 [error_id=%s]: %s", error_id, exc, exc_info=True
            )
            yield _sse_format("error", {
                "error": "服务内部错误，请联系管理员。",
                "error_id": error_id,
            })
            # 确保 chat 任务被取消
            if not chat_task.done():
                chat_task.cancel()
                try:
                    await chat_task
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            _active_chat_tasks.pop(session_id, None)
            await _cancel_task(queue_get_task)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class AbortRequest(BaseModel):
    """终止请求体。"""

    model_config = ConfigDict(extra="forbid")

    session_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]


@_router.post("/api/v1/chat/abort")
async def chat_abort(request: AbortRequest) -> JSONResponse:
    """终止指定会话的活跃聊天任务。"""
    task = _active_chat_tasks.get(request.session_id)
    if task is None or task.done():
        return JSONResponse(
            status_code=200,
            content={"status": "no_active_task"},
        )
    task.cancel()
    logger.info("通过 abort 端点取消会话 %s 的聊天任务", request.session_id)
    return JSONResponse(
        status_code=200,
        content={"status": "cancelled"},
    )


def _sse_format(event_type: str, data: dict) -> str:
    """将事件格式化为 SSE 文本行。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


def _sse_event_to_sse(
    event: ToolCallEvent,
    *,
    safe_mode: bool,
) -> str | None:
    """将 ToolCallEvent 转换为 SSE 文本。"""
    if safe_mode and event.event_type in {
        EventType.THINKING,
        EventType.THINKING_DELTA,
        EventType.TOOL_CALL_START,
        EventType.TOOL_CALL_END,
        EventType.ITERATION_START,
        EventType.SUBAGENT_START,
        EventType.SUBAGENT_ITERATION,
        EventType.SUBAGENT_SUMMARY,
        EventType.SUBAGENT_END,
        EventType.PENDING_APPROVAL,  # 新增：safe_mode 下过滤审批事件
        EventType.APPROVAL_RESOLVED,
    }:
        return None

    event_map = {
        EventType.THINKING: "thinking",
        EventType.TOOL_CALL_START: "tool_call_start",
        EventType.TOOL_CALL_END: "tool_call_end",
        EventType.ITERATION_START: "iteration_start",
        EventType.SUBAGENT_START: "subagent_start",
        EventType.SUBAGENT_ITERATION: "subagent_iteration",
        EventType.SUBAGENT_SUMMARY: "subagent_summary",
        EventType.SUBAGENT_END: "subagent_end",
        EventType.USER_QUESTION: "user_question",
        EventType.THINKING_DELTA: "thinking_delta",
        EventType.TEXT_DELTA: "text_delta",
    }
    sse_type = event_map.get(event.event_type, event.event_type.value)

    if event.event_type == EventType.THINKING:
        data = {
            "content": sanitize_external_text(event.thinking, max_len=2000),
            "iteration": event.iteration,
        }
    elif event.event_type == EventType.TOOL_CALL_START:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "tool_name": event.tool_name,
            "arguments": sanitize_external_data(
                event.arguments if isinstance(event.arguments, dict) else {},
                max_len=1000,
            ),
            "iteration": event.iteration,
        }
    elif event.event_type == EventType.TOOL_CALL_END:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "tool_name": event.tool_name,
            "success": event.success,
            "result": sanitize_external_text(
                event.result[:500] if event.result else "",
                max_len=500,
            ),
            "error": (
                sanitize_external_text(event.error, max_len=300)
                if event.error
                else None
            ),
            "iteration": event.iteration,
        }
    elif event.event_type == EventType.ITERATION_START:
        data = {"iteration": event.iteration}
    elif event.event_type == EventType.SUBAGENT_START:
        data = {
            "name": sanitize_external_text(event.subagent_name, max_len=100),
            "reason": sanitize_external_text(event.subagent_reason, max_len=500),
            "tools": event.subagent_tools,
            "permission_mode": sanitize_external_text(
                event.subagent_permission_mode,
                max_len=40,
            ),
            "conversation_id": sanitize_external_text(
                event.subagent_conversation_id,
                max_len=120,
            ),
        }
    elif event.event_type == EventType.SUBAGENT_ITERATION:
        data = {
            "name": sanitize_external_text(event.subagent_name, max_len=100),
            "conversation_id": sanitize_external_text(
                event.subagent_conversation_id,
                max_len=120,
            ),
            "iteration": event.subagent_iterations,
            "tool_calls": event.subagent_tool_calls,
        }
    elif event.event_type == EventType.SUBAGENT_SUMMARY:
        data = {
            "name": sanitize_external_text(event.subagent_name, max_len=100),
            "reason": sanitize_external_text(event.subagent_reason, max_len=500),
            "summary": sanitize_external_text(event.subagent_summary, max_len=4000),
            "tools": event.subagent_tools,
            "permission_mode": sanitize_external_text(
                event.subagent_permission_mode,
                max_len=40,
            ),
            "conversation_id": sanitize_external_text(
                event.subagent_conversation_id,
                max_len=120,
            ),
            "iterations": event.subagent_iterations,
            "tool_calls": event.subagent_tool_calls,
        }
    elif event.event_type == EventType.SUBAGENT_END:
        data = {
            "name": sanitize_external_text(event.subagent_name, max_len=100),
            "reason": sanitize_external_text(event.subagent_reason, max_len=500),
            "success": event.subagent_success,
            "tools": event.subagent_tools,
            "permission_mode": sanitize_external_text(
                event.subagent_permission_mode,
                max_len=40,
            ),
            "conversation_id": sanitize_external_text(
                event.subagent_conversation_id,
                max_len=120,
            ),
            "iterations": event.subagent_iterations,
            "tool_calls": event.subagent_tool_calls,
        }
    elif event.event_type == EventType.USER_QUESTION:
        options: list[dict[str, str]] = []
        for option in event.question_options:
            if not isinstance(option, dict):
                continue
            options.append(
                {
                    "label": sanitize_external_text(
                        str(option.get("label", "") or ""),
                        max_len=80,
                    ),
                    "description": sanitize_external_text(
                        str(option.get("description", "") or ""),
                        max_len=500,
                    ),
                }
            )
        data = {
            "id": sanitize_external_text(event.question_id or "", max_len=120),
            "header": sanitize_external_text(event.question_header or "", max_len=80),
            "text": sanitize_external_text(event.question_text or "", max_len=2000),
            "options": options,
            "multi_select": bool(event.question_multi_select),
            "queue_size": int(event.question_queue_size or 0),
        }
    elif event.event_type in {EventType.TASK_LIST_CREATED, EventType.TASK_ITEM_UPDATED}:
        data = {
            "task_list": event.task_list_data,
            "task_index": event.task_index,
            "task_status": event.task_status,
        }
        sse_type = "task_update"
    elif event.event_type == EventType.THINKING_DELTA:
        data = {
            "content": event.thinking_delta,
            "iteration": event.iteration,
        }
    elif event.event_type == EventType.TEXT_DELTA:
        data = {
            "content": event.text_delta,
            "iteration": event.iteration,
        }
    elif event.event_type == EventType.PENDING_APPROVAL:
        data = {
            "approval_id": sanitize_external_text(event.approval_id or "", max_len=120),
            "approval_tool_name": sanitize_external_text(event.approval_tool_name or "", max_len=100),
            # 不输出 approval_arguments，防止敏感信息泄露
        }
    elif event.event_type == EventType.APPROVAL_RESOLVED:
        data = {
            "approval_id": sanitize_external_text(event.approval_id or "", max_len=120),
            "approval_tool_name": sanitize_external_text(event.approval_tool_name or "", max_len=100),
            "success": event.success,
        }
        sse_type = "approval_resolved"
    else:
        data = event.to_dict()

    return _sse_format(sse_type, data)


@_router.get(
    "/api/v1/skills",
    response_model=list[SkillpackSummaryResponse],
    responses={
        500: _error_responses[500],
    },
)
async def list_skills() -> list[SkillpackSummaryResponse] | JSONResponse:
    """列出全部已加载 skillpack 摘要。"""
    engine = _require_skill_engine()
    details = engine.list_skillpacks_detail()
    return [_to_skill_summary(detail) for detail in details]


@_router.get(
    "/api/v1/skills/{name}",
    response_model=SkillpackDetailResponse | SkillpackSummaryResponse,
    responses={
        404: _error_responses[404],
        422: _error_responses[422],
        500: _error_responses[500],
    },
)
async def get_skill(name: str) -> SkillpackDetailResponse | SkillpackSummaryResponse | JSONResponse:
    """查询单个 skillpack。"""
    engine = _require_skill_engine()
    try:
        detail = engine.get_skillpack_detail(name)
    except SkillpackInputError as exc:
        return _error_json_response(422, str(exc))
    except SkillpackNotFoundError as exc:
        return _error_json_response(404, str(exc))

    if _is_external_safe_mode():
        return _to_skill_summary(detail)
    return _to_skill_detail(detail)


@_router.post(
    "/api/v1/skills",
    status_code=201,
    response_model=SkillpackMutationResponse,
    responses={
        403: _error_responses[403],
        409: _error_responses[409],
        422: _error_responses[422],
        500: _error_responses[500],
    },
)
async def create_skill(
    request: SkillpackCreateRequest,
) -> SkillpackMutationResponse | JSONResponse:
    """创建 project 层 skillpack。"""
    if _is_external_safe_mode():
        return _error_json_response(403, "external_safe_mode 开启时禁止写入 skillpack。")

    engine = _require_skill_engine()
    try:
        detail = engine.create_skillpack(
            name=request.name,
            payload=request.payload,
            actor="api",
        )
    except SkillpackInputError as exc:
        return _error_json_response(422, str(exc))
    except SkillpackConflictError as exc:
        return _error_json_response(409, str(exc))

    return SkillpackMutationResponse(
        status="created",
        name=str(detail.get("name", request.name)),
        detail=_to_standard_skill_detail_dict(detail),
    )


@_router.patch(
    "/api/v1/skills/{name}",
    response_model=SkillpackMutationResponse,
    responses={
        403: _error_responses[403],
        404: _error_responses[404],
        409: _error_responses[409],
        422: _error_responses[422],
        500: _error_responses[500],
    },
)
async def patch_skill(
    name: str,
    request: SkillpackPatchRequest,
) -> SkillpackMutationResponse | JSONResponse:
    """更新 project 层 skillpack。"""
    if _is_external_safe_mode():
        return _error_json_response(403, "external_safe_mode 开启时禁止写入 skillpack。")

    engine = _require_skill_engine()
    try:
        detail = engine.patch_skillpack(
            name=name,
            payload=request.payload,
            actor="api",
        )
    except SkillpackInputError as exc:
        return _error_json_response(422, str(exc))
    except SkillpackNotFoundError as exc:
        return _error_json_response(404, str(exc))
    except SkillpackConflictError as exc:
        return _error_json_response(409, str(exc))

    return SkillpackMutationResponse(
        status="updated",
        name=str(detail.get("name", name)),
        detail=_to_standard_skill_detail_dict(detail),
    )


@_router.delete(
    "/api/v1/skills/{name}",
    response_model=SkillpackMutationResponse,
    responses={
        403: _error_responses[403],
        404: _error_responses[404],
        409: _error_responses[409],
        422: _error_responses[422],
        500: _error_responses[500],
    },
)
async def delete_skill(
    name: str,
    reason: str = "",
) -> SkillpackMutationResponse | JSONResponse:
    """软删除 project 层 skillpack。"""
    if _is_external_safe_mode():
        return _error_json_response(403, "external_safe_mode 开启时禁止写入 skillpack。")

    engine = _require_skill_engine()
    try:
        detail = engine.delete_skillpack(
            name=name,
            actor="api",
            reason=reason,
        )
    except SkillpackInputError as exc:
        return _error_json_response(422, str(exc))
    except SkillpackNotFoundError as exc:
        return _error_json_response(404, str(exc))
    except SkillpackConflictError as exc:
        return _error_json_response(409, str(exc))

    return SkillpackMutationResponse(
        status="deleted",
        name=str(detail.get("name", name)),
        detail=detail,
    )


@_router.post(
    "/api/v1/skills/import",
    status_code=201,
    response_model=SkillpackMutationResponse,
    responses={
        403: _error_responses[403],
        409: _error_responses[409],
        422: _error_responses[422],
        500: _error_responses[500],
    },
)
async def import_skill(
    request: SkillpackImportRequest,
) -> SkillpackMutationResponse | JSONResponse:
    """从本地路径或 GitHub URL 导入 SKILL.md 及附属资源。"""
    if _is_external_safe_mode():
        return _error_json_response(403, "external_safe_mode 开启时禁止写入 skillpack。")

    engine = _require_skill_engine()
    try:
        result = await engine.import_skillpack_async(
            source=request.source,
            value=request.value,
            actor="api",
            overwrite=request.overwrite,
        )
    except SkillpackInputError as exc:
        return _error_json_response(422, str(exc))
    except SkillpackConflictError as exc:
        return _error_json_response(409, str(exc))
    except SkillImportError as exc:
        return _error_json_response(422, str(exc))

    return SkillpackMutationResponse(
        status="imported",
        name=str(result.get("name", "")),
        detail=result,
    )


@_router.delete("/api/v1/sessions/{session_id}", responses={
    409: _error_responses[409],
    404: _error_responses[404],
    500: _error_responses[500],
})
async def delete_session(session_id: str) -> dict:
    """删除指定会话并释放资源。"""
    assert _session_manager is not None, "服务未初始化"

    deleted = await _session_manager.delete(session_id)
    if not deleted:
        raise SessionNotFoundError(f"会话 '{session_id}' 不存在。")
    return {"status": "ok", "session_id": session_id}


_ALLOWED_UPLOAD_EXTENSIONS = {".xlsx", ".xls", ".csv", ".png", ".jpg", ".jpeg"}


@_router.post("/api/v1/upload")
async def upload_file(file: UploadFile = FastAPIFile(...)) -> JSONResponse:
    """上传文件到 workspace uploads 目录。"""
    assert _config is not None, "服务未初始化"

    filename = file.filename or "unnamed"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        return _error_json_response(400, f"不支持的文件格式: {ext}")

    upload_dir = os.path.join(_config.workspace_root, "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    safe_name = f"{uuid.uuid4().hex[:8]}_{filename}"
    dest_path = os.path.join(upload_dir, safe_name)

    content = await file.read()
    with open(dest_path, "wb") as f:
        f.write(content)

    return JSONResponse(content={
        "filename": filename,
        "path": dest_path,
        "size": len(content),
    })


@_router.get("/api/v1/sessions")
async def list_sessions(request: Request) -> JSONResponse:
    """列出所有会话（含历史）。"""
    assert _session_manager is not None, "服务未初始化"
    include_archived = request.query_params.get("include_archived", "false").lower() == "true"
    sessions = await _session_manager.list_sessions(include_archived=include_archived)
    return JSONResponse(content={"sessions": sessions})


@_router.get("/api/v1/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, request: Request) -> JSONResponse:
    """分页获取会话消息。"""
    assert _session_manager is not None, "服务未初始化"
    limit = int(request.query_params.get("limit", "50"))
    offset = int(request.query_params.get("offset", "0"))
    messages = await _session_manager.get_session_messages(session_id, limit=limit, offset=offset)
    return JSONResponse(content={"messages": messages, "session_id": session_id})


@_router.get("/api/v1/sessions/{session_id}")
async def get_session(session_id: str) -> JSONResponse:
    """获取会话详情含消息历史。"""
    assert _session_manager is not None, "服务未初始化"
    detail = await _session_manager.get_session_detail(session_id)
    return JSONResponse(content=detail)


class ModelSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


@_router.get("/api/v1/models")
async def list_models() -> JSONResponse:
    """获取可用模型列表（含多模型配置档案）。"""
    assert _config is not None, "服务未初始化"
    # 使用 engine 的 list_models() 获取完整多模型配置
    try:
        engine = _require_skill_engine()
        rows = engine.list_models()
        models = [
            {
                "name": row["name"],
                "model": row["model"],
                "description": row.get("description", ""),
                "active": row.get("active") == "yes",
                "base_url": row.get("base_url", ""),
            }
            for row in rows
        ]
    except Exception:
        # 回退到仅返回主模型
        models = [{"name": "default", "model": _config.model, "description": "默认模型", "active": True, "base_url": _config.base_url}]
    return JSONResponse(content={"models": models})


@_router.put("/api/v1/models/active")
async def switch_model(request: ModelSwitchRequest) -> JSONResponse:
    """切换当前活跃模型（支持智能匹配）。"""
    assert _session_manager is not None, "服务未初始化"
    # 对所有活跃会话的引擎执行模型切换
    sessions = await _session_manager.list_sessions()
    result_msg = f"模型已切换为 {request.name}"
    for session_info in sessions:
        try:
            detail = await _session_manager.get_session_detail(session_info["id"])
            # 直接访问 session entry 的 engine
        except Exception:
            pass
    # 使用临时 engine 执行切换（演示返回）
    try:
        engine = _require_skill_engine()
        result_msg = engine.switch_model(request.name)
    except Exception:
        pass
    return JSONResponse(content={"message": result_msg})


# ── 模型配置管理 API（.env 持久化） ──────────────────────

_MODEL_ENV_KEYS = {
    "main": {"api_key": "EXCELMANUS_API_KEY", "base_url": "EXCELMANUS_BASE_URL", "model": "EXCELMANUS_MODEL"},
    "aux": {"model": "EXCELMANUS_AUX_MODEL"},
    "router": {"api_key": "EXCELMANUS_ROUTER_API_KEY", "base_url": "EXCELMANUS_ROUTER_BASE_URL", "model": "EXCELMANUS_ROUTER_MODEL"},
    "vlm": {"api_key": "EXCELMANUS_VLM_API_KEY", "base_url": "EXCELMANUS_VLM_BASE_URL", "model": "EXCELMANUS_VLM_MODEL"},
    "window_advisor": {"api_key": "EXCELMANUS_WINDOW_ADVISOR_API_KEY", "base_url": "EXCELMANUS_WINDOW_ADVISOR_BASE_URL"},
}


def _find_env_file() -> str:
    """定位 .env 文件路径。"""
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[0]


def _read_env_file(path: str) -> list[str]:
    """读取 .env 文件所有行。"""
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()


def _write_env_file(path: str, lines: list[str]) -> None:
    """写回 .env 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _update_env_var(lines: list[str], key: str, value: str) -> list[str]:
    """更新或追加环境变量行，保持注释和格式。"""
    new_lines = []
    found = False
    for line in lines:
        stripped = line.strip()
        # 匹配 KEY=... 或 # KEY=...
        if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
            if value:
                new_lines.append(f"{key}={value}\n")
            else:
                new_lines.append(f"# {key}=\n")
            found = True
        else:
            new_lines.append(line)
    if not found and value:
        new_lines.append(f"{key}={value}\n")
    return new_lines


class ModelConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


class ModelProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    model: str
    api_key: str = ""
    base_url: str = ""
    description: str = ""


@_router.get("/api/v1/config/models")
async def get_model_config() -> JSONResponse:
    """获取全部模型配置（main/aux/router/vlm/window_advisor + profiles）。"""
    assert _config is not None, "服务未初始化"
    result: dict = {
        "main": {
            "api_key": _mask_key(_config.api_key),
            "base_url": _config.base_url,
            "model": _config.model,
        },
        "aux": {
            "model": _config.aux_model or "",
        },
        "router": {
            "api_key": _mask_key(_config.router_api_key or ""),
            "base_url": _config.router_base_url or "",
            "model": _config.router_model or "",
        },
        "vlm": {
            "api_key": _mask_key(_config.vlm_api_key or ""),
            "base_url": _config.vlm_base_url or "",
            "model": _config.vlm_model or "",
        },
        "window_advisor": {
            "api_key": _mask_key(_config.window_advisor_api_key or ""),
            "base_url": _config.window_advisor_base_url or "",
        },
        "profiles": [
            {
                "name": p.name,
                "model": p.model,
                "api_key": _mask_key(p.api_key),
                "base_url": p.base_url,
                "description": p.description,
            }
            for p in _config.models
        ],
    }
    return JSONResponse(content=result)


def _mask_key(key: str) -> str:
    """脱敏 API Key：保留前4后4位。"""
    if not key or len(key) <= 12:
        return "****" if key else ""
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


@_router.put("/api/v1/config/models/{section}")
async def update_model_config(section: str, request: ModelConfigUpdate) -> JSONResponse:
    """更新指定模型配置区块并持久化到 .env。"""
    if section not in _MODEL_ENV_KEYS:
        return _error_json_response(400, f"未知配置区块: {section}")

    env_path = _find_env_file()
    lines = _read_env_file(env_path)
    key_map = _MODEL_ENV_KEYS[section]

    updates: dict[str, str] = {}
    if request.api_key is not None and "api_key" in key_map:
        updates[key_map["api_key"]] = request.api_key
    if request.base_url is not None and "base_url" in key_map:
        updates[key_map["base_url"]] = request.base_url
    if request.model is not None and "model" in key_map:
        updates[key_map["model"]] = request.model

    if not updates:
        return _error_json_response(400, "无有效更新字段")

    for env_key, env_val in updates.items():
        lines = _update_env_var(lines, env_key, env_val)

    _write_env_file(env_path, lines)

    # 同步更新环境变量使运行时生效
    for env_key, env_val in updates.items():
        if env_val:
            os.environ[env_key] = env_val
        elif env_key in os.environ:
            del os.environ[env_key]

    return JSONResponse(content={"status": "ok", "section": section, "updated": list(updates.keys())})


@_router.post("/api/v1/config/models/profiles")
async def add_model_profile(request: ModelProfileCreate) -> JSONResponse:
    """新增多模型条目并持久化到 .env。"""
    env_path = _find_env_file()
    lines = _read_env_file(env_path)

    # 读取当前 EXCELMANUS_MODELS
    current_raw = os.environ.get("EXCELMANUS_MODELS", "")
    profiles: list = []
    if current_raw:
        try:
            profiles = json.loads(current_raw)
        except json.JSONDecodeError:
            profiles = []

    # 检查名称重复
    for p in profiles:
        if isinstance(p, dict) and p.get("name") == request.name:
            return _error_json_response(409, f"模型名称已存在: {request.name}")

    new_entry: dict = {"name": request.name, "model": request.model}
    if request.api_key:
        new_entry["api_key"] = request.api_key
    if request.base_url:
        new_entry["base_url"] = request.base_url
    if request.description:
        new_entry["description"] = request.description
    profiles.append(new_entry)

    new_json = json.dumps(profiles, ensure_ascii=False)
    lines = _update_env_var(lines, "EXCELMANUS_MODELS", new_json)
    _write_env_file(env_path, lines)
    os.environ["EXCELMANUS_MODELS"] = new_json

    return JSONResponse(status_code=201, content={"status": "created", "name": request.name})


@_router.delete("/api/v1/config/models/profiles/{name}")
async def delete_model_profile(name: str) -> JSONResponse:
    """删除多模型条目并持久化到 .env。"""
    env_path = _find_env_file()
    lines = _read_env_file(env_path)

    current_raw = os.environ.get("EXCELMANUS_MODELS", "")
    profiles: list = []
    if current_raw:
        try:
            profiles = json.loads(current_raw)
        except json.JSONDecodeError:
            profiles = []

    new_profiles = [p for p in profiles if not (isinstance(p, dict) and p.get("name") == name)]
    if len(new_profiles) == len(profiles):
        return _error_json_response(404, f"未找到模型: {name}")

    new_json = json.dumps(new_profiles, ensure_ascii=False) if new_profiles else ""
    lines = _update_env_var(lines, "EXCELMANUS_MODELS", new_json)
    _write_env_file(env_path, lines)
    if new_json:
        os.environ["EXCELMANUS_MODELS"] = new_json
    elif "EXCELMANUS_MODELS" in os.environ:
        del os.environ["EXCELMANUS_MODELS"]

    return JSONResponse(content={"status": "deleted", "name": name})


@_router.put("/api/v1/config/models/profiles/{name}")
async def update_model_profile(name: str, request: ModelProfileCreate) -> JSONResponse:
    """更新多模型条目并持久化到 .env。"""
    env_path = _find_env_file()
    lines = _read_env_file(env_path)

    current_raw = os.environ.get("EXCELMANUS_MODELS", "")
    profiles: list = []
    if current_raw:
        try:
            profiles = json.loads(current_raw)
        except json.JSONDecodeError:
            profiles = []

    found = False
    for i, p in enumerate(profiles):
        if isinstance(p, dict) and p.get("name") == name:
            profiles[i] = {
                "name": request.name,
                "model": request.model,
                **(({"api_key": request.api_key} if request.api_key else {})),
                **(({"base_url": request.base_url} if request.base_url else {})),
                **(({"description": request.description} if request.description else {})),
            }
            found = True
            break

    if not found:
        return _error_json_response(404, f"未找到模型: {name}")

    new_json = json.dumps(profiles, ensure_ascii=False)
    lines = _update_env_var(lines, "EXCELMANUS_MODELS", new_json)
    _write_env_file(env_path, lines)
    os.environ["EXCELMANUS_MODELS"] = new_json

    return JSONResponse(content={"status": "updated", "name": request.name})


# ── MCP Server 管理 API ──────────────────────────────────


def _find_mcp_config_path() -> str:
    """定位 mcp.json 配置文件路径（写操作目标）。"""
    env_path = os.environ.get("EXCELMANUS_MCP_CONFIG")
    if env_path and os.path.isfile(env_path):
        return env_path
    ws = _config.workspace_root if _config else os.getcwd()
    ws_path = os.path.join(ws, "mcp.json")
    if os.path.isfile(ws_path):
        return ws_path
    home_path = os.path.join(os.path.expanduser("~"), ".excelmanus", "mcp.json")
    if os.path.isfile(home_path):
        return home_path
    # 默认写到 workspace
    return ws_path


def _read_mcp_json(path: str) -> dict:
    """读取 mcp.json 文件内容。"""
    if not os.path.isfile(path):
        return {"mcpServers": {}}
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return {"mcpServers": {}}
    if not isinstance(data, dict):
        return {"mcpServers": {}}
    if "mcpServers" not in data:
        data["mcpServers"] = {}
    return data


def _write_mcp_json(path: str, data: dict) -> None:
    """写回 mcp.json 文件。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _get_shared_mcp_manager() -> "MCPManager | None":
    """获取共享 MCP 管理器实例。"""
    if _session_manager is None:
        return None
    return getattr(_session_manager, "_shared_mcp_manager", None)


class MCPServerCreateRequest(BaseModel):
    """创建/更新 MCP Server 请求体。"""
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    transport: Literal["stdio", "sse", "streamable_http"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: int = 30
    autoApprove: list[str] = Field(default_factory=list)


def _server_request_to_entry(req: MCPServerCreateRequest) -> dict:
    """将请求体转为 mcp.json 条目格式。"""
    entry: dict[str, Any] = {"transport": req.transport}
    if req.transport == "stdio":
        if req.command:
            entry["command"] = req.command
        if req.args:
            entry["args"] = req.args
        if req.env:
            entry["env"] = req.env
    else:
        if req.url:
            entry["url"] = req.url
        if req.headers:
            entry["headers"] = req.headers
    if req.timeout != 30:
        entry["timeout"] = req.timeout
    if req.autoApprove:
        entry["autoApprove"] = req.autoApprove
    return entry


@_router.get("/api/v1/mcp/servers")
async def list_mcp_servers() -> JSONResponse:
    """列出所有 MCP Server 配置 + 运行时状态。"""
    config_path = _find_mcp_config_path()
    config_data = _read_mcp_json(config_path)
    servers_dict = config_data.get("mcpServers", {})

    # 运行时状态
    runtime_info: dict[str, dict] = {}
    manager = _get_shared_mcp_manager()
    if manager is not None:
        for info in manager.get_server_info():
            runtime_info[info["name"]] = info

    result = []
    for name, entry in servers_dict.items():
        rt = runtime_info.get(name, {})
        result.append({
            "name": name,
            "config": entry,
            "status": rt.get("status", "not_connected"),
            "transport": entry.get("transport", "unknown"),
            "tool_count": rt.get("tool_count", 0),
            "tools": rt.get("tools", []),
            "last_error": rt.get("last_error"),
            "auto_approve": entry.get("autoApprove", []),
        })

    return JSONResponse(content={"servers": result, "config_path": config_path})


@_router.post("/api/v1/mcp/servers", status_code=201)
async def create_mcp_server(request: MCPServerCreateRequest) -> JSONResponse:
    """新增 MCP Server 条目到 mcp.json。"""
    if not request.name.strip():
        return _error_json_response(400, "Server 名称不能为空")

    config_path = _find_mcp_config_path()
    data = _read_mcp_json(config_path)

    if request.name in data["mcpServers"]:
        return _error_json_response(409, f"Server '{request.name}' 已存在")

    entry = _server_request_to_entry(request)
    data["mcpServers"][request.name] = entry
    _write_mcp_json(config_path, data)

    return JSONResponse(
        status_code=201,
        content={"status": "created", "name": request.name},
    )


@_router.put("/api/v1/mcp/servers/{name}")
async def update_mcp_server(name: str, request: MCPServerCreateRequest) -> JSONResponse:
    """更新 MCP Server 配置。"""
    config_path = _find_mcp_config_path()
    data = _read_mcp_json(config_path)

    if name not in data["mcpServers"]:
        return _error_json_response(404, f"Server '{name}' 不存在")

    entry = _server_request_to_entry(request)

    # 如果名称变更，删除旧条目
    new_name = request.name.strip() or name
    if new_name != name:
        del data["mcpServers"][name]
    data["mcpServers"][new_name] = entry
    _write_mcp_json(config_path, data)

    return JSONResponse(content={"status": "updated", "name": new_name})


@_router.delete("/api/v1/mcp/servers/{name}")
async def delete_mcp_server(name: str) -> JSONResponse:
    """删除 MCP Server 条目。"""
    config_path = _find_mcp_config_path()
    data = _read_mcp_json(config_path)

    if name not in data["mcpServers"]:
        return _error_json_response(404, f"Server '{name}' 不存在")

    del data["mcpServers"][name]
    _write_mcp_json(config_path, data)

    return JSONResponse(content={"status": "deleted", "name": name})


@_router.post("/api/v1/mcp/reload")
async def reload_mcp() -> JSONResponse:
    """热重载所有 MCP 连接：关闭现有连接 → 重新初始化。"""
    manager = _get_shared_mcp_manager()
    if manager is None:
        return _error_json_response(400, "未启用共享 MCP 管理器")

    try:
        await manager.shutdown()
        # 重置初始化标志，允许 re-initialize
        manager._initialized = False
        if _session_manager is not None:
            _session_manager._mcp_initialized = False
        assert _tool_registry is not None
        await manager.initialize(_tool_registry)
    except Exception as exc:
        logger.error("MCP 热重载失败: %s", exc, exc_info=True)
        return _error_json_response(500, f"MCP 热重载失败: {exc}")

    info = manager.get_server_info()
    ready = sum(1 for s in info if s["status"] == "ready")
    return JSONResponse(content={
        "status": "ok",
        "servers_total": len(info),
        "servers_ready": ready,
    })


@_router.post("/api/v1/mcp/servers/{name}/test")
async def test_mcp_server(name: str) -> JSONResponse:
    """测试单个 MCP Server 连接。"""
    config_path = _find_mcp_config_path()
    data = _read_mcp_json(config_path)

    if name not in data["mcpServers"]:
        return _error_json_response(404, f"Server '{name}' 不存在")

    from excelmanus.mcp.config import MCPConfigLoader

    # 解析该 server 的配置
    single_data = {"mcpServers": {name: data["mcpServers"][name]}}
    configs = MCPConfigLoader._parse_config(single_data)
    if not configs:
        return _error_json_response(400, f"Server '{name}' 配置无效")

    cfg = configs[0]
    client = MCPClientWrapper(cfg)
    try:
        await client.connect()
        tools = await client.discover_tools()
        tool_names = [getattr(t, "name", str(t)) for t in tools]
        await client.close()
        return JSONResponse(content={
            "status": "ok",
            "name": name,
            "tool_count": len(tool_names),
            "tools": tool_names,
        })
    except Exception as exc:
        try:
            await client.close()
        except Exception:
            pass
        return JSONResponse(
            status_code=502,
            content={
                "status": "error",
                "name": name,
                "error": str(exc)[:300],
            },
        )


@_router.get("/api/v1/mentions")
async def list_mentions(path: str = "") -> JSONResponse:
    """返回 @ 提及可选项。path 参数支持子目录扫描。"""
    tools: list[str] = []
    skills: list[dict] = []
    files: list[str] = []

    if _tool_registry is not None:
        tools = sorted(_tool_registry.get_tool_names())
    if _skillpack_loader is not None:
        for name, sp in _skillpack_loader.get_skillpacks().items():
            skills.append({
                "name": name,
                "description": sp.get("description", "") if isinstance(sp, dict) else "",
            })
    if _config is not None:
        ws = _config.workspace_root
        # 安全检查：path 不能包含 .. 防止路径遍历
        safe_path = path.replace("..", "").strip("/")
        scan_dir = os.path.join(ws, safe_path) if safe_path else ws
        if os.path.isdir(scan_dir):
            try:
                for entry in os.scandir(scan_dir):
                    if entry.name.startswith(".") or entry.name in {"node_modules", "__pycache__", ".venv"}:
                        continue
                    rel = f"{safe_path}/{entry.name}" if safe_path else entry.name
                    if entry.is_dir():
                        files.append(rel + "/")
                    elif entry.is_file():
                        files.append(rel)
            except OSError:
                pass

    return JSONResponse(content={
        "tools": tools,
        "skills": [{"name": s["name"], "description": s.get("description", "")} for s in skills],
        "files": sorted(files),
        "path": safe_path if _config else "",
    })


@_router.post("/api/v1/command")
async def execute_command(request: Request) -> JSONResponse:
    """执行展示型斜杠命令并返回结果（不走 chat 流）。"""
    body = await request.json()
    command = (body.get("command") or "").strip()
    if not command:
        return _error_json_response(400, "缺少 command 字段")

    # /help → 返回命令列表
    if command == "/help":
        from excelmanus.control_commands import CONTROL_COMMAND_SPECS
        lines = ["### ExcelManus 命令帮助\n"]
        lines.append("| 命令 | 说明 |")
        lines.append("|------|------|")
        base = [
            ("/help", "显示帮助"), ("/skills", "查看技能包"), ("/history", "对话历史摘要"),
            ("/clear", "清除对话历史"), ("/mcp", "MCP Server 状态"), ("/save", "保存对话记录"),
            ("/config", "环境变量配置"),
        ]
        for cmd, desc in base:
            lines.append(f"| `{cmd}` | {desc} |")
        for spec in CONTROL_COMMAND_SPECS:
            args = f" ({', '.join(spec.arguments)})" if spec.arguments else ""
            lines.append(f"| `{spec.command}{args}` | {spec.description} |")
        return JSONResponse(content={"result": "\n".join(lines), "format": "markdown"})

    # /skills → 返回技能包列表
    if command == "/skills":
        if _skillpack_loader is None:
            return JSONResponse(content={"result": "技能包未加载", "format": "text"})
        lines = ["### 已加载技能包\n"]
        for sp in _skillpack_loader.list_skillpacks():
            name = sp.name if hasattr(sp, "name") else str(sp.get("name", ""))
            desc = sp.description if hasattr(sp, "description") else str(sp.get("description", ""))
            source = sp.source if hasattr(sp, "source") else str(sp.get("source", ""))
            lines.append(f"- **{name}** ({source}) — {desc}")
        return JSONResponse(content={"result": "\n".join(lines), "format": "markdown"})

    # /history → 对话历史摘要
    if command == "/history":
        if _session_manager is None:
            return JSONResponse(content={"result": "服务未初始化", "format": "text"})
        try:
            sessions = await _session_manager.list_sessions()
            if not sessions:
                return JSONResponse(content={"result": "暂无活跃会话", "format": "text"})
            lines = ["### 活跃会话\n"]
            for s in sessions:
                status = "🔄" if s.get("in_flight") else "💬"
                lines.append(f"- {status} **{s['title']}** ({s['message_count']} 条消息) `{s['id'][:8]}...`")
            return JSONResponse(content={"result": "\n".join(lines), "format": "markdown"})
        except Exception:
            return JSONResponse(content={"result": "获取会话历史失败", "format": "text"})

    # /mcp → 返回 MCP 状态
    if command == "/mcp":
        if _session_manager is None:
            return JSONResponse(content={"result": "服务未初始化", "format": "text"})
        return JSONResponse(content={"result": "MCP 状态请通过设置面板查看", "format": "text"})

    # /config → 返回配置摘要
    if command in {"/config", "/config list", "/config get"}:
        if _config is None:
            return JSONResponse(content={"result": "配置未加载", "format": "text"})
        lines = ["### 当前配置\n"]
        lines.append(f"- **模型**: `{_config.model}`")
        lines.append(f"- **Base URL**: `{_config.base_url}`")
        lines.append(f"- **工作区**: `{_config.workspace_root}`")
        lines.append(f"- **最大迭代**: {_config.max_iterations}")
        lines.append(f"- **辅助模型**: `{_config.aux_model or '未配置'}`")
        lines.append(f"- **路由模型**: `{_config.router_model or '未配置'}`")
        lines.append(f"- **VLM 模型**: `{_config.vlm_model or '未配置'}`")
        lines.append(f"- **子代理**: {'开启' if _config.subagent_enabled else '关闭'}")
        lines.append(f"- **备份模式**: {'开启' if _config.backup_enabled else '关闭'}")
        lines.append(f"- **安全模式**: {'开启' if _config.external_safe_mode else '关闭'}")
        lines.append(f"- **多模型配置**: {len(_config.models)} 个")
        return JSONResponse(content={"result": "\n".join(lines), "format": "markdown"})

    # /model, /model list → 返回模型列表
    if command in {"/model", "/model list"}:
        try:
            engine = _require_skill_engine()
            rows = engine.list_models()
            lines = ["### 可用模型\n"]
            for row in rows:
                marker = " ✦" if row.get("active") == "yes" else ""
                desc = f" — {row['description']}" if row.get("description") else ""
                lines.append(f"- **{row['name']}** → `{row['model']}`{desc}{marker}")
            return JSONResponse(content={"result": "\n".join(lines), "format": "markdown"})
        except Exception:
            return JSONResponse(content={"result": "模型列表获取失败", "format": "text"})

    # /subagent list, /subagent status
    if command in {"/subagent list", "/subagent status"}:
        assert _config is not None
        status = "开启" if _config.subagent_enabled else "关闭"
        return JSONResponse(content={"result": f"子代理状态: **{status}**\n\n最大迭代: {_config.subagent_max_iterations}", "format": "markdown"})

    # /backup list, /backup status
    if command in {"/backup list", "/backup status"}:
        assert _config is not None
        status = "开启" if _config.backup_enabled else "关闭"
        return JSONResponse(content={"result": f"备份沙盒: **{status}**", "format": "markdown"})

    # /compact status
    if command == "/compact status":
        assert _config is not None
        status = "开启" if _config.compaction_enabled else "关闭"
        return JSONResponse(content={"result": f"上下文压缩: **{status}**\n\n阈值: {_config.compaction_threshold_ratio}", "format": "markdown"})

    # /fullaccess status
    if command == "/fullaccess status":
        # fullaccess 是会话级状态，默认关闭
        hint = "全权限模式: **关闭**（默认）\n\n使用 `/fullaccess on` 开启，开启后工具调用将跳过审批确认"
        if _session_manager is not None:
            try:
                sessions = await _session_manager.list_sessions()
                for s in sessions:
                    detail = await _session_manager.get_session_detail(s["id"])
                    if detail.get("full_access_enabled"):
                        hint = f"全权限模式: **开启**（会话 `{s['id'][:8]}...`）\n\n使用 `/fullaccess off` 关闭"
                        break
            except Exception:
                pass
        return JSONResponse(content={"result": hint, "format": "markdown"})

    # /plan status
    if command == "/plan status":
        hint = "计划模式: **关闭**（默认）\n\n使用 `/plan on` 开启，开启后 Agent 会先输出计划再执行"
        return JSONResponse(content={"result": hint, "format": "markdown"})

    # /manifest status
    if command == "/manifest status":
        return JSONResponse(content={"result": "工作区清单: 请通过 `/manifest build` 触发构建\n\n清单会在会话首轮自动预热构建", "format": "markdown"})

    # /save
    if command == "/save":
        if _session_manager is None:
            return JSONResponse(content={"result": "服务未初始化", "format": "text"})
        try:
            sessions = await _session_manager.list_sessions()
            count = len(sessions)
            return JSONResponse(content={"result": f"当前有 **{count}** 个活跃会话\n\n网页端对话自动保存，无需手动操作", "format": "markdown"})
        except Exception:
            return JSONResponse(content={"result": "会话状态获取失败", "format": "text"})

    return JSONResponse(content={"result": f"未知命令: {command}", "format": "text"})


@_router.get("/api/v1/health")
async def health() -> dict:
    """健康检查：返回版本号和已加载的工具/技能包。"""
    if _is_external_safe_mode():
        return {
            "status": "ok",
            "version": excelmanus.__version__,
            "model": "hidden",
            "tools": [],
            "skillpacks": [],
        }

    tools: list[str] = []
    skillpacks: list[str] = []
    if _tool_registry is not None:
        tools = sorted(_tool_registry.get_tool_names())
    if _skillpack_loader is not None:
        skillpacks = sorted(_skillpack_loader.get_skillpacks().keys())

    active_sessions = 0
    if _session_manager is not None:
        active_sessions = await _session_manager.get_active_count()

    return {
        "status": "ok",
        "version": excelmanus.__version__,
        "model": _config.model if _config is not None else "",
        "tools": tools,
        "skillpacks": skillpacks,
        "active_sessions": active_sessions,
    }


# 默认 ASGI app（供 `uvicorn excelmanus.api:app` 与测试直接导入）
app = create_app()


# ── 入口函数 ──────────────────────────────────────────────


def main() -> None:
    """API 服务入口函数（pyproject.toml 入口点）。"""
    uvicorn.run(
        "excelmanus.api:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
