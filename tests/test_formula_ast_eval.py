"""公式列求值应使用 AST 白名单，禁止 eval / 任意调用。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import Workbook

from excelmanus.workbook.data import _resolve_formula_columns


def _write_formula_xlsx(
    path: Path,
    headers: list[str],
    rows: list[list[object]],
) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


def _load_and_resolve(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, header=0)
    return _resolve_formula_columns(df, path, None, header_row=0)


class TestSimpleFormulaColumn:
    def test_a2_plus_b2_fills_nan_column(self, tmp_path: Path) -> None:
        path = _write_formula_xlsx(
            tmp_path / "add.xlsx",
            ["A", "B", "C"],
            [
                [1, 2, "=A2+B2"],
                [3, 4, "=A3+B3"],
            ],
        )

        out = _load_and_resolve(path)

        assert list(out["C"]) == [3.0, 7.0]
        meta = out.attrs["formula_resolution"]
        assert "C" in meta["resolved_columns"]
        assert "C" not in meta["unresolved_columns"]


class TestForbiddenFormulaEval:
    def test_function_call_foo_is_rejected(self, tmp_path: Path) -> None:
        path = _write_formula_xlsx(
            tmp_path / "call.xlsx",
            ["A", "evil"],
            [
                [1, "=A2+foo()"],
                [2, "=A3+foo()"],
            ],
        )

        out = _load_and_resolve(path)

        meta = out.attrs["formula_resolution"]
        assert "evil" in meta["unresolved_columns"]
        assert meta["unresolved_details"]["evil"] == "公式求值失败"
        assert out["evil"].isna().all()

    def test_import_os_call_is_rejected(self, tmp_path: Path) -> None:
        path = _write_formula_xlsx(
            tmp_path / "import.xlsx",
            ["A", "evil"],
            [
                [1, "=A2+__import__('os')"],
                [2, "=A3+__import__('os')"],
            ],
        )

        out = _load_and_resolve(path)

        meta = out.attrs["formula_resolution"]
        assert "evil" in meta["unresolved_columns"]
        assert meta["unresolved_details"]["evil"] == "公式求值失败"
        assert out["evil"].isna().all()

    def test_series_method_call_is_rejected(self, tmp_path: Path) -> None:
        """eval 可通过 Series 方法绕过空 builtins；AST 白名单必须拒绝。"""
        path = _write_formula_xlsx(
            tmp_path / "attr.xlsx",
            ["A", "evil"],
            [
                [1, "=A2.clip(0)"],
                [-1, "=A3.clip(0)"],
            ],
        )

        out = _load_and_resolve(path)

        meta = out.attrs["formula_resolution"]
        assert "evil" in meta["unresolved_columns"]
        assert meta["unresolved_details"]["evil"] == "公式求值失败"
        assert out["evil"].isna().all()
