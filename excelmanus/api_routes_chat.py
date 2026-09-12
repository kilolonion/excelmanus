"""对话 / SSE / 回滚 / 引导 / 审批 API。

从 api.py 抽出的独立路由模块。运行时状态只从 api_app_state 读取，
禁止 ``from excelmanus.api import _config`` 反向导入。
由 api.py 在 create_app 中 include_router 注册。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated, Any, AsyncIterator, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from excelmanus import api_app_state as _app_state
from excelmanus.api_app_state import (
    error_json_response as _error_json_response,
    get_config,
    get_config_incomplete,
    get_session_manager,
    has_session_access as _has_session_access,
    resolve_workspace as _resolve_workspace,
)
from excelmanus.api_sse import (
    SessionStreamState as _SessionStreamState,
    inject_seq_into_sse as _inject_seq,
    sse_event_to_sse as _sse_event_to_sse_impl,
    sse_format as _sse_format,
)
from excelmanus.engine import ChatResult, ToolCallResult
from excelmanus.error_guidance import FailureGuidance, classify_failure
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.logger import get_logger
from excelmanus.mentions import MentionParser, MentionResolver
from excelmanus.mentions.parser import ResolvedMention
from excelmanus.output_guard import (
    guard_public_reply,
    sanitize_external_data,
    sanitize_external_text,
)
from excelmanus.session import (
    SessionBusyError,
    SessionManager,
    SessionNotFoundError,
)
from excelmanus.session_title import instant_session_title, title_from_messages

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine

logger = get_logger("api.chat")

router = APIRouter()


def _fire_and_forget(coro: Any, *, name: str = "bridge_notify") -> None:
    """安全地 fire-and-forget 一个协程。"""
    import inspect
    from excelmanus.engine_utils import fire_and_forget

    if not inspect.isawaitable(coro):
        return
    fire_and_forget(coro, name=name)


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
    chat_mode: Literal["write", "read", "plan"] = "write"
    present_as: Literal["native", "code"] | None = None
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
    # 自动生成的会话标题（仅首轮返回）
    title: str | None = None

class ErrorResponse(BaseModel):
    """错误响应体（不暴露内部堆栈）。"""

    error: str
    error_id: str


def _build_reply_sse(chat_result: Any, engine: Any) -> str:
    """构建 reply SSE 事件文本（chat_stream 与 chat_subscribe 共用）。"""
    normalized_reply = guard_public_reply((chat_result.reply or "").strip())
    route = engine.last_route_result
    return _sse_format("reply", {
        "content": normalized_reply,
        "skills_used": route.skills_used,
        "tool_scope": route.tool_scope,
        "route_mode": route.route_mode,
        "iterations": chat_result.iterations,
        "truncated": chat_result.truncated,
        "prompt_tokens": chat_result.prompt_tokens,
        "completion_tokens": chat_result.completion_tokens,
        "total_tokens": chat_result.total_tokens,
    })


def _public_excel_path(path: str) -> str:
    """将 Excel 路径规范化为前端可直接回传的形式。

    公开身份只认 CanonicalPath（方案 P1）。overlay / backups 映射到正本，
    映射不到则省略，不把 staging 路径当交付物。未知绝对路径脱敏，不泄露家目录。
    """
    raw = str(path or "").strip()
    if not raw:
        return ""

    from pathlib import Path

    from excelmanus.workspace.identity import (
        is_overlay_leftover,
        is_reserved_relative,
        public_identity,
    )

    workspace = None
    if get_config() is not None:
        workspace = Path(get_config().workspace_root).resolve()

    ident = public_identity(raw, workspace)
    if ident:
        return ident

    probe = raw.removeprefix("<path>/").strip() if raw.startswith("<path>/") else raw
    if is_overlay_leftover(probe) or is_reserved_relative(probe.replace("\\", "/")):
        return ""

    if raw.startswith("<path>/"):
        basename = raw.removeprefix("<path>/").strip()
        return f"./{basename}" if basename else ""

    if workspace is not None:
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                rel = candidate.resolve().relative_to(workspace)
                ident = public_identity(rel.as_posix(), workspace)
                return ident
            except Exception:
                return sanitize_external_text(raw, max_len=500)

    normalized = raw.replace("\\", "/")
    if normalized.startswith("./"):
        return public_identity(normalized, workspace) or ""
    if normalized.startswith("/"):
        return sanitize_external_text(normalized, max_len=500)
    return public_identity(normalized, workspace) or ""




def _persist_excel_event(session_id: str, event: ToolCallEvent) -> None:
    """将 EXCEL_DIFF / EXCEL_PREVIEW / FILES_CHANGED 事件持久化到 SQLite。"""
    if get_session_manager() is None or get_session_manager().chat_history is None:
        return
    ch = get_session_manager().chat_history
    try:
        if event.event_type == EventType.EXCEL_DIFF:
            pub_path = _public_excel_path(event.excel_file_path)
            ch.save_excel_diff(
                session_id=session_id,
                tool_call_id=event.tool_call_id or "",
                file_path=pub_path,
                sheet=event.excel_sheet or "",
                affected_range=event.excel_affected_range or "",
                changes=list(event.excel_changes or [])[:200],
            )
            ch.save_affected_file(session_id, pub_path)
        elif event.event_type == EventType.EXCEL_PREVIEW:
            pub_path = _public_excel_path(event.excel_file_path)
            ch.save_excel_preview(
                session_id=session_id,
                tool_call_id=event.tool_call_id or "",
                file_path=pub_path,
                sheet=event.excel_sheet or "",
                columns=list(event.excel_columns or [])[:100],
                rows=list(event.excel_rows or [])[:50],
                total_rows=event.excel_total_rows or 0,
                truncated=bool(event.excel_truncated),
                cell_styles=list(event.excel_cell_styles or [])[:51],
            )
            ch.save_affected_file(session_id, pub_path)
        elif event.event_type == EventType.FILES_CHANGED:
            for f in (event.changed_files or [])[:50]:
                pub = _public_excel_path(f)
                if pub:
                    ch.save_affected_file(session_id, pub)
    except Exception:
        logger.debug("持久化 Excel 事件失败", exc_info=True)


def _serialize_images(images: list[ImageAttachment]) -> list[dict[str, str]]:
    """将请求中的图片附件标准化为引擎可消费的字典列表。"""
    return [img.model_dump() for img in images]


def _public_tool_calls(tool_calls: list[ToolCallResult]) -> list[dict]:
    """将工具调用明细脱敏后返回。"""
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


        })
    return rows

def _extract_provider_from_base_url(base_url: str | None) -> str:
    """从 base_url 提取 provider 名（如 openai/deepseek/anthropic）。"""
    if not base_url:
        return ""
    try:
        from urllib.parse import urlparse
        hostname = urlparse(base_url).hostname or ""
        parts = hostname.split(".")
        if len(parts) >= 2:
            return parts[-2]
        return parts[0] if parts else ""
    except Exception:
        return ""


def _failure_guidance_sse(guidance: FailureGuidance) -> str:
    """将 FailureGuidance 格式化为单个 failure_guidance SSE 文本。"""
    return _sse_format("failure_guidance", guidance.to_dict())


def _failure_guidance_text(guidance: FailureGuidance) -> str:
    """将结构化失败引导转换为可持久化的 assistant 文本。"""
    lines: list[str] = []
    if guidance.title:
        lines.append(f"⚠️ {guidance.title}")
    if guidance.message:
        lines.append(guidance.message)
    if guidance.diagnostic_id:
        lines.append(f"诊断 ID: {guidance.diagnostic_id}")
    return "\n".join(lines).strip() or "服务处理出现异常，请稍后重试。"


def _persist_failure_guidance_message(
    *,
    session_id: str | None,
    engine: Any | None,
    guidance: FailureGuidance,
) -> None:
    """将失败引导以 assistant 消息持久化，防止前端刷新后只剩用户消息。"""
    if not session_id or engine is None or get_session_manager() is None:
        return
    try:
        raw_messages = getattr(engine, "raw_messages", None)
        if (
            isinstance(raw_messages, list)
            and raw_messages
            and isinstance(raw_messages[-1], dict)
            and raw_messages[-1].get("role") == "assistant"
        ):
            return
        engine.memory.add_assistant_message(_failure_guidance_text(guidance))
        get_session_manager().flush_messages_sync(session_id)
    except Exception:
        logger.debug("会话 %s 失败引导消息持久化失败", session_id, exc_info=True)

# OpenAPI 错误响应 schema（供路由 responses 参数引用）
_error_responses: dict = {
    403: {"model": ErrorResponse, "description": "无权限执行"},
    404: {"model": ErrorResponse, "description": "会话不存在"},
    409: {"model": ErrorResponse, "description": "会话正在处理中"},
    422: {"model": ErrorResponse, "description": "请求参数错误"},
    429: {"model": ErrorResponse, "description": "会话数量超限"},
    500: {"model": ErrorResponse, "description": "服务内部错误"},
}


def _is_save_command(message: str) -> bool:
    """判断消息是否为 /save 命令（含 /save 或 /save <路径>）。

    支持带文件上传前缀的消息格式（如 "[已上传文件: x]\\n\\n/save"）。
    """
    stripped = message.strip()
    # 若有 \\n\\n，取最后一段作为用户输入（网页端上传文件时会在前加前缀）
    if "\n\n" in stripped:
        cmd_part = stripped.split("\n\n")[-1].strip()
    else:
        cmd_part = stripped
    if not cmd_part.lower().startswith("/save"):
        return False
    parts = cmd_part.split(None, 1)
    return parts[0].lower() == "/save"


async def _handle_save_command(
    session_manager: SessionManager,
    session_id: str,
    engine: Any,
    message: str,
) -> str:
    """执行 /save：将对话导出为 JSON 文件，返回保存路径作为回复。"""
    path: str | None = None
    stripped = message.strip()
    cmd_part = stripped.split("\n\n")[-1].strip() if "\n\n" in stripped else stripped
    parts = cmd_part.split(None, 1)
    if len(parts) > 1:
        path = parts[1].strip() or None

    engine.memory.add_user_message(message)
    if hasattr(engine, "state") and engine.state is not None:
        engine._state.increment_turn()

    saved_path = engine.save_conversation(path)
    if saved_path:
        reply = f"对话已保存至：`{saved_path}`"
    else:
        reply = "当前对话为空，未生成文件。"

    engine.memory.add_assistant_message(reply)
    await session_manager.release_for_chat(session_id)
    return reply


async def _resolve_mentions(
    message: str,
    engine: Any,
) -> tuple[str, list[ResolvedMention] | None]:
    """解析用户消息中的 @ 提及标记，返回 (display_text, mention_contexts)。

    display_text 将 ``@file:name`` 替换为 ``name``，确保 LLM 看到的文本不含 @ 前缀。
    """
    try:
        parse_result = MentionParser.parse(message)
        if not parse_result.mentions:
            return message, None

        from excelmanus.security.guard import FileAccessGuard

        guard = FileAccessGuard(engine.get_config().workspace_root)
        skill_loader = getattr(engine, "_skill_loader", None)
        if skill_loader is None:
            _router = getattr(engine, "_skill_router", None)
            if _router is not None:
                skill_loader = getattr(_router, "_loader", None)
        mcp_manager = getattr(engine, "_mcp_manager", None)
        resolver = MentionResolver(
            workspace_root=engine.get_config().workspace_root,
            guard=guard,
            skill_loader=skill_loader,
            mcp_manager=mcp_manager,
        )
        mention_contexts = await resolver.resolve(list(parse_result.mentions))
        return parse_result.display_text, mention_contexts
    except Exception:
        logger.debug("API 层 @ 提及解析失败，回退到原始消息", exc_info=True)
        return message, None


@router.post("/api/v1/chat", response_model=ChatResponse, responses=_error_responses)
async def chat(request: ChatRequest, raw_request: Request) -> ChatResponse:
    """对话接口：创建或复用会话，将消息传递给 AgentEngine。"""
    if get_session_manager() is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    if get_config_incomplete():
        raise HTTPException(
            status_code=503,
            detail="模型尚未配置，请先在设置页面或 .env 文件中配置 API Key、Base URL 和 Model。",
        )

    try:
        session_id, engine = await get_session_manager().acquire_for_chat(
            request.session_id,
        )
    except SessionBusyError:
        queued = await get_session_manager().enqueue_user_interrupt(
            request.session_id or "",
            request.message,
        )
        if queued:
            return ChatResponse(
                session_id=request.session_id or "",
                reply="已加入下一轮，当前回合结束后再生效。",
                skills_used=[],
                route_mode="queued_interrupt",
            )
        raise

    if _is_save_command(request.message):
        try:
            reply_text = await _handle_save_command(
                get_session_manager(), session_id, engine, request.message
            )
        finally:
            await get_session_manager().release_for_chat(session_id)
        return ChatResponse(
            session_id=session_id,
            reply=guard_public_reply(reply_text),
            skills_used=[],
            route_mode="control_command",
        )

    try:
        display_text, mention_contexts = await _resolve_mentions(
            request.message, engine,
        )

        def _on_event_sync(event: ToolCallEvent) -> None:
            _persist_excel_event(session_id, event)

        chat_result = await engine.followup(
                display_text,
                on_event=_on_event_sync,
                mention_contexts=mention_contexts,
                images=_serialize_images(request.images),
                chat_mode=request.chat_mode,
                present_as=request.present_as,
            )
    except Exception as _chat_exc:
        # 非流式路径：池健康信号更新 + 即时评估
        _pool_aid_err_json = getattr(engine, "_pool_account_id", None)
        if _pool_aid_err_json:
            try:
                from excelmanus.pool.signals import update_pool_health_from_error
                from excelmanus.error_guidance import _extract_status_code
                _pool_svc_err_json = getattr(raw_request.app.state, "pool_service", None)
                if _pool_svc_err_json is not None:
                    update_pool_health_from_error(
                        pool_service=_pool_svc_err_json,
                        pool_account_id=_pool_aid_err_json,
                        status_code=_extract_status_code(_chat_exc),
                        error_message=str(_chat_exc)[:500],
                        session_id=session_id or "",
                        user_id="",
                        model=getattr(engine, "_active_model", ""),
                        breaker=getattr(raw_request.app.state, "pool_breaker_manager", None),
                    )
                # 即时触发自动轮换评估
                _auto_rotate_err_json = getattr(raw_request.app.state, "pool_auto_rotate_service", None)
                if _auto_rotate_err_json is not None:
                    _fire_and_forget(
                        _auto_rotate_err_json.evaluate_on_error("openai-codex", "*"),
                        name="pool_auto_rotate_eval",
                    )
            except Exception:
                logger.debug("非流式号池健康信号更新失败", exc_info=True)
        raise
    finally:
        await get_session_manager().release_for_chat(session_id)

    normalized_reply = guard_public_reply(chat_result.reply.strip())

    # 号池台账写入（非流式路径）
    _pool_aid_json = getattr(engine, "_pool_account_id", None)
    if _pool_aid_json and chat_result.total_tokens > 0:
        try:
            _pool_svc_json = getattr(raw_request.app.state, "pool_service", None)
            if _pool_svc_json is not None:
                _pool_svc_json.log_usage(
                    pool_account_id=_pool_aid_json,
                    session_id=session_id or "",
                    user_id="",
                    model=getattr(engine, "_active_model", ""),
                    prompt_tokens=chat_result.prompt_tokens,
                    completion_tokens=chat_result.completion_tokens,
                    total_tokens=chat_result.total_tokens,
                    outcome="success",
                )
                from excelmanus.pool.signals import update_pool_health_on_success
                update_pool_health_on_success(
                    _pool_svc_json, _pool_aid_json,
                    breaker=getattr(raw_request.app.state, "pool_breaker_manager", None),
                )
        except Exception:
            logger.debug("非流式号池台账写入失败", exc_info=True)
    # 记录已认证用户 token 使用量（非流式路径）
    # ── 自动标题（仅首轮）：截取用户消息 + 后台 AI 生成 ──
    generated_title: str | None = None
    if engine.session_turn == 1:
        generated_title = _truncate_user_message_as_title(display_text)
        if generated_title:
            _ch = get_session_manager().chat_history if get_session_manager() else None
            if _ch is not None:
                try:
                    _ch.update_session(session_id, title=generated_title, title_source="truncated")
                except Exception:
                    logger.debug("截取标题写入失败", exc_info=True)
        _fire_and_forget(_generate_session_title_background(
            session_id=session_id,
            user_message=display_text,
            assistant_reply=normalized_reply,
        ), name="session_title")
    route = engine.last_route_result
    return ChatResponse(
        session_id=session_id,
        reply=normalized_reply,
        skills_used=route.skills_used,
        tool_scope=route.tool_scope,
        route_mode=route.route_mode,
        iterations=chat_result.iterations,
        truncated=chat_result.truncated,
        tool_calls=_public_tool_calls(chat_result.tool_calls),
        prompt_tokens=chat_result.prompt_tokens,
        completion_tokens=chat_result.completion_tokens,
        total_tokens=chat_result.total_tokens,
        title=generated_title,
    )


@router.post("/api/v1/chat/stream", responses=_error_responses)
async def chat_stream(request: ChatRequest, raw_request: Request) -> StreamingResponse:
    """SSE 流式对话接口：实时推送思考过程、工具调用、最终回复。

    延迟初始化架构：SSE 连接立即建立并推送进度事件，
    会话获取、@引用解析等阻塞操作在流内部执行，
    实现毫秒级首次视觉反馈。
    """
    if get_session_manager() is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    if get_config_incomplete():
        raise HTTPException(
            status_code=503,
            detail="模型尚未配置，请先在设置页面或 .env 文件中配置 API Key、Base URL 和 Model。",
        )
    if not (request.message or "").strip() and not request.images:
        raise HTTPException(status_code=400, detail="消息内容不能为空。")

    async def _event_generator() -> AsyncIterator[str]:
        """SSE 事件生成器：所有阻塞操作在首个 yield 之后执行。

        延迟初始化架构：SSE 连接立即建立并推送进度事件，
        会话获取、@引用解析等阻塞操作在流内部执行，
        实现毫秒级首次视觉反馈。
        """
        # ── 所有可能在 finally 中引用的变量预初始化 ──
        session_id: str | None = None
        engine: AgentEngine | None = None
        acquired = False
        stream_state: _SessionStreamState | None = None
        chat_task: asyncio.Task[Any] | None = None
        queue_get_task: asyncio.Task[Any] | None = None

        async def _cancel_task(task: asyncio.Task[Any] | None) -> None:
            if task is None or task.done():
                return
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        _last_pipeline_stage = "initializing"

        try:
            # ── 立即推送进度，让前端在任何阻塞操作前就有视觉反馈 ──
            yield _sse_format("pipeline_progress", {
                "stage": "initializing",
                "message": "正在初始化...",
            })

            # ── 延迟初始化：会话获取（可能创建 Engine + MCP sync） ──
            try:
                session_id, engine = await get_session_manager().acquire_for_chat(
                    request.session_id,
                )
                acquired = True
            except SessionBusyError:
                queued = await get_session_manager().enqueue_user_interrupt(
                    request.session_id or "",
                    request.message,
                )
                if queued:
                    yield _sse_format("pipeline_progress", {
                        "stage": "queued",
                        "message": "已加入下一轮，当前回合结束后再生效。",
                    })
                    yield _sse_format("done", {"queued": True})
                    return
                raise
            except Exception as exc:
                _sid = request.session_id or "unknown"
                logger.error(
                    "会话获取失败 [session=%s]: %s",
                    _sid, exc, exc_info=True,
                )
                _guidance = classify_failure(exc, stage="initializing")
                yield _failure_guidance_sse(_guidance)
                yield _sse_format("done", {})
                return

            assert session_id is not None and engine is not None
            yield _sse_format("session_init", {"session_id": session_id})

            # ── 保存命令快速路径 ──
            if _is_save_command(request.message):
                try:
                    reply_text = await _handle_save_command(
                        get_session_manager(), session_id, engine, request.message
                    )
                    yield _sse_format("reply", {
                        "content": guard_public_reply(reply_text),
                        "skills_used": [],
                        "tool_scope": [],
                        "route_mode": "control_command",
                        "iterations": 0,
                        "truncated": False,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    })
                except Exception as exc:
                    logger.error("SSE /save 流异常: %s", exc, exc_info=True)
                    _save_provider = _extract_provider_from_base_url(
                        getattr(engine, "active_base_url", None) or (getattr(get_config(), "base_url", None) if get_config() else None)
                    )
                    _save_model = getattr(engine, "current_model", None) or (getattr(get_config(), "model", "") if get_config() else "")
                    _save_guidance = classify_failure(exc, stage="save_command", provider=_save_provider, model=_save_model)
                    yield _failure_guidance_sse(_save_guidance)
                else:
                    yield _sse_format("done", {})
                return

            # ── @引用解析 + 图片日志 ──
            display_text, mention_contexts = await _resolve_mentions(
                request.message, engine,
            )

            if request.images:
                logger.info(
                    "chat_stream 收到 %d 张图片附件 (media_types=%s, data_lens=%s)",
                    len(request.images),
                    [img.media_type for img in request.images],
                    [len(img.data) for img in request.images],
                )

            # ── 设置事件流管道 ──
            stream_state = _SessionStreamState()
            _app_state._session_stream_states[session_id] = stream_state
            event_queue = stream_state.attach()

            yield _sse_format("stream_init", {
                "stream_id": stream_state.stream_id,
                "seq": 1,
            })

            _sse_event_count = 0

            _flush_scheduled = False

            def _schedule_flush() -> None:
                """将同步 flush 调度为异步任务，避免在事件回调中阻塞事件循环。

                同一轮 tool batch 中多个 TOOL_CALL_END 事件只触发一次 flush：
                首个 TOOL_CALL_END 设置标记并调度，后续的跳过。
                标记在 flush 完成后重置，为下一轮 batch 做准备。
                """
                nonlocal _flush_scheduled
                if _flush_scheduled:
                    return
                _flush_scheduled = True

                async def _do_flush() -> None:
                    nonlocal _flush_scheduled
                    try:
                        if get_session_manager() is not None:
                            await asyncio.to_thread(
                                get_session_manager().flush_messages_sync, session_id,
                            )
                    except Exception:
                        logger.debug("异步消息持久化失败", exc_info=True)
                    finally:
                        _flush_scheduled = False

                _fire_and_forget(_do_flush(), name="flush_messages")

            def _on_event(event: ToolCallEvent) -> None:
                """引擎事件回调：通过 stream_state 投递，同时持久化 Excel 事件。

                消息持久化通过 asyncio.to_thread 异步执行，不阻塞事件循环。
                """
                nonlocal _sse_event_count
                _sse_event_count += 1
                has_subscriber = stream_state.subscriber_queue is not None
                logger.debug(
                    "SSE 事件投递 #%d [%s] subscriber=%s qsize=%s",
                    _sse_event_count,
                    event.event_type.value if hasattr(event.event_type, 'value') else event.event_type,
                    has_subscriber,
                    stream_state.subscriber_queue.qsize() if has_subscriber else "N/A",
                )
                stream_state.deliver(event)
                _persist_excel_event(session_id, event)
                if (
                    event.event_type == EventType.TOOL_CALL_END
                    and get_session_manager() is not None
                ):
                    _schedule_flush()

            async def _run_chat_inner() -> ChatResult:
                """后台执行 engine.chat，完成后释放会话锁。"""
                try:
                    result = await engine.followup(
                        display_text,
                        on_event=_on_event,
                        mention_contexts=mention_contexts,
                        images=_serialize_images(request.images),
                        chat_mode=request.chat_mode,
                        present_as=request.present_as,
                    )
                    return result
                finally:
                    await get_session_manager().release_for_chat(session_id)
                    nonlocal acquired
                    acquired = False

            # ── 启动 chat 任务 ──
            chat_task = asyncio.create_task(_run_chat_inner())
            _app_state._active_chat_tasks[session_id] = chat_task

            def _cleanup_active_chat_task(done_task: asyncio.Task[Any]) -> None:
                """后台 chat 任务完成后清理活跃任务映射与流状态。"""
                if _app_state._active_chat_tasks.get(session_id) is done_task:
                    _app_state._active_chat_tasks.pop(session_id, None)
                try:
                    done_task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.warning(
                        "会话 %s 的后台聊天任务异常结束",
                        session_id,
                        exc_info=True,
                    )

            chat_task.add_done_callback(_cleanup_active_chat_task)
            queue_get_task = asyncio.create_task(event_queue.get())

            # ── 事件消费循环 ──
            while True:
                assert queue_get_task is not None
                done, _ = await asyncio.wait(
                    [queue_get_task, chat_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if queue_get_task in done:
                    seq_item = queue_get_task.result()
                    if seq_item is not None:
                        _seq, event = seq_item
                        if event.event_type == EventType.PIPELINE_PROGRESS and event.pipeline_stage:
                            _last_pipeline_stage = event.pipeline_stage
                        sse = _sse_event_to_sse(event)
                        if sse is not None:
                            yield _inject_seq(sse, _seq, stream_state.stream_id)
                    if chat_task.done():
                        queue_get_task = None
                    else:
                        queue_get_task = asyncio.create_task(event_queue.get())

                if chat_task in done:
                    # 排空队列中剩余事件
                    while True:
                        try:
                            seq_item = event_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if seq_item is not None:
                            _seq, event = seq_item
                            if event.event_type == EventType.PIPELINE_PROGRESS and event.pipeline_stage:
                                _last_pipeline_stage = event.pipeline_stage
                            sse = _sse_event_to_sse(event)
                            if sse is not None:
                                yield _inject_seq(sse, _seq, stream_state.stream_id)
                    break

            # chat 任务完成：获取结果
            chat_result = chat_task.result()
            normalized_reply = guard_public_reply((chat_result.reply or "").strip())
            yield _build_reply_sse(chat_result, engine)

            # 记录已认证用户的 token 使用量
            # 号池台账写入：pool_oauth 来源时记录用量
            _pool_aid = getattr(engine, "_pool_account_id", None)
            if _pool_aid and chat_result.total_tokens > 0:
                try:
                    _pool_svc = getattr(raw_request.app.state, "pool_service", None)
                    if _pool_svc is not None:
                        _pool_svc.log_usage(
                            pool_account_id=_pool_aid,
                            session_id=session_id or "",
                            user_id="",
                            model=getattr(engine, "_active_model", ""),
                            prompt_tokens=chat_result.prompt_tokens,
                            completion_tokens=chat_result.completion_tokens,
                            total_tokens=chat_result.total_tokens,
                            outcome="success",
                        )
                        from excelmanus.pool.signals import update_pool_health_on_success
                        update_pool_health_on_success(
                            _pool_svc, _pool_aid,
                            breaker=getattr(raw_request.app.state, "pool_breaker_manager", None),
                        )
                except Exception:
                    logger.debug("号池台账写入失败", exc_info=True)
            # ── 自动标题（仅首轮）：立即截取用户消息，后台异步 AI 生成 ──
            if engine.session_turn == 1:
                _instant_title = _truncate_user_message_as_title(display_text)
                if _instant_title:
                    yield _sse_format("session_title", {
                        "session_id": session_id,
                        "title": _instant_title,
                    })
                    # 同步写入 DB，确保 poll 能立即拿到
                    _ch = get_session_manager().chat_history if get_session_manager() else None
                    if _ch is not None:
                        try:
                            _ch.update_session(session_id, title=_instant_title, title_source="truncated")
                        except Exception:
                            logger.debug("截取标题写入失败", exc_info=True)
                # fire-and-forget：后台 AI 生成更好的标题，前端通过 SessionSync poll 获取
                _fire_and_forget(
                    _generate_session_title_background(
                        session_id=session_id,
                        user_message=display_text,
                        assistant_reply=normalized_reply,
                    ),
                    name="generate_session_title",
                )
            yield _sse_format("done", {})

        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断开（如页面刷新）时允许 chat 在后台继续。
            if stream_state is not None:
                stream_state.detach()
            if session_id is not None:
                logger.info("会话 %s 的流式连接已断开，后台任务继续执行", session_id)
        except Exception as exc:
            logger.error(
                "SSE 流异常: %s", exc, exc_info=True
            )
            _provider = _extract_provider_from_base_url(
                getattr(engine, "active_base_url", None) or (getattr(get_config(), "base_url", None) if get_config() else None)
            )
            _model_name = getattr(engine, "current_model", None) or (getattr(get_config(), "model", "") if get_config() else "")
            _top_guidance = classify_failure(
                exc,
                stage=_last_pipeline_stage,
                provider=_provider,
                model=_model_name,
            )
            _persist_failure_guidance_message(
                session_id=session_id,
                engine=engine,
                guidance=_top_guidance,
            )
            # 号池健康信号：从错误中更新池账号状态
            _pool_aid_err = getattr(engine, "_pool_account_id", None)
            if _pool_aid_err:
                try:
                    from excelmanus.pool.signals import update_pool_health_from_error
                    from excelmanus.error_guidance import _extract_status_code
                    _pool_svc_err = getattr(raw_request.app.state, "pool_service", None)
                    if _pool_svc_err is not None:
                        update_pool_health_from_error(
                            pool_service=_pool_svc_err,
                            pool_account_id=_pool_aid_err,
                            status_code=_extract_status_code(exc),
                            error_message=str(exc)[:500],
                            session_id=session_id or "",
                            user_id="",
                            model=_model_name or "",
                            breaker=getattr(raw_request.app.state, "pool_breaker_manager", None),
                        )
                    # 即时触发自动轮换评估
                    _auto_rotate_err = getattr(raw_request.app.state, "pool_auto_rotate_service", None)
                    if _auto_rotate_err is not None:
                        _fire_and_forget(
                            _auto_rotate_err.evaluate_on_error("openai-codex", "*"),
                            name="pool_auto_rotate_eval",
                        )
                except Exception:
                    logger.debug("号池健康信号更新失败", exc_info=True)
            yield _failure_guidance_sse(_top_guidance)
            yield _sse_format("done", {})
            # 确保 chat 任务被取消
            if chat_task is not None and not chat_task.done():
                chat_task.cancel()
                try:
                    await chat_task
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            if stream_state is not None:
                stream_state.detach()
            if chat_task is not None and chat_task.done():
                if _app_state._active_chat_tasks.get(session_id) is chat_task:
                    _app_state._active_chat_tasks.pop(session_id, None)
                _app_state._session_stream_states.pop(session_id, None)
            await _cancel_task(queue_get_task)
            # 安全网：确保 in_flight 锁被释放（正常路径已在 _run_chat_inner 中释放）
            if acquired and session_id is not None:
                try:
                    await get_session_manager().release_for_chat(session_id)
                except Exception:
                    logger.debug("安全网 release_for_chat 异常", exc_info=True)

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


class GuideRequest(BaseModel):
    """引导消息请求体：向运行中的会话注入追加指令。"""

    model_config = ConfigDict(extra="forbid")

    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)
    ]


class AnswerQuestionRequest(BaseModel):
    """回答问题请求体（阻塞式 ask_user）。"""

    model_config = ConfigDict(extra="forbid")

    question_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    answer: str


class ApproveRequest(BaseModel):
    """审批决策请求体（阻塞式审批）。"""

    model_config = ConfigDict(extra="forbid")

    approval_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    decision: Literal["accept", "reject", "fullaccess"]


class RollbackRequest(BaseModel):
    """对话回退请求体。"""

    model_config = ConfigDict(extra="forbid")

    session_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    turn_index: int = Field(..., ge=0, description="目标用户轮次索引（0-indexed）")
    new_message: str | None = Field(default=None, description="替换该轮用户消息内容（可选）")
    resend_mode: bool = Field(default=False, description="重发模式：移除目标用户消息，调用方随后通过 /chat/stream 重新发送")


class RollbackPreviewRequest(BaseModel):
    """回滚预览请求体。"""
    model_config = ConfigDict(extra="forbid")
    session_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    turn_index: int = Field(..., ge=0, description="目标用户轮次索引（0-indexed）")


@router.post("/api/v1/chat/rollback/preview")
async def chat_rollback_preview(
    request: RollbackPreviewRequest, raw_request: Request,
) -> JSONResponse:
    """预览回滚到指定用户轮次后会影响的文件变更（不实际执行）。"""
    if get_session_manager() is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    engine = get_session_manager().get_engine(request.session_id)
    if engine is None:
        raise HTTPException(status_code=404, detail=f"会话 '{request.session_id}' 不存在或未加载。")
    try:
        preview = engine.rollback_preview(request.turn_index)
    except IndexError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    return JSONResponse(status_code=200, content=preview)


@router.post("/api/v1/chat/rollback")
async def chat_rollback(request: RollbackRequest, raw_request: Request) -> JSONResponse:
    """回退对话到指定用户轮次。不改磁盘文件。"""
    if get_session_manager() is None:
        raise HTTPException(status_code=503, detail="服务未初始化")

    try:
        result = await get_session_manager().rollback_session(
            request.session_id,
            request.turn_index,
            new_message=request.new_message,
            resend_mode=request.resend_mode,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SessionBusyError:
        return JSONResponse(status_code=409, content={"detail": "会话正在处理中，请等待完成后再回退。"})
    except IndexError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "removed_messages": result["removed_messages"],
            "turn_index": result["turn_index"],
        },
    )


@router.get("/api/v1/chat/turns")
async def chat_turns(session_id: str, request: Request) -> JSONResponse:
    """列出指定会话的用户轮次摘要。"""
    if get_session_manager() is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    engine = get_session_manager().get_engine(session_id)
    if engine is None:
        return JSONResponse(status_code=404, content={"detail": f"会话 '{session_id}' 不存在或未加载。"})

    turns = engine.list_user_turns()
    return JSONResponse(
        status_code=200,
        content={"turns": turns},
    )


class _SubscribeRequest(BaseModel):
    """SSE 重连请求体。"""

    model_config = ConfigDict(extra="forbid")

    session_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    stream_id: str | None = Field(
        default=None,
        description="目标流标识，用于校验流身份一致性。",
    )
    after_seq: int = Field(
        default=0,
        ge=0,
        description="仅补发 seq > after_seq 的缓冲事件。0 表示全量重放。",
    )
    skip_replay: bool = Field(
        default=False,
        description="跳过缓冲区重放，仅保留 thinking 类事件。",
    )


@router.post("/api/v1/chat/subscribe", responses=_error_responses)
async def chat_subscribe(request: _SubscribeRequest, raw_request: Request) -> StreamingResponse:
    """SSE 重连端点：重放缓冲事件并接续实时流。"""
    session_id = request.session_id
    skip_replay = request.skip_replay

    # skip_replay 时仅保留的事件类型
    _REPLAY_KEEP_TYPES = {
        EventType.THINKING,
        EventType.THINKING_DELTA,
        EventType.ITERATION_START,
        EventType.RETRACT_THINKING,
    }

    if not await _has_session_access(session_id, raw_request):
        return _error_json_response(404, f"会话 '{session_id}' 不存在。")  # type: ignore[return-value]

    chat_task = _app_state._active_chat_tasks.get(session_id)
    stream_state = _app_state._session_stream_states.get(session_id)
    after_seq = request.after_seq

    def _streaming_response(gen: AsyncIterator[str]) -> StreamingResponse:
        return StreamingResponse(
            gen,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── 无活跃流状态：会话已完成或不存在 ──
    if stream_state is None:
        async def _done_stream() -> AsyncIterator[str]:
            yield _sse_format("session_init", {"session_id": session_id})
            yield _sse_format("subscribe_resume", {
                "status": "completed",
                "buffered_count": 0,
            })
            yield _sse_format("done", {})

        _app_state._active_chat_tasks.pop(session_id, None)
        return _streaming_response(_done_stream())

    _sid = stream_state.stream_id

    # ── 检测 gap：after_seq 精确补发 + 缓冲溢出检测 ──
    # 当事件曾被丢弃且缓冲中最早 seq 大于客户端期望的下一个 seq 时，
    # 说明存在不可恢复的事件缺口，需要通知前端走快照回源。
    _has_gap = False
    if stream_state.has_dropped:
        first_buf = stream_state.first_buffered_seq
        if first_buf is not None and first_buf > after_seq + 1:
            _has_gap = True
        elif first_buf is None:
            # 缓冲区为空但有丢弃记录（零容量缓冲或全部溢出）
            _has_gap = True

    # ── chat 任务已完成：重放缓冲后结束 ──
    if chat_task is None or chat_task.done():
        replay_items = stream_state.drain_buffer(after_seq=after_seq)

        async def _completed_stream() -> AsyncIterator[str]:
            try:
                yield _sse_format("session_init", {"session_id": session_id})
                if _has_gap:
                    yield _sse_format("resume_failed", {
                        "reason": "buffer_overflow",
                        "stream_id": _sid,
                        "after_seq": after_seq,
                        "available_from_seq": replay_items[0][0] if replay_items else None,
                    })
                    yield _sse_format("done", {})
                    return
                yield _sse_format("subscribe_resume", {
                    "status": "completed",
                    "stream_id": _sid,
                    "buffered_count": len(replay_items),
                })
                for seq, event in replay_items:
                    if skip_replay and event.event_type not in _REPLAY_KEEP_TYPES:
                        continue
                    sse = _sse_event_to_sse(event)
                    if sse is not None:
                        yield _inject_seq(sse, seq, _sid)
                yield _sse_format("done", {})
            finally:
                _app_state._active_chat_tasks.pop(session_id, None)
                _app_state._session_stream_states.pop(session_id, None)

        return _streaming_response(_completed_stream())

    # ── chat 任务仍在运行：重放缓冲 + 接续实时流 ──
    event_queue = stream_state.attach()
    replay_items = stream_state.drain_buffer(after_seq=after_seq)

    async def _subscribe_generator() -> AsyncIterator[str]:
        queue_get_task: asyncio.Task[Any] | None = asyncio.create_task(
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
            yield _sse_format("session_init", {"session_id": session_id})

            # gap 检测：缓冲溢出导致事件不连续
            if _has_gap:
                yield _sse_format("resume_failed", {
                    "reason": "buffer_overflow",
                    "stream_id": _sid,
                    "after_seq": after_seq,
                    "available_from_seq": replay_items[0][0] if replay_items else None,
                })
                yield _sse_format("done", {})
                return

            yield _sse_format("subscribe_resume", {
                "status": "reconnected",
                "stream_id": _sid,
                "buffered_count": len(replay_items),
            })

            for seq, event in replay_items:
                if skip_replay and event.event_type not in _REPLAY_KEEP_TYPES:
                    continue
                sse = _sse_event_to_sse(event)
                if sse is not None:
                    yield _inject_seq(sse, seq, _sid)

            while True:
                assert queue_get_task is not None
                wait_set: set[asyncio.Task[Any]] = {queue_get_task}
                if not chat_task.done():
                    wait_set.add(chat_task)
                done_set, _ = await asyncio.wait(
                    wait_set,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if queue_get_task in done_set:
                    seq_item = queue_get_task.result()
                    if seq_item is not None:
                        _seq, event = seq_item
                        sse = _sse_event_to_sse(event)
                        if sse is not None:
                            yield _inject_seq(sse, _seq, _sid)
                    if chat_task.done():
                        queue_get_task = None
                    else:
                        queue_get_task = asyncio.create_task(event_queue.get())

                if chat_task.done() and (queue_get_task is None or queue_get_task not in done_set):
                    while True:
                        try:
                            seq_item = event_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if seq_item is not None:
                            _seq, event = seq_item
                            sse = _sse_event_to_sse(event)
                            if sse is not None:
                                yield _inject_seq(sse, _seq, _sid)
                    break

            if not chat_task.cancelled():
                try:
                    chat_result = chat_task.result()
                    if get_session_manager() is None:
                        return
                    engine = get_session_manager().get_engine(session_id)
                    if engine is not None:
                        normalized_reply = guard_public_reply((chat_result.reply or "").strip())
                        yield _build_reply_sse(chat_result, engine)
                        if engine.session_turn == 1:
                            _user_msg = title_from_messages(engine.raw_messages)
                            _instant_title = _truncate_user_message_as_title(_user_msg)
                            if _instant_title:
                                yield _sse_format("session_title", {
                                    "session_id": session_id,
                                    "title": _instant_title,
                                })
                                _ch = get_session_manager().chat_history if get_session_manager() else None
                                if _ch is not None:
                                    try:
                                        _ch.update_session(session_id, title=_instant_title, title_source="truncated")
                                    except Exception:
                                        pass
                            _fire_and_forget(_generate_session_title_background(
                                session_id=session_id,
                                user_message=_user_msg,
                                assistant_reply=normalized_reply,
                            ), name="session_title")
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.warning(
                        "subscribe 获取 chat 结果失败: %s",
                        exc,
                        exc_info=True,
                    )
                    _sub_guidance = classify_failure(exc, stage="subscribe_resume")
                    yield _failure_guidance_sse(_sub_guidance)
            yield _sse_format("done", {})

        except (asyncio.CancelledError, GeneratorExit):
            logger.info("会话 %s 的重连流式连接再次断开", session_id)
        except Exception as exc:
            logger.error("SSE subscribe 流异常: %s", exc, exc_info=True)
            _sub_top_guidance = classify_failure(exc, stage="subscribe_resume")
            yield _failure_guidance_sse(_sub_top_guidance)
            yield _sse_format("done", {})
        finally:
            stream_state.detach()
            if chat_task.done():
                _app_state._active_chat_tasks.pop(session_id, None)
                _app_state._session_stream_states.pop(session_id, None)
            await _cancel_task(queue_get_task)

    return _streaming_response(_subscribe_generator())

@router.post("/api/v1/chat/abort")
async def chat_abort(request: AbortRequest, raw_request: Request) -> JSONResponse:
    """终止指定会话的活跃聊天任务。"""
    if not await _has_session_access(request.session_id, raw_request):
        return JSONResponse(
            status_code=200,
            content={"status": "no_active_task"},
        )
    task = _app_state._active_chat_tasks.get(request.session_id)
    if task is None or task.done():
        return JSONResponse(
            status_code=200,
            content={"status": "no_active_task"},
        )
    # 中断当前会话正在执行的 sleep 工具（会话隔离，不影响其他会话）
    if get_session_manager() is not None:
        engine = get_session_manager().get_engine(request.session_id)
        if engine is not None and engine._tool_dispatcher is not None:
            engine._tool_dispatcher.request_cancel()

    task.cancel()
    logger.info("通过 abort 端点取消会话 %s 的聊天任务", request.session_id)
    return JSONResponse(
        status_code=200,
        content={"status": "cancelled"},
    )


@router.post("/api/v1/chat/{session_id}/guide", responses=_error_responses)
async def chat_guide(
    session_id: str,
    request: GuideRequest,
    raw_request: Request,
) -> JSONResponse:
    """向运行中的会话注入引导消息（不启动新 chat）。

    消息存入 engine 内部队列，agent 在下次 LLM 迭代时自动看到。
    即使当前没有 in-flight 任务也可投递，下次 chat() 时生效。
    """
    if get_session_manager() is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    if not await _has_session_access(session_id, raw_request):
        return JSONResponse(status_code=403, content={"error": "无权访问此会话"})

    engine = get_session_manager().get_engine(session_id)
    if engine is None:
        return _error_json_response(404, f"会话 '{session_id}' 不存在或未加载")

    engine.push_guide_message(request.message)
    in_flight = session_id in _app_state._active_chat_tasks and not _app_state._active_chat_tasks[session_id].done()
    logger.info(
        "Guide 消息已投递: session=%s, in_flight=%s, len=%d",
        session_id[:8], in_flight, len(request.message),
    )
    return JSONResponse(
        status_code=200,
        content={"status": "delivered", "in_flight": in_flight},
    )


@router.post("/api/v1/chat/{session_id}/answer", responses=_error_responses)
async def chat_answer(
    session_id: str,
    request: AnswerQuestionRequest,
    raw_request: Request,
) -> JSONResponse:
    """提交 ask_user 问题的回答，resolve 阻塞中的 Future。"""
    if get_session_manager() is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    if not await _has_session_access(session_id, raw_request):
        return JSONResponse(status_code=403, content={"error": "无权访问此会话"})

    engine = get_session_manager().get_engine(session_id)
    if engine is None:
        return JSONResponse(status_code=404, content={"error": "会话不存在或未激活"})

    registry = engine.interaction_registry
    # 构造 payload：兼容 QuestionFlowManager 的 parse_answer 格式
    payload = {"raw_input": request.answer, "question_id": request.question_id}

    # 尝试解析选项（如果 question_flow 中有对应问题）
    try:
        pending_q = engine._question_flow.current()
        if pending_q is not None and pending_q.question_id == request.question_id:
            parsed = engine._question_flow.parse_answer(request.answer, pending_q)
            payload = parsed.to_tool_result()
    except Exception:
        logger.debug("解析回答失败，使用原始文本", exc_info=True)

    ok = registry.resolve(request.question_id, payload)
    if not ok:
        return JSONResponse(
            status_code=404,
            content={"error": f"问题 {request.question_id} 不存在或已回答"},
        )
    logger.info("问题已回答: session=%s question=%s", session_id, request.question_id)
    return JSONResponse(status_code=200, content={"status": "answered"})


@router.post("/api/v1/chat/{session_id}/approve", responses=_error_responses)
async def chat_approve(
    session_id: str,
    request: ApproveRequest,
    raw_request: Request,
) -> JSONResponse:
    """提交审批决策，resolve 阻塞中的 Future。"""
    if get_session_manager() is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    if not await _has_session_access(session_id, raw_request):
        return JSONResponse(status_code=403, content={"error": "无权访问此会话"})

    engine = get_session_manager().get_engine(session_id)
    if engine is None:
        return JSONResponse(status_code=404, content={"error": "会话不存在或未激活"})

    registry = engine.interaction_registry
    payload = {"decision": request.decision, "approval_id": request.approval_id}
    ok = registry.resolve(request.approval_id, payload)
    if not ok:
        return JSONResponse(
            status_code=404,
            content={"error": f"审批 {request.approval_id} 不存在或已处理"},
        )
    logger.info(
        "审批已决策: session=%s approval=%s decision=%s",
        session_id, request.approval_id, request.decision,
    )
    return JSONResponse(status_code=200, content={"status": "resolved"})



def _truncate_user_message_as_title(user_message: str) -> str | None:
    """从用户消息得到即时会话标题（去除上传前缀，取首行；显示层再做省略）。"""
    return instant_session_title(user_message)


async def _generate_session_title_background(
    *,
    session_id: str,
    user_message: str,
    assistant_reply: str,
) -> None:
    """后台 fire-and-forget：独立客户端润色标题，不进入主 Agent 循环。

    前端通过 SessionSync 的 list_sessions 轮询自动获取更新后的标题。
    若用户已发起下一轮，跳过以免与主任务抢同一激活模型配额。
    """
    try:
        sm = get_session_manager()
        if sm is not None and await sm.is_session_in_flight(session_id):
            logger.debug("会话 %s 主任务进行中，跳过后台标题生成", session_id)
            return
        title = await _generate_session_title_with_timeout(
            session_id=session_id,
            user_message=user_message,
            assistant_reply=assistant_reply,
            timeout=8.0,
        )
        if title:
            logger.info("会话 %s 后台 AI 标题已更新: %s", session_id, title)
    except Exception:
        logger.debug("会话 %s 后台标题生成失败", session_id, exc_info=True)


async def _generate_session_title_with_timeout(
    *,
    session_id: str,
    user_message: str,
    assistant_reply: str,
    timeout: float = 5.0,
) -> str | None:
    """用独立客户端调用激活模型生成会话标题，超时返回 None。"""
    if get_config() is None or get_session_manager() is None:
        return None
    sm = get_session_manager()
    if sm is not None and await sm.is_session_in_flight(session_id):
        logger.debug("会话 %s 主任务进行中，跳过标题生成", session_id)
        return None
    ch = sm.chat_history if sm is not None else None
    if ch is None:
        return None
    if not get_config().model or not get_config().api_key:
        return None

    try:
        from excelmanus.providers import create_client as _create_client
        from excelmanus.session_title import generate_session_title

        client = _create_client(
            api_key=get_config().api_key,
            base_url=get_config().base_url,
            protocol=get_config().protocol,
        )
        title = await asyncio.wait_for(
            generate_session_title(
                user_message=user_message,
                assistant_reply=assistant_reply,
                client=client,
                model=get_config().model,
            ),
            timeout=timeout,
        )
        if title:
            ch.update_session(session_id, title=title, title_source="auto")
            logger.info("会话 %s 自动标题: %s", session_id, title)
        return title
    except asyncio.TimeoutError:
        logger.info("会话 %s 标题生成超时 (%.1fs)", session_id, timeout)
        return None
    except Exception:
        logger.warning("会话 %s 标题生成失败", session_id, exc_info=True)
        return None


def _sse_event_to_sse(event: ToolCallEvent) -> str | None:
    """将 ToolCallEvent 转换为 SSE 文本（委托到 api/sse.py）。"""
    return _sse_event_to_sse_impl(
        event,
        public_path_fn=_public_excel_path,
    )
