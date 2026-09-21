"""会话列表/详情、导出、压缩记忆与操作历史 API。

从 api.py 抽出的独立路由模块。运行时状态只从 api_app_state 读取，
禁止 ``from excelmanus.api import _config`` 反向导入。
由 api.py 在 create_app 中 include_router 注册。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from excelmanus.api_app_state import (
    error_json_response as _error_json_response,
    get_config,
    get_database,
    get_session_manager,
    has_session_access as _has_session_access,
)
from excelmanus.logger import get_logger
from excelmanus.output_guard import sanitize_external_data, sanitize_external_text
from excelmanus.session import SessionNotFoundError

logger = get_logger("api.sessions")

router = APIRouter()


class SubagentControlRequest(BaseModel):
    action: Literal["status", "wait", "send", "cancel", "pause", "resume"]
    message: str = ""
    wait_seconds: float = Field(default=30, ge=0, le=60)


class ResponsesControlRequest(BaseModel):
    action: Literal["status", "cancel", "steer"]
    message: str = ""


async def _engine_for_session(session_id: str, request: Request):
    if not await _has_session_access(session_id, request):
        raise HTTPException(status_code=404, detail="会话不存在")
    manager = get_session_manager()
    if manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    engine = await manager.get_or_restore_engine(session_id)
    if engine is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return engine


@router.get("/api/v1/sessions/{session_id}/turn")
async def get_main_turn(session_id: str, request: Request) -> dict:
    engine = await _engine_for_session(session_id, request)
    return {"turn": engine._driver.current_turn()}


@router.get("/api/v1/sessions/{session_id}/trace")
async def get_session_trace(session_id: str, request: Request) -> dict[str, Any]:
    """Read the bounded trace, including spans restored from session snapshots."""
    engine = await _engine_for_session(session_id, request)
    trace = getattr(engine, "_trace", None)
    return {"trace": trace.snapshot() if trace is not None else {"trace_id": "", "spans": []}}


@router.get("/api/v1/sessions/{session_id}/tool-calls")
async def list_tool_calls(session_id: str, request: Request) -> dict:
    engine = await _engine_for_session(session_id, request)
    return {"calls": engine._tool_runtime.call_states()}


@router.post("/api/v1/sessions/{session_id}/tool-calls/{execution_id}/cancel")
async def cancel_tool_call(session_id: str, execution_id: str, request: Request) -> dict:
    engine = await _engine_for_session(session_id, request)
    try:
        return {"call": engine._tool_runtime.cancel_call(execution_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="工具执行不存在或已过期") from None


@router.get("/api/v1/sessions/{session_id}/handoff")
async def get_compaction_handoff(session_id: str, request: Request) -> dict[str, Any]:
    """Read saved progress and continuity state without running a model or tool."""
    engine = await _engine_for_session(session_id, request)
    from excelmanus.compaction import handoff_from_memory

    artifact, error = handoff_from_memory(engine.memory)
    return {"handoff": artifact, "continuity_error": error}


@router.get("/api/v1/sessions/{session_id}/subagents")
async def list_subagent_runs(session_id: str, request: Request) -> dict:
    runtime = (await _engine_for_session(session_id, request))._subagent_runtime
    return {"runs": runtime.list_runs()}


@router.get("/api/v1/sessions/{session_id}/task-list")
async def get_session_task_list(session_id: str, request: Request) -> dict:
    """返回会话当前任务清单快照；无任务清单时 task_list 为 None。"""
    engine = await _engine_for_session(session_id, request)
    store = getattr(engine, "_task_store", None)
    current = store.current if store is not None else None
    payload = sanitize_external_data(current.to_dict()) if current is not None else None
    if payload is not None and store.plan_file_path:
        payload["plan_file_path"] = sanitize_external_text(
            store.plan_file_path, max_len=500
        )
    return {"task_list": payload}


@router.post("/api/v1/sessions/{session_id}/subagents/{run_id}")
async def control_subagent_run(
    session_id: str, run_id: str, body: SubagentControlRequest, request: Request,
) -> dict:
    from excelmanus.subagent.errors import SubagentError

    runtime = (await _engine_for_session(session_id, request))._subagent_runtime
    try:
        if body.action == "wait":
            return {"run": await runtime.wait(run_id, body.wait_seconds)}
        if body.action == "send":
            await runtime.send_message(run_id, body.message)
        elif body.action in {"pause", "cancel"}:
            await runtime.interrupt(run_id, pause=body.action == "pause")
        elif body.action == "resume":
            run_id = await runtime.resume(run_id, body.message)
        return {"run": runtime.get_run(run_id)}
    except SubagentError as exc:
        raise HTTPException(status_code=404 if exc.code == "NOT_FOUND" else 409,
                            detail=exc.message) from exc


@router.post("/api/v1/sessions/{session_id}/responses/{response_id}")
async def control_responses_background(
    session_id: str,
    response_id: str,
    body: ResponsesControlRequest,
    request: Request,
) -> dict[str, Any]:
    """Query, cancel, or steer a stored Responses background response."""
    engine = await _engine_for_session(session_id, request)
    try:
        if body.action == "status":
            payload = await engine.get_responses_background(response_id)
        elif body.action == "cancel":
            payload = await engine.cancel_responses_background(response_id)
        else:
            if not body.message.strip():
                raise HTTPException(status_code=422, detail="steer message 不能为空")
            result = await engine.steer_responses(response_id, body.message.strip())
            payload = {
                "id": getattr(result, "response_id", "") or "",
                "status": "completed",
                "output": getattr(result.choices[0].message, "content", "")
                if getattr(result, "choices", None) else "",
            }
        return {"response": sanitize_external_data(payload)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/v1/sessions/{session_id}/mutations/{operation_id}")
async def get_workspace_operation(
    session_id: str, operation_id: str, request: Request,
) -> dict[str, Any]:
    """Return the durable idempotent mutation receipt for a session operation."""
    engine = await _engine_for_session(session_id, request)
    from excelmanus.workspace.file_service import WorkspaceFileService

    service = WorkspaceFileService(engine._workspace.root_dir)
    receipt = service.get_receipt(operation_id, recover=False)
    if receipt is None:
        raise HTTPException(status_code=404, detail="找不到写入操作")
    return {"operation": sanitize_external_data(receipt.to_dict())}


@router.post("/api/v1/sessions/{session_id}/mutations/{operation_id}/abort")
async def abort_workspace_operation(
    session_id: str, operation_id: str, request: Request,
) -> dict[str, Any]:
    """Explicitly abandon an unfinished partial mutation while keeping published files."""
    engine = await _engine_for_session(session_id, request)
    from excelmanus.workspace.file_service import WorkspaceFileService

    service = WorkspaceFileService(engine._workspace.root_dir)
    try:
        receipt = service.abort_recovery(operation_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"operation": sanitize_external_data(receipt.to_dict())}

# OpenAPI 错误响应（与 api.py _error_responses 对应项保持一致）
_error_responses: dict = {
    403: {"description": "无权限执行"},
    404: {"description": "会话不存在"},
    409: {"description": "会话正在处理中"},
    422: {"description": "请求参数错误"},
    429: {"description": "会话数量超限"},
    500: {"description": "服务内部错误"},
}


def _change_type(before_exists: bool, after_exists: bool) -> str:
    """从 before/after 存在状态推导变更类型。"""
    if not before_exists and after_exists:
        return "added"
    if before_exists and not after_exists:
        return "deleted"
    return "modified"


@router.delete("/api/v1/sessions/{session_id}", responses={
    409: _error_responses[409],
    404: _error_responses[404],
    500: _error_responses[500],
})
async def delete_session(session_id: str, request: Request) -> dict:
    """删除指定会话并释放资源。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")

    deleted = await session_manager.delete(session_id)
    if not deleted:
        raise SessionNotFoundError(f"会话 '{session_id}' 不存在。")
    return {"status": "ok", "session_id": session_id}


@router.patch("/api/v1/sessions/{session_id}/title", responses={
    404: _error_responses[404],
    500: _error_responses[500],
})
async def update_session_title_api(session_id: str, request: Request) -> JSONResponse:
    """用户手动更新会话标题。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        return _error_json_response(400, "标题不能为空")
    if len(title) > 100:
        return _error_json_response(400, "标题长度不能超过 100 字符")
    ok = await session_manager.update_session_title(
        session_id, title
    )
    if not ok:
        return _error_json_response(404, "会话不存在")
    return JSONResponse(content={"status": "ok", "title": title})


@router.get("/api/v1/approvals")
async def list_approvals(request: Request) -> JSONResponse:
    """列出已执行的审批记录（支持分页与筛选）。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")

    limit = int(request.query_params.get("limit", "50"))
    undoable_only = request.query_params.get("undoable_only", "false").lower() == "true"
    session_id = request.query_params.get("session_id")
    if not session_id:
        return _error_json_response(400, "缺少 session_id 参数。")
    if not await _has_session_access(session_id, request):
        return _error_json_response(404, "会话不存在。")
    engine = session_manager.get_engine(session_id)

    if engine is None:
        return JSONResponse(content={"approvals": []})

    records = engine._approval.list_applied(limit=limit, undoable_only=undoable_only)
    items = []
    for rec in records:
        item: dict = {
            "id": rec.approval_id,
            "tool_name": rec.tool_name,
            "created_at_utc": rec.created_at_utc,
            "applied_at_utc": rec.applied_at_utc,
            "execution_status": rec.execution_status,
            "undoable": rec.undoable,
            "result_preview": sanitize_external_text(rec.result_preview or "", max_len=200),
            "arguments": sanitize_external_data(rec.arguments),
            "changes": [
                {"path": c.path, "before_exists": c.before_exists, "after_exists": c.after_exists}
                for c in (rec.changes or [])
            ],
        }
        items.append(item)
    return JSONResponse(content={"approvals": items})


@router.post("/api/v1/approvals/{approval_id}/undo")
async def undo_approval(approval_id: str, request: Request) -> JSONResponse:
    """回滚指定审批操作。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")

    session_id = request.query_params.get("session_id")
    if not session_id:
        return _error_json_response(400, "缺少 session_id 参数。")
    if not await _has_session_access(session_id, request):
        return _error_json_response(404, "会话不存在。")
    engine = await session_manager.get_or_restore_engine(session_id)

    if engine is None:
        return _error_json_response(404, "没有活跃会话。")

    result_msg = engine._approval.undo(approval_id)
    success = "已回滚" in result_msg
    return JSONResponse(content={
        "status": "ok" if success else "error",
        "message": result_msg,
        "approval_id": approval_id,
    })


@router.get("/api/v1/sessions/{session_id}/operations")
async def list_operations(
    session_id: str,
    request: Request,
    limit: int = 50,
    offset: int = 0,
) -> JSONResponse:
    """列出指定会话的写入操作历史（时间线）。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    if not await _has_session_access(session_id, request):
        return _error_json_response(404, f"会话 '{session_id}' 不存在。")
    engine = session_manager.get_engine(session_id)

    if engine is None:
        return JSONResponse(content={"operations": [], "total": 0, "has_more": False})

    from excelmanus.tools.policy import sanitize_approval_args_summary

    # 获取比请求多 1 条以判断 has_more
    records = engine._approval.list_applied(
        limit=offset + limit + 1,
        session_id=session_id,
    )
    total = len(records)
    has_more = total > offset + limit
    page = records[offset : offset + limit]

    items = []
    for rec in page:
        changes = []
        for c in rec.changes or []:
            changes.append({
                "path": c.path,
                "change_type": _change_type(c.before_exists, c.after_exists),
                "before_size": c.before_size,
                "after_size": c.after_size,
                "is_binary": c.is_binary,
            })
        item: dict[str, Any] = {
            "approval_id": rec.approval_id,
            "tool_name": rec.tool_name,
            "arguments_summary": sanitize_approval_args_summary(rec.arguments),
            "session_turn": rec.session_turn,
            "created_at_utc": rec.created_at_utc,
            "applied_at_utc": rec.applied_at_utc,
            "execution_status": rec.execution_status,
            "undoable": rec.undoable,
            "changes": changes,
            "result_preview": sanitize_external_text(
                rec.result_preview or "", max_len=300,
            ),
        }
        items.append(item)

    return JSONResponse(content={
        "operations": items,
        "total": total,
        "has_more": has_more,
    })


@router.get("/api/v1/sessions/{session_id}/operations/{approval_id}")
async def get_operation_detail(
    session_id: str,
    approval_id: str,
    request: Request,
) -> JSONResponse:
    """获取单条操作的详情（含 diff 内容）。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    if not await _has_session_access(session_id, request):
        return _error_json_response(404, f"会话 '{session_id}' 不存在。")
    engine = session_manager.get_engine(session_id)

    if engine is None:
        return _error_json_response(404, "没有活跃会话。")

    rec = engine._approval.get_applied(approval_id)
    if rec is None:
        return _error_json_response(404, f"操作 '{approval_id}' 不存在。")
    if rec.session_id and rec.session_id != session_id:
        return _error_json_response(404, f"操作 '{approval_id}' 不属于此会话。")

    from excelmanus.tools.policy import sanitize_approval_args_summary

    changes = []
    for c in rec.changes or []:
        changes.append({
            "path": c.path,
            "change_type": _change_type(c.before_exists, c.after_exists),
            "before_size": c.before_size,
            "after_size": c.after_size,
            "is_binary": c.is_binary,
        })

    # 读取 patch 文件内容（如果存在）
    patch_content: str | None = None
    if rec.patch_file:
        patch_path = Path(engine._config.workspace_root) / rec.patch_file
        if patch_path.is_file():
            try:
                patch_content = patch_path.read_text(encoding="utf-8")[:50000]
            except OSError:
                pass

    result: dict[str, Any] = {
        "approval_id": rec.approval_id,
        "tool_name": rec.tool_name,
        "arguments_summary": sanitize_approval_args_summary(rec.arguments),
        "arguments": sanitize_external_data(rec.arguments),
        "session_turn": rec.session_turn,
        "created_at_utc": rec.created_at_utc,
        "applied_at_utc": rec.applied_at_utc,
        "execution_status": rec.execution_status,
        "undoable": rec.undoable,
        "changes": changes,
        "result_preview": sanitize_external_text(
            rec.result_preview or "", max_len=1000,
        ),
        "patch_content": patch_content,
        "error_type": rec.error_type,
        "error_message": rec.error_message,
    }
    return JSONResponse(content=result)


@router.post("/api/v1/sessions/{session_id}/operations/{approval_id}/undo")
async def undo_operation(
    session_id: str,
    approval_id: str,
    request: Request,
) -> JSONResponse:
    """回滚指定操作。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    if not await _has_session_access(session_id, request):
        return _error_json_response(404, f"会话 '{session_id}' 不存在。")
    engine = await session_manager.get_or_restore_engine(
        session_id,
    )

    if engine is None:
        return _error_json_response(404, "没有活跃会话。")

    # 验证操作属于此会话
    rec = engine._approval.get_applied(approval_id)
    if rec is None:
        return _error_json_response(404, f"操作 '{approval_id}' 不存在。")
    if rec.session_id and rec.session_id != session_id:
        return _error_json_response(404, f"操作 '{approval_id}' 不属于此会话。")

    result_msg = engine._approval.undo(approval_id)
    success = "已回滚" in result_msg
    return JSONResponse(content={
        "status": "ok" if success else "error",
        "message": result_msg,
        "approval_id": approval_id,
    })


def _public_excel_path(path: str) -> str:
    """将 Excel 路径规范化为前端可直接回传的形式。

    公开身份只认 CanonicalPath（方案 P1）。overlay / backups 映射到正本，
    映射不到则省略，不把 staging 路径当交付物。未知绝对路径脱敏，不泄露家目录。
    """
    raw = str(path or "").strip()
    if not raw:
        return ""

    from excelmanus.workspace.identity import (
        is_overlay_leftover,
        is_reserved_relative,
        public_identity,
    )

    _config = get_config()
    workspace = Path(_config.workspace_root).resolve() if _config is not None else None

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
                return public_identity(rel.as_posix(), workspace)
            except Exception:
                return sanitize_external_text(raw, max_len=500)

    normalized = raw.replace("\\", "/")
    if normalized.startswith("./"):
        return public_identity(normalized, workspace) or ""
    if normalized.startswith("/"):
        return sanitize_external_text(normalized, max_len=500)
    return public_identity(normalized, workspace) or ""


@router.get("/api/v1/sessions")
async def list_sessions(request: Request) -> JSONResponse:
    """列出所有会话（含历史）。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    sessions = await session_manager.list_sessions()
    return JSONResponse(content={"sessions": sessions})


@router.post("/api/v1/sessions", responses={
    400: _error_responses[422],
    500: _error_responses[500],
})
async def create_session_api(request: Request) -> JSONResponse:
    """Create or reuse a blank session in the conversation list. No engine."""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    workspace_id = (body.get("workspace_id") or "").strip() or None
    workspace_path = (body.get("workspace_path") or "").strip() or None
    title = (body.get("title") or "").strip() or "新对话"
    from excelmanus.stores.workspace_store import WorkspacePathError
    try:
        session = await session_manager.create_or_reuse_session(
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            title=title,
        )
    except WorkspacePathError as exc:
        return _error_json_response(400, str(exc))
    except FileNotFoundError as exc:
        return _error_json_response(400, str(exc))
    return JSONResponse(content=session, status_code=200)


@router.delete("/api/v1/sessions", responses={409: _error_responses[409]})
async def clear_all_sessions(request: Request) -> JSONResponse:
    """清空全部会话历史。若有会话正在处理中则返回 409。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    sess_count, msg_count = await session_manager.clear_all_sessions()
    return JSONResponse(content={
        "status": "ok",
        "sessions_deleted": sess_count,
        "messages_deleted": msg_count,
    })


@router.get("/api/v1/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, request: Request) -> JSONResponse:
    """分页获取会话消息。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    try:
        limit = max(1, min(500, int(request.query_params.get("limit", "50"))))
        offset = max(0, int(request.query_params.get("offset", "0")))
    except (ValueError, TypeError):
        return JSONResponse(status_code=400, content={"detail": "limit/offset 必须为整数"})
    messages = await session_manager.get_session_messages(
        session_id, limit=limit, offset=offset
    )
    normalized_messages: list[dict[str, Any]] = []
    for idx, message in enumerate(messages):
        if isinstance(message, dict):
            normalized = dict(message)
        elif hasattr(message, "model_dump"):
            normalized = message.model_dump()
        else:
            try:
                normalized = dict(message)
            except Exception:
                normalized = {"role": "assistant", "content": str(message)}

        if not normalized.get("message_id"):
            normalized["message_id"] = f"volatile:{session_id}:{offset + idx}"
        normalized_messages.append(normalized)

    return JSONResponse(content={"messages": normalized_messages, "session_id": session_id})


@router.get("/api/v1/sessions/{session_id}/excel-events")
async def get_session_excel_events(session_id: str, request: Request) -> JSONResponse:
    """返回持久化的 Excel diff 和改动文件列表，供前端重启后恢复。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    ch = session_manager.chat_history
    if ch is None:
        return JSONResponse(content={"diffs": [], "affected_files": [], "previews": []})
    diffs = ch.load_excel_diffs(session_id)
    affected_files = ch.load_affected_files(session_id)
    previews = ch.load_excel_previews(session_id)
    safe_diffs = []
    for d in diffs:
        safe_diffs.append({
            "tool_call_id": d["tool_call_id"],
            "file_path": _public_excel_path(d["file_path"]),
            "sheet": d["sheet"],
            "affected_range": d["affected_range"],
            "changes": d["changes"],
            "timestamp": d["timestamp"],
        })
    safe_previews = []
    for p in previews:
        safe_previews.append({
            "tool_call_id": p["tool_call_id"],
            "file_path": _public_excel_path(p["file_path"]),
            "sheet": p["sheet"],
            "columns": p["columns"],
            "rows": p["rows"],
            "total_rows": p["total_rows"],
            "truncated": p["truncated"],
        })
    safe_files = [
        _public_excel_path(f)
        for f in affected_files if f
    ]
    return JSONResponse(content={
        "diffs": safe_diffs,
        "previews": safe_previews,
        "affected_files": safe_files,
    })


@router.get("/api/v1/sessions/{session_id}/export")
async def export_session(session_id: str, request: Request) -> Response:
    """导出会话为 Markdown 报告或 JSON 对话档案。

    Query params:
        format: md | json (默认 md)
    """
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    fmt = (request.query_params.get("format") or "md").lower().strip()
    if fmt not in ("md", "json"):
        return JSONResponse(status_code=400, content={"detail": f"不支持的格式: {fmt}，可选: md, json"})

    from urllib.parse import quote

    from excelmanus.session_export import export_json, export_markdown

    messages = await session_manager.get_session_messages(
        session_id, limit=100000, offset=0,
    )
    if not messages:
        return _error_json_response(404, f"会话 '{session_id}' 不存在或无消息")

    ch = session_manager.chat_history
    session_meta: dict[str, Any] = {"id": session_id, "title": "未命名会话", "created_at": "", "updated_at": ""}
    if ch is not None:
        meta = ch.get_session_meta(session_id)
        if meta:
            session_meta.update(meta)

    excel_diffs: list[dict] = []
    excel_previews: list[dict] = []
    affected_files: list[str] = []
    if ch is not None:
        excel_diffs = [
            {**d, "file_path": _public_excel_path(d.get("file_path", ""))}
            for d in ch.load_excel_diffs(session_id)
        ]
        excel_previews = [
            {**p, "file_path": _public_excel_path(p.get("file_path", ""))}
            for p in ch.load_excel_previews(session_id)
        ]
        affected_files = []
        for f in ch.load_affected_files(session_id):
            if not f:
                continue
            ident = _public_excel_path(f)
            if ident:
                affected_files.append(ident)

    raw_title = session_meta.get("title", "session") or "session"
    ascii_title = "".join(c for c in raw_title if c.isascii() and (c.isalnum() or c in " _-")).strip()[:50] or "session"
    utf8_title = "".join(c for c in raw_title if c.isalnum() or c in " _-").strip()[:50] or "session"

    def _cd(ext: str) -> str:
        return (
            f"attachment; filename=\"{ascii_title}.{ext}\"; "
            f"filename*=UTF-8''{quote(utf8_title)}.{ext}"
        )

    if fmt == "md":
        content = export_markdown(session_meta, messages, excel_diffs, excel_previews, affected_files)
        return Response(
            content=content.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": _cd("md")},
        )

    data = export_json(session_meta, messages, excel_diffs, excel_previews, affected_files)
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": _cd("json")},
    )


@router.get("/api/v1/sessions/{session_id}/status")
async def get_session_status(session_id: str, request: Request) -> JSONResponse:
    """获取会话运行时状态（上下文压缩 + 文件注册表）。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")

    def _normalize_registry_status(registry_payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(registry_payload)
        state = str(normalized.get("state") or "idle").lower()
        if state == "ready":
            state = "built"
        if state not in {"idle", "building", "built", "error"}:
            state = "idle"
        normalized["state"] = state

        # 兼容前端旧字段约定：将 total_files 回填到 sheet_count。
        total_files = normalized.get("total_files")
        if normalized.get("sheet_count") is None and total_files is not None:
            try:
                normalized["sheet_count"] = int(total_files)
            except (TypeError, ValueError):
                pass
        return normalized

    # 性能优化：仅查询内存中已有的引擎，不触发引擎创建/恢复。
    # 如果会话被 TTL 清理出内存，返回 idle 默认值即可——
    # 引擎会在用户真正发消息时才创建，避免轮询导致不必要的重量级初始化。
    engine = session_manager.get_engine(session_id)

    if engine is None:
        _idle = _normalize_registry_status({"state": "idle"})
        return JSONResponse(content={
            "session_id": session_id,
            "compaction": {"enabled": False},
            "registry": _idle,
        })

    # 上下文压缩状态
    compaction: dict[str, Any] = {"enabled": False}
    try:
        compaction = engine.get_compaction_status()
    except Exception:
        pass

    # 文件注册表扫描状态
    registry: dict[str, Any] = {"state": "idle"}
    try:
        registry = engine.registry_scan_status()
    except Exception:
        pass
    registry = _normalize_registry_status(registry)

    return JSONResponse(content={
        "session_id": session_id,
        "compaction": compaction,
        "registry": registry,
    })


@router.post("/api/v1/sessions/{session_id}/compact")
async def compact_session_context(session_id: str, request: Request) -> JSONResponse:
    """在指定会话内执行 /compact，并返回执行结果。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")

    try:
        engine = await _engine_for_session(session_id, request)
    except HTTPException as exc:
        return _error_json_response(exc.status_code, str(exc.detail))
    if engine._driver.running:
        return _error_json_response(409, "当前会话正在执行，请在步骤结束后压缩。")

    try:
        result = await engine._command_handler.handle("/compact")
    except Exception as exc:
        logger.warning("会话 %s 执行 /compact 失败: %s", session_id, exc)
        return _error_json_response(500, f"压缩执行失败: {exc}")

    if not result:
        result = "压缩命令已执行，但未返回可展示结果。"

    return JSONResponse(content={
        "session_id": session_id,
        "result": result,
    })


@router.post("/api/v1/sessions/{session_id}/memory/extract")
async def extract_session_memory(session_id: str, request: Request) -> JSONResponse:
    """手动触发指定会话的记忆提取。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")

    engine = await session_manager.get_or_restore_engine(
        session_id
    )
    if engine is None:
        return _error_json_response(404, f"会话不存在: {session_id}")

    try:
        entries = await engine.extract_and_save_memory(trigger="manual")
    except Exception as exc:
        logger.warning("会话 %s 手动记忆提取失败: %s", session_id, exc)
        return _error_json_response(500, f"记忆提取失败: {exc}")

    return JSONResponse(content={
        "session_id": session_id,
        "count": len(entries),
        "entries": [
            {"id": e.id, "content": e.content, "category": e.category.value}
            for e in entries
        ],
    })


@router.post("/api/v1/sessions/{session_id}/registry/scan")
async def scan_session_registry(session_id: str, request: Request) -> JSONResponse:
    """触发指定会话的 FileRegistry 后台扫描（force=True）。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")

    engine = await session_manager.get_or_restore_engine(
        session_id
    )
    if engine is None:
        return _error_json_response(404, f"会话不存在: {session_id}")

    try:
        started = engine.start_registry_scan(force=True)
    except Exception as exc:
        logger.warning("会话 %s 触发 registry scan 失败: %s", session_id, exc)
        return _error_json_response(500, f"文件扫描失败: {exc}")

    return JSONResponse(content={
        "session_id": session_id,
        "started": started,
        "message": "文件扫描已启动" if started else "扫描正在进行中，请稍候",
    })


@router.post("/api/v1/sessions/{session_id}/full-access")
async def toggle_full_access(session_id: str, request: Request) -> JSONResponse:
    """切换完全访问：跳过审批并放开 run_code 的网络/进程/文件围栏。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")

    body = await request.json()
    enabled = bool(body.get("enabled", True))

    engine = await session_manager.get_or_restore_engine(
        session_id
    )
    if engine is not None:
        engine._full_access_enabled = enabled
        engine._persist_full_access(enabled)
        engine._auto_approve_enabled = False
        engine._persist_auto_approve(False)
        if not enabled:
            # 关闭时驱逐受限 skill，与 command_handler 保持一致
            blocked = set(engine._restricted_code_skillpacks)
            before = len(engine._active_skills)
            engine._active_skills = [
                s for s in engine._active_skills if s.name not in blocked
            ]
            if len(engine._active_skills) != before:
                from excelmanus.request.series import series_of

                series_of(engine).note("catalog/change")
    else:
        # 会话尚未创建（local-first），仅持久化到 UserConfigStore
        database = get_database()
        if database is not None:
            try:
                from excelmanus.stores.config_store import UserConfigStore
                uc = UserConfigStore(database.conn)
                uc.set_full_access(enabled)
            except Exception:
                logger.debug("持久化 full_access 失败（无会话）", exc_info=True)
            try:
                uc.set_auto_approve(False)
            except Exception:
                logger.debug("持久化 auto_approve 失败（无会话）", exc_info=True)

    return JSONResponse(content={
        "session_id": session_id,
        "full_access_enabled": enabled,
        "auto_approve_enabled": bool(getattr(engine, "_auto_approve_enabled", False)) if engine is not None else False,
    })


@router.post("/api/v1/sessions/{session_id}/auto-approve")
async def toggle_auto_approve(session_id: str, request: Request) -> JSONResponse:
    """切换仅自动审批：不授予网络、子进程或越界文件能力。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")

    body = await request.json()
    enabled = bool(body.get("enabled", True))
    engine = await session_manager.get_or_restore_engine(session_id)
    if engine is not None:
        engine._auto_approve_enabled = enabled
        engine._persist_auto_approve(enabled)
        if enabled:
            engine._full_access_enabled = False
            engine._persist_full_access(False)
    else:
        database = get_database()
        if database is not None:
            try:
                from excelmanus.stores.config_store import UserConfigStore
                uc = UserConfigStore(database.conn)
                uc.set_auto_approve(enabled)
                if enabled:
                    uc.set_full_access(False)
            except Exception:
                logger.debug("持久化 auto_approve 失败（无会话）", exc_info=True)

    return JSONResponse(content={
        "session_id": session_id,
        "full_access_enabled": bool(getattr(engine, "_full_access_enabled", False)) if engine is not None else False,
        "auto_approve_enabled": enabled,
    })


@router.get("/api/v1/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> JSONResponse:
    """获取会话详情含消息历史。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    # 当前会话冷恢复后才能展示可继续提交的交互；读取不启动 Driver。
    await session_manager.get_or_restore_engine(session_id)
    try:
        detail = await session_manager.get_session_detail(session_id)
    except SessionNotFoundError:
        # 会话可能尚未在后端创建（前端 local-first 乐观创建），返回空默认值。
        return JSONResponse(content={
            "id": session_id,
            "message_count": 0,
            "in_flight": False,
            "messages": [],
            "full_access_enabled": False,
            "auto_approve_enabled": False,
            "chat_mode": "write",
            "current_model": None,
            "current_model_name": None,
            "vision_capable": None,
            "pending_approval": None,
            "pending_question": None,
            "last_route": None,
        })
    return JSONResponse(content=dict(detail))
