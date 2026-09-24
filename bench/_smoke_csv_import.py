# -*- coding: utf-8 -*-
"""source_csv 导入冒烟：GB18030 CSV → workbook_spec → xlsx。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from excelmanus.tools.context import use_workspace
from excelmanus.tools.workbook_tools import apply_spreadsheet_changes, observe_spreadsheet


def main() -> int:
    ws = Path(tempfile.mkdtemp(prefix="em_csv_import_"))
    csv = ws / "月度.csv"
    csv.write_text("月份,金额\n1月,100\n2月,250.5\n3月,300\n", encoding="gb18030")

    with use_workspace(ws):
        r = apply_spreadsheet_changes(
            file_path="汇总.xlsx",
            workbook_spec={
                "sheets": [{
                    "name": "数据",
                    "source_csv": {"file_path": "月度.csv"},
                }],
                "uncertainties": [],
            },
        )
        print("edit:", "OK" if r.success else r.model_text[:300])
        if not r.success:
            return 1

        v = observe_spreadsheet(file_path="汇总.xlsx", range="A1:B4")
        print("verify:", "OK" if v.success else v.model_text[:200])
        txt = v.model_text
        for needle in ["月份", "100", "250.5", "300"]:
            assert needle in txt, f"missing {needle}: {txt}"
        print("rows: header + 3 data rows, types inferred (100/300 numeric)")

        # skip_rows 冒烟：跳过表头只要数据
        r2 = apply_spreadsheet_changes(
            file_path="无表头.xlsx",
            workbook_spec={
                "sheets": [{
                    "name": "数据",
                    "source_csv": {"file_path": "月度.csv", "skip_rows": 1},
                }],
                "uncertainties": [],
            },
        )
        print("skip_rows:", "OK" if r2.success else r2.model_text[:200])
        assert r2.success
        v2 = observe_spreadsheet(file_path="无表头.xlsx", range="A1:B3")
        assert "100" in v2.model_text and "月份" not in v2.model_text
        print("smoke OK")
        return 0


if __name__ == "__main__":
    sys.exit(main())
