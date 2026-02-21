"""API 服务模块：基于 FastAPI 的 REST API 服务。

端点：
- POST   /api/v1/chat                  对话接口（完整 JSON）
- POST   /api/v1/chat/stream            对话接口（SSE 流式）
- GET    /api/v1/skills                列出 Skillpack 摘要
- GET    /api/v1/skills/{name}         查询 Skillpack 详情
- POST   /api/v1/skills                创建 project Skillpack
- PATCH  /api/v1/skills/{name}         更新 project Skillpack
- DELETE /api/v1/skills/{name}         软删除 project Skillpack
- DELETE /api/v1/sessions/{session_id}  删除会话
- GET    /api/v1/health                 健康检查
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator, Literal

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StringConstraints

import excelmanus
from excelmanus.config import (
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
from excelmanus.tools import ToolRegistry

logger = get_logger("api")
_APP_CORS_ALLOW_ORIGINS = tuple(load_cors_allow_origins())

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
    tool_scope: list[str] = Field(default_factory=list, deprecated="v5.2: 始终为空，保留向后兼容")
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

    # 加载配置
    _config = load_config()
    setup_logging(_config.log_level)

    # 初始化工具层
    _tool_registry = ToolRegistry()
    _tool_registry.register_builtin_tools(_config.workspace_root)

    # 初始化 Skillpack 层
    _skillpack_loader = SkillpackLoader(_config, _tool_registry)
    _skillpack_loader.load_all()
    _skill_router = SkillRouter(_config, _skillpack_loader)

    # 初始化会话管理器
    shared_mcp_manager: MCPManager | None = None
    if _config.mcp_shared_manager:
        shared_mcp_manager = MCPManager(_config.workspace_root)

    # 启动时校验 CORS 配置入口一致性，避免“配置已加载但中间件未消费”
    if _APP_CORS_ALLOW_ORIGINS != _config.cors_allow_origins:
        logger.warning(
            "CORS 配置来源不一致：middleware=%s config=%s",
            list(_APP_CORS_ALLOW_ORIGINS),
            list(_config.cors_allow_origins),
        )

    _session_manager = SessionManager(
        max_sessions=_config.max_sessions,
        ttl_seconds=_config.session_ttl_seconds,
        config=_config,
        registry=_tool_registry,
        skill_router=_skill_router,
        shared_mcp_manager=shared_mcp_manager,
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

    logger.info("API 服务已关闭")


# ── FastAPI 应用 ──────────────────────────────────────────

app = FastAPI(
    title="ExcelManus API",
    version=excelmanus.__version__,
    lifespan=lifespan,
)

# CORS 中间件必须在应用启动前注册，避免 Starlette 运行时报错
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_APP_CORS_ALLOW_ORIGINS),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


# ── 全局异常处理 ──────────────────────────────────────────


@app.exception_handler(SessionNotFoundError)
async def _handle_session_not_found(
    request: Request, exc: SessionNotFoundError
) -> JSONResponse:
    """会话不存在 → 404。"""
    error_id = str(uuid.uuid4())
    body = ErrorResponse(error=str(exc), error_id=error_id)
    return JSONResponse(status_code=404, content=body.model_dump())


@app.exception_handler(SessionLimitExceededError)
async def _handle_session_limit(
    request: Request, exc: SessionLimitExceededError
) -> JSONResponse:
    """会话数量超限 → 429。"""
    error_id = str(uuid.uuid4())
    body = ErrorResponse(error=str(exc), error_id=error_id)
    return JSONResponse(status_code=429, content=body.model_dump())


@app.exception_handler(SessionBusyError)
async def _handle_session_busy(
    request: Request, exc: SessionBusyError
) -> JSONResponse:
    """会话正在处理中 → 409。"""
    error_id = str(uuid.uuid4())
    body = ErrorResponse(error=str(exc), error_id=error_id)
    return JSONResponse(status_code=409, content=body.model_dump())


@app.exception_handler(Exception)
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


@app.post("/api/v1/chat", response_model=ChatResponse, responses=_error_responses)
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


@app.post("/api/v1/chat/stream", responses=_error_responses)
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

        try:
            while True:
                # 优先检查队列中的事件
                try:
                    event = event_queue.get_nowait()
                except asyncio.QueueEmpty:
                    # 队列为空，检查 chat 任务是否已完成
                    if chat_task.done():
                        # 排空队列中剩余事件
                        while not event_queue.empty():
                            event = event_queue.get_nowait()
                            if event is not None:
                                sse = _sse_event_to_sse(event, safe_mode=safe_mode)
                                if sse is not None:
                                    yield sse
                        break
                    # 等待新事件或任务完成
                    done, _ = await asyncio.wait(
                        [
                            asyncio.create_task(event_queue.get()),
                            chat_task,
                        ],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        if task is not chat_task:
                            event = task.result()
                            if event is not None:
                                sse = _sse_event_to_sse(event, safe_mode=safe_mode)
                                if sse is not None:
                                    yield sse
                    continue

                if event is not None:
                    sse = _sse_event_to_sse(event, safe_mode=safe_mode)
                    if sse is not None:
                        yield sse

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

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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
            "tool_name": event.tool_name,
            "arguments": sanitize_external_data(
                event.arguments if isinstance(event.arguments, dict) else {},
                max_len=1000,
            ),
            "iteration": event.iteration,
        }
    elif event.event_type == EventType.TOOL_CALL_END:
        data = {
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
    else:
        data = event.to_dict()

    return _sse_format(sse_type, data)


@app.get(
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


@app.get(
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


@app.post(
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


@app.patch(
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


@app.delete(
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


@app.delete("/api/v1/sessions/{session_id}", responses={
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


@app.get("/api/v1/health")
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


# ── 入口函数 ──────────────────────────────────────────────


def main() -> None:
    """API 服务入口函数（pyproject.toml 入口点）。"""
    uvicorn.run(
        "excelmanus.api:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
