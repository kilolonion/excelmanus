"""API 服务模块：基于 FastAPI 的 REST API 服务。

端点：
- POST   /api/v1/chat                  对话接口
- DELETE /api/v1/sessions/{session_id}  删除会话
- GET    /api/v1/health                 健康检查
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

import excelmanus
from excelmanus.config import ExcelManusConfig, load_config
from excelmanus.logger import get_logger, setup_logging
from excelmanus.session import (
    SessionBusyError,
    SessionLimitExceededError,
    SessionManager,
    SessionNotFoundError,
)
from excelmanus.skills import SkillRegistry

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


class ChatResponse(BaseModel):
    """对话响应体。"""

    session_id: str
    reply: str


class ErrorResponse(BaseModel):
    """错误响应体（不暴露内部堆栈）。"""

    error: str
    error_id: str


# ── 全局状态（由 lifespan 初始化） ────────────────────────

_session_manager: SessionManager | None = None
_registry: SkillRegistry | None = None
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


# ── Lifespan ──────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：初始化配置、注册 Skill、启动清理任务。"""
    global _session_manager, _registry, _config, _cleanup_task

    # 加载配置
    _config = load_config()
    setup_logging(_config.log_level)

    # 初始化 Skill 注册中心并自动发现
    _registry = SkillRegistry()
    _registry.auto_discover()

    # 初始化会话管理器
    _session_manager = SessionManager(
        max_sessions=_config.max_sessions,
        ttl_seconds=_config.session_ttl_seconds,
        config=_config,
        registry=_registry,
    )

    # 启动定期清理后台任务
    cleanup_interval = _cleanup_interval_from_ttl(_config.session_ttl_seconds)
    _cleanup_task = asyncio.create_task(
        _periodic_cleanup(_session_manager, interval=cleanup_interval)
    )

    skill_names = [name for name in _registry._skills]
    logger.info(
        "API 服务启动完成，已加载 %d 个 Skill: %s",
        len(skill_names),
        ", ".join(skill_names) if skill_names else "无",
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
        reply = await engine.chat(request.message)
    finally:
        await _session_manager.release_for_chat(session_id)

    normalized_reply = reply.strip()
    if not normalized_reply:
        logger.warning("会话 %s 返回空回复，已替换为默认文案", session_id)
        normalized_reply = "未生成有效回复，请重试。"
    return ChatResponse(session_id=session_id, reply=normalized_reply)


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
    """健康检查：返回版本号和已加载的 Skill 列表。"""
    skills: list[str] = []
    if _registry is not None:
        skills = list(_registry._skills.keys())

    return {
        "status": "ok",
        "version": excelmanus.__version__,
        "skills": skills,
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
