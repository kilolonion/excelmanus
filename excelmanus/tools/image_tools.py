"""图片工具：read_image。规格编译走 workbook.spec，创建写入走 apply_spreadsheet_changes。"""

from __future__ import annotations

from io import BytesIO
import json
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
_LAYOUT_MAX_SIDE = 1600
_LAYOUT_MAX_LINES = 64


def _dark_runs(values: list[int], threshold: int, *, scale: float) -> list[dict[str, int]]:
    """Return bounded runs of projection values above ``threshold``.

    This is intentionally a small, deterministic hint for a visual-replica
    workflow.  It is not OCR and does not claim that every dark run is a
    spreadsheet border.  Keeping the result as runs (rather than every pixel)
    makes it useful to the model without encouraging repeated pixel scans in
    ``run_code``.
    """
    runs: list[dict[str, int]] = []
    start: int | None = None
    for index, value in enumerate(values):
        if value >= threshold and start is None:
            start = index
        elif value < threshold and start is not None:
            runs.append({"start_px": round(start / scale), "end_px": round(index / scale - 1)})
            start = None
    if start is not None:
        runs.append({"start_px": round(start / scale), "end_px": round((len(values) - 1) / scale)})
    # Very long runs are usually the page edge/background rather than useful
    # row separators.  Preserve a bounded, stable prefix for disclosure.
    return runs[:_LAYOUT_MAX_LINES]


def _layout_summary(source: bytes, *, attachment_id: str | None = None) -> dict[str, Any]:
    """Estimate image rules and bounds in one bounded pass.

    The summary deliberately reports *candidates* and an algorithm marker.
    A model still needs the attached image/preview to make the final visual
    decision, but it no longer needs to write a new PIL threshold script for
    every small adjustment.
    """
    from PIL import Image, ImageOps

    with Image.open(BytesIO(source)) as original:
        image = ImageOps.exif_transpose(original).convert("L")
        width, height = image.size
        scale = min(1.0, _LAYOUT_MAX_SIDE / max(width, height, 1))
        if scale < 1.0:
            sample = image.resize((max(1, round(width * scale)), max(1, round(height * scale))))
        else:
            sample = image
        sw, sh = sample.size
        flattened = getattr(sample, "get_flattened_data", None)
        pixels = list(flattened()) if callable(flattened) else list(sample.getdata())
    # Dark-pixel projections are cheap and dependency free.  The threshold is
    # intentionally conservative so faint receipt rules remain candidates.
    threshold = 210
    row_counts = [0] * sh
    col_counts = [0] * sw
    min_x, min_y, max_x, max_y = sw, sh, -1, -1
    for y in range(sh):
        offset = y * sw
        row = pixels[offset:offset + sw]
        count = 0
        for x, value in enumerate(row):
            if value < threshold:
                count += 1
                col_counts[x] += 1
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
        row_counts[y] = count
    horizontal = _dark_runs(row_counts, max(3, round(sw * 0.42)), scale=scale)
    vertical = _dark_runs(col_counts, max(3, round(sh * 0.42)), scale=scale)
    bounds = None
    if max_x >= min_x and max_y >= min_y:
        bounds = {
            "left_px": round(min_x / scale),
            "top_px": round(min_y / scale),
            "right_px": round(max_x / scale),
            "bottom_px": round(max_y / scale),
        }
    result: dict[str, Any] = {
        "algorithm": "dark_pixel_projection_v1",
        "source_size": {"width": width, "height": height},
        "analysis_size": {"width": sw, "height": sh},
        "threshold": threshold,
        "candidate_horizontal_rules": horizontal,
        "candidate_vertical_rules": vertical,
        "ink_bounds": bounds,
        "grid_hint": bool(len(horizontal) >= 2 and len(vertical) >= 2),
        "confidence": "hint_only",
    }
    # When both axes expose at least two candidates, offer a directly
    # translatable LayoutReference shape.  It remains explicitly a candidate
    # because text strokes can also produce dark projection runs.
    if bounds and len(horizontal) >= 2 and len(vertical) >= 2:
        left, top = bounds["left_px"], bounds["top_px"]
        right, bottom = bounds["right_px"], bounds["bottom_px"]
        width_px, height_px = max(1, right - left), max(1, bottom - top)

        def _centers(runs: list[dict[str, int]], start: int, end: int) -> list[float]:
            positions = [
                (float(run["start_px"]) + float(run["end_px"])) / 2
                for run in runs
                if start <= run["start_px"] <= end
            ]
            interior = [
                max(0.0, min(1.0, (pos - start) / max(1, end - start)))
                for pos in positions
            ]
            # The outer border is already represented by 0/1.  Suppress its
            # one-pixel duplicate so a framed receipt does not become an
            # extra empty row/column in the candidate.
            interior = [value for value in interior if 0.02 < value < 0.98]
            return sorted(set(round(value, 6) for value in [0.0, *interior, 1.0]))

        result["layout_reference_candidate"] = {
            "attachment_id": attachment_id,
            "table_bbox_px": [left, top, width_px, height_px],
            "column_edges": _centers(vertical, left, right),
            "row_edges": _centers(horizontal, top, bottom),
            "target_width_px": min(8192, max(1, width_px)),
            "confidence": "candidate_requires_visual_check",
        }
    return result


def _get_guard() -> FileAccessGuard:
    return require_guard()


def init_guard(workspace_root: str) -> None:
    bind_workspace(workspace_root)


def _resolve_path(user_path: str) -> Path:
    return _get_guard().resolve_and_validate(user_path)


def _source_bytes(ref: Any, store: Any) -> bytes:
    """Read immutable source bytes, with a legacy normalized fallback."""
    try:
        return store.get_source(ref) if ref.source_digest else store.get_bytes(ref)
    except (AttachmentError, OSError):
        return store.get_bytes(ref)


def _inject(ref: Any, *, detail: str, extra: dict[str, Any] | None = None) -> ToolResult:
    extra = dict(extra or {})
    source_dimensions = ref.source_dimensions or ref.original_dimensions
    source_width = source_dimensions.width if source_dimensions is not None else ref.width
    source_height = source_dimensions.height if source_dimensions is not None else ref.height
    legacy_source = ref.source_digest is not None and ref.source_dimensions is None and ref.original_dimensions is not None
    source_media = ref.source_media_type or ("unknown" if legacy_source else ref.media_type)
    source_bytes = ref.source_bytes if ref.source_bytes is not None else ("unknown" if legacy_source else ref.bytes)
    value = {
        "status": "ok",
        "mime_type": ref.media_type,
        "attachment_id": ref.attachment_id,
        "width": ref.width,
        "height": ref.height,
        "source_digest": ref.source_digest,
        "source_dimensions": (
            {"width": source_width, "height": source_height}
            if source_dimensions is not None
            else None
        ),
        "source_media_type": source_media,
        "source_bytes": source_bytes,
        "source_orientation": int(ref.source_orientation or 1),
        "animated": bool(ref.animated),
        "frame_count": int(ref.frame_count or 1),
        "parent_attachment_id": ref.parent_attachment_id,
        "crop_in_parent": ref.crop_in_parent,
        "crop_zoom": ref.crop_zoom,
        **extra,
    }
    summary = (
        "图片已加载到视觉上下文"
        f"（attachment_id={ref.attachment_id}，源尺寸={source_width}x{source_height}px，"
        f"源格式={source_media}，源字节={source_bytes}，detail={detail}"
    )
    crop = extra.get("crop")
    if isinstance(crop, dict):
        summary += f"，crop={crop}"
    if ref.animated or ref.frame_count > 1:
        summary += f"，动画={ref.frame_count}帧（视觉请求取首帧）"
    if ref.source_orientation != 1:
        summary += f"，EXIF方向={ref.source_orientation}（已校正）"
    layout = extra.get("layout")
    if isinstance(layout, dict):
        # ``value`` is retained for UI/audit consumers, while the concise
        # candidate is also placed in model_text because that is the provider
        # facing channel for ToolResult.  Keep it bounded and avoid dumping
        # every projection pixel into the prompt.
        concise = {
            "source_size": layout.get("source_size"),
            "ink_bounds": layout.get("ink_bounds"),
            "horizontal_rules": layout.get("candidate_horizontal_rules", [])[:16],
            "vertical_rules": layout.get("candidate_vertical_rules", [])[:16],
            "layout_reference_candidate": layout.get("layout_reference_candidate"),
            "confidence": layout.get("confidence", "hint_only"),
        }
        summary += "；布局候选=" + json.dumps(concise, ensure_ascii=False, separators=(",", ":"))
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
    """Crop in source coordinates and optionally enlarge for a pixel-level read."""
    from dataclasses import replace
    import math
    from PIL import Image
    from excelmanus.attachments.normalize import _open_oriented
    store = get_attachment_store()
    source = _source_bytes(ref, store)
    try:
        x, y, width, height = (int(crop[key]) for key in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError):
        raise AttachmentError("crop 必须包含整数 x、y、width、height", "INVALID_ARGS")
    logical_dimensions = ref.source_dimensions or ref.original_dimensions
    logical_width = logical_dimensions.width if logical_dimensions is not None else ref.width
    logical_height = logical_dimensions.height if logical_dimensions is not None else ref.height
    try:
        zoom = float(crop.get("zoom", 1) or 1)
    except (TypeError, ValueError):
        raise AttachmentError("crop.zoom 必须是 1..8 的数字", "INVALID_ARGS")
    if not math.isfinite(zoom) or zoom < 1 or zoom > 8:
        raise AttachmentError("crop.zoom 必须是 1..8 的数字", "INVALID_ARGS")
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > logical_width or y + height > logical_height:
        raise AttachmentError("Crop exceeds attachment bounds", "INVALID_ARGS")
    image = _open_oriented(source)
    try:
        sx, sy = image.width / max(1, logical_width), image.height / max(1, logical_height)
        region = image.crop((round(x * sx), round(y * sy), round((x + width) * sx), round((y + height) * sy)))
        if zoom > 1:
            region = region.resize(
                (min(8192, max(1, round(region.width * zoom))),
                 min(8192, max(1, round(region.height * zoom)))),
                Image.Resampling.LANCZOS,
            )
        out = BytesIO()
        region.convert("RGBA" if "A" in region.getbands() else "RGB").save(out, format="PNG")
    finally:
        image.close()
    child = admit_image_bytes(out.getvalue(), media_type="image/png", store=store)
    child = replace(child, parent_attachment_id=ref.attachment_id,
                    crop_in_parent={"x": x, "y": y, "width": width, "height": height},
                    crop_zoom=zoom)
    return store.put(store.get_bytes(child), child)


def read_image(
    *,
    file_path: str | None = None,
    attachment_id: str | None = None,
    detail: str = "auto",
    crop: dict[str, Any] | None = None,
    analyze_layout: bool = False,
) -> ToolResult:
    """读取工作区路径或附件；可选裁剪并一次性给出布局候选。

    ``analyze_layout`` 是视觉复刻的只读辅助。它只报告有界的暗像素
    投影候选，不能替代模型查看原图，也不执行 OCR。
    """
    detail = str(detail or "auto").strip().lower()
    if detail not in {"auto", "low", "high"}:
        return error_result("detail 必须是 auto、low 或 high", code="INVALID_ARGS")
    if crop is not None and not isinstance(crop, dict):
        return error_result("crop 必须是 {x,y,width,height,zoom} 对象", code="INVALID_ARGS")
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
        if analyze_layout:
            try:
                source = _source_bytes(ref, get_attachment_store())
                extra["layout"] = _layout_summary(source, attachment_id=ref.attachment_id)
            except Exception as exc:
                return error_result(f"图片布局分析失败: {exc}", code="INVALID_ARGS")
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
        # Prefer the immutable source for legacy refs as well.  If a historic
        # source sidecar was pruned, the normalized object remains a safe
        # compatibility fallback.
        _source_bytes(ref, store)
        if crop:
            ref = _crop_reference(ref, crop)
    except AttachmentError as exc:
        code = "corrupt" if "CORRUPT" in str(exc.code) else "missing"
        return error_result(str(exc), code=code)
    extra = {"crop": dict(crop)} if isinstance(crop, dict) else {}
    if analyze_layout:
        try:
            extra["layout"] = _layout_summary(
                _source_bytes(ref, store),
                attachment_id=ref.attachment_id,
            )
        except Exception as exc:
            return error_result(f"图片布局分析失败: {exc}", code="INVALID_ARGS")
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
                        "description": "可选源图坐标裁剪框 {x,y,width,height,zoom}；zoom=1..8 会把该区域放大后再注入，适合小字/像素级复核",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                            "zoom": {"type": "number", "minimum": 1, "maximum": 8, "default": 1},
                        },
                        "required": ["x", "y", "width", "height"],
                        "additionalProperties": False,
                    },
                    "analyze_layout": {
                        "type": "boolean",
                        "default": False,
                        "description": "视觉复刻时建议为 true；一次性返回有界的行列线候选和墨迹边界（只读提示，不是 OCR）",
                    },
                },
                "additionalProperties": False,
            },
            func=read_image,
            # A bounded result keeps the layout hint and transform receipt
            # visible without allowing a large auxiliary payload to trigger
            # another image-reading loop.
            max_result_chars=3500,
            cache_ttl_seconds=600,
            write_effect="none",
        ),
    ]
