"""权威读不得臆算公式列。"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from excelmanus.security import FileAccessGuard
from excelmanus.tools._guard_ctx import set_guard
from excelmanus.workbook.data import init_guard, read_excel
from excelmanus.workbook_commit import seed_seen_versions


def _bind(root: Path) -> None:
    set_guard(FileAccessGuard(str(root)))
    init_guard(str(root))
    seed_seen_versions({})


def test_uncached_formulas_are_not_guessed(tmp_path: Path) -> None:
    _bind(tmp_path)
    path = tmp_path / "formulas.xlsx"
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "n"
    ws["B1"] = "twice"
    ws["A2"] = 2
    ws["B2"] = "=A2*2"
    ws["A3"] = 3
    ws["B3"] = "=A3*3"
    wb.save(path)
    wb.close()

    result = read_excel(file_path=str(path), sheet_name=ws.title)
    assert result.success
    payload = result.value
    assert isinstance(payload, dict)
    records = payload.get("data") or payload.get("records") or payload.get("values")
    assert records
    if isinstance(records[0], dict):
        values = [row.get("twice") for row in records]
    else:
        values = [row[1] for row in records[1:]]
    assert 4 not in values and 6 not in values
    assert hasattr(read_excel, "__call__")
    import excelmanus.workbook.data as data_mod

    assert not hasattr(data_mod, "_resolve_formula_columns")
