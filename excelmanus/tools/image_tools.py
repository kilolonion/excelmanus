"""图片工具：read_image。规格编译走 replica_spec，创建写入走 edit_spreadsheet。"""

from __future__ import annotations

import base64
from pathlib import Path

from excelmanus.engine_core.tool_result import ImageInjection, ToolResult, error_result
from excelmanus.prompt.canonical import TOOL_DESCRIPTIONS
from excelmanus.security import FileAccessGuard, SecurityViolationError
from excelmanus.tools._guard_ctx import get_guard as _get_ctx_guard
from excelmanus.tools.registry import ToolDef

_guard: FileAccessGuard | None = None
_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
_MAX_SIZE_BYTES = 20_000_000


def _get_guard() -> FileAccessGuard:
    """获取或创建 FileAccessGuard（优先 per-session contextvar）。"""
    ctx_guard = _get_ctx_guard()
    if ctx_guard is not None:
        return ctx_guard
    global _guard
    if _guard is None:
        _guard = FileAccessGuard(".")
    return _guard


def init_guard(workspace_root: str) -> None:
    global _guard
    _guard = FileAccessGuard(workspace_root)


def _resolve_path(user_path: str) -> Path:
    """解析路径；仅在显式初始化 guard 后执行工作区校验。"""
    if _guard is None:
        return Path(user_path)
    return _get_guard().resolve_and_validate(user_path)


def read_image(*, file_path: str, detail: str = "auto") -> ToolResult:
    """读取本地图片并注入视觉上下文；不把 base64 写进 model_text。"""
    try:
        path = _resolve_path(file_path)
    except SecurityViolationError as exc:
        return error_result(f"路径校验失败: {exc}", code="PATH_INVALID")
    if not path.is_file():
        return error_result(f"文件不存在: {file_path}", code="PATH_INVALID")
    if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
        return error_result(f"不支持的图片格式: {path.suffix}", code="INVALID_ARGS")
    size = path.stat().st_size
    if size > _MAX_SIZE_BYTES:
        return error_result(
            f"文件大小超限: {size} bytes > {_MAX_SIZE_BYTES}",
            code="LIMIT_EXCEEDED",
        )

    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    ext = path.suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }
    mime = mime_map.get(ext, "image/png")
    value = {
        "status": "ok",
        "mime_type": mime,
        "size_bytes": size,
        "file_path": str(path),
    }
    return ToolResult.from_image_injection(
        model_text="图片已加载到视觉上下文。",
        injection=ImageInjection(base64=b64, mime_type=mime, detail=detail),
        value=value,
    )


def get_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="read_image",
            description=TOOL_DESCRIPTIONS["read_image"],
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "图片文件的绝对或相对路径",
                    },
                    "detail": {
                        "type": "string",
                        "enum": ["auto", "low", "high"],
                        "default": "auto",
                        "description": "图片分析精度",
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=read_image,
            max_result_chars=2000,
            write_effect="none",
        ),
    ]
