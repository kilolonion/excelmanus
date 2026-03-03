"""Excel 文件扩展名共享常量。"""

from __future__ import annotations

# 项目内统一支持的 Excel 扩展名（单一事实来源）。
EXCEL_EXTENSIONS: frozenset[str] = frozenset({
    ".xlsx",
    ".xls",
    ".xlsm",
    ".xlsb",
})

# Excel + CSV（文件扫描、注册表等需要同时匹配 CSV 的场景）。
EXCEL_AND_CSV_EXTENSIONS: frozenset[str] = EXCEL_EXTENSIONS | frozenset({".csv"})

