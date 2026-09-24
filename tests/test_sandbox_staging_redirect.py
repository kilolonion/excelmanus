"""STAGING_MAP is ignored. Sandbox writes stay on the user path."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from excelmanus.security.sandbox_hook import generate_wrapper_script


def _run_in_sandbox(
    workspace: Path,
    script_content: str,
    *,
    staging_map: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    script = workspace / "test_script.py"
    script.write_text(script_content, encoding="utf-8")
    wrapper = generate_wrapper_script("GREEN", str(workspace))
    wrapper_path = workspace / "_wrapper.py"
    wrapper_path.write_text(wrapper, encoding="utf-8")
    env = os.environ.copy()
    if staging_map:
        env["EXCELMANUS_STAGING_MAP"] = json.dumps(staging_map)
    return subprocess.run(
        [sys.executable, str(wrapper_path), str(script)],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )


def test_staging_map_does_not_redirect_open(tmp_path: Path) -> None:
    original = tmp_path / "data.txt"
    original.write_text("original_content", encoding="utf-8")
    staged = tmp_path / "outputs" / "backups" / "data_staged.txt"
    staged.parent.mkdir(parents=True)
    staged.write_text("staged_content", encoding="utf-8")

    code = (
        f"with open(r'{original}', 'w') as f:\n"
        f"    f.write('modified')\n"
        f"print('done')\n"
    )
    result = _run_in_sandbox(
        tmp_path,
        code,
        staging_map={str(original): str(staged)},
    )
    assert result.returncode == 0, result.stderr
    assert original.read_text(encoding="utf-8") == "original_content"
    assert staged.read_text(encoding="utf-8") == "staged_content"
    pending_dir = tmp_path / ".excelmanus" / "pending"
    pending_files = list(pending_dir.rglob("*_data.txt")) if pending_dir.is_dir() else []
    assert pending_files, "txt writes should land in pending, not the live path"
    assert pending_files[0].read_text(encoding="utf-8") == "modified"


def test_staging_map_does_not_redirect_openpyxl_save(tmp_path: Path) -> None:
    from openpyxl import Workbook, load_workbook

    original = tmp_path / "report.xlsx"
    wb = Workbook()
    wb.active["A1"] = "original_data"
    wb.save(str(original))
    wb.close()

    staged = tmp_path / "outputs" / "backups" / "report_staged.xlsx"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(original.read_bytes())

    code = (
        "from openpyxl import load_workbook\n"
        f"wb = load_workbook(r'{original}')\n"
        "wb.active['A1'] = 'modified_data'\n"
        f"wb.save(r'{original}')\n"
        "print('saved')\n"
    )
    result = _run_in_sandbox(
        tmp_path,
        code,
        staging_map={str(original): str(staged)},
    )
    assert result.returncode != 0
    assert "em.apply_spreadsheet_changes" in result.stderr or "em.apply_spreadsheet_changes" in result.stderr
    wb_orig = load_workbook(original)
    assert wb_orig.active["A1"].value == "original_data"
    wb_orig.close()
    wb_staged = load_workbook(staged)
    assert wb_staged.active["A1"].value == "original_data"
    wb_staged.close()
