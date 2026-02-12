"""API 服务模块：基于 FastAPI 的 REST API 服务。

端点：
- POST   /api/v1/chat                  对话接口（完整 JSON）
- POST   /api/v1/chat/stream            对话接口（SSE 流式）
- DELETE /api/v1/sessions/{session_id}  删除会话
- GET    /api/v1/health                 健康检查
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, StringConstraints

import excelmanus
from excelmanus.config import (
    ExcelManusConfig,
    load_config,
    load_cors_allow_origins,
)
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.logger import get_logger, setup_logging
from excelmanus.output_guard import guard_public_reply, sanitize_external_text
from excelmanus.session import (
    SessionBusyError,
    SessionLimitExceededError,
    SessionManager,
    SessionNotFoundError,
)
from excelmanus.skillpacks import SkillpackLoader, SkillRouter
from excelmanus.tools import ToolRegistry

logger = get_logger("api")

# ── 请求 / 响应模型 ──────────────────────────────────────


class ChatRequest(BaseModel):
    """对话请求体。"""

    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1)
    ]
    session_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ] | None = None
    skill_hints: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]] | None = None


class ChatResponse(BaseModel):
    """对话响应体。"""

    session_id: str
    reply: str
    skills_used: list[str]
    tool_scope: list[str]
    route_mode: str


class ErrorResponse(BaseModel):
    """错误响应体（不暴露内部堆栈）。"""

    error: str
    error_id: str


# ── 全局状态（由 lifespan 初始化） ────────────────────────

_session_manager: SessionManager | None = None
_tool_registry: ToolRegistry | None = None
_skillpack_loader: SkillpackLoader | None = None
_skill_router: SkillRouter | None = None
_config: ExcelManusConfig | None = None
_cleanup_task: asyncio.Task[None] | None = None


# ── 定期清理后台任务 ──────────────────────────────────────


async def _periodic_cleanup(manager: SessionManager, interval: int) -> None:
    """后台协程：定期清理过期会话。"""
    while True:
        await asyncio.sleep(interval)
        try:
            cleaned = await manager.cleanup_expired()
            if cleaned:
                logger.info("定期清理：已清理 %d 个过期会话", cleaned)
        except Exception:
            logger.warning("定期清理异常", exc_info=True)


def _cleanup_interval_from_ttl(ttl_seconds: int) -> int:
    """根据 TTL 计算清理间隔，确保小 TTL 场景及时清理。"""
    return max(1, min(60, ttl_seconds // 2 if ttl_seconds > 1 else 1))


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


# ── Lifespan ──────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：初始化配置、注册 Skill、启动清理任务。"""
    global _session_manager, _tool_registry, _skillpack_loader, _skill_router, _config, _cleanup_task

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
    _session_manager = SessionManager(
        max_sessions=_config.max_sessions,
        ttl_seconds=_config.session_ttl_seconds,
        config=_config,
        registry=_tool_registry,
        skill_router=_skill_router,
    )

    # 启动定期清理后台任务
    cleanup_interval = _cleanup_interval_from_ttl(_config.session_ttl_seconds)
    _cleanup_task = asyncio.create_task(
        _periodic_cleanup(_session_manager, interval=cleanup_interval)
    )

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

    # 关闭清理任务
    if _cleanup_task is not None:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass

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
    allow_origins=list(load_cors_allow_origins()),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


# ── 全局异常处理 ──────────────────────────────────────────


@app.exception_handler(SessionNotFoundError)
async def _handle_session_not_found(
    request: Request, exc: SessionNotFoundError
) -> JSONResponse:
    """会话不存在 → 404。"""
    return JSONResponse(
        status_code=404,
        content={"error": str(exc)},
    )


@app.exception_handler(SessionLimitExceededError)
async def _handle_session_limit(
    request: Request, exc: SessionLimitExceededError
) -> JSONResponse:
    """会话数量超限 → 429。"""
    return JSONResponse(
        status_code=429,
        content={"error": str(exc)},
    )


@app.exception_handler(SessionBusyError)
async def _handle_session_busy(
    request: Request, exc: SessionBusyError
) -> JSONResponse:
    """会话正在处理中 → 409。"""
    return JSONResponse(
        status_code=409,
        content={"error": str(exc)},
    )


@app.exception_handler(Exception)
async def _handle_unexpected(
    request: Request, exc: Exception
) -> JSONResponse:
    """未预期异常 → 500，返回 error_id，不暴露堆栈。"""
    error_id = str(uuid.uuid4())
    logger.error(
        "未预期异常 [error_id=%s]: %s", error_id, exc, exc_info=True
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "服务内部错误，请联系管理员。",
            "error_id": error_id,
        },
    )


# ── 端点 ──────────────────────────────────────────────────


@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """对话接口：创建或复用会话，将消息传递给 AgentEngine。"""
    assert _session_manager is not None, "服务未初始化"

    session_id, engine = await _session_manager.acquire_for_chat(
        request.session_id
    )
    try:
        if request.skill_hints:
            reply = await engine.chat(
                request.message,
                skill_hints=request.skill_hints,
            )
        else:
            reply = await engine.chat(request.message)
    finally:
        await _session_manager.release_for_chat(session_id)

    normalized_reply = guard_public_reply(reply.strip())
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
    )


@app.post("/api/v1/chat/stream")
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

    async def _run_chat() -> str:
        """后台执行 engine.chat，完成后向队列发送结束信号。"""
        try:
            if request.skill_hints:
                reply = await engine.chat(
                    request.message,
                    on_event=_on_event,
                    skill_hints=request.skill_hints,
                )
            else:
                reply = await engine.chat(
                    request.message,
                    on_event=_on_event,
                )
            return reply
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
            reply = chat_task.result()
            normalized_reply = guard_public_reply((reply or "").strip())
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
        EventType.TOOL_CALL_START,
        EventType.TOOL_CALL_END,
        EventType.ITERATION_START,
    }:
        return None

    event_map = {
        EventType.THINKING: "thinking",
        EventType.TOOL_CALL_START: "tool_call_start",
        EventType.TOOL_CALL_END: "tool_call_end",
        EventType.ITERATION_START: "iteration_start",
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
            "arguments": event.arguments,
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
    else:
        data = event.to_dict()

    return _sse_format(sse_type, data)


@app.delete("/api/v1/sessions/{session_id}")
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

    return {
        "status": "ok",
        "version": excelmanus.__version__,
        "model": _config.model if _config is not None else "",
        "tools": tools,
        "skillpacks": skillpacks,
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
