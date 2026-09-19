"""回合末临时文件残留扫描。"""

from __future__ import annotations

from pathlib import Path

from excelmanus.workspace.scratch import leftover_reminder, scan_scratch_leftovers


def test_scan_scratch_leftovers(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "scratch_job.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "scripts" / "scratch_job.py.em-lock").write_bytes(b"")
    (tmp_path / "scratch_root.py").write_text("x\n", encoding="utf-8")
    found = scan_scratch_leftovers(tmp_path)
    assert "scripts/scratch_job.py" in found
    assert "scripts/scratch_job.py.em-lock" not in found
    assert "scratch_root.py" in found
    text = leftover_reminder(tmp_path)
    assert "delete_file" in text
    assert "scratch_job.py" in text
