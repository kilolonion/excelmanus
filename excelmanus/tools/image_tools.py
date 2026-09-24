"""图片工具：read_image。规格编译走 workbook.spec，创建写入走 apply_spreadsheet_changes。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from excelmanus.attachments.admit import admit_image_bytes, admit_image_path
from excelmanus.attachments.store import get_attachment_store
from excelmanus.attachments.types import AttachmentError
from excelmanus.engine_core.error_payload import PERMISSION_DENIED
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


def _inject(ref: Any, *, detail: str, extra: dict[str, Any] | None = None) -> ToolResult:
    extra = dict(extra or {})
    value = {
        "status": "ok",
        "mime_type": ref.media_type,
        "attachment_id": ref.attachment_id,
        "width": ref.width,
        "height": ref.height,
        "source_digest": ref.source_digest,
        "parent_attachment_id": ref.parent_attachment_id,
        "crop_in_parent": ref.crop_in_parent,
        **extra,
    }
    summary = (
        "图片已加载到视觉上下文"
        f"（attachment_id={ref.attachment_id}，尺寸={ref.width}x{ref.height}px，detail={detail}"
    )
    crop = extra.get("crop")
    if isinstance(crop, dict):
        summary += f"，crop={crop}"
    summary += "）。"
    return ToolResult.from_image_injection(
        model_text=summary,
        injection=ImageInjection(
            mime_type=ref.media_type,
            detail=detail,
            attachment=ref.to_dict(),
        ),
        value=value,
    )


def _crop_reference(ref: Any, crop: dict[str, Any]) -> Any:
    """Crop in attachment coordinates, preserving ancestry and original pixels."""
    from dataclasses import replace
    from PIL import Image, ImageOps
    store = get_attachment_store()
    source = store.get_source(ref) if ref.source_digest else store.get_bytes(ref)
    x, y, width, height = (int(crop.get(key, 0)) for key in ("x", "y", "width", "height"))
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > ref.width or y + height > ref.height:
        raise AttachmentError("Crop exceeds attachment bounds", "INVALID_ARGS")
    with Image.open(BytesIO(source)) as original:
        image = ImageOps.exif_transpose(original)
        sx, sy = image.width / ref.width, image.height / ref.height
        region = image.crop((round(x * sx), round(y * sy), round((x + width) * sx), round((y + height) * sy)))
        out = BytesIO()
        region.convert("RGBA" if "A" in region.getbands() else "RGB").save(out, format="PNG")
    child = admit_image_bytes(out.getvalue(), media_type="image/png")
    child = replace(child, parent_attachment_id=ref.attachment_id,
                    crop_in_parent={"x": x, "y": y, "width": width, "height": height})
    return store.put(store.get_bytes(child), child)


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
                ref = _crop_reference(ref, crop)
        except AttachmentError as exc:
            return error_result(str(exc), code=exc.code)
        extra = {"file_path": str(path), "size_bytes": size}
        if isinstance(crop, dict):
            extra["crop"] = dict(crop)
        return _inject(ref, detail=detail, extra=extra)

    store = get_attachment_store()
    ref = store.get_ref(attach)
    if ref is None:
        return error_result(f"附件不存在: {attach}", code="missing")
    ctx = current_call()
    if ctx is None or attach not in ctx.durable_attachment_ids:
        return error_result(
            f"无权按 attachment_id 取回: {attach}",
            code=PERMISSION_DENIED,
            remediation=(
                "该附件未出现在当前会话的有效历史中。若用户已提供工作区原图，"
                "可用 read_image(file_path=...) 读取；否则请用户在本会话重新上传。不要同参重试。"
            ),
        )
    try:
        raw = store.get_bytes(ref)
        if crop:
            ref = _crop_reference(ref, crop)
    except AttachmentError as exc:
        code = "corrupt" if "CORRUPT" in str(exc.code) else "missing"
        return error_result(str(exc), code=code)
    extra = {"crop": dict(crop)} if isinstance(crop, dict) else None
    return _inject(ref, detail=detail, extra=extra)


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
