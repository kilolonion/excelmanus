"""WorkspaceManifest — 工作区 Excel 文件轻量级元数据清单。

会话启动时递归扫描工作区所有 Excel 文件的 sheet 名称、行列数、列头，
构建紧凑的 JSON 清单用于：
1. 注入 system prompt，让 agent 首轮即知工作区全貌；
2. 供 inspect_excel_files 的 search 功能使用缓存加速。

设计原则：
- 仅读取元信息（sheet names + 前 2 行用于表头识别），不加载数据
- openpyxl read_only 模式，内存占用极低
- 基于 mtime 的增量更新，避免重复 IO
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from excelmanus.logger import get_logger

logger = get_logger("workspace_manifest")

# 递归扫描时跳过的噪音目录
_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".venv", "node_modules", "__pycache__",
    ".worktrees", "dist", "build", "outputs",
})

# Manifest 摘要注入 system prompt 的阈值
_INJECT_FULL_THRESHOLD = 20      # ≤20 文件：完整注入
_INJECT_COMPACT_THRESHOLD = 100  # 20-100 文件：紧凑注入
# >100 文件：仅注入统计摘要


@dataclass
class SheetMeta:
    """单个 sheet 的元数据。"""
    name: str
    rows: int
    columns: int
    headers: list[str] = field(default_factory=list)


@dataclass
class ExcelFileMeta:
    """单个 Excel 文件的元数据。"""
    path: str          # 相对于 workspace_root 的路径
    name: str          # 文件名
    size_bytes: int
    modified_ts: float  # mtime 时间戳
    sheets: list[SheetMeta] = field(default_factory=list)


@dataclass
class WorkspaceManifest:
    """工作区 Excel 文件清单。"""
    workspace_root: str
    scan_time: str          # ISO 格式扫描时间
    scan_duration_ms: int   # 扫描耗时（毫秒）
    total_files: int
    files: list[ExcelFileMeta] = field(default_factory=list)

    # ── 内部状态 ──
    _mtime_cache: dict[str, float] = field(default_factory=dict, repr=False)

    def get_system_prompt_summary(self) -> str:
        """生成用于注入 system prompt 的工作区概览文本。

        根据文件数量自动选择详细度：
        - ≤20 文件：完整列出每个文件的 sheet 名 + 行列数 + 列头
        - 20-100 文件：仅列出文件路径 + sheet 名列表
        - >100 文件：仅统计摘要
        """
        if not self.files:
            return ""

        lines: list[str] = [
            "## 工作区 Excel 文件概览",
            f"共 {self.total_files} 个 Excel 文件"
            f"（扫描于 {self.scan_time}，耗时 {self.scan_duration_ms}ms）：",
        ]

        if self.total_files <= _INJECT_FULL_THRESHOLD:
            # 完整模式
            for fm in self.files:
                sheet_parts: list[str] = []
                for sm in fm.sheets:
                    header_hint = ""
                    if sm.headers:
                        cols_str = ", ".join(sm.headers[:6])
                        if len(sm.headers) > 6:
                            cols_str += f" +{len(sm.headers) - 6}列"
                        header_hint = f" [{cols_str}]"
                    sheet_parts.append(f"{sm.name}({sm.rows}×{sm.columns}){header_hint}")
                sheets_str = " | ".join(sheet_parts) if sheet_parts else "(空)"
                lines.append(f"- `{fm.path}` → {sheets_str}")

        elif self.total_files <= _INJECT_COMPACT_THRESHOLD:
            # 紧凑模式：文件路径 + sheet 名列表
            for fm in self.files:
                sheet_names = [sm.name for sm in fm.sheets]
                sheets_str = ", ".join(sheet_names) if sheet_names else "(空)"
                lines.append(f"- `{fm.path}` → [{sheets_str}]")

        else:
            # 统计摘要模式
            total_sheets = sum(len(fm.sheets) for fm in self.files)
            # 按目录分组统计
            dir_counts: dict[str, int] = {}
            for fm in self.files:
                parent = str(Path(fm.path).parent)
                if parent == ".":
                    parent = "(根目录)"
                dir_counts[parent] = dir_counts.get(parent, 0) + 1
            top_dirs = sorted(dir_counts.items(), key=lambda x: -x[1])[:10]
            lines.append(f"共 {total_sheets} 个工作表")
            lines.append("热点目录：")
            for d, count in top_dirs:
                lines.append(f"  - `{d}/` ({count} 个文件)")
            lines.append(
                "使用 `inspect_excel_files(search=\"关键词\")` 可按文件名或 sheet 名快速定位。"
            )

        return "\n".join(lines)


def build_manifest(
    workspace_root: str,
    *,
    max_files: int = 500,
    header_scan_rows: int = 5,
) -> WorkspaceManifest:
    """递归扫描工作区，构建 Excel 文件元数据清单。

    Args:
        workspace_root: 工作区根目录路径。
        max_files: 最多扫描文件数，默认 500。
        header_scan_rows: 每个 sheet 扫描前 N 行用于表头识别。

    Returns:
        WorkspaceManifest 实例。
    """
    from openpyxl import load_workbook

    root = Path(workspace_root).resolve()
    start_ts = time.monotonic()

    # 递归收集 Excel 文件
    excel_paths: list[Path] = []
    for ext in ("*.xlsx", "*.xlsm"):
        for p in root.rglob(ext):
            if p.name.startswith((".", "~$")):
                continue
            rel_parts = p.relative_to(root).parts[:-1]
            if any(part in _SKIP_DIRS for part in rel_parts):
                continue
            excel_paths.append(p)
            if len(excel_paths) >= max_files:
                break
        if len(excel_paths) >= max_files:
            break

    excel_paths.sort(key=lambda p: str(p.relative_to(root)).lower())

    files: list[ExcelFileMeta] = []
    mtime_cache: dict[str, float] = {}

    for fp in excel_paths:
        try:
            stat = fp.stat()
        except OSError:
            continue

        rel_path = str(fp.relative_to(root))
        mtime_cache[rel_path] = stat.st_mtime

        sheets: list[SheetMeta] = []
        try:
            wb = load_workbook(fp, read_only=True, data_only=True)
            try:
                for sn in wb.sheetnames:
                    ws = wb[sn]
                    total_rows = ws.max_row or 0
                    total_cols = ws.max_column or 0

                    # 读取前几行识别表头
                    headers: list[str] = []
                    if total_rows > 0:
                        scan_limit = min(header_scan_rows, total_rows)
                        rows_raw: list[list[Any]] = []
                        for row in ws.iter_rows(
                            min_row=1,
                            max_row=scan_limit,
                            min_col=1,
                            max_col=min(total_cols, 30),
                            values_only=True,
                        ):
                            rows_raw.append(list(row))

                        if rows_raw:
                            # 简单启发式：取非空字符串占比最高的一行作为表头
                            best_idx = 0
                            best_score = -1
                            for idx, row in enumerate(rows_raw):
                                non_empty = [
                                    v for v in row
                                    if v is not None and str(v).strip()
                                ]
                                str_count = sum(
                                    1 for v in non_empty
                                    if isinstance(v, str)
                                )
                                score = str_count * 2 + len(non_empty)
                                if score > best_score:
                                    best_score = score
                                    best_idx = idx
                            header_row = rows_raw[best_idx]
                            headers = [
                                str(v).strip()
                                for v in header_row
                                if v is not None and str(v).strip()
                            ]

                    sheets.append(SheetMeta(
                        name=sn,
                        rows=total_rows,
                        columns=total_cols,
                        headers=headers,
                    ))
            finally:
                wb.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("扫描文件 %s 失败: %s", fp, exc)

        files.append(ExcelFileMeta(
            path=rel_path,
            name=fp.name,
            size_bytes=stat.st_size,
            modified_ts=stat.st_mtime,
            sheets=sheets,
        ))

    elapsed_ms = int((time.monotonic() - start_ts) * 1000)
    scan_time = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    manifest = WorkspaceManifest(
        workspace_root=str(root),
        scan_time=scan_time,
        scan_duration_ms=elapsed_ms,
        total_files=len(files),
        files=files,
        _mtime_cache=mtime_cache,
    )

    logger.info(
        "Workspace manifest 构建完成: %d 文件, 耗时 %dms",
        len(files), elapsed_ms,
    )
    return manifest


def refresh_manifest(
    manifest: WorkspaceManifest,
    *,
    max_files: int = 500,
    header_scan_rows: int = 5,
) -> WorkspaceManifest:
    """增量更新 Manifest：仅重新扫描 mtime 变化或新增的文件。

    Args:
        manifest: 现有 Manifest。
        max_files: 最多扫描文件数。
        header_scan_rows: 表头识别行数。

    Returns:
        更新后的 WorkspaceManifest（新实例）。
    """
    from openpyxl import load_workbook

    root = Path(manifest.workspace_root).resolve()
    start_ts = time.monotonic()

    # 收集当前工作区所有 Excel 文件
    current_paths: list[Path] = []
    for ext in ("*.xlsx", "*.xlsm"):
        for p in root.rglob(ext):
            if p.name.startswith((".", "~$")):
                continue
            rel_parts = p.relative_to(root).parts[:-1]
            if any(part in _SKIP_DIRS for part in rel_parts):
                continue
            current_paths.append(p)
            if len(current_paths) >= max_files:
                break
        if len(current_paths) >= max_files:
            break

    current_paths.sort(key=lambda p: str(p.relative_to(root)).lower())

    old_by_path: dict[str, ExcelFileMeta] = {
        fm.path: fm for fm in manifest.files
    }
    old_mtime = manifest._mtime_cache

    files: list[ExcelFileMeta] = []
    new_mtime_cache: dict[str, float] = {}
    changed_count = 0

    for fp in current_paths:
        try:
            stat = fp.stat()
        except OSError:
            continue

        rel_path = str(fp.relative_to(root))
        new_mtime_cache[rel_path] = stat.st_mtime

        # mtime 未变且已有缓存 → 复用
        if rel_path in old_by_path and old_mtime.get(rel_path) == stat.st_mtime:
            files.append(old_by_path[rel_path])
            continue

        # 需要重新扫描
        changed_count += 1
        sheets: list[SheetMeta] = []
        try:
            wb = load_workbook(fp, read_only=True, data_only=True)
            try:
                for sn in wb.sheetnames:
                    ws = wb[sn]
                    total_rows = ws.max_row or 0
                    total_cols = ws.max_column or 0
                    headers: list[str] = []
                    if total_rows > 0:
                        scan_limit = min(header_scan_rows, total_rows)
                        for row in ws.iter_rows(
                            min_row=1,
                            max_row=scan_limit,
                            min_col=1,
                            max_col=min(total_cols, 30),
                            values_only=True,
                        ):
                            non_empty = [
                                str(v).strip()
                                for v in row
                                if v is not None and str(v).strip()
                            ]
                            if non_empty:
                                headers = non_empty
                                break
                    sheets.append(SheetMeta(
                        name=sn, rows=total_rows, columns=total_cols,
                        headers=headers,
                    ))
            finally:
                wb.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("增量扫描文件 %s 失败: %s", fp, exc)

        files.append(ExcelFileMeta(
            path=rel_path,
            name=fp.name,
            size_bytes=stat.st_size,
            modified_ts=stat.st_mtime,
            sheets=sheets,
        ))

    elapsed_ms = int((time.monotonic() - start_ts) * 1000)
    scan_time = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    logger.info(
        "Workspace manifest 增量更新: %d 文件 (变更 %d), 耗时 %dms",
        len(files), changed_count, elapsed_ms,
    )

    return WorkspaceManifest(
        workspace_root=manifest.workspace_root,
        scan_time=scan_time,
        scan_duration_ms=elapsed_ms,
        total_files=len(files),
        files=files,
        _mtime_cache=new_mtime_cache,
    )
