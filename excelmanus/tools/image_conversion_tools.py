"""Workspace image conversion used by workbook/report pipelines.

The chat image reader deliberately remains read-only.  ``convert_image`` is a
separate, explicit write tool so raster normalization (PNG/JPEG/WebP/GIF/BMP)
can be composed with Excel exports without smuggling binary data through the
model response.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from excelmanus.engine_core.tool_result import from_payload, error_result
from excelmanus.prompt.canonical import TOOL_DESCRIPTIONS
from excelmanus.security import SecurityViolationError
from excelmanus.tools.context import bind_workspace, require_guard, operation_id_for
from excelmanus.tools.registry import ToolDef

_FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP", "gif": "GIF", "bmp": "BMP"}
_MAX_BYTES = 50 * 1024 * 1024


def init_guard(workspace_root: str) -> None:
    bind_workspace(workspace_root)


def convert_image(
    file_path: str,
    output_path: str,
    format: str | None = None,
    width: int | None = None,
    height: int | None = None,
    fit: str = "contain",
    quality: int = 90,
    expected_output_version: str | None = None,
):
    """Convert one workspace image and publish the result atomically."""
    try:
        from PIL import Image, ImageOps
        from excelmanus.workspace.file_service import TargetSpec, ReadDependency, service_for_guard
        from excelmanus.workbook_commit import content_version_of_file

        guard = require_guard()
        src = guard.resolve_and_validate(file_path)
        dest = guard.resolve_and_validate(output_path)
        if not src.is_file():
            return error_result(f"图片文件不存在: {file_path}", code="PATH_INVALID")
        if src.stat().st_size > _MAX_BYTES:
            return error_result(f"图片超过 {_MAX_BYTES} 字节限制", code="LIMIT_EXCEEDED")
        if dest == src:
            return error_result("输出路径必须与源图片不同", code="INVALID_ARGS")
        out_format = (format or dest.suffix.lstrip(".") or "png").lower()
        if out_format not in _FORMATS:
            return error_result(f"不支持的输出格式: {out_format}", code="INVALID_ARGS")
        if fit not in {"contain", "cover", "stretch"}:
            return error_result("fit 必须是 contain、cover 或 stretch", code="INVALID_ARGS")
        if width is not None and (isinstance(width, bool) or width < 1 or width > 12000):
            return error_result("width 必须在 1..12000", code="INVALID_ARGS")
        if height is not None and (isinstance(height, bool) or height < 1 or height > 12000):
            return error_result("height 必须在 1..12000", code="INVALID_ARGS")
        if isinstance(quality, bool) or not 1 <= quality <= 100:
            return error_result("quality 必须在 1..100", code="INVALID_ARGS")
        with Image.open(src) as original:
            image = ImageOps.exif_transpose(original)
            source_size = {"width": image.width, "height": image.height}
            if width or height:
                target_w, target_h = width or round(image.width * (height / image.height)), height or round(image.height * (width / image.width))
                if fit == "stretch":
                    image = image.resize((max(1, target_w), max(1, target_h)), Image.Resampling.LANCZOS)
                elif fit == "cover":
                    image = ImageOps.fit(image, (max(1, target_w), max(1, target_h)), method=Image.Resampling.LANCZOS)
                else:
                    image.thumbnail((max(1, target_w), max(1, target_h)), Image.Resampling.LANCZOS)
            # JPEG has no alpha channel; flatten transparent pixels against white.
            if _FORMATS[out_format] == "JPEG":
                if image.mode in {"RGBA", "LA", "P"}:
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, "white")
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")
            elif _FORMATS[out_format] == "PNG" and image.mode not in {"RGB", "RGBA", "L", "LA", "P"}:
                image = image.convert("RGBA")
            stream = BytesIO()
            save_options: dict[str, Any] = {}
            if _FORMATS[out_format] in {"JPEG", "WEBP"}:
                save_options["quality"] = quality
            if _FORMATS[out_format] == "GIF":
                save_options["save_all"] = True
            image.save(stream, format=_FORMATS[out_format], **save_options)
        data = stream.getvalue()
        rel_src = src.relative_to(guard.workspace_root).as_posix()
        rel_dest = dest.relative_to(guard.workspace_root).as_posix()
        if dest.exists() and expected_output_version is None:
            return error_result("输出文件已存在，请提供 expected_output_version", code="VERSION_CONFLICT")
        receipt = service_for_guard(guard).apply_batch(
            [TargetSpec("update" if dest.exists() else "create", rel_dest, data=data, expected_version=expected_output_version if dest.exists() else None)],
            operation_id=operation_id_for("convert_image"),
            read_dependencies=[ReadDependency(rel_src, content_version_of_file(src) or "")],
        )
        after = content_version_of_file(dest)
        return from_payload({
            "status": "success", "file_path": rel_dest, "source_path": rel_src,
            "format": out_format, "source_size": source_size,
            "output_size": {"width": image.width, "height": image.height},
            "size_bytes": len(data), "content_version": after,
            "source_version": content_version_of_file(src), "receipt": receipt.to_dict(),
        })
    except SecurityViolationError as exc:
        return error_result(f"路径校验失败: {exc}", code="PATH_INVALID")
    except Exception as exc:
        return error_result(f"图片转换失败: {exc}", code=getattr(exc, "code", "INVALID_ARGS"))


def get_tools() -> list[ToolDef]:
    return [ToolDef(
        name="convert_image",
        description=TOOL_DESCRIPTIONS["convert_image"],
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "file_path": {"type": "string"}, "output_path": {"type": "string"},
                "format": {"type": "string", "enum": sorted(_FORMATS)},
                "width": {"type": "integer", "minimum": 1, "maximum": 12000},
                "height": {"type": "integer", "minimum": 1, "maximum": 12000},
                "fit": {"type": "string", "enum": ["contain", "cover", "stretch"]},
                "quality": {"type": "integer", "minimum": 1, "maximum": 100},
                "expected_output_version": {"type": "string"},
            }, "required": ["file_path", "output_path"],
        }, func=convert_image, write_effect="workspace_write", max_result_chars=4000,
    )]
