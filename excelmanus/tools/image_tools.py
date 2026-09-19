"""图片工具：read_image。规格编译走 replica_spec，创建写入走 edit_spreadsheet。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from excelmanus.attachments.admit import admit_image_bytes, admit_image_path
from excelmanus.attachments.store import get_attachment_store
from excelmanus.attachments.types import AttachmentError
from excelmanus.engine_core.tool_result import ImageInjection, ToolResult, error_result
from excelmanus.prompt.canonical import TOOL_DESCRIPTIONS
from excelmanus.security import FileAccessGuard, SecurityViolationError
from excelmanus.tools.context import bind_workspace, current_call, require_guard
from excelmanus.tools.registry import ToolDef

_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
_MAX_SIZE_BYTES = 20_000_000


def _get_guard() -> FileAccessGuard:
    return require_guard()


def init_guard(workspace_root: str) -> None:
    bind_workspace(workspace_root)


def _resolve_path(user_path: str) -> Path:
    return _get_guard().resolve_and_validate(user_path)


def _apply_crop(data: bytes, crop: Any) -> bytes:
    if not isinstance(crop, dict):
        return data
    try:
        left = int(crop.get("x", 0))
        top = int(crop.get("y", 0))
        width = int(crop.get("width") or crop.get("w") or 0)
        height = int(crop.get("height") or crop.get("h") or 0)
    except (TypeError, ValueError) as exc:
        raise AttachmentError(f"invalid crop box: {exc}", "INVALID_ARGS") from exc
    if width <= 0 or height <= 0:
        raise AttachmentError("crop width/height must be positive", "INVALID_ARGS")
    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        image.load()
        box = (left, top, left + width, top + height)
        cropped = image.crop(box)
        out = BytesIO()
        fmt = "PNG" if image.mode in {"RGBA", "LA", "P"} else "JPEG"
        cropped.save(out, format=fmt)
        return out.getvalue()


def _inject(ref: Any, *, detail: str, extra: dict[str, Any] | None = None) -> ToolResult:
    value = {
        "status": "ok",
        "mime_type": ref.media_type,
        "attachment_id": ref.attachment_id,
        "width": ref.width,
        "height": ref.height,
        **(extra or {}),
    }
    return ToolResult.from_image_injection(
        model_text="图片已加载到视觉上下文。",
        injection=ImageInjection(
            mime_type=ref.media_type,
            detail=detail,
            attachment=ref.to_dict(),
        ),
        value=value,
    )


def read_image(
    *,
    file_path: str | None = None,
    attachment_id: str | None = None,
    detail: str = "auto",
    crop: dict[str, Any] | None = None,
) -> ToolResult:
    """读取工作区路径或按 attachment_id 取回；可选裁剪后注入新变体。"""
    path_text = str(file_path or "").strip()
    attach = str(attachment_id or "").strip()
    if not path_text and not attach:
        return error_result("file_path 或 attachment_id 必须提供一个", code="INVALID_ARGS")

    if path_text:
        try:
            path = _resolve_path(path_text)
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
        try:
            ref = admit_image_path(path)
            if crop:
                raw = get_attachment_store().get_bytes(ref)
                ref = admit_image_bytes(_apply_crop(raw, crop), media_type=ref.media_type)
        except AttachmentError as exc:
            return error_result(str(exc), code=exc.code)
        return _inject(ref, detail=detail, extra={"file_path": str(path), "size_bytes": size})

    store = get_attachment_store()
    ref = store.get_ref(attach)
    if ref is None:
        return error_result(f"附件不存在: {attach}", code="missing")
    ctx = current_call()
    if ctx is None or attach not in ctx.durable_attachment_ids:
        return error_result(
            f"无权按 attachment_id 取回: {attach}",
            code="permission_denied",
        )
    try:
        raw = store.get_bytes(ref)
        if crop:
            raw = _apply_crop(raw, crop)
            ref = admit_image_bytes(raw, media_type=ref.media_type)
    except AttachmentError as exc:
        code = "corrupt" if "CORRUPT" in str(exc.code) else "missing"
        return error_result(str(exc), code=code)
    return _inject(ref, detail=detail)


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
                    "attachment_id": {
                        "type": "string",
                        "description": "当前会话历史中的附件 id（sha256:...）",
                    },
                    "detail": {
                        "type": "string",
                        "enum": ["auto", "low", "high"],
                        "default": "auto",
                        "description": "图片分析精度",
                    },
                    "crop": {
                        "type": "object",
                        "description": "可选裁剪框 {x,y,width,height}",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
                    },
                },
                "additionalProperties": False,
            },
            func=read_image,
            max_result_chars=2000,
            write_effect="none",
        ),
    ]
