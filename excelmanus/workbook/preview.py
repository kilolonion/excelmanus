"""Readonly visual observations; exports use the separate artifact tool."""

from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from pathlib import Path
import shutil
import sys
import tempfile

from excelmanus.workbook.snapshot import open_snapshot
from excelmanus.workbook.service import WorkbookService
from excelmanus.workbook.read_cache import ReadCache
from excelmanus.workbook.render_environment import (
    renderer_assets,
    environment_fingerprint,
)

_RENDER_CACHE = ReadCache(max_bytes=32 * 1024 * 1024, max_entries=16)
_RENDER_TTL = 300
from excelmanus.tools.spreadsheet_engine_tools import _run, _office_convert, _cancelled
from excelmanus.workbook_commit import CommitError
from excelmanus.attachments.admit import admit_image_bytes
from excelmanus.engine_core.tool_result import ToolResult, ImageInjection


def preview_workbook(
    file_path: str,
    *,
    sheet: str,
    range: str,
    expected_version: str | None = None,
    surface: str = "auto",
    page: int = 1,
) -> ToolResult:
    if (
        surface not in {"auto", "workbench", "print"}
        or isinstance(page, bool)
        or not isinstance(page, int)
        or not 1 <= page <= 200
    ):
        raise ValueError("surface must be auto/workbench/print; page must be 1..200")
    from excelmanus.workbook.refs import parse_rect

    rect = parse_rect(range)
    if rect.whole_column or rect.whole_row:
        raise ValueError("Preview requires one finite rectangle")
    snap = open_snapshot(file_path, expected_version=expected_version)
    if snap.is_csv():
        raise CommitError(
            "RENDERER_UNAVAILABLE",
            "CSV has no presentation geometry; create an XLSX for visual preview",
        )
    observation = WorkbookService().observe_snapshot(
        snap,
        sheet=sheet,
        range=range,
        mode="range",
        facets=["data", "presentation", "geometry", "objects"],
    )
    requested_surface = surface
    drawing_coverage = _drawing_coverage(snap, sheet, rect, observation)
    fit_range = surface == "auto" and bool(drawing_coverage["intersecting_objects"])
    if surface == "auto":
        surface = "print" if fit_range else "workbench"
    if fit_range and page != 1:
        raise ValueError("auto 图表预览整段缩放为一页；实际打印分页请用 surface='print'")
    _cancelled()
    assets, asset_digest = renderer_assets() if surface == "workbench" else (None, None)
    font_fingerprint = environment_fingerprint()
    from excelmanus.attachments.store import get_attachment_store

    cache_key = (
        str(get_attachment_store().root),
        snap.id.key(),
        sheet,
        range,
        surface,
        fit_range,
        page,
        asset_digest,
        font_fingerprint,
        int(time.time() // _RENDER_TTL),
    )

    def render():
        return _render(
            snap,
            observation,
            file_path,
            sheet,
            range,
            surface,
            page,
            assets,
            asset_digest,
            font_fingerprint,
            fit_range=fit_range,
        )

    if surface == "workbench":
        value, attachment = _RENDER_CACHE.get_or_create(
            cache_key, lambda: _cache_product(render())
        )
    else:
        value, attachment = render()
    _cancelled()
    # The cache owns immutable values; callers cannot mutate subsequent results.
    value, attachment = deepcopy(value), deepcopy(attachment)
    value["requested_surface"] = requested_surface
    value["visual_coverage"] = {
        **drawing_coverage,
        "complete_objects": drawing_coverage["complete_objects"] if surface == "print" else [],
        "whole_region": surface == "workbench" or value.get("measured", {}).get("page_count") == 1,
        "fit_to_page": fit_range,
    }
    return ToolResult.from_image_injection(
        model_text=json.dumps(value, ensure_ascii=False),
        injection=ImageInjection(
            mime_type=attachment["mediaType"], detail="high", attachment=attachment
        ),
        value=value,
    )


def _cache_product(result):
    from excelmanus.attachments.store import get_attachment_store

    value, attachment = result
    ref = get_attachment_store().get_ref(attachment["attachmentId"])
    return result, ref.bytes + len(json.dumps(value))


def _drawing_coverage(snap, sheet, rect, observation):
    from excelmanus.workbook.geometry import axis_offset, drawing_preview_range
    workbook = snap.open_workbook(data_only=False, read_only=False)
    try:
        ws = workbook[sheet]
        x0, y0 = axis_offset(ws, rect.min_col, axis="column"), axis_offset(ws, rect.min_row, axis="row")
        x1, y1 = axis_offset(ws, rect.max_col + 1, axis="column"), axis_offset(ws, rect.max_row + 1, axis="row")
        suggestions = {obj["id"]: drawing_preview_range(ws, obj) for obj in observation["regions"][0].get("objects", [])}
    finally:
        workbook.close()
    intersecting, complete, cropped = [], [], []
    for obj in observation["regions"][0].get("objects", []):
        box = obj.get("bounds") or {}
        if any(box.get(key) is None for key in ("x", "y", "width", "height")):
            continue
        left, top, right, bottom = box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"]
        if left < x1 and right > x0 and top < y1 and bottom > y0:
            intersecting.append(obj["id"])
            (complete if left >= x0 and top >= y0 and right <= x1 and bottom <= y1 else cropped).append(obj["id"])
    return {"intersecting_objects": intersecting, "complete_objects": complete,
            "cropped_objects": cropped, "geometry_basis": "estimated_sheet_pixels",
            "next_calls": [{"tool": "preview_spreadsheet", "arguments": {
                "file_path": snap.file.relative, "expected_version": snap.content_version,
                "sheet": sheet, "range": suggestions[key], "surface": "auto"}}
                for key in cropped if suggestions.get(key)]}


def _render(
    snap,
    observation,
    file_path,
    sheet,
    range,
    surface,
    page,
    assets,
    asset_digest,
    font_fingerprint,
    *, fit_range=False,
):
    with tempfile.TemporaryDirectory(prefix="excelmanus-preview-") as temp:
        root = Path(temp)
        output = root / "preview.png"
        if surface == "workbench":
            if page != 1:
                raise ValueError("workbench preview uses ranges, not page numbers")
            source, meta = root / "observation.json", root / "render.json"
            source.write_text(json.dumps(observation, ensure_ascii=False, default=str), encoding="utf-8")
            command = (
                [sys.executable, "--workbook-preview-worker"]
                if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "excelmanus.workbook.preview_worker"]
            )
            try:
                _run([*command, str(source), str(output), str(meta)], timeout=45)
            except CommitError as exc:
                if "RENDERER_UNAVAILABLE" in str(
                    exc
                ) or "Executable doesn't exist" in str(exc):
                    raise CommitError(
                        "RENDERER_UNAVAILABLE",
                        "Install Chromium with python -m playwright install chromium",
                    ) from exc
                raise
            measured = json.loads(meta.read_text(encoding="utf-8"))
            engine_digest = asset_digest
        else:
            from excelmanus.workbook.ooxml import render_selection

            src = root / "input.xlsx"
            src.write_bytes(render_selection(snap.read_bytes(), sheet, range, fit_to_page=fit_range))
            out = root / "out"
            out.mkdir()
            pdf, engine = _office_convert(src, out, "pdf:calc_pdf_Export")
            import re
            counter = shutil.which("pdfinfo")
            count = re.search(r"Pages:\s*(\d+)", _run([counter, str(pdf)], timeout=15)) if counter else None
            page_count = int(count[1]) if count else None
            if page_count is not None and page > page_count:
                raise ValueError(f"预览只有 {page_count} 页，page={page} 超出范围")
            converter = shutil.which("pdftoppm")
            if not converter:
                raise CommitError(
                    "RENDERER_UNAVAILABLE", "Print image preview requires pdftoppm"
                )
            _run(
                [
                    converter,
                    "-png",
                    "-singlefile",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-r",
                    "96",
                    str(pdf),
                    str(root / "preview"),
                ],
                timeout=45,
            )
            measured = {
                "engine": "libreoffice-print",
                "page": page,
                "page_count": page_count,
                "geometry_mapping": "unavailable",
                "calculation": "renderer_may_recalculate",
                "renderer_version": _run([engine, "--version"], timeout=10).strip(),
                "input_version": snap.content_version,
                "dpi": 96,
                "zoom": 1,
            }
            engine_digest = hashlib.sha256(
                measured["renderer_version"].encode()
            ).hexdigest()
        _cancelled()
        from PIL import Image

        with Image.open(output) as img:
            source_size = {"width": img.width, "height": img.height}
        ref = admit_image_bytes(
            output.read_bytes(),
            media_type="image/png",
            name=f"{Path(file_path).stem}-{sheet}.png",
        )
    render_identity = hashlib.sha256(
        json.dumps(
            [
                snap.id.key(),
                observation["observation_id"],
                surface,
                page,
                engine_digest,
                measured.get("fonts"),
                ref.attachment_id,
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    value = {
        "status": "success",
        "schema_version": "workbook/2",
        "file_path": snap.file.relative,
        "content_version": snap.content_version,
        "snapshot_id": snap.id.key(),
        "observation_id": observation["observation_id"],
        "render_id": f"render:{render_identity}",
        "attachment_id": ref.attachment_id,
        "surface": surface,
        "sheet": sheet,
        "range": range,
        "renderer_digest": engine_digest,
        "measured": measured,
        "source_pixel_size": source_size,
        "attachment_pixel_size": {"width": ref.width, "height": ref.height},
        "request_pixel_size": {"status": "assigned_at_request_projection"},
        "renderer_version": measured.get(
            "browser_version", measured.get("renderer_version")
        ),
        "font_fingerprint": font_fingerprint,
        "locale": "zh-CN",
        "dpi": 96,
        "zoom": 1,
        "device_scale": 1,
        "source_to_attachment": {
            "scale_x": ref.width / source_size["width"],
            "scale_y": ref.height / source_size["height"],
        },
        "cell_to_pixel_map": _pixel_map(measured),
        "geometry": observation["regions"][0].get("geometry"),
        "coverage": {
            **observation["coverage"],
            "rendered_objects": "unsupported"
            if surface == "workbench"
            else "renderer_dependent",
            "region_facets": observation["regions"][0]["coverage"],
        },
        "limitations": [
            "Font substitution can change glyph metrics",
            "Workbench core does not render drawing objects or evaluate conditional formatting",
            "Merged ranges may show a renderer outline that is not a cell border in the file; "
            "verify borders via observe_spreadsheet(facets=['presentation'])",
            "Displayed text is not returned as data here (renderer_required); "
            "treat the image as the display evidence and cell values as the fact",
        ]
        if surface == "workbench"
        else ["Print layout; not the workbench viewport"],
        "objects": observation["regions"][0].get("objects", []),
    }
    return value, ref.to_dict()


def _pixel_map(measured):
    if "clip" not in measured:
        return {
            "status": "unsupported",
            "reason": "print pagination has no workbench grid transform",
        }
    clip = measured["clip"]
    return {
        "status": "measured",
        "coordinate_space": "source_image_pixels",
        "representation": "separable_axes; merge geometry in observation",
        "columns": [
            {"index": d["index"], "x": d["x"] - clip["x"], "width": d["width"]}
            for d in measured["columns"]
        ],
        "rows": [
            {"index": d["index"], "y": d["y"] - clip["y"], "height": d["height"]}
            for d in measured["rows"]
        ],
    }
