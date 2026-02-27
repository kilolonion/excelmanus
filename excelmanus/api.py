"""API 服务模块：基于 FastAPI 的 REST API 服务。

端点：
- POST   /api/v1/chat                        对话接口（完整 JSON）
- POST   /api/v1/chat/stream                  对话接口（SSE 流式）
- POST   /api/v1/chat/subscribe                 SSE 重连（页面刷新后接入正在执行的任务）
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
- GET    /api/v1/files/excel                  返回 xlsx 文件二进制流（Univer 加载）
- GET    /api/v1/files/excel/snapshot         返回 Excel 轻量 JSON 快照（聊天内嵌预览）
- POST   /api/v1/files/excel/write            侧边面板编辑回写单元格
- DELETE /api/v1/sessions/{session_id}        删除会话
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
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
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
    ModelProfile,
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
    chat_mode: Literal["write", "read", "plan"] = "write"
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


class _SessionStreamState:
    """管理单个会话的 SSE 事件流状态，支持断连后缓冲与重连。

    当客户端断开时（如页面刷新），事件被缓冲到 event_buffer；
    新客户端通过 /chat/subscribe 重连时，先重放缓冲事件，再接收实时事件。
    """

    __slots__ = ("event_buffer", "subscriber_queue", "_buffer_limit")

    def __init__(self, buffer_limit: int = 500) -> None:
        self.event_buffer: list[ToolCallEvent] = []
        self.subscriber_queue: asyncio.Queue[ToolCallEvent | None] | None = None
        self._buffer_limit = buffer_limit

    def deliver(self, event: ToolCallEvent) -> None:
        """投递事件：有订阅者时入队，否则缓冲。"""
        q = self.subscriber_queue
        if q is not None:
            q.put_nowait(event)
        else:
            if len(self.event_buffer) < self._buffer_limit:
                self.event_buffer.append(event)

    def attach(self) -> asyncio.Queue[ToolCallEvent | None]:
        """创建新订阅者队列并附着。返回新队列。"""
        q: asyncio.Queue[ToolCallEvent | None] = asyncio.Queue()
        self.subscriber_queue = q
        return q

    def detach(self) -> None:
        """断开当前订阅者，后续事件进入缓冲。"""
        self.subscriber_queue = None

    def drain_buffer(self) -> list[ToolCallEvent]:
        """取出并清空缓冲区。"""
        buf = self.event_buffer
        self.event_buffer = []
        return buf


_session_stream_states: dict[str, _SessionStreamState] = {}
_rules_manager: Any = None  # 类型：RulesManager | None
_api_persistent_memory: Any = None  # 类型：PersistentMemory | None（API 层共享）
_database: Any = None  # 类型：Database | None
_config_store: Any = None  # 类型：ConfigStore | None
_file_registries: dict[str, Any] = {}  # workspace_root → FileRegistry（上传接入）
_router = APIRouter()


def _get_file_registry(workspace_root: str, *, user_id: str | None = None) -> Any:
    """获取或懒创建指定工作区的 FileRegistry 实例。

    ISO-3: 当 user_id 非空时使用 ScopedDatabase 创建 FileRegistry，
    确保 SQLite 模式下读写用户独立的 data.db。
    """
    if _database is None:
        return None
    cache_key = f"{workspace_root}:{user_id or ''}"
    reg = _file_registries.get(cache_key)
    if reg is not None:
        return reg
    try:
        from excelmanus.file_registry import FileRegistry
        db_conn = _database
        if user_id is not None and _config is not None:
            from excelmanus.user_scope import UserScope
            scope = UserScope.create(user_id, _database, _config.workspace_root)
            db_conn = scope.scoped_db
        reg = FileRegistry(db_conn, workspace_root)
        _file_registries[cache_key] = reg
        return reg
    except Exception:
        logger.debug("FileRegistry 创建失败 (%s)", workspace_root, exc_info=True)
        return None


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


def _get_isolation_user_id(request: Request) -> str | None:
    """Auth 启用时返回当前用户 ID，否则返回 None。

    Auth 即隔离：不再需要额外的 session_isolation_enabled 开关。
    """
    if not getattr(request.app.state, "auth_enabled", False):
        return None
    from excelmanus.auth.dependencies import extract_user_id
    return extract_user_id(request)


def _resolve_workspace(request: Request) -> "IsolatedWorkspace":
    """解析当前请求对应的隔离工作区。"""
    assert _config is not None
    from excelmanus.workspace import IsolatedWorkspace, SandboxConfig
    user_id = _get_isolation_user_id(request)
    auth_enabled = getattr(request.app.state, "auth_enabled", False)
    docker_enabled = getattr(request.app.state, "docker_sandbox_enabled", False)
    return IsolatedWorkspace.resolve(
        _config.workspace_root,
        user_id=user_id,
        auth_enabled=auth_enabled,
        sandbox_config=SandboxConfig(docker_enabled=docker_enabled),
        transaction_enabled=_config.backup_enabled,
    )


def _resolve_workspace_root(request: Request) -> str:
    """返回当前请求对应的工作区根目录路径字符串。"""
    return str(_resolve_workspace(request).root_dir)


async def _has_session_access(session_id: str, request: Request) -> bool:
    """会话存在且属于当前用户时返回 True（不存在/无权均返回 False）。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(request)
    try:
        await _session_manager.get_session_detail(session_id, user_id=user_id)
    except SessionNotFoundError:
        return False
    return True


async def _require_admin_if_auth_enabled(request: Request) -> JSONResponse | None:
    """认证启用时要求管理员权限；未启用认证时跳过校验。"""
    if not getattr(request.app.state, "auth_enabled", False):
        return None
    from excelmanus.auth.dependencies import get_current_user_from_request

    user = await get_current_user_from_request(request)
    if user.role != "admin":
        return _error_json_response(403, "需要管理员权限。")
    return None


def _get_user_allowed_models(request: Request) -> list[str]:
    """返回当前用户的 allowed_models 列表。空列表表示不限制。

    认证未启用时始终返回空列表（不限制）。
    """
    if not getattr(request.app.state, "auth_enabled", False):
        return []
    user_store = getattr(request.app.state, "user_store", None)
    if user_store is None:
        return []
    user_id = getattr(getattr(request, "state", None), "user_id", None)
    if not user_id:
        return []
    user = user_store.get_by_id(user_id)
    if user is None:
        return []
    import json as _json
    raw = getattr(user, "allowed_models", None)
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = _json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _public_route_fields(route_mode: str, skills_used: list[str], tool_scope: list[str]) -> tuple[str, list[str], list[str]]:
    """根据安全模式裁剪路由元信息。"""
    if _is_external_safe_mode():
        return "hidden", [], []
    return route_mode, skills_used, tool_scope


def _public_excel_path(path: str, *, safe_mode: bool) -> str:
    """将 Excel 路径规范化为前端可直接回传的形式。

    - 优先返回 workspace 相对路径（`./subdir/file.xlsx`），避免泄露绝对路径且保持可用。
    - 已被脱敏为 `<path>/name.xlsx` 的历史值，降级为 `./name.xlsx` 以兼容旧数据。
    - 对工作区外绝对路径，safe_mode 下仍保留脱敏行为。
    - 处理用户隔离架构的路径（`./users/{user_id}/xxx` → `./xxx`）。
    """
    raw = str(path or "").strip()
    if not raw:
        return ""

    if raw.startswith("<path>/"):
        basename = raw.removeprefix("<path>/").strip()
        return f"./{basename}" if basename else ""

    from pathlib import Path

    # 处理 ./users/{user_id}/ 格式的路径（用户隔离架构）
    if raw.startswith("./users/"):
        parts = raw.split("/", 3)  # ['.', 'users', '{user_id}', 'rest']
        if len(parts) >= 4:
            # 提取用户相对路径部分
            user_relative = parts[3]
            return f"./{user_relative}" if user_relative else ""

    if _config is not None:
        workspace = Path(_config.workspace_root).resolve()
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                rel = candidate.resolve().relative_to(workspace)
                rel_str = rel.as_posix()
                # 如果是 users/{user_id}/xxx 格式，提取用户相对路径
                if rel_str.startswith("users/"):
                    parts = rel_str.split("/", 2)
                    if len(parts) >= 3:
                        return f"./{parts[2]}"
                return f"./{rel_str}"
            except Exception:
                return sanitize_external_text(raw, max_len=500) if safe_mode else raw

    normalized = raw.replace("\\", "/")
    if normalized.startswith("./"):
        return normalized
    if normalized.startswith("/"):
        return sanitize_external_text(normalized, max_len=500) if safe_mode else normalized
    return f"./{normalized}"




def _persist_excel_event(session_id: str, event: ToolCallEvent) -> None:
    """将 EXCEL_DIFF / EXCEL_PREVIEW / FILES_CHANGED 事件持久化到 SQLite。"""
    if _session_manager is None or _session_manager.chat_history is None:
        return
    ch = _session_manager.chat_history
    try:
        if event.event_type == EventType.EXCEL_DIFF:
            pub_path = _public_excel_path(event.excel_file_path, safe_mode=False)
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
            pub_path = _public_excel_path(event.excel_file_path, safe_mode=False)
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
                pub = _public_excel_path(f, safe_mode=False)
                if pub:
                    ch.save_affected_file(session_id, pub)
    except Exception:
        logger.debug("持久化 Excel 事件失败", exc_info=True)


def _error_json_response(status_code: int, message: str) -> JSONResponse:
    """构建统一错误响应。"""
    error_id = str(uuid.uuid4())
    body = ErrorResponse(error=message, error_id=error_id)
    return JSONResponse(status_code=status_code, content=body.model_dump())


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


        })
    return rows


# ── Lifespan ──────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：初始化配置、注册 Skill、启动清理任务。"""
    global _session_manager, _tool_registry, _skillpack_loader, _skill_router, _config, _database

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
    auth_enabled = os.environ.get("EXCELMANUS_AUTH_ENABLED", "").strip().lower() in ("1", "true", "yes")
    need_database = _config.chat_history_enabled or auth_enabled
    if need_database:
        resolved_db_path = os.path.expanduser(
            _config.chat_history_db_path or _config.db_path
        )
        if _config.database_url:
            _database = Database(database_url=_config.database_url)
            logger.info("统一数据库已启用 (PostgreSQL)")
        else:
            _database = Database(resolved_db_path)
            logger.info("统一数据库已启用: %s", resolved_db_path)
        if _config.chat_history_enabled:
            from excelmanus.chat_history import ChatHistoryStore
            chat_history = ChatHistoryStore(_database)

    # 初始化 ConfigStore 并从 .env 迁移已有 profiles
    global _config_store
    if _database is not None:
        from excelmanus.stores.config_store import ConfigStore
        _config_store = ConfigStore(_database)
        existing = _config_store.list_profiles()
        if not existing:
            env_models_raw = os.environ.get("EXCELMANUS_MODELS", "")
            if env_models_raw:
                n = _config_store.import_profiles_from_env(
                    env_models_raw, _config.api_key, _config.base_url,
                )
                if n:
                    logger.info("已从 EXCELMANUS_MODELS 迁移 %d 个模型 profile 到数据库", n)
                    # 迁移完成后清除 .env 中的 EXCELMANUS_MODELS，避免双数据源
                    try:
                        env_path = _find_env_file()
                        lines = _read_env_file(env_path)
                        cleaned = [
                            ln for ln in lines
                            if not ln.strip().startswith("EXCELMANUS_MODELS=")
                        ]
                        if len(cleaned) != len(lines):
                            _write_env_file(env_path, cleaned)
                            logger.info("已从 .env 清除 EXCELMANUS_MODELS（数据库为唯一来源）")
                    except Exception:
                        logger.debug("清除 .env 中 EXCELMANUS_MODELS 失败", exc_info=True)
                    os.environ.pop("EXCELMANUS_MODELS", None)
        _sync_config_profiles_from_db()
        app.state.config_store = _config_store
        logger.info("ConfigStore 已初始化")

    # 初始化会话管理器
    # 始终创建共享 MCP 管理器并在启动时自动连接
    shared_mcp_manager = MCPManager(_config.workspace_root)
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
        user_store=None,  # 延迟设置，等 UserStore 初始化完成后注入
    )
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
            _api_persistent_memory = _PM(
                memory_dir=_config.memory_dir,
                auto_load_lines=_config.memory_auto_load_lines,
                database=_database,
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
    # 初始化认证模块（UserStore）+ 速率限制器
    _user_store = None
    if _database is not None:
        try:
            from excelmanus.auth.store import UserStore
            _user_store = UserStore(_database)
            app.state.user_store = _user_store
            app.state.auth_enabled = auth_enabled
            app.state.workspace_root = _config.workspace_root
            # Auth 即隔离：auth_enabled 时自动启用会话隔离，无需额外开关。
            app.state.session_isolation_enabled = auth_enabled
            # 将 UserStore 注入 SessionManager，使其能读取用户自定义 LLM 配置
            if _session_manager is not None:
                _session_manager.set_user_store(_user_store)
            if auth_enabled:
                logger.info("认证系统已启用")
            else:
                logger.info("认证系统已初始化（未强制启用，设置 EXCELMANUS_AUTH_ENABLED=true 启用）")
        except Exception:
            logger.warning("认证模块初始化失败", exc_info=True)

    if auth_enabled:
        try:
            from excelmanus.auth.rate_limit import RateLimiter
            app.state.rate_limiter = RateLimiter()
            logger.info("速率限制器已启用")
        except Exception:
            logger.warning("速率限制器初始化失败", exc_info=True)

    # Docker 沙盒开关：默认关闭，管理员可通过 config_kv 或环境变量开启
    _docker_env = os.environ.get(
        "EXCELMANUS_DOCKER_SANDBOX", ""
    ).strip().lower() in ("1", "true", "yes")
    if _config_store is not None:
        _docker_db = _config_store.get("docker_sandbox_enabled")
        if _docker_db:
            _docker_env = _docker_db.lower() in ("1", "true", "yes")
    if _docker_env:
        from excelmanus.security.docker_sandbox import is_docker_available, is_sandbox_image_ready
        if not is_docker_available():
            logger.warning("Docker 沙盒已开启但 Docker daemon 不可用，已自动关闭")
            _docker_env = False
        elif not is_sandbox_image_ready():
            logger.warning(
                "Docker 沙盒已开启但镜像 excelmanus-sandbox:latest 未找到，"
                "请运行 docker build -t excelmanus-sandbox:latest -f Dockerfile.sandbox ."
            )
        else:
            logger.info("Docker 沙盒已启用")
    app.state.docker_sandbox_enabled = _docker_env
    from excelmanus.tools.code_tools import init_docker_sandbox
    init_docker_sandbox(_docker_env)
    # 将 Docker 沙盒状态传播到会话管理器，用于按工作区注入。
    if _session_manager is not None:
        _session_manager.set_sandbox_docker_enabled(_docker_env)

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

    # 构建 CORS 允许来源列表：除了显式配置的来源外，自动添加本机 LAN IP
    # 的前端端口来源，以便浏览器直连后端的 SSE 流式请求不被 CORS 拦截。
    cors_origins = set(bootstrap_config.cors_allow_origins)
    try:
        import socket
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                cors_origins.add(f"http://{ip}:3000")
    except Exception:
        pass  # 无法获取 LAN IP 时静默降级

    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    # 认证中间件（在 CORS 之后运行，确保预检 OPTIONS 请求正常通过）
    from excelmanus.auth.middleware import AuthMiddleware
    application.add_middleware(AuthMiddleware)

    _register_exception_handlers(application)

    # 注册认证路由
    from excelmanus.auth.router import router as auth_router
    application.include_router(auth_router)

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

        guard = FileAccessGuard(engine._config.workspace_root)
        skill_loader = getattr(engine, "_skill_loader", None)
        if skill_loader is None:
            _router = getattr(engine, "_skill_router", None)
            if _router is not None:
                skill_loader = getattr(_router, "_loader", None)
        mcp_manager = getattr(engine, "_mcp_manager", None)
        resolver = MentionResolver(
            workspace_root=engine._config.workspace_root,
            guard=guard,
            skill_loader=skill_loader,
            mcp_manager=mcp_manager,
        )
        mention_contexts = await resolver.resolve(list(parse_result.mentions))
        return parse_result.display_text, mention_contexts
    except Exception:
        logger.debug("API 层 @ 提及解析失败，回退到原始消息", exc_info=True)
        return message, None


@_router.post("/api/v1/chat", response_model=ChatResponse, responses=_error_responses)
async def chat(request: ChatRequest, raw_request: Request) -> ChatResponse:
    """对话接口：创建或复用会话，将消息传递给 AgentEngine。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")

    from excelmanus.auth.dependencies import extract_user_id
    auth_user_id = extract_user_id(raw_request)
    isolation_user_id = _get_isolation_user_id(raw_request)

    rate_limiter = getattr(raw_request.app.state, "rate_limiter", None)
    if rate_limiter is not None and auth_user_id:
        rate_limiter.check_chat(auth_user_id)

    session_id, engine = await _session_manager.acquire_for_chat(
        request.session_id, user_id=isolation_user_id,
    )

    if _is_save_command(request.message):
        try:
            reply_text = await _handle_save_command(
                _session_manager, session_id, engine, request.message
            )
        finally:
            await _session_manager.release_for_chat(session_id)
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

        chat_result = _normalize_chat_result(
            await engine.chat(
                display_text,
                on_event=_on_event_sync,
                mention_contexts=mention_contexts,
                images=_serialize_images(request.images),
                chat_mode=request.chat_mode,
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
async def chat_stream(request: ChatRequest, raw_request: Request) -> StreamingResponse:
    """SSE 流式对话接口：实时推送思考过程、工具调用、最终回复。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")

    from excelmanus.auth.dependencies import extract_user_id
    auth_user_id = extract_user_id(raw_request)
    isolation_user_id = _get_isolation_user_id(raw_request)

    rate_limiter = getattr(raw_request.app.state, "rate_limiter", None)
    if rate_limiter is not None and auth_user_id:
        rate_limiter.check_chat(auth_user_id)

    session_id, engine = await _session_manager.acquire_for_chat(
        request.session_id, user_id=isolation_user_id,
    )

    if _is_save_command(request.message):
        async def _save_stream() -> AsyncIterator[str]:
            yield _sse_format("session_init", {"session_id": session_id})
            try:
                reply_text = await _handle_save_command(
                    _session_manager, session_id, engine, request.message
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
                error_id = str(uuid.uuid4())
                logger.error("SSE /save 流异常 [error_id=%s]: %s", error_id, exc, exc_info=True)
                yield _sse_format("error", {
                    "error": "保存对话失败，请联系管理员。",
                    "error_id": error_id,
                })
            else:
                yield _sse_format("done", {})
            finally:
                # R1: 必须释放 in_flight 锁，否则会话永久不可用
                await _session_manager.release_for_chat(session_id)

        return StreamingResponse(
            _save_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 用 _SessionStreamState 管理事件缓冲与订阅者队列，支持断连重连
    stream_state = _SessionStreamState()
    _session_stream_states[session_id] = stream_state
    event_queue = stream_state.attach()

    def _on_event(event: ToolCallEvent) -> None:
        """引擎事件回调：通过 stream_state 投递，同时持久化 Excel 事件。

        F4: 在 TOOL_CALL_END 事件后增量持久化消息到 SQLite，
        防止流式传输中途刷新或进程重启导致消息丢失。
        """
        stream_state.deliver(event)
        _persist_excel_event(session_id, event)
        if (
            event.event_type == EventType.TOOL_CALL_END
            and _session_manager is not None
        ):
            _session_manager.flush_messages_sync(session_id)

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

    async def _run_chat() -> ChatResult:
        """后台执行 engine.chat，完成后向队列发送结束信号。"""
        try:
            result = await engine.chat(
                display_text,
                on_event=_on_event,
                mention_contexts=mention_contexts,
                images=_serialize_images(request.images),
                chat_mode=request.chat_mode,
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
        # 立即推送初始进度，让前端在 chat 任务启动前就有视觉反馈
        yield _sse_format("pipeline_progress", {
            "stage": "initializing",
            "message": "正在初始化会话...",
        })

        # 启动 chat 任务
        chat_task = asyncio.create_task(_run_chat())
        _active_chat_tasks[session_id] = chat_task

        def _cleanup_active_chat_task(done_task: asyncio.Task[Any]) -> None:
            """后台 chat 任务完成后清理活跃任务映射与流状态。"""
            if _active_chat_tasks.get(session_id) is done_task:
                _active_chat_tasks.pop(session_id, None)
            # 任务完成后，清理 stream state（如果没有活跃订阅者）
            ss = _session_stream_states.get(session_id)
            if ss is not None and ss.subscriber_queue is None:
                _session_stream_states.pop(session_id, None)
            try:
                done_task.result()
            except asyncio.CancelledError:
                # 用户手动停止或 abort 取消属于预期路径。
                pass
            except Exception:
                logger.warning(
                    "会话 %s 的后台聊天任务异常结束",
                    session_id,
                    exc_info=True,
                )

        chat_task.add_done_callback(_cleanup_active_chat_task)
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
            # 记录已认证用户的 token 使用量
            if auth_user_id and chat_result.total_tokens > 0:
                try:
                    _user_store = getattr(raw_request.app.state, "user_store", None)
                    if _user_store is not None:
                        _user_store.record_token_usage(auth_user_id, chat_result.total_tokens)
                except Exception:
                    logger.debug("记录用户 token 用量失败", exc_info=True)
            yield _sse_format("done", {})

        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断开（如页面刷新）时允许 chat 在后台继续。
            # 分离订阅者，后续事件进入缓冲，供 /chat/subscribe 重连时重放。
            stream_state.detach()
            logger.info("会话 %s 的流式连接已断开，后台任务继续执行", session_id)
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
            stream_state.detach()
            if chat_task.done():
                if _active_chat_tasks.get(session_id) is chat_task:
                    _active_chat_tasks.pop(session_id, None)
                _session_stream_states.pop(session_id, None)
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


class BackupApplyRequest(BaseModel):
    """将备份文件应用回原文件。"""
    session_id: str
    files: list[str] | None = Field(default=None, description="要应用的原始文件路径列表。为空或 null 时应用全部。")


class BackupDiscardRequest(BaseModel):
    """丢弃备份文件。"""
    session_id: str
    files: list[str] | None = Field(default=None, description="要丢弃的原始文件路径列表。为空或 null 时丢弃全部。")


class RollbackRequest(BaseModel):
    """对话回退请求体。"""

    model_config = ConfigDict(extra="forbid")

    session_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    turn_index: int = Field(..., ge=0, description="目标用户轮次索引（0-indexed）")
    rollback_files: bool = Field(default=False, description="是否同时回滚文件变更")
    new_message: str | None = Field(default=None, description="替换该轮用户消息内容（可选）")
    resend_mode: bool = Field(default=False, description="重发模式：移除目标用户消息，调用方随后通过 /chat/stream 重新发送")


@_router.get("/api/v1/backup/list")
async def backup_list(session_id: str, request: Request) -> JSONResponse:
    """列出指定会话的待应用备份文件。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(request)
    engine = _session_manager.get_engine(session_id, user_id=user_id)
    if engine is None:
        return _error_json_response(404, f"会话 '{session_id}' 不存在或未加载。")
    tx = engine.transaction
    if tx is None:
        return JSONResponse(status_code=200, content={"files": [], "backup_enabled": False})
    staged = tx.list_staged()
    files = []
    for b in staged:
        bp = Path(b["backup"])
        files.append({
            "original_path": tx.to_relative(b["original"]),
            "backup_path": tx.to_relative(b["backup"]),
            "exists": b["exists"] == "True",
            "modified_at": bp.stat().st_mtime if bp.exists() else None,
        })
    return JSONResponse(status_code=200, content={"files": files, "backup_enabled": True})


@_router.post("/api/v1/backup/apply")
async def backup_apply(request: BackupApplyRequest, raw_request: Request) -> JSONResponse:
    """将备份副本应用回原始文件。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(raw_request)
    engine = _session_manager.get_engine(request.session_id, user_id=user_id)
    if engine is None:
        return _error_json_response(404, f"会话 '{request.session_id}' 不存在或未加载。")
    # W10: 防止在 agent 活跃写入期间 apply，避免文件竞态
    if await _session_manager.is_session_in_flight(request.session_id):
        return _error_json_response(409, "会话正在处理中，请等待完成后再应用备份。")
    tx = engine.transaction
    if tx is None:
        return _error_json_response(400, "该会话未启用备份模式。")
    if request.files:
        applied = []
        original_rel_paths: set[str] = set()
        for fp in request.files:
            result = tx.commit_one(fp)
            if result:
                applied.append({
                    "original": tx.to_relative(result["original"]),
                    "backup": tx.to_relative(result["backup"]),
                })
                original_rel_paths.add(tx.to_relative(result["original"]))
        if original_rel_paths:
            engine._approval.mark_non_undoable_for_paths(original_rel_paths)
        return JSONResponse(status_code=200, content={"status": "ok", "applied": applied, "count": len(applied)})
    else:
        raw_applied = tx.commit_all()
        applied = []
        original_rel_paths_all: set[str] = set()
        for a in raw_applied:
            applied.append({
                "original": tx.to_relative(a["original"]),
                "backup": tx.to_relative(a["backup"]),
            })
            original_rel_paths_all.add(tx.to_relative(a["original"]))
        if original_rel_paths_all:
            engine._approval.mark_non_undoable_for_paths(original_rel_paths_all)
        return JSONResponse(status_code=200, content={"status": "ok", "applied": applied, "count": len(applied)})


@_router.post("/api/v1/backup/discard")
async def backup_discard(request: BackupDiscardRequest, raw_request: Request) -> JSONResponse:
    """丢弃备份映射。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(raw_request)
    engine = _session_manager.get_engine(request.session_id, user_id=user_id)
    if engine is None:
        return _error_json_response(404, f"会话 '{request.session_id}' 不存在或未加载。")
    # R3: 防止在 agent 活跃写入期间 discard，避免删除正在被写入的 staged 文件
    if await _session_manager.is_session_in_flight(request.session_id):
        return _error_json_response(409, "会话正在处理中，请等待完成后再丢弃备份。")
    tx = engine.transaction
    if tx is None:
        return _error_json_response(400, "该会话未启用备份模式。")
    if request.files:
        count = 0
        for fp in request.files:
            if tx.rollback_one(fp):
                count += 1
        return JSONResponse(status_code=200, content={"status": "ok", "discarded": count})
    else:
        tx.rollback_all()
        return JSONResponse(status_code=200, content={"status": "ok", "discarded": "all"})


# ── 工作区事务别名（规范名称） ────────
# 委托到 backup_* 处理器以保持向后兼容。
# /backup/* 路径保留为废弃别名。

@_router.get("/api/v1/workspace/staged")
async def workspace_staged(session_id: str, request: Request) -> JSONResponse:
    return await backup_list(session_id, request)


@_router.post("/api/v1/workspace/commit")
async def workspace_commit(request: BackupApplyRequest, raw_request: Request) -> JSONResponse:
    return await backup_apply(request, raw_request)


@_router.post("/api/v1/workspace/rollback")
async def workspace_rollback(request: BackupDiscardRequest, raw_request: Request) -> JSONResponse:
    return await backup_discard(request, raw_request)


# ── 轮次 Checkpoint API ──────────────────────────────────


@_router.get("/api/v1/checkpoint/list")
async def checkpoint_list(session_id: str, request: Request) -> JSONResponse:
    """列出指定会话的轮次 checkpoint 时间线。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(request)
    engine = _session_manager.get_engine(session_id, user_id=user_id)
    if engine is None:
        return _error_json_response(404, f"会话 '{session_id}' 不存在或未加载。")
    if not engine.checkpoint_enabled:
        return JSONResponse(status_code=200, content={
            "checkpoints": [], "checkpoint_enabled": False,
        })
    _reg = engine.file_registry
    if _reg is None or not getattr(_reg, 'has_versions', False):
        return JSONResponse(status_code=200, content={
            "checkpoints": [], "checkpoint_enabled": True,
            "error": "FileRegistry not available",
        })
    cps = _reg.list_turn_checkpoints()
    items = []
    for cp in cps:
        items.append({
            "turn_number": cp.turn_number,
            "created_at": cp.created_at,
            "files_modified": cp.files_modified,
            "tool_names": cp.tool_names,
            "version_count": len(cp.version_ids),
        })
    return JSONResponse(status_code=200, content={
        "checkpoints": items, "checkpoint_enabled": True,
    })


class CheckpointRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    turn_number: int


@_router.post("/api/v1/checkpoint/rollback")
async def checkpoint_rollback(
    request: CheckpointRollbackRequest, raw_request: Request,
) -> JSONResponse:
    """回退到指定轮次之前的文件状态。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(raw_request)
    engine = _session_manager.get_engine(request.session_id, user_id=user_id)
    if engine is None:
        return _error_json_response(404, f"会话 '{request.session_id}' 不存在或未加载。")
    if not engine.checkpoint_enabled:
        return _error_json_response(400, "该会话未启用 checkpoint 模式。")
    if await _session_manager.is_session_in_flight(request.session_id):
        return _error_json_response(409, "会话正在处理中，请等待完成后再回退。")
    _reg = engine.file_registry
    if _reg is None or not getattr(_reg, 'has_versions', False):
        return _error_json_response(400, "FileRegistry not available for rollback.")
    restored = _reg.rollback_to_turn(request.turn_number)
    return JSONResponse(status_code=200, content={
        "status": "ok",
        "turn_number": request.turn_number,
        "restored_files": restored,
        "count": len(restored),
    })


class RollbackPreviewRequest(BaseModel):
    """回滚预览请求体。"""
    model_config = ConfigDict(extra="forbid")
    session_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    turn_index: int = Field(..., ge=0, description="目标用户轮次索引（0-indexed）")


@_router.post("/api/v1/chat/rollback/preview")
async def chat_rollback_preview(
    request: RollbackPreviewRequest, raw_request: Request,
) -> JSONResponse:
    """预览回滚到指定用户轮次后会影响的文件变更（不实际执行）。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(raw_request)
    engine = _session_manager.get_engine(request.session_id, user_id=user_id)
    if engine is None:
        return _error_json_response(404, f"会话 '{request.session_id}' 不存在或未加载。")
    try:
        preview = engine.rollback_preview(request.turn_index)
    except IndexError as exc:
        return _error_json_response(400, str(exc))
    return JSONResponse(status_code=200, content=preview)


@_router.post("/api/v1/chat/rollback")
async def chat_rollback(request: RollbackRequest, raw_request: Request) -> JSONResponse:
    """回退对话到指定用户轮次，可选回滚文件变更。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(raw_request)

    try:
        result = await _session_manager.rollback_session(
            request.session_id,
            request.turn_index,
            rollback_files=request.rollback_files,
            new_message=request.new_message,
            resend_mode=request.resend_mode,
            user_id=user_id,
        )
    except SessionNotFoundError as exc:
        return _error_json_response(404, str(exc))
    except IndexError as exc:
        return _error_json_response(400, str(exc))

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "removed_messages": result["removed_messages"],
            "file_rollback_results": result["file_rollback_results"],
            "turn_index": result["turn_index"],
        },
    )


@_router.get("/api/v1/chat/turns")
async def chat_turns(session_id: str, request: Request) -> JSONResponse:
    """列出指定会话的用户轮次摘要。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(request)
    engine = _session_manager.get_engine(session_id, user_id=user_id)
    if engine is None:
        return _error_json_response(404, f"会话 '{session_id}' 不存在或未加载。")

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
    skip_replay: bool = Field(
        default=False,
        description="跳过缓冲事件重放，仅接收实时新事件。"
        "前端已从后端加载了当前消息时使用，避免重复 block。",
    )


@_router.post("/api/v1/chat/subscribe", responses=_error_responses)
async def chat_subscribe(request: _SubscribeRequest, raw_request: Request) -> StreamingResponse:
    """SSE 重连端点：页面刷新后重新接入正在执行的聊天任务事件流。

    先重放断连期间缓冲的事件，再实时推送后续事件，直到任务完成。
    若任务已结束，返回 done 事件后关闭。
    """
    session_id = request.session_id

    if not await _has_session_access(session_id, raw_request):
        return _error_json_response(404, f"会话 '{session_id}' 不存在。")  # type: ignore[return-value]

    chat_task = _active_chat_tasks.get(session_id)
    stream_state = _session_stream_states.get(session_id)

    # 没有活跃任务或 stream_state → 返回即时 done
    if chat_task is None or chat_task.done() or stream_state is None:
        async def _done_stream() -> AsyncIterator[str]:
            yield _sse_format("session_init", {"session_id": session_id})
            yield _sse_format("subscribe_resume", {"status": "completed"})
            yield _sse_format("done", {})

        return StreamingResponse(
            _done_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 附着新的订阅者队列
    event_queue = stream_state.attach()
    # skip_replay=True 时仅保留 SSE-only 事件（thinking/iteration 等不进 SQLite，
    # 前端无法从后端消息恢复），丢弃可从后端持久化消息恢复的事件。
    _SSE_ONLY_EVENT_TYPES = {
        EventType.THINKING, EventType.THINKING_DELTA,
        EventType.ITERATION_START, EventType.RETRACT_THINKING,
    }
    if request.skip_replay:
        all_buffered = stream_state.drain_buffer()
        buffered_events = [e for e in all_buffered if e.event_type in _SSE_ONLY_EVENT_TYPES]
    else:
        buffered_events = stream_state.drain_buffer()

    safe_mode = _is_external_safe_mode()

    async def _subscribe_generator() -> AsyncIterator[str]:
        yield _sse_format("session_init", {"session_id": session_id})
        yield _sse_format("subscribe_resume", {
            "status": "reconnected",
            "buffered_count": len(buffered_events),
        })

        # 重放缓冲事件（skip_replay 时为空）
        for event in buffered_events:
            sse = _sse_event_to_sse(event, safe_mode=safe_mode)
            if sse is not None:
                yield sse

        # 实时消费后续事件
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
                wait_set: set[asyncio.Task[Any]] = {queue_get_task}
                if not chat_task.done():
                    wait_set.add(chat_task)
                done_set, _ = await asyncio.wait(
                    wait_set,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if queue_get_task in done_set:
                    event = queue_get_task.result()
                    if event is not None:
                        sse = _sse_event_to_sse(event, safe_mode=safe_mode)
                        if sse is not None:
                            yield sse
                    if chat_task.done():
                        queue_get_task = None
                    else:
                        queue_get_task = asyncio.create_task(event_queue.get())

                if chat_task.done() and (queue_get_task is None or queue_get_task not in done_set):
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

            # chat 任务完成：获取结果并发送 reply + done
            if not chat_task.cancelled():
                try:
                    chat_result = chat_task.result()
                    if _session_manager is None:
                        return  # 服务未初始化，静默退出
                    engine = _session_manager.get_engine(session_id)
                    if engine is not None:
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
                except (asyncio.CancelledError, Exception):
                    pass
            yield _sse_format("done", {})

        except (asyncio.CancelledError, GeneratorExit):
            stream_state.detach()
            logger.info("会话 %s 的重连流式连接再次断开", session_id)
        except Exception as exc:
            error_id = str(uuid.uuid4())
            logger.error(
                "SSE subscribe 流异常 [error_id=%s]: %s", error_id, exc, exc_info=True
            )
            yield _sse_format("error", {
                "error": "服务内部错误，请联系管理员。",
                "error_id": error_id,
            })
        finally:
            stream_state.detach()
            if chat_task.done():
                _session_stream_states.pop(session_id, None)
            await _cancel_task(queue_get_task)

    return StreamingResponse(
        _subscribe_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@_router.post("/api/v1/chat/abort")
async def chat_abort(request: AbortRequest, raw_request: Request) -> JSONResponse:
    """终止指定会话的活跃聊天任务。"""
    if not await _has_session_access(request.session_id, raw_request):
        return JSONResponse(
            status_code=200,
            content={"status": "no_active_task"},
        )
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


@_router.post("/api/v1/chat/{session_id}/answer", responses=_error_responses)
async def chat_answer(
    session_id: str,
    request: AnswerQuestionRequest,
    raw_request: Request,
) -> JSONResponse:
    """提交 ask_user 问题的回答，resolve 阻塞中的 Future。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    if not await _has_session_access(session_id, raw_request):
        return JSONResponse(status_code=403, content={"error": "无权访问此会话"})

    engine = _session_manager.get_engine(session_id)
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


@_router.post("/api/v1/chat/{session_id}/approve", responses=_error_responses)
async def chat_approve(
    session_id: str,
    request: ApproveRequest,
    raw_request: Request,
) -> JSONResponse:
    """提交审批决策，resolve 阻塞中的 Future。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    if not await _has_session_access(session_id, raw_request):
        return JSONResponse(status_code=403, content={"error": "无权访问此会话"})

    engine = _session_manager.get_engine(session_id)
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
        EventType.SUBAGENT_TOOL_START,
        EventType.SUBAGENT_TOOL_END,
        EventType.PENDING_APPROVAL,  # 新增：safe_mode 下过滤审批事件
        EventType.APPROVAL_RESOLVED,
        EventType.RETRACT_THINKING,
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
        EventType.SUBAGENT_TOOL_START: "subagent_tool_start",
        EventType.SUBAGENT_TOOL_END: "subagent_tool_end",
        EventType.USER_QUESTION: "user_question",
        EventType.THINKING_DELTA: "thinking_delta",
        EventType.TEXT_DELTA: "text_delta",
        EventType.TOOL_CALL_ARGS_DELTA: "tool_call_args_delta",
        EventType.EXCEL_PREVIEW: "excel_preview",
        EventType.EXCEL_DIFF: "excel_diff",
        EventType.TEXT_DIFF: "text_diff",
        EventType.FILES_CHANGED: "files_changed",
        EventType.PIPELINE_PROGRESS: "pipeline_progress",
        EventType.MEMORY_EXTRACTED: "memory_extracted",
        EventType.FILE_DOWNLOAD: "file_download",
        EventType.VERIFICATION_REPORT: "verification_report",
        EventType.RETRACT_THINKING: "retract_thinking",
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
    elif event.event_type == EventType.SUBAGENT_TOOL_START:
        data = {
            "conversation_id": sanitize_external_text(
                event.subagent_conversation_id,
                max_len=120,
            ),
            "tool_name": event.tool_name,
            "arguments": sanitize_external_data(
                event.arguments if isinstance(event.arguments, dict) else {},
                max_len=500,
            ),
            "tool_index": event.subagent_tool_index,
        }
    elif event.event_type == EventType.SUBAGENT_TOOL_END:
        data = {
            "conversation_id": sanitize_external_text(
                event.subagent_conversation_id,
                max_len=120,
            ),
            "tool_name": event.tool_name,
            "success": event.success,
            "result": sanitize_external_text(
                event.result[:300] if event.result else "",
                max_len=300,
            ),
            "error": (
                sanitize_external_text(event.error, max_len=200)
                if event.error
                else None
            ),
            "tool_index": event.subagent_tool_index,
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
    elif event.event_type == EventType.TOOL_CALL_ARGS_DELTA:
        data = {
            "tool_call_id": event.tool_call_id,
            "tool_name": event.tool_name,
            "args_delta": event.args_delta,
        }
    elif event.event_type == EventType.PENDING_APPROVAL:
        data = {
            "approval_id": sanitize_external_text(event.approval_id or "", max_len=120),
            "approval_tool_name": sanitize_external_text(event.approval_tool_name or "", max_len=100),
            "tool_call_id": sanitize_external_text(event.tool_call_id or "", max_len=160),
            "risk_level": sanitize_external_text(event.approval_risk_level or "high", max_len=20),
            "args_summary": sanitize_external_data(
                event.approval_args_summary if isinstance(event.approval_args_summary, dict) else {},
                max_len=1000,
            ),
        }
    elif event.event_type == EventType.APPROVAL_RESOLVED:
        data = {
            "approval_id": sanitize_external_text(event.approval_id or "", max_len=120),
            "approval_tool_name": sanitize_external_text(event.approval_tool_name or "", max_len=100),
            "tool_call_id": sanitize_external_text(event.tool_call_id or "", max_len=160),
            "result": sanitize_external_text(event.result or "", max_len=2000),
            "success": event.success,
            "undoable": event.approval_undoable,
            "has_changes": event.approval_has_changes,
        }
        sse_type = "approval_resolved"
    elif event.event_type == EventType.EXCEL_PREVIEW:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "file_path": _public_excel_path(event.excel_file_path, safe_mode=safe_mode),
            "sheet": sanitize_external_text(event.excel_sheet, max_len=100),
            "columns": event.excel_columns[:100],
            "rows": event.excel_rows[:50],
            "total_rows": event.excel_total_rows,
            "truncated": event.excel_truncated,
            "cell_styles": event.excel_cell_styles[:51] if event.excel_cell_styles else [],
            "merge_ranges": event.excel_merge_ranges[:200] if event.excel_merge_ranges else [],
            "metadata_hints": event.excel_metadata_hints[:20] if event.excel_metadata_hints else [],
        }
    elif event.event_type == EventType.EXCEL_DIFF:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "file_path": _public_excel_path(event.excel_file_path, safe_mode=safe_mode),
            "sheet": sanitize_external_text(event.excel_sheet, max_len=100),
            "affected_range": sanitize_external_text(event.excel_affected_range, max_len=50),
            "changes": event.excel_changes[:200],
            "merge_ranges": event.excel_merge_ranges[:200] if event.excel_merge_ranges else [],
            "old_merge_ranges": event.excel_old_merge_ranges[:200] if event.excel_old_merge_ranges else [],
            "metadata_hints": event.excel_metadata_hints[:20] if event.excel_metadata_hints else [],
        }
    elif event.event_type == EventType.TEXT_DIFF:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "file_path": sanitize_external_text(event.text_diff_file_path, max_len=500),
            "hunks": event.text_diff_hunks[:300],
            "additions": event.text_diff_additions,
            "deletions": event.text_diff_deletions,
            "truncated": event.text_diff_truncated,
        }
    elif event.event_type == EventType.FILES_CHANGED:
        data = {
            "files": [
                _public_excel_path(f, safe_mode=safe_mode)
                for f in (event.changed_files or [])[:50]
            ],
        }
    elif event.event_type == EventType.PIPELINE_PROGRESS:
        data = {
            "stage": sanitize_external_text(event.pipeline_stage, max_len=60),
            "message": sanitize_external_text(event.pipeline_message, max_len=200),
        }
    elif event.event_type == EventType.MEMORY_EXTRACTED:
        data = {
            "entries": (event.memory_entries or [])[:50],
            "trigger": event.memory_trigger or "session_end",
            "count": len(event.memory_entries or []),
        }
    elif event.event_type == EventType.FILE_DOWNLOAD:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "file_path": _public_excel_path(event.download_file_path, safe_mode=safe_mode),
            "filename": sanitize_external_text(event.download_filename, max_len=260),
            "description": sanitize_external_text(event.download_description, max_len=500),
        }
    elif event.event_type == EventType.VERIFICATION_REPORT:
        data = {
            "verdict": event.verification_verdict,
            "confidence": event.verification_confidence,
            "checks": event.verification_checks[:10],
            "issues": event.verification_issues[:10],
            "mode": event.verification_mode,
        }
    elif event.event_type == EventType.RETRACT_THINKING:
        data = {"iteration": event.iteration}
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
async def delete_session(session_id: str, request: Request) -> dict:
    """删除指定会话并释放资源。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(request)

    deleted = await _session_manager.delete(session_id, user_id=user_id)
    if not deleted:
        raise SessionNotFoundError(f"会话 '{session_id}' 不存在。")
    return {"status": "ok", "session_id": session_id}


@_router.patch("/api/v1/sessions/{session_id}/archive", responses={
    404: _error_responses[404],
    500: _error_responses[500],
})
async def archive_session(session_id: str, request: Request) -> dict:
    """归档或取消归档会话。

    请求体: {"archive": true}  归档
    请求体: {"archive": false} 取消归档
    """
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")

    body = await request.json()
    archive = body.get("archive", True)
    user_id = _get_isolation_user_id(request)

    updated = await _session_manager.archive_session(
        session_id, archive=archive, user_id=user_id
    )
    if not updated:
        raise SessionNotFoundError(f"会话 '{session_id}' 不存在。")
    return {
        "status": "ok",
        "session_id": session_id,
        "archived": archive,
    }


# ── Approvals / Undo ─────────────────────────────────────


@_router.get("/api/v1/approvals")
async def list_approvals(request: Request) -> JSONResponse:
    """列出已执行的审批记录（支持分页与筛选）。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")

    limit = int(request.query_params.get("limit", "50"))
    undoable_only = request.query_params.get("undoable_only", "false").lower() == "true"
    session_id = request.query_params.get("session_id")
    if not session_id:
        return _error_json_response(400, "缺少 session_id 参数。")
    if not await _has_session_access(session_id, request):
        return _error_json_response(404, "会话不存在。")
    user_id = _get_isolation_user_id(request)
    engine = _session_manager.get_engine(session_id, user_id=user_id)

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
        }
        if not _is_external_safe_mode():
            item["arguments"] = sanitize_external_data(rec.arguments)
            item["changes"] = [
                {"path": c.path, "before_exists": c.before_exists, "after_exists": c.after_exists}
                for c in (rec.changes or [])
            ]
        items.append(item)
    return JSONResponse(content={"approvals": items})


@_router.post("/api/v1/approvals/{approval_id}/undo")
async def undo_approval(approval_id: str, request: Request) -> JSONResponse:
    """回滚指定审批操作。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")

    session_id = request.query_params.get("session_id")
    if not session_id:
        return _error_json_response(400, "缺少 session_id 参数。")
    if not await _has_session_access(session_id, request):
        return _error_json_response(404, "会话不存在。")
    user_id = _get_isolation_user_id(request)
    engine = await _session_manager.get_or_restore_engine(session_id, user_id=user_id)

    if engine is None:
        return _error_json_response(404, "没有活跃会话。")

    result_msg = engine._approval.undo(approval_id)
    success = "已回滚" in result_msg
    return JSONResponse(content={
        "status": "ok" if success else "error",
        "message": result_msg,
        "approval_id": approval_id,
    })


# ── Excel 预览 API ────────────────────────────────────────


def _resolve_excel_path(
    path: str,
    session_id: str | None = None,
    *,
    workspace_root: str | None = None,
    user_id: str | None = None,
) -> str | None:
    """将相对/绝对路径解析为安全的绝对路径。

    当提供 workspace_root 时基于该目录解析（多用户隔离），否则回退到全局配置。
    当提供 session_id 且该会话启用了备份模式时，自动将路径重定向到备份副本。

    支持的路径形式：
    - 相对路径: ``./foo.xlsx``, ``foo.xlsx``, ``subdir/foo.xlsx``
    - 绝对路径: 仅当位于 workspace 内时允许
    """
    if _config is None:
        return None
    from pathlib import Path

    ws_root = workspace_root or _config.workspace_root
    workspace = Path(ws_root).resolve()
    workspace_str = str(workspace)

    resolved: str | None = None

    candidate = Path(path)
    if candidate.is_absolute():
        abs_resolved = candidate.resolve()
        if str(abs_resolved).startswith(workspace_str) and abs_resolved.is_file():
            resolved = str(abs_resolved)
    else:
        target = (workspace / path).resolve()
        if str(target).startswith(workspace_str) and target.is_file():
            resolved = str(target)

    if resolved is None:
        return None

    if session_id and _session_manager is not None:
        engine = _session_manager.get_engine(session_id, user_id=user_id)
        if engine is not None and engine.backup_enabled:
            tx = engine.transaction
            if tx is not None:
                try:
                    staged_resolved = tx.resolve_read(resolved)
                    if staged_resolved != resolved and Path(staged_resolved).is_file():
                        return staged_resolved
                except ValueError:
                    pass

    return resolved


@_router.get("/api/v1/files/excel/list")
async def list_excel_files(request: Request) -> JSONResponse:
    """扫描当前用户 workspace 中所有 Excel 文件，返回路径列表。"""
    assert _config is not None, "服务未初始化"
    from pathlib import Path as _Path

    workspace = _Path(_resolve_workspace_root(request)).resolve()
    excel_exts = {".xlsx", ".xls", ".csv"}
    skip_dirs = {"node_modules", "__pycache__", ".venv", ".git", ".next"}
    results: list[dict[str, Any]] = []

    import re
    _upload_prefix_re = re.compile(r"^[0-9a-f]{8}_")
    uploads_dir = str(workspace / "uploads")
    backups_dir = str((workspace / "outputs" / "backups").resolve())
    audits_dir = str((workspace / "outputs" / "audits").resolve())

    for root, dirs, filenames in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        root_resolved = str(_Path(root).resolve())
        if root_resolved.startswith(backups_dir) or root_resolved.startswith(audits_dir):
            dirs.clear()
            continue
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in excel_exts:
                continue
            full = os.path.join(root, fname)
            try:
                rel = os.path.relpath(full, workspace)
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            display_name = fname
            if root == uploads_dir and _upload_prefix_re.match(fname):
                display_name = fname[9:]
            results.append({
                "path": f"./{rel}",
                "filename": display_name,
                "modified_at": mtime,
            })

    results.sort(key=lambda x: x["modified_at"], reverse=True)
    return JSONResponse(content={"files": results})


_MAX_WORKSPACE_FILES = 2000

# 工作区内部目录，不向前端暴露
_WORKSPACE_HIDDEN_DIRS = frozenset({
    ".tmp", ".staging", ".versions", ".git", ".venv",
    "__pycache__", "node_modules",
})

# uploads/ 下 hash 前缀正则（8位hex + 下划线）
_UPLOAD_PREFIX_RE = re.compile(r"^[0-9a-f]{8}_")


@_router.get("/api/v1/files/workspace/list")
async def list_workspace_files(request: Request) -> JSONResponse:
    """扫描用户工作区根目录中的文件与文件夹（用于文件树视图）。

    扫描范围：完整工作区根目录（包含 agent 创建的文件、uploads/ 等）。
    排除：隐藏目录/文件、内部目录（.tmp/.staging/scripts/temp 等）。
    """
    assert _config is not None, "服务未初始化"

    ws = _resolve_workspace(request)
    ws_root = str(ws.root_dir)  # 完整工作区根目录

    results: list[dict[str, Any]] = []
    for root, dirs, filenames in os.walk(ws_root):
        # 统一使用 / 分隔符（兼容 Windows）
        rel_root = os.path.relpath(root, ws_root).replace("\\", "/")

        # 过滤隐藏目录与内部目录
        dirs[:] = sorted(
            d for d in dirs
            if not d.startswith(".")
            and d not in _WORKSPACE_HIDDEN_DIRS
            # scripts/temp 是 run_code 临时脚本目录，不展示
            and not (rel_root == "scripts" and d == "temp")
            # outputs/backups 和 outputs/audits 是内部备份/审计目录
            and not (rel_root == "outputs" and d in ("backups", "audits"))
        )

        for dname in dirs:
            full = os.path.join(root, dname)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            rel = f"{rel_root}/{dname}" if rel_root != "." else dname
            results.append({
                "path": rel,
                "filename": dname,
                "modified_at": mtime,
                "is_dir": True,
            })
        for fname in sorted(filenames):
            if fname.startswith(".") or fname.startswith("_rc_") or fname.startswith("_sw_"):
                continue
            full = os.path.join(root, fname)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            display_name = fname
            # uploads/ 下的 hash 前缀文件显示原始名
            if rel_root == "uploads" or (rel_root != "." and rel_root.startswith("uploads/")):
                if _UPLOAD_PREFIX_RE.match(fname):
                    display_name = fname[9:]
            rel = f"{rel_root}/{fname}" if rel_root != "." else fname
            results.append({
                "path": rel,
                "filename": display_name,
                "modified_at": mtime,
                "is_dir": False,
            })
            if len(results) >= _MAX_WORKSPACE_FILES:
                break
        if len(results) >= _MAX_WORKSPACE_FILES:
            break

    results.sort(key=lambda x: (not x["is_dir"], x["path"].lower()))
    return JSONResponse(content={"files": results, "truncated": len(results) >= _MAX_WORKSPACE_FILES})


@_router.get("/api/v1/files/registry")
async def get_file_registry(request: Request) -> JSONResponse:
    """返回 FileRegistry 全量文件列表 + 可选事件历史。

    Query params:
      - include_deleted: bool (default false) — 是否包含已软删除的文件
      - include_events: bool (default false) — 是否附带每个文件的事件历史
      - file_id: str (optional) — 仅返回指定文件及其事件/谱系
    """
    assert _config is not None, "服务未初始化"

    ws_root = _resolve_workspace_root(request)
    _iso_uid = _get_isolation_user_id(request)
    registry = _get_file_registry(ws_root, user_id=_iso_uid)
    if registry is None:
        return JSONResponse(content={"files": [], "total": 0})

    include_deleted = request.query_params.get("include_deleted", "").lower() in ("1", "true")
    include_events = request.query_params.get("include_events", "").lower() in ("1", "true")
    file_id = request.query_params.get("file_id", "").strip()

    def _event_to_dict(evt: Any) -> dict[str, Any]:
        return {
            "id": evt.id,
            "file_id": evt.file_id,
            "event_type": evt.event_type,
            "session_id": evt.session_id,
            "turn": evt.turn,
            "tool_name": evt.tool_name,
            "details": evt.details,
            "created_at": evt.created_at,
        }

    # 单文件查询模式
    if file_id:
        entry = registry.get_by_id(file_id)
        if entry is None:
            # 退而求其次：按路径查
            entry = registry.get_by_path(file_id)
        if entry is None:
            return _error_json_response(404, f"文件未找到: {file_id}")
        file_dict = entry.to_dict()
        if include_events:
            file_dict["events"] = [_event_to_dict(e) for e in registry.get_events(entry.id)]
        children = registry.get_children(entry.id)
        file_dict["children"] = [c.to_dict() for c in children]
        lineage = registry.get_lineage(entry.id)
        file_dict["lineage"] = [a.to_dict() for a in lineage]
        return JSONResponse(content={"file": file_dict})

    # 全量列表模式
    entries = registry.list_all(include_deleted=include_deleted)
    files: list[dict[str, Any]] = []
    for entry in entries:
        d = entry.to_dict()
        if include_events:
            d["events"] = [_event_to_dict(e) for e in registry.get_events(entry.id)]
        files.append(d)

    return JSONResponse(content={"files": files, "total": len(files)})


@_router.get("/api/v1/files/excel")
async def get_excel_file(request: Request) -> StreamingResponse:
    """返回 xlsx 文件二进制流供前端 Univer 加载。"""
    assert _config is not None, "服务未初始化"

    path = request.query_params.get("path", "")
    session_id = request.query_params.get("session_id")
    if not path:
        return _error_json_response(400, "缺少 path 参数")  # type: ignore[return-value]

    ws_root = _resolve_workspace_root(request)
    _iso_uid = _get_isolation_user_id(request)
    resolved = _resolve_excel_path(path, session_id, workspace_root=ws_root, user_id=_iso_uid)
    if resolved is None:
        return _error_json_response(404, f"文件不存在或路径非法: {path}")  # type: ignore[return-value]

    from pathlib import Path as _Path

    file_path = _Path(resolved)
    suffix = file_path.suffix.lower()
    if suffix not in {".xlsx", ".xls", ".csv"}:
        return _error_json_response(400, f"不支持的文件格式: {suffix}")  # type: ignore[return-value]

    content_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if suffix == ".xlsx"
        else "application/vnd.ms-excel" if suffix == ".xls"
        else "text/csv"
    )

    def _iter_file():
        with open(resolved, "rb") as f:  # type: ignore[arg-type]
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        _iter_file(),
        media_type=content_type,
        headers={"Content-Disposition": _make_content_disposition(file_path.name)},
    )


@_router.get("/api/v1/files/spec")
async def get_spec_file(request: Request) -> JSONResponse:
    """返回 ReplicaSpec JSON 文件内容（供 VLM pipeline 迷你表格预览）。"""
    assert _config is not None, "服务未初始化"

    path = request.query_params.get("path", "")
    if not path:
        return _error_json_response(400, "缺少 path 参数")  # type: ignore[return-value]

    from pathlib import Path as _Path

    ws_root = _resolve_workspace_root(request)
    file_path = _Path(path)
    if not file_path.is_absolute():
        file_path = _Path(ws_root) / file_path

    if not file_path.is_file() or file_path.suffix.lower() != ".json":
        return _error_json_response(404, f"Spec 文件不存在: {path}")  # type: ignore[return-value]

    import json as _json

    try:
        content = file_path.read_text(encoding="utf-8")
        data = _json.loads(content)
    except Exception as exc:
        return _error_json_response(500, f"读取 spec 失败: {exc}")  # type: ignore[return-value]

    return JSONResponse(content=data)


@_router.get("/api/v1/files/image")
async def get_image_file(request: Request) -> StreamingResponse:
    """返回 workspace 内图片文件的二进制流（供 VLM pipeline 原图预览）。"""
    assert _config is not None, "服务未初始化"

    path = request.query_params.get("path", "")
    if not path:
        return _error_json_response(400, "缺少 path 参数")  # type: ignore[return-value]

    from pathlib import Path as _Path
    import mimetypes

    ws_root = _resolve_workspace_root(request)
    file_path = _Path(path)
    if not file_path.is_absolute():
        file_path = _Path(ws_root) / file_path

    _IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}
    if not file_path.is_file() or file_path.suffix.lower() not in _IMAGE_EXTENSIONS:
        return _error_json_response(404, f"图片文件不存在: {path}")  # type: ignore[return-value]

    content_type = mimetypes.guess_type(file_path.name)[0] or "image/png"

    def _iter_image():
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        _iter_image(),
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@_router.get("/api/v1/files/download")
async def download_file(request: Request) -> StreamingResponse:
    """通用文件下载：返回 workspace 内任意文件的二进制流。"""
    assert _config is not None, "服务未初始化"

    path = request.query_params.get("path", "")
    session_id = request.query_params.get("session_id")
    if not path:
        return _error_json_response(400, "缺少 path 参数")  # type: ignore[return-value]

    ws_root = _resolve_workspace_root(request)
    _iso_uid = _get_isolation_user_id(request)
    resolved = _resolve_excel_path(path, session_id, workspace_root=ws_root, user_id=_iso_uid)
    if resolved is None:
        return _error_json_response(404, f"文件不存在或路径非法: {path}")  # type: ignore[return-value]

    from pathlib import Path as _Path
    import mimetypes

    file_path = _Path(resolved)
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

    def _iter_file():
        with open(resolved, "rb") as f:  # type: ignore[arg-type]
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        _iter_file(),
        media_type=content_type,
        headers={"Content-Disposition": _make_content_disposition(file_path.name)},
    )


@_router.get("/api/v1/files/excel/snapshot")
async def get_excel_snapshot(request: Request) -> JSONResponse:
    """返回 Excel 文件的轻量 JSON 快照（供聊天内嵌预览）。

    参数:
      - path: 文件路径
      - sheet: 指定工作表名（可选）
      - max_rows: 最大行数（默认 50）
      - session_id: 会话 ID（可选）
      - all_sheets: 设为 1 时一次返回所有工作表快照（减少 HTTP 往返）
    """
    assert _config is not None, "服务未初始化"

    path = request.query_params.get("path", "")
    sheet = request.query_params.get("sheet")
    max_rows = int(request.query_params.get("max_rows", "50"))
    session_id = request.query_params.get("session_id")
    all_sheets = request.query_params.get("all_sheets", "").strip() in ("1", "true")
    with_styles = request.query_params.get("with_styles", "1").strip() in ("1", "true")

    if not path:
        return _error_json_response(400, "缺少 path 参数")

    ws_root = _resolve_workspace_root(request)
    _iso_uid = _get_isolation_user_id(request)
    resolved = _resolve_excel_path(path, session_id, workspace_root=ws_root, user_id=_iso_uid)
    if resolved is None:
        return _error_json_response(404, f"文件不存在或路径非法: {path}")

    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter

        from excelmanus.tools._style_extract import extract_cell_style as _extract_cell_style

        wb = load_workbook(resolved, data_only=True, read_only=not with_styles)
        sheet_names = wb.sheetnames

        def _read_sheet(ws_obj: Any) -> dict:
            """读取单个工作表并返回快照 dict。"""
            s_total_rows = ws_obj.max_row or 0
            s_total_cols = ws_obj.max_column or 0
            s_headers: list[str] = []
            s_col_letters: list[str] = []
            for c in range(1, min(s_total_cols + 1, 101)):
                s_col_letters.append(get_column_letter(c))
                cell_val = ws_obj.cell(row=1, column=c).value
                s_headers.append(str(cell_val) if cell_val is not None else "")
            s_rows: list[list[Any]] = []
            s_row_limit = min(max_rows, s_total_rows, 200)
            for r in range(2, s_row_limit + 2):
                if r > s_total_rows:
                    break
                row_data: list[Any] = []
                for c in range(1, min(s_total_cols + 1, 101)):
                    val = ws_obj.cell(row=r, column=c).value
                    if val is None:
                        row_data.append(None)
                    elif isinstance(val, (int, float, bool)):
                        row_data.append(val)
                    else:
                        row_data.append(str(val))
                s_rows.append(row_data)

            result: dict[str, Any] = {
                "sheet": ws_obj.title,
                "shape": {"rows": s_total_rows, "columns": s_total_cols},
                "column_letters": s_col_letters,
                "headers": s_headers,
                "rows": s_rows,
                "total_rows": s_total_rows,
                "truncated": s_total_rows > s_row_limit,
            }

            # 提取样式（仅 with_styles=True 时）
            if with_styles:
                cell_styles: dict[str, dict] = {}
                merged: list[dict] = []
                for r in range(1, s_row_limit + 2):
                    if r > s_total_rows:
                        break
                    for c in range(1, min(s_total_cols + 1, 101)):
                        cell_obj = ws_obj.cell(row=r, column=c)
                        style = _extract_cell_style(cell_obj)
                        if style:
                            cell_styles[f"{r-1},{c-1}"] = style
                # 合并单元格
                try:
                    for merge_range in ws_obj.merged_cells.ranges:
                        merged.append({
                            "startRow": merge_range.min_row - 1,
                            "startColumn": merge_range.min_col - 1,
                            "endRow": merge_range.max_row - 1,
                            "endColumn": merge_range.max_col - 1,
                        })
                except Exception:
                    pass
                # 列宽
                col_widths: dict[str, float] = {}
                try:
                    for col_letter, dim in ws_obj.column_dimensions.items():
                        if dim.width and dim.width != 8.43:  # 默认宽度
                            col_idx = 0
                            for i, ch in enumerate(reversed(col_letter.upper())):
                                col_idx += (ord(ch) - 64) * (26 ** i)
                            col_widths[str(col_idx - 1)] = dim.width
                except Exception:
                    pass
                # 行高
                row_heights: dict[str, float] = {}
                try:
                    for row_idx, dim in ws_obj.row_dimensions.items():
                        if dim.height and dim.height != 15:  # 默认行高
                            row_heights[str(row_idx - 1)] = dim.height
                except Exception:
                    pass

                if cell_styles:
                    result["cell_styles"] = cell_styles
                if merged:
                    result["merged_cells"] = merged
                if col_widths:
                    result["column_widths"] = col_widths
                if row_heights:
                    result["row_heights"] = row_heights

            return result

        if all_sheets:
            # 一次返回所有工作表快照
            snapshots = []
            for sn in sheet_names:
                ws_obj = wb[sn]
                snapshots.append(_read_sheet(ws_obj))
            wb.close()
            return JSONResponse(content={
                "file": os.path.basename(resolved),
                "sheets": sheet_names,
                "all_snapshots": snapshots,
            })

        # 单 sheet 模式（向后兼容）
        ws = wb[sheet] if sheet and sheet in sheet_names else wb.active
        if ws is None:
            wb.close()
            return _error_json_response(404, "工作表不存在")

        snap = _read_sheet(ws)
        snap["file"] = os.path.basename(resolved)
        snap["sheets"] = sheet_names
        wb.close()
        return JSONResponse(content=snap)
    except Exception as exc:
        logger.error("Excel snapshot 生成失败: %s", exc, exc_info=True)
        return _error_json_response(500, f"读取文件失败: {exc}")


class ExcelWriteRequest(BaseModel):
    """Excel 单元格写入请求。"""

    model_config = ConfigDict(extra="forbid")
    session_id: str | None = None
    path: str
    sheet: str | None = None
    changes: list[dict[str, Any]]


@_router.post("/api/v1/files/excel/write")
async def write_excel_cells(request: ExcelWriteRequest, raw_request: Request) -> JSONResponse:
    """侧边面板编辑回写：将单元格变更写入文件。

    当备份模式启用时，写操作会自动重定向到备份副本（通过 ensure_backup
    确保备份存在），避免直接修改原始文件。
    """
    assert _config is not None, "服务未初始化"

    ws_root = _resolve_workspace_root(raw_request)

    # 先解析原始路径（不经过 backup 重定向），用于 ensure_backup
    _iso_uid = _get_isolation_user_id(raw_request)
    resolved = _resolve_excel_path(request.path, None, workspace_root=ws_root, user_id=_iso_uid)
    if resolved is None:
        return _error_json_response(404, f"文件不存在或路径非法: {request.path}")

    # 备份模式下，写操作需要 ensure_backup（创建备份副本如果尚不存在），
    # 然后写入备份副本而非原始文件
    if request.session_id and _session_manager is not None:
        engine = _session_manager.get_engine(request.session_id, user_id=_iso_uid)
        if engine is not None and engine.backup_enabled:
            tx = engine.transaction
            if tx is not None:
                try:
                    resolved = tx.stage_for_write(resolved)
                except ValueError:
                    pass

    try:
        from openpyxl import load_workbook
        from openpyxl.utils.cell import coordinate_to_tuple

        wb = load_workbook(resolved)
        sheet_names = wb.sheetnames
        ws = wb[request.sheet] if request.sheet and request.sheet in sheet_names else wb.active
        if ws is None:
            wb.close()
            return _error_json_response(404, "工作表不存在")

        cells_written = 0
        for change in request.changes:
            cell_ref = change.get("cell", "")
            value = change.get("value")
            if not cell_ref:
                continue
            row, col = coordinate_to_tuple(cell_ref.upper())
            ws.cell(row=row, column=col, value=value)
            cells_written += 1

        wb.save(resolved)
        wb.close()

        return JSONResponse(content={
            "status": "success",
            "cells_written": cells_written,
        })
    except Exception as exc:
        logger.error("Excel write 失败: %s", exc, exc_info=True)
        return _error_json_response(500, f"写入失败: {exc}")


_ALLOWED_UPLOAD_EXTENSIONS = {".xlsx", ".xls", ".csv", ".png", ".jpg", ".jpeg"}


def _safe_uploads_path(uploads_dir: "Path", relative: str) -> "Path | None":
    """在 uploads_dir 下解析 relative 路径并确保不越界。"""
    from pathlib import Path as _Path
    cleaned = relative.replace("\\", "/").strip("/")
    if ".." in cleaned.split("/"):
        return None
    target = (uploads_dir / cleaned).resolve()
    if not str(target).startswith(str(uploads_dir.resolve())):
        return None
    return target


_UPLOAD_MAX_PART_SIZE = 100 * 1024 * 1024  # 100 MB – 覆盖 Starlette 默认的 1 MB 限制


@_router.post("/api/v1/upload")
async def upload_file(raw_request: Request) -> JSONResponse:
    """上传文件到 workspace uploads 目录（支持可选 folder 参数指定子目录）。

    注意: 不使用 FastAPI 的 UploadFile 依赖注入，改为手动调用
    ``request.form(max_part_size=...)`` 以突破 Starlette 0.50+ 默认的
    1 MB multipart 大小限制。
    """
    assert _config is not None, "服务未初始化"

    form = await raw_request.form(max_part_size=_UPLOAD_MAX_PART_SIZE)
    file = form.get("file")
    logger.info("upload_file: form keys=%s, file type=%s, file=%r", list(form.keys()), type(file).__name__, file)
    if file is None or not hasattr(file, "read"):
        return _error_json_response(400, f"缺少 file 字段 (got {type(file).__name__})")

    filename = file.filename or "unnamed"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        return _error_json_response(400, f"不支持的文件格式: {ext}")

    from excelmanus.auth.dependencies import extract_user_id
    user_id = extract_user_id(raw_request)
    ws = _resolve_workspace(raw_request)

    content = await file.read()

    auth_enabled = getattr(raw_request.app.state, "auth_enabled", False)
    if auth_enabled and user_id:
        allowed, reason = ws.check_upload_allowed(len(content))
        if not allowed:
            return _error_json_response(413, reason)

    upload_dir = ws.get_upload_dir()

    # 支持可选的 folder= 表单字段或查询参数
    folder = raw_request.query_params.get("folder", "")
    if not folder:
        folder = str(form.get("folder", ""))

    if folder:
        target_dir = _safe_uploads_path(upload_dir, folder)
        if target_dir is None:
            return _error_json_response(400, "非法目标路径")
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        target_dir = upload_dir

    safe_name = f"{uuid.uuid4().hex[:8]}_{filename}"
    dest_path = target_dir / safe_name

    with open(dest_path, "wb") as f:
        f.write(content)

    if auth_enabled and user_id:
        ws.enforce_quota()

    rel_path = f"./{dest_path.relative_to(ws.root_dir)}"

    # 注册到 FileRegistry
    registry = _get_file_registry(str(ws.root_dir), user_id=user_id)
    if registry is not None:
        try:
            registry.register_upload(
                canonical_path=str(dest_path.relative_to(ws.root_dir)),
                original_name=filename,
                size_bytes=len(content),
            )
        except Exception:
            logger.debug("FileRegistry register_upload 失败", exc_info=True)

    return JSONResponse(content={
        "filename": filename,
        "path": rel_path,
        "size": len(content),
    })


@_router.post("/api/v1/upload-from-url")
async def upload_file_from_url(raw_request: Request) -> JSONResponse:
    """从 URL 下载文件并保存到 workspace uploads 目录。

    请求体 JSON::

        {"url": "https://example.com/data.xlsx"}
    """
    assert _config is not None, "服务未初始化"

    try:
        body = await raw_request.json()
    except Exception:
        return _error_json_response(400, "请求体必须是 JSON")

    url: str = (body.get("url") or "").strip()
    if not url:
        return _error_json_response(400, "缺少 url 字段")

    # 仅允许 http/https
    if not url.lower().startswith(("http://", "https://")):
        return _error_json_response(400, "仅支持 http/https 链接")

    import httpx
    from urllib.parse import urlparse, unquote

    # 从 URL 路径推断文件名
    parsed = urlparse(url)
    url_path = unquote(parsed.path.rstrip("/"))
    raw_filename = url_path.split("/")[-1] if "/" in url_path else ""
    if not raw_filename or "." not in raw_filename:
        return _error_json_response(400, "无法从 URL 推断文件名（需带扩展名，如 .xlsx/.csv/.png）")

    ext = os.path.splitext(raw_filename)[1].lower()
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        return _error_json_response(400, f"不支持的文件格式: {ext}")

    # 下载文件（限制大小）
    max_download = _UPLOAD_MAX_PART_SIZE  # 复用上传限制
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return _error_json_response(502, f"远程服务器返回 {exc.response.status_code}")
    except Exception as exc:
        return _error_json_response(502, f"下载失败: {exc}")

    content = resp.content
    if len(content) > max_download:
        return _error_json_response(413, f"文件过大 (>{max_download // (1024*1024)} MB)")
    if len(content) == 0:
        return _error_json_response(400, "下载到空文件")

    from excelmanus.auth.dependencies import extract_user_id
    user_id = extract_user_id(raw_request)
    ws = _resolve_workspace(raw_request)

    auth_enabled = getattr(raw_request.app.state, "auth_enabled", False)
    if auth_enabled and user_id:
        allowed, reason = ws.check_upload_allowed(len(content))
        if not allowed:
            return _error_json_response(413, reason)

    upload_dir = ws.get_upload_dir()
    safe_name = f"{uuid.uuid4().hex[:8]}_{raw_filename}"
    dest_path = upload_dir / safe_name

    with open(dest_path, "wb") as f:
        f.write(content)

    if auth_enabled and user_id:
        ws.enforce_quota()

    rel_path = f"./{dest_path.relative_to(ws.root_dir)}"

    # 注册到 FileRegistry
    registry = _get_file_registry(str(ws.root_dir), user_id=user_id)
    if registry is not None:
        try:
            registry.register_upload(
                canonical_path=str(dest_path.relative_to(ws.root_dir)),
                original_name=raw_filename,
                size_bytes=len(content),
            )
        except Exception:
            logger.debug("FileRegistry register_upload 失败 (from-url)", exc_info=True)

    return JSONResponse(content={
        "filename": raw_filename,
        "path": rel_path,
        "size": len(content),
    })


# ── 文件管理 API ─────────────────────────────────────


@_router.post("/api/v1/files/workspace/mkdir")
async def workspace_mkdir(request: Request) -> JSONResponse:
    """在 uploads/ 下创建子目录。"""
    assert _config is not None, "服务未初始化"
    body = await request.json()
    path = body.get("path", "").strip()
    if not path:
        return _error_json_response(400, "缺少 path 参数")

    ws = _resolve_workspace(request)
    uploads = ws.get_upload_dir()
    target = _safe_uploads_path(uploads, path)
    if target is None:
        return _error_json_response(400, "非法目标路径")
    if target.exists():
        return _error_json_response(409, "目录已存在")
    target.mkdir(parents=True, exist_ok=True)
    return JSONResponse(content={"status": "created", "path": path})


@_router.post("/api/v1/files/workspace/create")
async def workspace_create_file(request: Request) -> JSONResponse:
    """在 uploads/ 下创建空文件。"""
    assert _config is not None, "服务未初始化"
    body = await request.json()
    path = body.get("path", "").strip()
    if not path:
        return _error_json_response(400, "缺少 path 参数")

    ws = _resolve_workspace(request)
    uploads = ws.get_upload_dir()
    target = _safe_uploads_path(uploads, path)
    if target is None:
        return _error_json_response(400, "非法目标路径")
    if target.exists():
        return _error_json_response(409, "文件已存在")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()
    return JSONResponse(content={"status": "created", "path": path})


@_router.delete("/api/v1/files/workspace/item")
async def workspace_delete_item(request: Request) -> JSONResponse:
    """删除 uploads/ 下的文件或文件夹。"""
    assert _config is not None, "服务未初始化"
    body = await request.json()
    path = body.get("path", "").strip()
    if not path:
        return _error_json_response(400, "缺少 path 参数")

    ws = _resolve_workspace(request)
    uploads = ws.get_upload_dir()
    target = _safe_uploads_path(uploads, path)
    if target is None:
        return _error_json_response(400, "非法目标路径")
    if not target.exists():
        return _error_json_response(404, "路径不存在")
    # 防止删除上传根目录本身
    if target.resolve() == uploads.resolve():
        return _error_json_response(400, "无法删除根目录")

    import shutil
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    # W4: 通知所有活跃 session 清理关联 staging 条目
    if _session_manager is not None:
        _session_manager.notify_file_deleted(str(target))
    return JSONResponse(content={"status": "deleted", "path": path})


@_router.post("/api/v1/files/workspace/rename")
async def workspace_rename_item(request: Request) -> JSONResponse:
    """重命名 uploads/ 下的文件或文件夹。"""
    assert _config is not None, "服务未初始化"
    body = await request.json()
    old_path = body.get("old_path", "").strip()
    new_path = body.get("new_path", "").strip()
    if not old_path or not new_path:
        return _error_json_response(400, "缺少 old_path 或 new_path 参数")

    ws = _resolve_workspace(request)
    uploads = ws.get_upload_dir()
    src = _safe_uploads_path(uploads, old_path)
    dst = _safe_uploads_path(uploads, new_path)
    if src is None or dst is None:
        return _error_json_response(400, "非法路径")
    if not src.exists():
        return _error_json_response(404, "源路径不存在")
    if dst.exists():
        return _error_json_response(409, "目标路径已存在")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    # W5: 通知所有活跃 session 更新 staging 映射
    if _session_manager is not None:
        _session_manager.notify_file_renamed(str(src), str(dst))
    return JSONResponse(content={"status": "renamed", "old_path": old_path, "new_path": new_path})


@_router.post("/api/v1/files/reveal")
async def reveal_file(request: Request) -> JSONResponse:
    """在本地文件管理器中打开文件所在目录。"""
    import platform
    import subprocess

    body = await request.json()
    file_path = body.get("path", "").strip()
    if not file_path:
        return _error_json_response(400, "缺少 path 参数")

    target = os.path.abspath(file_path)
    # 安全校验：限制在工作区范围内，防止路径遍历
    if _config is not None:
        ws_root = os.path.abspath(_config.workspace_root)
        if not (target == ws_root or target.startswith(ws_root + os.sep)):
            return _error_json_response(403, "路径不在工作区范围内")
    if not os.path.exists(target):
        return _error_json_response(404, f"路径不存在: {target}")

    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", "-R", target])
        elif system == "Windows":
            subprocess.Popen(["explorer", "/select,", target])
        else:
            parent = os.path.dirname(target) if os.path.isfile(target) else target
            subprocess.Popen(["xdg-open", parent])
    except Exception as exc:
        logger.warning("打开文件管理器失败: %s", exc)
        return _error_json_response(500, f"打开失败: {exc}")

    return JSONResponse(content={"status": "ok", "path": target})


@_router.get("/api/v1/sessions")
async def list_sessions(request: Request) -> JSONResponse:
    """列出所有会话（含历史）。认证启用时仅返回当前用户的会话。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(request)
    include_archived = request.query_params.get("include_archived", "false").lower() == "true"
    sessions = await _session_manager.list_sessions(
        include_archived=include_archived, user_id=user_id
    )
    return JSONResponse(content={"sessions": sessions})


@_router.delete("/api/v1/sessions", responses={409: _error_responses[409]})
async def clear_all_sessions(request: Request) -> JSONResponse:
    """清空当前用户的会话历史。认证启用时仅删除当前用户的会话。若有会话正在处理中则返回 409。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(request)
    sess_count, msg_count = await _session_manager.clear_all_sessions(user_id=user_id)
    return JSONResponse(content={
        "status": "ok",
        "sessions_deleted": sess_count,
        "messages_deleted": msg_count,
    })


@_router.get("/api/v1/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, request: Request) -> JSONResponse:
    """分页获取会话消息。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(request)
    try:
        limit = max(1, min(500, int(request.query_params.get("limit", "50"))))
        offset = max(0, int(request.query_params.get("offset", "0")))
    except (ValueError, TypeError):
        return JSONResponse(status_code=400, content={"detail": "limit/offset 必须为整数"})
    messages = await _session_manager.get_session_messages(
        session_id, limit=limit, offset=offset, user_id=user_id
    )
    return JSONResponse(content={"messages": messages, "session_id": session_id})


@_router.get("/api/v1/sessions/{session_id}/excel-events")
async def get_session_excel_events(session_id: str, request: Request) -> JSONResponse:
    """返回持久化的 Excel diff 和改动文件列表，供前端重启后恢复。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(request)
    ch = _session_manager.chat_history
    if ch is None:
        return JSONResponse(content={"diffs": [], "affected_files": [], "previews": []})
    if user_id is not None and not ch.session_owned_by(session_id, user_id):
        return JSONResponse(content={"diffs": [], "affected_files": [], "previews": []})
    diffs = ch.load_excel_diffs(session_id)
    affected_files = ch.load_affected_files(session_id)
    previews = ch.load_excel_previews(session_id)
    safe_mode = _is_external_safe_mode()
    safe_diffs = []
    for d in diffs:
        safe_diffs.append({
            "tool_call_id": d["tool_call_id"],
            "file_path": _public_excel_path(d["file_path"], safe_mode=safe_mode),
            "sheet": d["sheet"],
            "affected_range": d["affected_range"],
            "changes": d["changes"],
            "timestamp": d["timestamp"],
        })
    safe_previews = []
    for p in previews:
        safe_previews.append({
            "tool_call_id": p["tool_call_id"],
            "file_path": _public_excel_path(p["file_path"], safe_mode=safe_mode),
            "sheet": p["sheet"],
            "columns": p["columns"],
            "rows": p["rows"],
            "total_rows": p["total_rows"],
            "truncated": p["truncated"],
        })
    safe_files = [
        _public_excel_path(f, safe_mode=safe_mode)
        for f in affected_files if f
    ]
    return JSONResponse(content={
        "diffs": safe_diffs,
        "previews": safe_previews,
        "affected_files": safe_files,
    })


@_router.get("/api/v1/sessions/{session_id}/status")
async def get_session_status(session_id: str, request: Request) -> JSONResponse:
    """获取会话运行时状态（上下文压缩 + 文件注册表）。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(request)

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

    can_restore = _session_manager.can_restore_session(
        session_id, user_id=user_id
    )
    engine = await _session_manager.get_or_restore_engine(
        session_id, user_id=user_id
    )

    if engine is None:
        if can_restore:
            _idle = _normalize_registry_status({"state": "idle"})
            return JSONResponse(content={
                "session_id": session_id,
                "compaction": {"enabled": False},
                "registry": _idle,
            })
        return _error_json_response(404, f"会话不存在: {session_id}")

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


@_router.post("/api/v1/sessions/{session_id}/compact")
async def compact_session_context(session_id: str, request: Request) -> JSONResponse:
    """在指定会话内执行 /compact，并返回执行结果。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(request)

    engine = await _session_manager.get_or_restore_engine(
        session_id, user_id=user_id
    )
    if engine is None:
        return _error_json_response(404, f"会话不存在: {session_id}")

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


@_router.post("/api/v1/sessions/{session_id}/memory/extract")
async def extract_session_memory(session_id: str, request: Request) -> JSONResponse:
    """手动触发指定会话的记忆提取。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(request)

    engine = await _session_manager.get_or_restore_engine(
        session_id, user_id=user_id
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


@_router.post("/api/v1/sessions/{session_id}/registry/scan")
async def scan_session_registry(session_id: str, request: Request) -> JSONResponse:
    """触发指定会话的 FileRegistry 后台扫描（force=True）。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(request)

    engine = await _session_manager.get_or_restore_engine(
        session_id, user_id=user_id
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


@_router.get("/api/v1/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> JSONResponse:
    """获取会话详情含消息历史。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(request)
    detail = await _session_manager.get_session_detail(session_id, user_id=user_id)
    return JSONResponse(content=detail)


class ModelSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


@_router.get("/api/v1/models")
async def list_models(request: Request) -> JSONResponse:
    """获取可用模型列表（含多模型配置档案）。

    注意：模型列表为只读信息，不需要管理员权限，所有已认证用户均可访问。
    """
    assert _config is not None, "服务未初始化"

    user_id = _get_isolation_user_id(request)
    active_name: str | None = None
    if _config_store is not None:
        try:
            from excelmanus.stores.config_store import UserConfigStore
            _user_cfg = UserConfigStore(
                _config_store._conn if hasattr(_config_store, '_conn') else _config_store._conn,
                user_id=user_id,
            )
            active_name = _user_cfg.get_active_model()
        except Exception:
            active_name = _config_store.get_active_model() if hasattr(_config_store, 'get_active_model') else None
    models: list[dict] = [{
        "name": "default",
        "model": _config.model,
        "description": "默认模型（主配置）",
        "active": active_name is None,
        "base_url": _config.base_url,
    }]
    db_profiles = _config_store.list_profiles() if _config_store else []
    for p in db_profiles:
        models.append({
            "name": p["name"],
            "model": p["model"],
            "description": p.get("description", ""),
            "active": p["name"] == active_name,
            "base_url": p.get("base_url", ""),
        })

    # 按用户 allowed_models 过滤（空列表 = 不限制）
    allowed = _get_user_allowed_models(request)
    if allowed:
        models = [m for m in models if m["name"] == "default" or m["name"] in allowed]

    return JSONResponse(content={"models": models})


@_router.put("/api/v1/models/active")
async def switch_model(request: ModelSwitchRequest, raw_request: Request) -> JSONResponse:
    """切换当前活跃模型并持久化到数据库，同时同步所有活跃会话的 engine。

    所有已认证用户均可切换模型（不再要求管理员权限）。
    如果用户配置了 allowed_models，则只能切换到允许的模型。
    """
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    assert _config is not None, "服务未初始化"

    name = request.name.strip()
    if not name:
        return _error_json_response(400, "请指定模型名称。")

    # 验证目标模型存在
    if name.lower() != "default":
        profile = _config_store.get_profile(name) if _config_store else None
        if profile is None:
            available = ", ".join(p["name"] for p in (_config_store.list_profiles() if _config_store else []))
            return _error_json_response(404, f"未找到模型 {name!r}。可用模型：default" + (f", {available}" if available else ""))

    # 校验用户模型权限（allowed_models 为空表示不限制）
    allowed = _get_user_allowed_models(raw_request)
    if allowed and name.lower() != "default" and name not in allowed:
        return _error_json_response(403, f"您没有使用模型 {name!r} 的权限。")

    # 持久化到用户级配置（auth 启用时隔离，匿名时写全局）
    user_id = _get_isolation_user_id(raw_request)
    if _config_store is not None:
        try:
            from excelmanus.stores.config_store import UserConfigStore
            _user_cfg = UserConfigStore(
                _config_store._conn if hasattr(_config_store, '_conn') else _config_store._conn,
                user_id=user_id,
            )
            _user_cfg.set_active_model(None if name.lower() == "default" else name)
        except Exception:
            _config_store.set_active_model(None if name.lower() == "default" else name)

    # 仅同步当前用户的活跃会话 engine
    result_msg = f"模型已切换为 {name}"
    sessions = await _session_manager.list_sessions(user_id=user_id)
    for session_info in sessions:
        try:
            engine = _session_manager.get_engine(session_info["id"], user_id=user_id)
            if engine is not None:
                result_msg = engine.switch_model(name)
        except Exception:
            pass

    # 模型切换后尝试从缓存加载新模型的能力探测结果
    db = _session_manager.database if _session_manager else None
    if db is not None:
        try:
            from excelmanus.model_probe import load_capabilities

            for session_info in sessions:
                engine = _session_manager.get_engine(session_info["id"], user_id=user_id)
                if engine is not None:
                    caps = load_capabilities(
                        db, engine.current_model, engine.active_base_url,
                    )
                    if caps is not None:
                        engine.set_model_capabilities(caps)
        except Exception:
            logger.debug("模型切换后加载能力缓存失败", exc_info=True)

    return JSONResponse(content={"message": result_msg})


# ── Thinking 配置 API ──────────────────────────────────


class ThinkingConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effort: str | None = None  # none|minimal|low|medium|high|xhigh
    budget: int | None = None  # 精确 token 预算（0 = 使用 effort 换算）


@_router.get("/api/v1/thinking")
async def get_thinking_config(raw_request: Request) -> JSONResponse:
    """获取当前 thinking 配置（等级 + 预算）。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    user_id = _get_isolation_user_id(raw_request)
    sessions = await _session_manager.list_sessions(user_id=user_id)
    # 取第一个活跃 session 的 thinking_config
    for s in sessions:
        engine = _session_manager.get_engine(s["id"], user_id=user_id)
        if engine is not None:
            tc = engine.thinking_config
            return JSONResponse(content={
                "effort": tc.effort,
                "budget": tc.budget_tokens,
                "effective_budget": tc.effective_budget(),
            })
    # 回退到全局配置
    assert _config is not None
    return JSONResponse(content={
        "effort": _config.thinking_effort,
        "budget": _config.thinking_budget,
        "effective_budget": 0,
    })


@_router.put("/api/v1/thinking")
async def set_thinking_config(request: ThinkingConfigRequest, raw_request: Request) -> JSONResponse:
    """设置 thinking 等级和/或预算，同步到所有活跃会话。"""
    if _session_manager is None:
        return _error_json_response(503, "服务未初始化")
    from excelmanus.engine import _EFFORT_RATIOS
    if request.effort is not None and request.effort not in _EFFORT_RATIOS:
        return _error_json_response(400, f"无效的 effort 值: {request.effort!r}。可选: {', '.join(sorted(_EFFORT_RATIOS))}")

    user_id = _get_isolation_user_id(raw_request)
    sessions = await _session_manager.list_sessions(user_id=user_id)
    updated = 0
    result_tc = None
    for s in sessions:
        engine = _session_manager.get_engine(s["id"], user_id=user_id)
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


# ── 模型配置管理 API（.env 持久化） ──────────────────────

_MODEL_ENV_KEYS = {
    "main": {"api_key": "EXCELMANUS_API_KEY", "base_url": "EXCELMANUS_BASE_URL", "model": "EXCELMANUS_MODEL", "protocol": "EXCELMANUS_PROTOCOL"},
    "aux": {"api_key": "EXCELMANUS_AUX_API_KEY", "base_url": "EXCELMANUS_AUX_BASE_URL", "model": "EXCELMANUS_AUX_MODEL", "enabled": "EXCELMANUS_AUX_ENABLED", "protocol": "EXCELMANUS_AUX_PROTOCOL"},
    "vlm": {"api_key": "EXCELMANUS_VLM_API_KEY", "base_url": "EXCELMANUS_VLM_BASE_URL", "model": "EXCELMANUS_VLM_MODEL", "enabled": "EXCELMANUS_VLM_ENABLED", "protocol": "EXCELMANUS_VLM_PROTOCOL"},
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


@_router.get("/api/v1/config/models")
async def get_model_config(request: Request) -> JSONResponse:
    """获取全部模型配置（main/aux/vlm + profiles）。"""
    assert _config is not None, "服务未初始化"
    guard_error = await _require_admin_if_auth_enabled(request)
    if guard_error is not None:
        return guard_error
    result: dict = {
        "main": {
            "api_key": _mask_key(_config.api_key),
            "base_url": _config.base_url,
            "model": _config.model,
            "protocol": _config.protocol,
        },
        "aux": {
            "api_key": _mask_key(_config.aux_api_key or ""),
            "base_url": _config.aux_base_url or "",
            "model": _config.aux_model or "",
            "enabled": _config.aux_enabled,
            "protocol": _config.aux_protocol,
        },
        "vlm": {
            "api_key": _mask_key(_config.vlm_api_key or ""),
            "base_url": _config.vlm_base_url or "",
            "model": _config.vlm_model or "",
            "enabled": _config.vlm_enabled,
            "protocol": _config.vlm_protocol,
        },
        "profiles": [
            {
                "name": p["name"],
                "model": p["model"],
                "api_key": _mask_key(p.get("api_key", "")),
                "base_url": p.get("base_url", ""),
                "description": p.get("description", ""),
                "protocol": p.get("protocol", "auto"),
            }
            for p in (_config_store.list_profiles() if _config_store else [])
        ],
    }
    return JSONResponse(content=result)


def _mask_key(key: str) -> str:
    """脱敏 API Key：保留前4后4位。"""
    if not key or len(key) <= 12:
        return "****" if key else ""
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def _sync_config_profiles_from_db() -> None:
    """从数据库读取 model_profiles 并同步到 _config.models。"""
    if _config is None or _config_store is None:
        return
    rows = _config_store.list_profiles()
    default_api_key = _config.api_key
    default_base_url = _config.base_url
    profiles: list[ModelProfile] = []
    for row in rows:
        profiles.append(ModelProfile(
            name=row["name"],
            model=row["model"],
            api_key=row.get("api_key") or default_api_key,
            base_url=row.get("base_url") or default_base_url,
            description=row.get("description", ""),
            protocol=row.get("protocol", "auto"),
        ))
    object.__setattr__(_config, "models", tuple(profiles))


@_router.put("/api/v1/config/models/{section}")
async def update_model_config(
    section: str,
    request: ModelConfigUpdate,
    raw_request: Request,
) -> JSONResponse:
    """更新指定模型配置区块并持久化到 .env。"""
    guard_error = await _require_admin_if_auth_enabled(raw_request)
    if guard_error is not None:
        return guard_error
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
    if request.enabled is not None and "enabled" in key_map:
        updates[key_map["enabled"]] = "true" if request.enabled else "false"
    if request.protocol is not None and "protocol" in key_map:
        updates[key_map["protocol"]] = request.protocol

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

    # 同步更新内存中的 _config 实例
    if _config is not None:
        _SECTION_CONFIG_FIELDS = {
            "main": {"api_key": "api_key", "base_url": "base_url", "model": "model", "protocol": "protocol"},
            "aux": {"api_key": "aux_api_key", "base_url": "aux_base_url", "model": "aux_model", "enabled": "aux_enabled", "protocol": "aux_protocol"},
            "vlm": {"api_key": "vlm_api_key", "base_url": "vlm_base_url", "model": "vlm_model", "enabled": "vlm_enabled", "protocol": "vlm_protocol"},
        }
        field_map = _SECTION_CONFIG_FIELDS.get(section, {})
        for req_field, config_attr in field_map.items():
            val = getattr(request, req_field, None)
            if val is not None:
                object.__setattr__(_config, config_attr, val)

    # AUX 变更需广播到所有活跃 engine，避免子代理/路由/顾问使用过时快照
    if section == "aux" and _session_manager is not None:
        await _session_manager.broadcast_aux_config(
            aux_enabled=_config.aux_enabled if _config else True,
            aux_model=_config.aux_model if _config else None,
            aux_api_key=_config.aux_api_key if _config else None,
            aux_base_url=_config.aux_base_url if _config else None,
        )

    return JSONResponse(content={"status": "ok", "section": section, "updated": list(updates.keys())})


@_router.post("/api/v1/config/models/profiles")
async def add_model_profile(request: ModelProfileCreate, raw_request: Request) -> JSONResponse:
    """新增多模型条目并持久化到数据库。"""
    guard_error = await _require_admin_if_auth_enabled(raw_request)
    if guard_error is not None:
        return guard_error
    if _config_store is None:
        return _error_json_response(503, "配置存储未初始化")

    if _config_store.get_profile(request.name):
        return _error_json_response(409, f"模型名称已存在: {request.name}")

    _config_store.add_profile(
        name=request.name,
        model=request.model,
        api_key=request.api_key or "",
        base_url=request.base_url or "",
        description=request.description or "",
        protocol=request.protocol or "auto",
    )
    _sync_config_profiles_from_db()

    return JSONResponse(status_code=201, content={"status": "created", "name": request.name})


@_router.delete("/api/v1/config/models/profiles/{name}")
async def delete_model_profile(name: str, request: Request) -> JSONResponse:
    """删除多模型条目。"""
    guard_error = await _require_admin_if_auth_enabled(request)
    if guard_error is not None:
        return guard_error
    if _config_store is None:
        return _error_json_response(503, "配置存储未初始化")

    if not _config_store.delete_profile(name):
        return _error_json_response(404, f"未找到模型: {name}")

    _sync_config_profiles_from_db()
    return JSONResponse(content={"status": "deleted", "name": name})


@_router.put("/api/v1/config/models/profiles/{name}")
async def update_model_profile(
    name: str,
    request: ModelProfileCreate,
    raw_request: Request,
) -> JSONResponse:
    """更新多模型条目。"""
    guard_error = await _require_admin_if_auth_enabled(raw_request)
    if guard_error is not None:
        return guard_error
    if _config_store is None:
        return _error_json_response(503, "配置存储未初始化")

    if not _config_store.get_profile(name):
        return _error_json_response(404, f"未找到模型: {name}")

    _config_store.update_profile(
        name,
        new_name=request.name if request.name != name else None,
        model=request.model,
        api_key=request.api_key or None,
        base_url=request.base_url or None,
        description=request.description or None,
        protocol=request.protocol or None,
    )
    _sync_config_profiles_from_db()

    return JSONResponse(content={"status": "updated", "name": request.name})


# ── 模型配置导出/导入 API ──────────────────────────────


class ConfigExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: list[str] = ["main", "aux", "vlm", "profiles"]
    mode: Literal["password", "simple"] = "password"
    password: str | None = None


class ConfigImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str
    password: str | None = None


def _collect_raw_sections(section_names: list[str]) -> dict[str, Any]:
    """收集指定区块的原始（未脱敏）配置数据。"""
    assert _config is not None
    result: dict[str, Any] = {}
    if "main" in section_names:
        result["main"] = {
            "api_key": _config.api_key,
            "base_url": _config.base_url,
            "model": _config.model,
            "protocol": _config.protocol,
        }
    if "aux" in section_names:
        result["aux"] = {
            "api_key": _config.aux_api_key or "",
            "base_url": _config.aux_base_url or "",
            "model": _config.aux_model or "",
            "protocol": _config.aux_protocol,
        }
    if "vlm" in section_names:
        result["vlm"] = {
            "api_key": _config.vlm_api_key or "",
            "base_url": _config.vlm_base_url or "",
            "model": _config.vlm_model or "",
            "protocol": _config.vlm_protocol,
        }
    if "profiles" in section_names and _config_store is not None:
        result["profiles"] = _config_store.list_profiles()
    return result


def _collect_user_section(request: Request, user_id: str) -> dict[str, Any]:
    """收集当前用户个人的 LLM 配置（users 表中的 llm_* 覆盖）。"""
    store = getattr(request.app.state, "user_store", None)
    if store is None:
        return {"api_key": "", "base_url": "", "model": ""}
    user = store.get_by_id(user_id)
    if user is None:
        return {"api_key": "", "base_url": "", "model": ""}
    return {
        "api_key": user.llm_api_key or "",
        "base_url": user.llm_base_url or "",
        "model": user.llm_model or "",
    }


@_router.get("/api/v1/config/models/user")
async def get_user_model_config(request: Request) -> JSONResponse:
    """获取当前用户的自定义 LLM 配置（api_key 脱敏返回）。"""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return JSONResponse(content={"api_key": "", "base_url": "", "model": ""})
    data = _collect_user_section(request, user_id)
    # 脱敏 API key
    if data.get("api_key"):
        data["api_key"] = _mask_key(data["api_key"])
    return JSONResponse(content=data)


async def _get_config_transfer_scope(request: Request) -> tuple[str | None, bool] | JSONResponse:
    """返回配置导出/导入的权限范围。成功返回 (user_id, is_admin_scope)，失败返回错误响应。

    - 认证关闭: (None, True) 视为管理员，可操作全局配置
    - 认证开启: 须登录（AuthMiddleware 已注入 user_id），管理员返回 (user_id, True)，普通用户返回 (user_id, False)
    """
    if not getattr(request.app.state, "auth_enabled", False):
        return (None, True)
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return _error_json_response(401, "请先登录")
    user_role = getattr(request.state, "user_role", "user")
    return (user_id, user_role == "admin")


@_router.post("/api/v1/config/export")
async def export_model_config(
    request: ConfigExportRequest,
    raw_request: Request,
) -> JSONResponse:
    """将模型配置加密导出为令牌字符串。

    管理员可导出全局配置（main/aux/vlm/profiles），普通用户仅可导出自己的个人配置（user）。
    """
    scope_result = await _get_config_transfer_scope(raw_request)
    if isinstance(scope_result, JSONResponse):
        return scope_result
    user_id, is_admin_scope = scope_result

    if _config is None:
        return _error_json_response(503, "服务未初始化")

    if is_admin_scope:
        valid_sections = {"main", "aux", "vlm", "profiles"}
        invalid = set(request.sections) - valid_sections
        if invalid:
            return _error_json_response(400, f"无效的配置区块: {', '.join(invalid)}")
        sections = _collect_raw_sections(request.sections)
    else:
        valid_sections = {"user"}
        invalid = set(request.sections) - valid_sections
        if invalid:
            return _error_json_response(400, "普通用户仅可导出个人配置（user）")
        if "user" not in request.sections:
            return _error_json_response(400, "请选择导出个人配置")
        sections = {"user": _collect_user_section(raw_request, user_id)}

    try:
        from excelmanus.config_transfer import export_config
        token = export_config(sections, password=request.password, mode=request.mode)
    except ValueError as exc:
        return _error_json_response(400, str(exc))

    return JSONResponse(content={"token": token, "sections": list(sections.keys()), "mode": request.mode})


@_router.post("/api/v1/config/import")
async def import_model_config(
    request: ConfigImportRequest,
    raw_request: Request,
) -> JSONResponse:
    """解密并导入模型配置令牌。

    管理员可导入全局配置（main/aux/vlm/profiles），普通用户仅可导入自己的个人配置（user）。
    """
    scope_result = await _get_config_transfer_scope(raw_request)
    if isinstance(scope_result, JSONResponse):
        return scope_result
    user_id, is_admin_scope = scope_result

    if _config is None:
        return _error_json_response(503, "服务未初始化")

    try:
        from excelmanus.config_transfer import import_config
        payload = import_config(request.token, password=request.password)
    except ValueError as exc:
        return _error_json_response(400, str(exc))

    sections = payload.get("sections", {})
    imported: dict[str, Any] = {}

    if is_admin_scope:
        env_path = _find_env_file()
        lines = _read_env_file(env_path)
        env_dirty = False

        for section_key in ("main", "aux", "vlm"):
            section_data = sections.get(section_key)
            if not isinstance(section_data, dict):
                continue
            key_map = _MODEL_ENV_KEYS.get(section_key)
            if not key_map:
                continue

            updated_fields: list[str] = []
            for field_name in ("api_key", "base_url", "model"):
                val = section_data.get(field_name)
                if val is None or not isinstance(val, str):
                    continue
                env_key = key_map.get(field_name)
                if not env_key:
                    continue
                lines = _update_env_var(lines, env_key, val)
                os.environ[env_key] = val if val else os.environ.get(env_key, "")
                if val:
                    os.environ[env_key] = val
                elif env_key in os.environ:
                    del os.environ[env_key]
                updated_fields.append(field_name)
                env_dirty = True

            if _config is not None and updated_fields:
                field_map = {
                    "main": {"api_key": "api_key", "base_url": "base_url", "model": "model"},
                    "aux": {"api_key": "aux_api_key", "base_url": "aux_base_url", "model": "aux_model"},
                    "vlm": {"api_key": "vlm_api_key", "base_url": "vlm_base_url", "model": "vlm_model"},
                }.get(section_key, {})
                for f in updated_fields:
                    config_attr = field_map.get(f)
                    if config_attr:
                        object.__setattr__(_config, config_attr, section_data.get(f) or None)

            if updated_fields:
                imported[section_key] = updated_fields

        if env_dirty:
            _write_env_file(env_path, lines)

        profiles_data = sections.get("profiles")
        if isinstance(profiles_data, list) and _config_store is not None:
            profile_names: list[str] = []
            for p in profiles_data:
                if not isinstance(p, dict):
                    continue
                name = p.get("name", "").strip()
                model = p.get("model", "").strip()
                if not name or not model:
                    continue
                existing = _config_store.get_profile(name)
                if existing:
                    _config_store.update_profile(
                        name,
                        model=model,
                        api_key=p.get("api_key", ""),
                        base_url=p.get("base_url", ""),
                        description=p.get("description", ""),
                    )
                else:
                    _config_store.add_profile(
                        name=name,
                        model=model,
                        api_key=p.get("api_key", ""),
                        base_url=p.get("base_url", ""),
                        description=p.get("description", ""),
                    )
                profile_names.append(name)
            if profile_names:
                imported["profiles"] = profile_names
                _sync_config_profiles_from_db()
    else:
        user_data = sections.get("user")
        if isinstance(user_data, dict):
            store = getattr(raw_request.app.state, "user_store", None)
            if store is not None:
                updates: dict[str, object] = {}
                for key, attr in (("api_key", "llm_api_key"), ("base_url", "llm_base_url"), ("model", "llm_model")):
                    val = user_data.get(key)
                    if val is not None and isinstance(val, str):
                        updates[attr] = val if val else None
                if updates:
                    store.update_user(user_id, **updates)
                    imported["user"] = [k for k in ("api_key", "base_url", "model") if k in user_data]

    return JSONResponse(content={
        "status": "ok",
        "imported": imported,
        "exported_at": payload.get("ts", ""),
    })


@_router.post("/api/v1/config/transfer/detect")
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
    """返回最新的主模型配置（始终从 _config 读取，不用引擎缓存）。"""
    assert _config is not None
    return _config.model, _config.base_url, _config.api_key


def _resolve_model_info(
    req_name: str | None,
    req_model: str | None,
    req_base_url: str | None,
) -> tuple[str, str, str]:
    """根据请求参数解析模型信息。

    优先级：profile name > model ID 匹配 profile > 直接使用 model+base_url。
    """
    assert _config is not None

    # 1) 无参数：返回主模型配置
    if not req_name and not req_model:
        return _resolve_active_engine_info()

    # 1.5) 内置 section 名称直接返回对应配置
    if req_name == "main":
        return _config.model, _config.base_url, _config.api_key
    if req_name == "aux":
        return (_config.aux_model or _config.model,
                _config.aux_base_url or _config.base_url,
                _config.aux_api_key or _config.api_key)
    if req_name == "vlm":
        return (_config.vlm_model or _config.model,
                _config.vlm_base_url or _config.base_url,
                _config.vlm_api_key or _config.api_key)

    # 2) 按 profile name 精确查找
    lookup_name = req_name or req_model
    if _config_store is not None and lookup_name:
        profile = _config_store.get_profile(lookup_name)
        if profile is not None:
            p_base_url = profile.get("base_url") or _config.base_url
            p_api_key = profile.get("api_key") or _config.api_key
            return profile["model"], p_base_url, p_api_key

    # 3) 按 model ID 在所有 profiles 中查找（处理前端传 model ID 而非 name 的情况）
    if _config_store is not None and req_model:
        for p in _config_store.list_profiles():
            if p["model"] == req_model:
                p_base_url = p.get("base_url") or _config.base_url
                p_api_key = p.get("api_key") or _config.api_key
                # 如果也指定了 base_url，需要匹配
                if req_base_url and p_base_url != req_base_url:
                    continue
                return p["model"], p_base_url, p_api_key

    # 4) 兜底：直接使用参数，API key 从最新 _config 取
    model = req_model or _config.model
    base_url = req_base_url or _config.base_url
    return model, base_url, _config.api_key


@_router.get("/api/v1/config/models/capabilities")
async def get_model_capabilities(request: Request) -> JSONResponse:
    """获取模型能力探测结果。支持 ?model=xxx 查询指定模型，否则返回当前活跃模型。"""
    guard_error = await _require_admin_if_auth_enabled(request)
    if guard_error is not None:
        return guard_error
    if _config is None:
        return _error_json_response(503, "服务未初始化")

    from excelmanus.model_probe import load_capabilities

    db = _session_manager.database if _session_manager else None
    if db is None:
        return JSONResponse(content={"capabilities": None, "reason": "数据库未启用"})

    req_name = request.query_params.get("name")
    req_model = request.query_params.get("model")
    req_base_url = request.query_params.get("base_url")
    model, base_url, _ = _resolve_model_info(req_name, req_model, req_base_url)

    caps = load_capabilities(db, model, base_url)
    return JSONResponse(content={
        "capabilities": caps.to_dict() if caps else None,
        "model": model,
        "base_url": base_url,
    })


@_router.get("/api/v1/config/models/capabilities/all")
async def get_all_model_capabilities(request: Request) -> JSONResponse:
    """获取所有已配置模型的能力探测结果（主模型 + profiles）。"""
    guard_error = await _require_admin_if_auth_enabled(request)
    if guard_error is not None:
        return guard_error
    if _config is None:
        return _error_json_response(503, "服务未初始化")

    from excelmanus.model_probe import load_capabilities

    db = _session_manager.database if _session_manager else None
    if db is None:
        return JSONResponse(content={"items": []})

    result: list[dict] = []

    # 主模型
    main_caps = load_capabilities(db, _config.model, _config.base_url)
    result.append({
        "name": "main",
        "model": _config.model,
        "base_url": _config.base_url,
        "capabilities": main_caps.to_dict() if main_caps else None,
    })

    # 模型配置档案
    profiles = _config_store.list_profiles() if _config_store else []
    for p in profiles:
        p_model = p["model"]
        p_base_url = p.get("base_url") or _config.base_url
        caps = load_capabilities(db, p_model, p_base_url)
        result.append({
            "name": p["name"],
            "model": p_model,
            "base_url": p_base_url,
            "capabilities": caps.to_dict() if caps else None,
        })

    return JSONResponse(content={"items": result})


@_router.post("/api/v1/config/models/capabilities/probe")
async def probe_model_capabilities(request: Request) -> JSONResponse:
    """手动触发模型能力探测。支持 body 指定 model/base_url/api_key，否则探测当前活跃模型。"""
    guard_error = await _require_admin_if_auth_enabled(request)
    if guard_error is not None:
        return guard_error
    if _config is None:
        return _error_json_response(503, "服务未初始化")

    from excelmanus.model_probe import delete_capabilities, run_full_probe
    from excelmanus.providers import create_client

    db = _session_manager.database if _session_manager else None

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    req_name = body.get("name")
    req_model = body.get("model")
    req_base_url = body.get("base_url")
    model, base_url, api_key = _resolve_model_info(req_name, req_model, req_base_url)
    if body.get("api_key"):
        api_key = body["api_key"]

    if db is not None:
        delete_capabilities(db, model, base_url)

    req_protocol = body.get("protocol", "auto") or "auto"
    client = create_client(api_key=api_key, base_url=base_url, protocol=req_protocol)

    try:
        caps = await run_full_probe(
            client=client,
            model=model,
            base_url=base_url,
            skip_if_cached=False,
            db=db,
        )
    except Exception as exc:
        return _error_json_response(500, f"探测失败: {exc}")

    # 同步到所有活跃会话的引擎
    if _session_manager is not None:
        await _session_manager.broadcast_model_capabilities(model, caps)

    return JSONResponse(content={"capabilities": caps.to_dict(), "model": model})


@_router.post("/api/v1/config/models/capabilities/probe-all")
async def probe_all_model_capabilities(request: Request) -> JSONResponse:
    """一键探测所有已配置模型的能力。"""
    guard_error = await _require_admin_if_auth_enabled(request)
    if guard_error is not None:
        return guard_error
    if _config is None:
        return _error_json_response(503, "服务未初始化")

    from excelmanus.model_probe import delete_capabilities, run_full_probe
    from excelmanus.providers import create_client

    db = _session_manager.database if _session_manager else None

    # 收集所有需要探测的 (name, model, base_url, api_key, protocol) 元组
    targets: list[tuple[str, str, str, str, str]] = []
    targets.append(("main", _config.model, _config.base_url, _config.api_key, _config.protocol))

    profiles = _config_store.list_profiles() if _config_store else []
    for p in profiles:
        p_base_url = p.get("base_url") or _config.base_url
        p_api_key = p.get("api_key") or _config.api_key
        p_protocol = p.get("protocol") or _config.protocol
        targets.append((p["name"], p["model"], p_base_url, p_api_key, p_protocol))

    results: list[dict] = []
    for name, model, base_url, api_key, protocol in targets:
        if db is not None:
            delete_capabilities(db, model, base_url)
        client = create_client(api_key=api_key, base_url=base_url, protocol=protocol)
        try:
            caps = await run_full_probe(
                client=client, model=model, base_url=base_url,
                skip_if_cached=False, db=db,
            )
            results.append({
                "name": name, "model": model,
                "capabilities": caps.to_dict(),
            })
            if _session_manager is not None:
                await _session_manager.broadcast_model_capabilities(model, caps)
        except Exception as exc:
            results.append({
                "name": name, "model": model,
                "error": str(exc)[:200],
            })

    return JSONResponse(content={"results": results})


@_router.post("/api/v1/config/models/test-connection")
async def test_model_connection(request: Request) -> JSONResponse:
    """轻量级模型连通测试：仅发送 Hi 检查模型是否可达、鉴权是否正常。"""
    guard_error = await _require_admin_if_auth_enabled(request)
    if guard_error is not None:
        return guard_error
    if _config is None:
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
    model, base_url, api_key = _resolve_model_info(req_name, req_model, req_base_url)
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

    req_protocol = body.get("protocol", "auto") or "auto"
    client = create_client(api_key=api_key, base_url=base_url, protocol=req_protocol)
    try:
        healthy, health_err = await probe_health(client, model, timeout=15.0)
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": f"连通测试异常: {exc}", "model": model})

    return JSONResponse(content={
        "ok": healthy,
        "error": health_err if not healthy else "",
        "model": model,
        "base_url": base_url,
    })


@_router.get("/api/v1/config/models/check-placeholder")
async def check_model_placeholder(request: Request) -> JSONResponse:
    """检测当前模型配置是否使用了默认占位符值。"""
    if _config is None:
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

    # 主模型
    if _is_placeholder(_config.api_key):
        results.append({"name": "main", "field": "api_key", "model": _config.model})
    if not _config.model or not _config.model.strip():
        results.append({"name": "main", "field": "model", "model": ""})

    # AUX
    if _config.aux_model:
        if _is_placeholder(_config.aux_api_key or ""):
            results.append({"name": "aux", "field": "api_key", "model": _config.aux_model})

    # VLM
    if _config.vlm_model:
        if _is_placeholder(_config.vlm_api_key or ""):
            results.append({"name": "vlm", "field": "api_key", "model": _config.vlm_model})

    # Profiles
    profiles = _config_store.list_profiles() if _config_store else []
    for p in profiles:
        p_api_key = p.get("api_key") or _config.api_key
        if _is_placeholder(p_api_key):
            results.append({"name": p["name"], "field": "api_key", "model": p["model"]})

    return JSONResponse(content={
        "has_placeholder": len(results) > 0,
        "items": results,
    })


@_router.put("/api/v1/config/models/capabilities")
async def update_model_capabilities(request: Request) -> JSONResponse:
    """手动覆盖模型能力标记（前端设置用）。"""
    guard_error = await _require_admin_if_auth_enabled(request)
    if guard_error is not None:
        return guard_error
    if _config is None:
        return _error_json_response(503, "服务未初始化")

    from excelmanus.model_probe import update_capabilities_override

    db = _session_manager.database if _session_manager else None
    if db is None:
        return _error_json_response(503, "数据库未启用")

    body = await request.json()
    overrides = body.get("overrides", {})
    if not overrides:
        return _error_json_response(400, "缺少 overrides 字段")

    req_name = body.get("name")
    req_model = body.get("model")
    req_base_url = body.get("base_url")
    model, base_url, _ = _resolve_model_info(req_name, req_model, req_base_url)

    caps = update_capabilities_override(db, model, base_url, overrides)

    if caps is not None and _session_manager is not None:
        await _session_manager.broadcast_model_capabilities(model, caps)

    return JSONResponse(content={
        "capabilities": caps.to_dict() if caps else None,
    })


# ── 运行时配置管理 API ──────────────────────────────────

_RUNTIME_ENV_KEYS: dict[str, str] = {
    "subagent_enabled": "EXCELMANUS_SUBAGENT_ENABLED",
    "backup_enabled": "EXCELMANUS_BACKUP_ENABLED",
    "checkpoint_enabled": "EXCELMANUS_CHECKPOINT_ENABLED",
    "external_safe_mode": "EXCELMANUS_EXTERNAL_SAFE_MODE",
    "max_iterations": "EXCELMANUS_MAX_ITERATIONS",
    "compaction_enabled": "EXCELMANUS_COMPACTION_ENABLED",
    "compaction_threshold_ratio": "EXCELMANUS_COMPACTION_THRESHOLD_RATIO",
    "code_policy_enabled": "EXCELMANUS_CODE_POLICY_ENABLED",
    "tool_schema_validation_mode": "EXCELMANUS_TOOL_SCHEMA_VALIDATION_MODE",
    "tool_schema_validation_canary_percent": "EXCELMANUS_TOOL_SCHEMA_VALIDATION_CANARY_PERCENT",
    "tool_schema_strict_path": "EXCELMANUS_TOOL_SCHEMA_STRICT_PATH",
    "guard_mode": "EXCELMANUS_GUARD_MODE",
    # ── 新增精选核心配置项 ──
    "session_ttl_seconds": "EXCELMANUS_SESSION_TTL_SECONDS",
    "max_sessions": "EXCELMANUS_MAX_SESSIONS",
    "max_consecutive_failures": "EXCELMANUS_MAX_CONSECUTIVE_FAILURES",
    "memory_enabled": "EXCELMANUS_MEMORY_ENABLED",
    "memory_auto_extract_interval": "EXCELMANUS_MEMORY_AUTO_EXTRACT_INTERVAL",
    "max_context_tokens": "EXCELMANUS_MAX_CONTEXT_TOKENS",
    "summarization_enabled": "EXCELMANUS_SUMMARIZATION_ENABLED",
    "window_perception_enabled": "EXCELMANUS_WINDOW_PERCEPTION_ENABLED",
    "vlm_enhance": "EXCELMANUS_VLM_ENHANCE",
    "main_model_vision": "EXCELMANUS_MAIN_MODEL_VISION",
    "parallel_readonly_tools": "EXCELMANUS_PARALLEL_READONLY_TOOLS",
    "chat_history_enabled": "EXCELMANUS_CHAT_HISTORY_ENABLED",
    "hooks_command_enabled": "EXCELMANUS_HOOKS_COMMAND_ENABLED",
    "log_level": "EXCELMANUS_LOG_LEVEL",
    "thinking_effort": "EXCELMANUS_THINKING_EFFORT",
    "thinking_budget": "EXCELMANUS_THINKING_BUDGET",
    "subagent_max_iterations": "EXCELMANUS_SUBAGENT_MAX_ITERATIONS",
    "subagent_timeout_seconds": "EXCELMANUS_SUBAGENT_TIMEOUT_SECONDS",
    "parallel_subagent_max": "EXCELMANUS_PARALLEL_SUBAGENT_MAX",
    "prompt_cache_key_enabled": "EXCELMANUS_PROMPT_CACHE_KEY_ENABLED",
    "auth_enabled": "EXCELMANUS_AUTH_ENABLED",
}


@_router.get("/api/v1/config/runtime")
async def get_runtime_config(request: Request) -> JSONResponse:
    """读取运行时行为配置。"""
    assert _config is not None, "服务未初始化"
    guard_error = await _require_admin_if_auth_enabled(request)
    if guard_error is not None:
        return guard_error
    return JSONResponse(content={
        "auth_enabled": getattr(request.app.state, "auth_enabled", False),
        "subagent_enabled": _config.subagent_enabled,
        "backup_enabled": _config.backup_enabled,
        "checkpoint_enabled": _config.checkpoint_enabled,
        "external_safe_mode": _config.external_safe_mode,
        "max_iterations": _config.max_iterations,
        "compaction_enabled": _config.compaction_enabled,
        "compaction_threshold_ratio": _config.compaction_threshold_ratio,
        "code_policy_enabled": _config.code_policy_enabled,
        "tool_schema_validation_mode": _config.tool_schema_validation_mode,
        "tool_schema_validation_canary_percent": _config.tool_schema_validation_canary_percent,
        "tool_schema_strict_path": _config.tool_schema_strict_path,
        "guard_mode": _config.guard_mode,
        # ── 新增精选核心配置项 ──
        "session_ttl_seconds": _config.session_ttl_seconds,
        "max_sessions": _config.max_sessions,
        "max_consecutive_failures": _config.max_consecutive_failures,
        "memory_enabled": _config.memory_enabled,
        "memory_auto_extract_interval": _config.memory_auto_extract_interval,
        "max_context_tokens": _config.max_context_tokens,
        "summarization_enabled": _config.summarization_enabled,
        "window_perception_enabled": _config.window_perception_enabled,
        "vlm_enhance": _config.vlm_enhance,
        "main_model_vision": _config.main_model_vision,
        "parallel_readonly_tools": _config.parallel_readonly_tools,
        "chat_history_enabled": _config.chat_history_enabled,
        "hooks_command_enabled": _config.hooks_command_enabled,
        "log_level": _config.log_level,
        "thinking_effort": _config.thinking_effort,
        "thinking_budget": _config.thinking_budget,
        "subagent_max_iterations": _config.subagent_max_iterations,
        "subagent_timeout_seconds": _config.subagent_timeout_seconds,
        "parallel_subagent_max": _config.parallel_subagent_max,
        "prompt_cache_key_enabled": _config.prompt_cache_key_enabled,
    })


class RuntimeConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subagent_enabled: bool | None = None
    backup_enabled: bool | None = None
    checkpoint_enabled: bool | None = None
    external_safe_mode: bool | None = None
    max_iterations: int | None = None
    compaction_enabled: bool | None = None
    compaction_threshold_ratio: float | None = None
    code_policy_enabled: bool | None = None
    tool_schema_validation_mode: Literal["off", "shadow", "enforce"] | None = None
    tool_schema_validation_canary_percent: int | None = Field(default=None, ge=0, le=100)
    tool_schema_strict_path: bool | None = None
    guard_mode: Literal["off", "soft"] | None = None
    # ── 新增精选核心配置项 ──
    session_ttl_seconds: int | None = Field(default=None, gt=0)
    max_sessions: int | None = Field(default=None, gt=0)
    max_consecutive_failures: int | None = Field(default=None, gt=0)
    memory_enabled: bool | None = None
    memory_auto_extract_interval: int | None = Field(default=None, ge=0)
    max_context_tokens: int | None = Field(default=None, gt=0)
    summarization_enabled: bool | None = None
    window_perception_enabled: bool | None = None
    vlm_enhance: bool | None = None
    main_model_vision: Literal["auto", "true", "false"] | None = None
    parallel_readonly_tools: bool | None = None
    chat_history_enabled: bool | None = None
    hooks_command_enabled: bool | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = None
    thinking_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
    thinking_budget: int | None = Field(default=None, ge=0)
    subagent_max_iterations: int | None = Field(default=None, gt=0)
    subagent_timeout_seconds: int | None = Field(default=None, gt=0)
    parallel_subagent_max: int | None = Field(default=None, gt=0)
    prompt_cache_key_enabled: bool | None = None
    auth_enabled: bool | None = None


@_router.put("/api/v1/config/runtime")
async def update_runtime_config(request: RuntimeConfigUpdate, raw_request: Request) -> JSONResponse:
    """更新运行时行为配置并持久化到 .env。"""
    assert _config is not None, "服务未初始化"
    guard_error = await _require_admin_if_auth_enabled(raw_request)
    if guard_error is not None:
        return guard_error

    env_path = _find_env_file()
    lines = _read_env_file(env_path)
    updates: dict[str, str] = {}

    payload = request.model_dump(exclude_none=True)
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
        lines = _update_env_var(lines, env_key, str_val)

    _write_env_file(env_path, lines)

    for env_key, env_val in updates.items():
        os.environ[env_key] = env_val

    # 同步更新内存中的 config 实例
    for field, value in payload.items():
        if hasattr(_config, field):
            object.__setattr__(_config, field, value)

    # auth_enabled 变更需要重启服务才能生效
    need_restart = "auth_enabled" in payload

    resp = JSONResponse(content={
        "status": "ok",
        "updated": list(payload.keys()),
        "restarting": need_restart,
    })
    if need_restart:
        from starlette.background import BackgroundTask
        resp.background = BackgroundTask(_restart_process_async)
    return resp


async def _restart_process_async() -> None:
    """响应发送完成后异步触发进程重启。"""
    import threading
    t = threading.Thread(target=_restart_process, daemon=False)
    t.start()
    await asyncio.sleep(5)
    os._exit(0)


def _restart_process() -> None:
    """启动一个独立的重启辅助进程，然后退出当前进程。"""
    import logging as _logging
    import tempfile
    import time as _time

    _log = _logging.getLogger(__name__)

    # 始终使用 venv 的 Python + 已知模块入口，避免 sys.orig_argv 指向全局 Python
    restart_args = [sys.executable, "-c", "from excelmanus.api import main; main()"]
    cmd_line = subprocess.list2cmdline(restart_args)
    _log.info("重启命令: %s", cmd_line)

    try:
        if sys.platform == "win32":
            bat_path = os.path.join(tempfile.gettempdir(), "_excelmanus_restart.bat")
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write("@echo off\r\n")
                f.write("ping 127.0.0.1 -n 6 >nul 2>&1\r\n")
                f.write(f"{cmd_line}\r\n")
            _log.info("重启脚本: %s", bat_path)
            subprocess.Popen(
                f'cmd /c "{bat_path}"',
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW,
            )
        else:
            subprocess.Popen(
                f"sleep 3 && {cmd_line}",
                shell=True,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        _log.exception("启动重启辅助进程失败")
        return

    _log.info("辅助进程已启动，1 秒后退出当前进程")
    _time.sleep(1)
    os._exit(0)


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
            _session_manager.reset_mcp_initialized()
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

    from excelmanus.mcp.client import MCPClientWrapper
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
async def list_mentions(request: Request, path: str = "") -> JSONResponse:
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
    safe_path = path.replace("..", "").strip("/")
    if _config is not None:
        ws = _resolve_workspace_root(request)
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
        "path": safe_path if _config is not None else "",
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
        lines.append(f"- **AUX Base URL**: `{_config.aux_base_url or '继承主配置'}`")
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

    # /registry status
    if command == "/registry status":
        return JSONResponse(content={"result": "文件注册表: 请通过 `/registry scan` 触发扫描\n\n注册表会在会话首轮自动扫描构建", "format": "markdown"})

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


# ── Rules API ───────────────────────────────────────────


class RuleCreateRequest(BaseModel):
    content: str


class RuleUpdateRequest(BaseModel):
    content: str | None = None
    enabled: bool | None = None


@_router.get("/api/v1/rules")
async def list_global_rules() -> list[dict]:
    """列出全局自定义规则。"""
    if _rules_manager is None:
        return []
    return [
        {"id": r.id, "content": r.content, "enabled": r.enabled, "created_at": r.created_at}
        for r in _rules_manager.list_global_rules()
    ]


@_router.post("/api/v1/rules")
async def create_global_rule(req: RuleCreateRequest, request: Request) -> dict:
    if _rules_manager is None:
        return JSONResponse(status_code=503, content={"detail": "规则功能未初始化"})  # type: ignore[return-value]
    guard_error = await _require_admin_if_auth_enabled(request)
    if guard_error is not None:
        return guard_error  # type: ignore[return-value]
    if not req.content.strip():
        return JSONResponse(status_code=400, content={"detail": "规则内容不能为空"})  # type: ignore[return-value]
    r = _rules_manager.add_global_rule(req.content)
    return {"id": r.id, "content": r.content, "enabled": r.enabled, "created_at": r.created_at}


@_router.patch("/api/v1/rules/{rule_id}")
async def update_global_rule(rule_id: str, req: RuleUpdateRequest, request: Request) -> dict:
    if _rules_manager is None:
        return JSONResponse(status_code=503, content={"detail": "规则功能未初始化"})  # type: ignore[return-value]
    guard_error = await _require_admin_if_auth_enabled(request)
    if guard_error is not None:
        return guard_error  # type: ignore[return-value]
    r = _rules_manager.update_global_rule(rule_id, content=req.content, enabled=req.enabled)
    if r is None:
        return JSONResponse(status_code=404, content={"detail": "规则不存在"})  # type: ignore[return-value]
    return {"id": r.id, "content": r.content, "enabled": r.enabled, "created_at": r.created_at}


@_router.delete("/api/v1/rules/{rule_id}")
async def delete_global_rule(rule_id: str, request: Request) -> dict:
    if _rules_manager is None:
        return JSONResponse(status_code=503, content={"detail": "规则功能未初始化"})  # type: ignore[return-value]
    guard_error = await _require_admin_if_auth_enabled(request)
    if guard_error is not None:
        return guard_error  # type: ignore[return-value]
    ok = _rules_manager.delete_global_rule(rule_id)
    if not ok:
        return JSONResponse(status_code=404, content={"detail": "规则不存在"})  # type: ignore[return-value]
    return {"status": "deleted"}


# ── Session Rules API ───────────────────────────────────


@_router.get("/api/v1/sessions/{session_id}/rules")
async def list_session_rules(session_id: str, request: Request) -> list[dict]:
    if _rules_manager is None:
        return []
    if not await _has_session_access(session_id, request):
        return []
    return [
        {"id": r.id, "content": r.content, "enabled": r.enabled, "created_at": r.created_at}
        for r in _rules_manager.list_session_rules(session_id)
    ]


@_router.post("/api/v1/sessions/{session_id}/rules")
async def create_session_rule(session_id: str, req: RuleCreateRequest, request: Request) -> dict:
    if _rules_manager is None:
        return JSONResponse(status_code=503, content={"detail": "规则功能未初始化"})  # type: ignore[return-value]
    if not await _has_session_access(session_id, request):
        return JSONResponse(status_code=404, content={"detail": "会话不存在"})  # type: ignore[return-value]
    if not req.content.strip():
        return JSONResponse(status_code=400, content={"detail": "规则内容不能为空"})  # type: ignore[return-value]
    r = _rules_manager.add_session_rule(session_id, req.content)
    if r is None:
        return JSONResponse(status_code=503, content={"detail": "会话级规则需要数据库支持"})  # type: ignore[return-value]
    return {"id": r.id, "content": r.content, "enabled": r.enabled, "created_at": r.created_at}


@_router.patch("/api/v1/sessions/{session_id}/rules/{rule_id}")
async def update_session_rule(session_id: str, rule_id: str, req: RuleUpdateRequest, request: Request) -> dict:
    if _rules_manager is None:
        return JSONResponse(status_code=503, content={"detail": "规则功能未初始化"})  # type: ignore[return-value]
    if not await _has_session_access(session_id, request):
        return JSONResponse(status_code=404, content={"detail": "会话不存在"})  # type: ignore[return-value]
    r = _rules_manager.update_session_rule(session_id, rule_id, content=req.content, enabled=req.enabled)
    if r is None:
        return JSONResponse(status_code=404, content={"detail": "规则不存在"})  # type: ignore[return-value]
    return {"id": r.id, "content": r.content, "enabled": r.enabled, "created_at": r.created_at}


@_router.delete("/api/v1/sessions/{session_id}/rules/{rule_id}")
async def delete_session_rule(session_id: str, rule_id: str, request: Request) -> dict:
    if _rules_manager is None:
        return JSONResponse(status_code=503, content={"detail": "规则功能未初始化"})  # type: ignore[return-value]
    if not await _has_session_access(session_id, request):
        return JSONResponse(status_code=404, content={"detail": "会话不存在"})  # type: ignore[return-value]
    ok = _rules_manager.delete_session_rule(session_id, rule_id)
    if not ok:
        return JSONResponse(status_code=404, content={"detail": "规则不存在"})  # type: ignore[return-value]
    return {"status": "deleted"}


# ── Memory API ──────────────────────────────────────────


@_router.get("/api/v1/memory")
async def list_memory_entries(request: Request, category: str | None = None) -> list[dict]:
    """列出持久记忆条目，可按类别筛选。

    优先从用户隔离的 SQLite 数据库读取（多租户模式），
    回退到全局 FileMemoryBackend（单用户/未认证模式）。
    """
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

    # 优先走用户隔离的数据库
    user_id = _get_isolation_user_id(request)
    if user_id is not None and _database is not None and _config is not None:
        try:
            from excelmanus.user_scope import UserScope
            scope = UserScope.create(user_id, _database, _config.workspace_root)
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
            # ISO-6: 多租户模式下不回退到全局共享实例，防止跨用户泄露
            return []

    # 仅在匿名模式（单用户）下回退到全局 FileMemoryBackend
    if _api_persistent_memory is None:
        return []
    entries = _api_persistent_memory.list_entries(cat)
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


@_router.delete("/api/v1/memory/{entry_id}")
async def delete_memory_entry(entry_id: str, request: Request) -> dict:
    # 优先走用户隔离的数据库
    user_id = _get_isolation_user_id(request)
    if user_id is not None and _database is not None and _config is not None:
        try:
            from excelmanus.user_scope import UserScope
            scope = UserScope.create(user_id, _database, _config.workspace_root)
            mem_store = scope.memory_store()
            ok = mem_store.delete_entry(entry_id)
            if not ok:
                return JSONResponse(status_code=404, content={"detail": "记忆条目不存在"})  # type: ignore[return-value]
            return {"status": "deleted"}
        except Exception:
            logger.debug("从用户隔离数据库删除记忆失败", exc_info=True)
            # ISO-6: 多租户模式下不回退到全局共享实例
            return JSONResponse(status_code=500, content={"detail": "记忆删除失败"})  # type: ignore[return-value]

    # 仅在匿名模式下回退到全局
    if _api_persistent_memory is None:
        return JSONResponse(status_code=503, content={"detail": "记忆功能未启用"})  # type: ignore[return-value]
    ok = _api_persistent_memory.delete_entry(entry_id)
    if not ok:
        return JSONResponse(status_code=404, content={"detail": "记忆条目不存在"})  # type: ignore[return-value]
    return {"status": "deleted"}


@_router.get("/api/v1/health")
async def health(request: Request) -> dict:
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

    auth_enabled = os.environ.get("EXCELMANUS_AUTH_ENABLED", "").strip().lower() in ("1", "true", "yes")

    # 登录方式配置（供前端登录/注册页动态渲染）
    login_methods = {
        "github_enabled": True,
        "google_enabled": True,
        "qq_enabled": False,
        "email_verify_required": False,
    }
    if auth_enabled:
        try:
            from excelmanus.auth.router import get_login_config
            lc = get_login_config(request)
            login_methods["github_enabled"] = lc.get("login_github_enabled", True)
            login_methods["google_enabled"] = lc.get("login_google_enabled", True)
            login_methods["qq_enabled"] = lc.get("login_qq_enabled", False)
            login_methods["email_verify_required"] = lc.get("email_verify_required", False)
        except Exception:
            pass

    return {
        "status": "ok",
        "version": excelmanus.__version__,
        "model": _config.model if _config is not None else "",
        "tools": tools,
        "skillpacks": skillpacks,
        "active_sessions": active_sessions,
        "auth_enabled": auth_enabled,
        "login_methods": login_methods,
        "session_isolation_enabled": getattr(request.app.state, "session_isolation_enabled", False),
        "docker_sandbox_enabled": getattr(request.app.state, "docker_sandbox_enabled", False),
    }


@_router.get("/api/v1/settings/session-isolation")
async def get_session_isolation(request: Request) -> JSONResponse:
    """查询会话用户隔离开关状态。"""
    return JSONResponse(content={
        "session_isolation_enabled": getattr(
            request.app.state, "session_isolation_enabled", False
        ),
    })


@_router.put("/api/v1/settings/session-isolation")
async def set_session_isolation(request: Request) -> JSONResponse:
    """[DEPRECATED] 会话隔离现在随 auth 自动启用，此接口保留仅为兼容。"""
    auth_enabled = getattr(request.app.state, "auth_enabled", False)
    return JSONResponse(content={
        "status": "ok",
        "session_isolation_enabled": auth_enabled,
        "deprecated": True,
        "message": "Session isolation is now automatic when auth is enabled.",
    })


@_router.get("/api/v1/settings/docker-sandbox")
async def get_docker_sandbox(request: Request) -> JSONResponse:
    """查询 Docker 沙盒状态（含 daemon / 镜像可用性）。"""
    from excelmanus.security.docker_sandbox import is_docker_available, is_sandbox_image_ready

    return JSONResponse(content={
        "docker_sandbox_enabled": getattr(
            request.app.state, "docker_sandbox_enabled", False
        ),
        "docker_available": is_docker_available(),
        "sandbox_image_ready": is_sandbox_image_ready(),
    })


@_router.put("/api/v1/settings/docker-sandbox")
async def set_docker_sandbox(request: Request) -> JSONResponse:
    """管理员切换 Docker 沙盒开关（持久化到 config_kv）。

    请求体: {"enabled": true}  启用
    请求体: {"enabled": false} 关闭
    启用时自动检测 Docker 可用性和镜像状态。
    """
    if getattr(request.app.state, "auth_enabled", False):
        from excelmanus.auth.dependencies import get_current_user_from_request
        user = await get_current_user_from_request(request)
        if user.role != "admin":
            return _error_json_response(403, "需要管理员权限。")

    body = await request.json()
    enabled = bool(body.get("enabled", False))

    if enabled:
        from excelmanus.security.docker_sandbox import (
            is_docker_available,
            is_sandbox_image_ready,
            build_sandbox_image,
        )

        if not is_docker_available():
            return _error_json_response(
                400, "Docker daemon 不可用，无法启用 Docker 沙盒。"
            )
        if not is_sandbox_image_ready():
            ok, msg = build_sandbox_image()
            if not ok:
                return _error_json_response(
                    400, f"沙盒镜像构建失败: {msg}"
                )

    if _config_store is not None:
        _config_store.set("docker_sandbox_enabled", "true" if enabled else "false")
    request.app.state.docker_sandbox_enabled = enabled

    from excelmanus.tools.code_tools import init_docker_sandbox
    init_docker_sandbox(enabled)

    logger.info("Docker 沙盒已%s（管理员操作）", "启用" if enabled else "关闭")
    return JSONResponse(content={
        "status": "ok",
        "docker_sandbox_enabled": enabled,
    })


@_router.post("/api/v1/settings/docker-sandbox/build")
async def build_docker_sandbox_image(request: Request) -> JSONResponse:
    """管理员触发构建/重建 Docker 沙盒镜像。"""
    if getattr(request.app.state, "auth_enabled", False):
        from excelmanus.auth.dependencies import get_current_user_from_request
        user = await get_current_user_from_request(request)
        if user.role != "admin":
            return _error_json_response(403, "需要管理员权限。")

    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    force = bool(body.get("force", False))

    from excelmanus.security.docker_sandbox import build_sandbox_image

    ok, msg = build_sandbox_image(force=force)
    if ok:
        return JSONResponse(content={"status": "ok", "message": msg})
    return _error_json_response(500, f"镜像构建失败: {msg}")


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
