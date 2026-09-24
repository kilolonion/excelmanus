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
    surface: str = "workbench",
    page: int = 1,
) -> ToolResult:
    if (
        surface not in {"workbench", "print"}
        or isinstance(page, bool)
        or not isinstance(page, int)
        or not 1 <= page <= 200
    ):
        raise ValueError("surface must be workbench/print; page must be 1..200")
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
    _cancelled()
    assets, asset_digest = renderer_assets() if surface == "workbench" else (None, None)
    font_fingerprint = environment_fingerprint()
    from excelmanus.attachments.store import get_attachment_store

    cache_key = (
        str(get_attachment_store().root),
        snap.id.key(),
        observation["observation_id"],
        surface,
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
            from io import BytesIO
            from openpyxl import load_workbook
            from excelmanus.workbook.adapter import ensure_editable

            ensure_editable(snap.read_bytes())
            wb = load_workbook(
                BytesIO(snap.read_bytes()), keep_vba=False, rich_text=True
            )
            try:
                selected = wb[sheet]
                for ws in wb:
                    ws.sheet_state = "visible" if ws is selected else "hidden"
                wb.active = wb.index(selected)
                selected.print_area = range
                src = root / "input.xlsx"
                serialized = BytesIO()
                wb.save(serialized)
                from excelmanus.workbook.adapter import (
                    preserve_workbook_extensions,
                    preserve_empty_custom_properties,
                )

                preserved = preserve_workbook_extensions(
                    snap.read_bytes(), serialized.getvalue()
                )
                src.write_bytes(
                    preserve_empty_custom_properties(snap.read_bytes(), preserved)
                )
            finally:
                wb.close()
            out = root / "out"
            out.mkdir()
            pdf, engine = _office_convert(src, out, "pdf:calc_pdf_Export")
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
