"""WorkspaceManifest 模块测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from excelmanus.workspace_manifest import (
    WorkspaceManifest,
    build_manifest,
    refresh_manifest,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """创建含多个 Excel 文件的临时工作区。"""
    # 根目录文件
    wb1 = Workbook()
    ws1 = wb1.active
    ws1.title = "销售数据"
    ws1.append(["姓名", "金额", "日期"])
    ws1.append(["张三", 100, "2025-01-01"])
    wb1.save(tmp_path / "sales.xlsx")

    # 多 sheet 文件
    wb2 = Workbook()
    ws2a = wb2.active
    ws2a.title = "Sheet1"
    ws2a.append(["ID", "产品"])
    ws2b = wb2.create_sheet("Sheet2")
    ws2b.append(["类型", "数量"])
    wb2.save(tmp_path / "products.xlsx")

    # 子目录中的文件
    sub = tmp_path / "data"
    sub.mkdir()
    wb3 = Workbook()
    ws3 = wb3.active
    ws3.title = "学生花名册"
    ws3.append(["学号", "姓名", "班级", "角色"])
    ws3.append(["001", "张三", "一班", "班长"])
    wb3.save(sub / "迎新活动排班表.xlsx")

    # 隐藏文件（应跳过）
    wb_hidden = Workbook()
    wb_hidden.save(tmp_path / ".hidden.xlsx")

    # 临时文件（应跳过）
    wb_temp = Workbook()
    wb_temp.save(tmp_path / "~$temp.xlsx")

    # 噪音目录中的文件（应跳过）
    noise = tmp_path / ".git"
    noise.mkdir()
    wb_noise = Workbook()
    wb_noise.save(noise / "noise.xlsx")

    return tmp_path


class TestBuildManifest:
    def test_basic_build(self, workspace: Path) -> None:
        manifest = build_manifest(str(workspace))
        assert manifest.total_files == 3
        names = [f.name for f in manifest.files]
        assert "sales.xlsx" in names
        assert "products.xlsx" in names
        assert "迎新活动排班表.xlsx" in names

    def test_skips_hidden_and_temp(self, workspace: Path) -> None:
        manifest = build_manifest(str(workspace))
        names = [f.name for f in manifest.files]
        assert ".hidden.xlsx" not in names
        assert "~$temp.xlsx" not in names

    def test_skips_noise_dirs(self, workspace: Path) -> None:
        manifest = build_manifest(str(workspace))
        names = [f.name for f in manifest.files]
        assert "noise.xlsx" not in names

    def test_sheet_metadata(self, workspace: Path) -> None:
        manifest = build_manifest(str(workspace))
        sales = next(f for f in manifest.files if f.name == "sales.xlsx")
        assert len(sales.sheets) == 1
        sheet = sales.sheets[0]
        assert sheet.name == "销售数据"
        assert sheet.rows == 2  # 1 header + 1 data
        assert sheet.columns == 3
        assert "姓名" in sheet.headers
        assert "金额" in sheet.headers

    def test_multi_sheet(self, workspace: Path) -> None:
        manifest = build_manifest(str(workspace))
        products = next(f for f in manifest.files if f.name == "products.xlsx")
        assert len(products.sheets) == 2
        sheet_names = [s.name for s in products.sheets]
        assert "Sheet1" in sheet_names
        assert "Sheet2" in sheet_names

    def test_subdirectory_files(self, workspace: Path) -> None:
        manifest = build_manifest(str(workspace))
        nested = next(f for f in manifest.files if f.name == "迎新活动排班表.xlsx")
        assert "data" in nested.path
        assert len(nested.sheets) == 1
        assert nested.sheets[0].name == "学生花名册"
        assert "学号" in nested.sheets[0].headers

    def test_max_files_limit(self, workspace: Path) -> None:
        manifest = build_manifest(str(workspace), max_files=1)
        assert manifest.total_files == 1

    def test_scan_duration_recorded(self, workspace: Path) -> None:
        manifest = build_manifest(str(workspace))
        assert manifest.scan_duration_ms >= 0
        assert manifest.scan_time != ""

    def test_empty_workspace(self, tmp_path: Path) -> None:
        manifest = build_manifest(str(tmp_path))
        assert manifest.total_files == 0
        assert manifest.files == []

    def test_mtime_cache_populated(self, workspace: Path) -> None:
        manifest = build_manifest(str(workspace))
        assert len(manifest._mtime_cache) == manifest.total_files


class TestRefreshManifest:
    def test_no_changes(self, workspace: Path) -> None:
        """无变化时应复用所有缓存。"""
        manifest = build_manifest(str(workspace))
        refreshed = refresh_manifest(manifest)
        assert refreshed.total_files == manifest.total_files
        # 文件元数据应相同
        for old, new in zip(manifest.files, refreshed.files):
            assert old.path == new.path
            assert old.name == new.name

    def test_detects_new_file(self, workspace: Path) -> None:
        """新增文件应被检测到。"""
        manifest = build_manifest(str(workspace))
        # 新增一个文件
        wb_new = Workbook()
        ws_new = wb_new.active
        ws_new.title = "新表"
        ws_new.append(["A", "B"])
        wb_new.save(workspace / "new_file.xlsx")

        refreshed = refresh_manifest(manifest)
        assert refreshed.total_files == manifest.total_files + 1
        names = [f.name for f in refreshed.files]
        assert "new_file.xlsx" in names

    def test_detects_modified_file(self, workspace: Path) -> None:
        """修改文件后应重新扫描该文件。"""
        manifest = build_manifest(str(workspace))
        # 修改 sales.xlsx — 添加新 sheet
        import time
        time.sleep(0.05)  # 确保 mtime 变化
        wb = Workbook()
        ws = wb.active
        ws.title = "新销售数据"
        ws.append(["新列"])
        wb.save(workspace / "sales.xlsx")

        refreshed = refresh_manifest(manifest)
        sales = next(f for f in refreshed.files if f.name == "sales.xlsx")
        assert sales.sheets[0].name == "新销售数据"


class TestSystemPromptSummary:
    def test_empty_manifest(self, tmp_path: Path) -> None:
        manifest = build_manifest(str(tmp_path))
        assert manifest.get_system_prompt_summary() == ""

    def test_full_mode_small_workspace(self, workspace: Path) -> None:
        """≤20 文件应使用完整模式。"""
        manifest = build_manifest(str(workspace))
        summary = manifest.get_system_prompt_summary()
        assert "## 工作区 Excel 文件概览" in summary
        assert "sales.xlsx" in summary
        assert "迎新活动排班表.xlsx" in summary
        assert "学生花名册" in summary
        assert "销售数据" in summary

    def test_compact_mode(self, tmp_path: Path) -> None:
        """21-100 文件应使用紧凑模式。"""
        # 创建 25 个文件
        for i in range(25):
            wb = Workbook()
            ws = wb.active
            ws.title = f"Sheet_{i}"
            ws.append([f"col_{i}"])
            wb.save(tmp_path / f"file_{i:03d}.xlsx")

        manifest = build_manifest(str(tmp_path))
        summary = manifest.get_system_prompt_summary()
        assert "## 工作区 Excel 文件概览" in summary
        assert "共 25 个 Excel 文件" in summary
        # 紧凑模式应包含 sheet 名但不包含列头
        assert "Sheet_0" in summary

    def test_summary_mode(self, tmp_path: Path) -> None:
        """>100 文件应使用统计摘要模式。"""
        for i in range(105):
            wb = Workbook()
            ws = wb.active
            ws.title = f"Sheet_{i}"
            wb.save(tmp_path / f"file_{i:04d}.xlsx")

        manifest = build_manifest(str(tmp_path), max_files=500)
        summary = manifest.get_system_prompt_summary()
        assert "## 工作区 Excel 文件概览" in summary
        assert "热点目录" in summary
        assert "inspect_excel_files" in summary
