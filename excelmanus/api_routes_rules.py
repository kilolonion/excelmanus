"""Rules + Memory API 路由。

从 api.py 提取的独立路由模块。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from excelmanus.logger import get_logger

logger = get_logger("api.rules")

router = APIRouter()


# ── 请求模型 ──────────────────────────────────────────

class RuleCreateRequest(BaseModel):
    content: str


class RuleUpdateRequest(BaseModel):
    content: str | None = None
    enabled: bool | None = None


# ── 辅助函数 ──────────────────────────────────────────

def _get_rules_manager():
    from excelmanus.api import _rules_manager
    return _rules_manager


def _get_api_persistent_memory():
    from excelmanus.api import _api_persistent_memory
    return _api_persistent_memory


async def _require_admin(request: Request):
    from excelmanus.api import _require_admin_if_auth_enabled
    return await _require_admin_if_auth_enabled(request)


async def _check_session_access(session_id: str, request: Request) -> bool:
    from excelmanus.api import _has_session_access
    return await _has_session_access(session_id, request)


def _get_isolation_user_id(request: Request):
    from excelmanus.api import _get_isolation_user_id
    return _get_isolation_user_id(request)


def _get_database():
    from excelmanus.api import _database
    return _database


def _get_config():
    from excelmanus.api import _config
    return _config


# ── Global Rules API ─────────────────────────────────

@router.get("/api/v1/rules")
async def list_global_rules() -> list[dict]:
    """列出全局自定义规则。"""
    rm = _get_rules_manager()
    if rm is None:
        return []
    return [
        {"id": r.id, "content": r.content, "enabled": r.enabled, "created_at": r.created_at}
        for r in rm.list_global_rules()
    ]


@router.post("/api/v1/rules")
async def create_global_rule(req: RuleCreateRequest, request: Request) -> dict:
    rm = _get_rules_manager()
    if rm is None:
        return JSONResponse(status_code=503, content={"detail": "规则功能未初始化"})  # type: ignore[return-value]
    guard_error = await _require_admin(request)
    if guard_error is not None:
        return guard_error  # type: ignore[return-value]
    if not req.content.strip():
        return JSONResponse(status_code=400, content={"detail": "规则内容不能为空"})  # type: ignore[return-value]
    r = rm.add_global_rule(req.content)
    return {"id": r.id, "content": r.content, "enabled": r.enabled, "created_at": r.created_at}


@router.patch("/api/v1/rules/{rule_id}")
async def update_global_rule(rule_id: str, req: RuleUpdateRequest, request: Request) -> dict:
    rm = _get_rules_manager()
    if rm is None:
        return JSONResponse(status_code=503, content={"detail": "规则功能未初始化"})  # type: ignore[return-value]
    guard_error = await _require_admin(request)
    if guard_error is not None:
        return guard_error  # type: ignore[return-value]
    r = rm.update_global_rule(rule_id, content=req.content, enabled=req.enabled)
    if r is None:
        return JSONResponse(status_code=404, content={"detail": "规则不存在"})  # type: ignore[return-value]
    return {"id": r.id, "content": r.content, "enabled": r.enabled, "created_at": r.created_at}


@router.delete("/api/v1/rules/{rule_id}")
async def delete_global_rule(rule_id: str, request: Request) -> dict:
    rm = _get_rules_manager()
    if rm is None:
        return JSONResponse(status_code=503, content={"detail": "规则功能未初始化"})  # type: ignore[return-value]
    guard_error = await _require_admin(request)
    if guard_error is not None:
        return guard_error  # type: ignore[return-value]
    ok = rm.delete_global_rule(rule_id)
    if not ok:
        return JSONResponse(status_code=404, content={"detail": "规则不存在"})  # type: ignore[return-value]
    return {"status": "deleted"}


# ── Session Rules API ────────────────────────────────

@router.get("/api/v1/sessions/{session_id}/rules")
async def list_session_rules(session_id: str, request: Request) -> list[dict]:
    rm = _get_rules_manager()
    if rm is None:
        return []
    if not await _check_session_access(session_id, request):
        return []
    return [
        {"id": r.id, "content": r.content, "enabled": r.enabled, "created_at": r.created_at}
        for r in rm.list_session_rules(session_id)
    ]


@router.post("/api/v1/sessions/{session_id}/rules")
async def create_session_rule(session_id: str, req: RuleCreateRequest, request: Request) -> dict:
    rm = _get_rules_manager()
    if rm is None:
        return JSONResponse(status_code=503, content={"detail": "规则功能未初始化"})  # type: ignore[return-value]
    if not await _check_session_access(session_id, request):
        return JSONResponse(status_code=404, content={"detail": "会话不存在"})  # type: ignore[return-value]
    if not req.content.strip():
        return JSONResponse(status_code=400, content={"detail": "规则内容不能为空"})  # type: ignore[return-value]
    r = rm.add_session_rule(session_id, req.content)
    if r is None:
        return JSONResponse(status_code=503, content={"detail": "会话级规则需要数据库支持"})  # type: ignore[return-value]
    return {"id": r.id, "content": r.content, "enabled": r.enabled, "created_at": r.created_at}


@router.patch("/api/v1/sessions/{session_id}/rules/{rule_id}")
async def update_session_rule(session_id: str, rule_id: str, req: RuleUpdateRequest, request: Request) -> dict:
    rm = _get_rules_manager()
    if rm is None:
        return JSONResponse(status_code=503, content={"detail": "规则功能未初始化"})  # type: ignore[return-value]
    if not await _check_session_access(session_id, request):
        return JSONResponse(status_code=404, content={"detail": "会话不存在"})  # type: ignore[return-value]
    r = rm.update_session_rule(session_id, rule_id, content=req.content, enabled=req.enabled)
    if r is None:
        return JSONResponse(status_code=404, content={"detail": "规则不存在"})  # type: ignore[return-value]
    return {"id": r.id, "content": r.content, "enabled": r.enabled, "created_at": r.created_at}


@router.delete("/api/v1/sessions/{session_id}/rules/{rule_id}")
async def delete_session_rule(session_id: str, rule_id: str, request: Request) -> dict:
    rm = _get_rules_manager()
    if rm is None:
        return JSONResponse(status_code=503, content={"detail": "规则功能未初始化"})  # type: ignore[return-value]
    if not await _check_session_access(session_id, request):
        return JSONResponse(status_code=404, content={"detail": "会话不存在"})  # type: ignore[return-value]
    ok = rm.delete_session_rule(session_id, rule_id)
    if not ok:
        return JSONResponse(status_code=404, content={"detail": "规则不存在"})  # type: ignore[return-value]
    return {"status": "deleted"}


# ── Memory API ───────────────────────────────────────

@router.get("/api/v1/memory")
async def list_memory_entries(request: Request, category: str | None = None) -> list[dict]:
    """列出持久记忆条目，可按类别筛选。"""
    from excelmanus.memory_models import MemoryCategory
    cat = None
    if category:
        try:
            cat = MemoryCategory(category)
        except ValueError:
            return JSONResponse(  # type: ignore[return-value]
                status_code=400,
                content={"detail": f"不支持的类别: {category}"},
            )

    user_id = _get_isolation_user_id(request)
    db = _get_database()
    cfg = _get_config()
    if user_id is not None and db is not None and cfg is not None:
        try:
            from excelmanus.user_scope import UserScope
            scope = UserScope.create(user_id, db, cfg.workspace_root, data_root=cfg.data_root)
            mem_store = scope.memory_store()
            if cat is not None:
                entries = mem_store.load_by_category(cat)
            else:
                entries = mem_store.load_all()
            return [
                {
                    "id": e.id,
                    "content": e.content,
                    "category": e.category.value,
                    "timestamp": e.timestamp.isoformat(),
                    "source": e.source,
                }
                for e in entries
            ]
        except Exception:
            logger.debug("从用户隔离数据库读取记忆失败", exc_info=True)
            return []

    pm = _get_api_persistent_memory()
    if pm is None:
        return []
    entries = pm.list_entries(cat)
    return [
        {
            "id": e.id,
            "content": e.content,
            "category": e.category.value,
            "timestamp": e.timestamp.isoformat(),
            "source": e.source,
        }
        for e in entries
    ]


@router.delete("/api/v1/memory/{entry_id}")
async def delete_memory_entry(entry_id: str, request: Request) -> dict:
    user_id = _get_isolation_user_id(request)
    db = _get_database()
    cfg = _get_config()
    if user_id is not None and db is not None and cfg is not None:
        try:
            from excelmanus.user_scope import UserScope
            scope = UserScope.create(user_id, db, cfg.workspace_root, data_root=cfg.data_root)
            mem_store = scope.memory_store()
            ok = mem_store.delete_entry(entry_id)
            if not ok:
                return JSONResponse(status_code=404, content={"detail": "记忆条目不存在"})  # type: ignore[return-value]
            return {"status": "deleted"}
        except Exception:
            logger.debug("从用户隔离数据库删除记忆失败", exc_info=True)
            return JSONResponse(status_code=500, content={"detail": "记忆删除失败"})  # type: ignore[return-value]

    pm = _get_api_persistent_memory()
    if pm is None:
        return JSONResponse(status_code=503, content={"detail": "记忆功能未启用"})  # type: ignore[return-value]
    ok = pm.delete_entry(entry_id)
    if not ok:
        return JSONResponse(status_code=404, content={"detail": "记忆条目不存在"})  # type: ignore[return-value]
    return {"status": "deleted"}
