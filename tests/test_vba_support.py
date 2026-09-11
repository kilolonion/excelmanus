"""VBA 支持相关回归测试：keep_vba、VBA 信息提取与查看、guard 豁免。"""

from __future__ import annotations

import json
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from excelmanus.engine import _user_requests_vba


# ── VBA 信息提取 ─────────────────────────────────────────────


class TestCollectVbaInfo:
    """_collect_vba_info 的单元测试。"""

    def test_xlsx_returns_no_vba(self, tmp_path: Path) -> None:
        """对 .xlsx 文件应返回 has_vba=False。"""
        import openpyxl

        from excelmanus.workbook.data import _collect_vba_info

        xlsx_path = tmp_path / "test.xlsx"
        wb = openpyxl.Workbook()
        wb.save(xlsx_path)
        wb.close()

        info = _collect_vba_info(xlsx_path)
        assert info["has_vba"] is False
        assert info["modules"] == []

    def test_xlsm_without_actual_vba(self, tmp_path: Path) -> None:
        """对无 VBA 内容的 .xlsm 文件应返回 has_vba=False。"""
        import openpyxl

        from excelmanus.workbook.data import _collect_vba_info

        # openpyxl 创建的 .xlsm 不包含 vbaProject.bin
        xlsm_path = tmp_path / "test.xlsm"
        wb = openpyxl.Workbook()
        wb.save(xlsm_path)
        wb.close()

        info = _collect_vba_info(xlsm_path)
        assert info["has_vba"] is False

    def test_non_excel_returns_no_vba(self, tmp_path: Path) -> None:
        """对非 Excel 文件应返回 has_vba=False。"""
        from excelmanus.workbook.data import _collect_vba_info

        txt_path = tmp_path / "test.txt"
        txt_path.write_text("not excel")

        info = _collect_vba_info(txt_path)
        assert info["has_vba"] is False

    def test_vba_dimension_in_include_dimensions(self) -> None:
        """vba 应在 INCLUDE_DIMENSIONS 中注册。"""
        from excelmanus.workbook.data import INCLUDE_DIMENSIONS

        assert "vba" in INCLUDE_DIMENSIONS

    def test_vba_dimension_in_scan_files_dimensions(self) -> None:
        """vba 应在 _SCAN_FILES_DIMENSIONS 中注册。"""
        from excelmanus.workbook.data import _SCAN_FILES_DIMENSIONS

        assert "vba" in _SCAN_FILES_DIMENSIONS


# ── VBA 用户请求检测 ─────────────────────────────────────────


class TestUserRequestsVba:
    """_user_requests_vba 检测模式的单元测试。"""

    @pytest.mark.parametrize(
        "text",
        [
            "查看这个文件的VBA代码",
            "这个文件有宏吗",
            "帮我解释一下这个macro",
            "提取VBA源码",
            "查看宏模块",
            "这个 .xlsm 有什么 VBA 宏",
            "read_excel include vba",
            "inspect vba macros",
            "解读VBA逻辑",
            "vbaProject 有哪些内容",
        ],
    )
    def test_detects_vba_requests(self, text: str) -> None:
        assert _user_requests_vba(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "帮我汇总销售数据",
            "格式化 Sheet1",
            "写入 A1 单元格",
            "读取前10行",
            "",
        ],
    )
    def test_does_not_false_positive(self, text: str) -> None:
        assert _user_requests_vba(text) is False

    def test_empty_returns_false(self) -> None:
        assert _user_requests_vba("") is False
        assert _user_requests_vba(None) is False  # type: ignore[arg-type]


# ── Engine 集成测试 — VBA 豁免 ──────────────────────────────


class TestVbaExemptEngineIntegration:
    """AgentEngine 中 VBA 豁免逻辑的集成测试。"""

    @staticmethod
    def _make_engine(**overrides):
        from excelmanus.config import ExcelManusConfig
        from excelmanus.engine import AgentEngine
        from excelmanus.tools.registry import ToolRegistry

        defaults = {
            "api_key": "test-key",
            "base_url": "https://test.example.com/v1",
            "model": "test-model",
            "max_iterations": 20,
            "max_consecutive_failures": 3,
            "workspace_root": str(Path(__file__).resolve().parent),
            "backup_enabled": False,
        }
        defaults.update(overrides)
        cfg = ExcelManusConfig(**defaults)
        registry = ToolRegistry()
        return AgentEngine(config=cfg, registry=registry)

    @staticmethod
    def _make_route_result(**kwargs):
        from excelmanus.skillpacks.models import SkillMatchResult

        defaults = dict(
            skills_used=[],
            route_mode="all_tools",
            system_contexts=[],
        )
        defaults.update(kwargs)
        return SkillMatchResult(**defaults)

    def test_vba_exempt_initialized_false(self) -> None:
        engine = self._make_engine()
        assert engine._vba_exempt is False

    @pytest.mark.asyncio
    async def test_vba_exempt_set_for_vba_request(self) -> None:
        """用户请求 VBA 时应设置 _vba_exempt=True。"""
        engine = self._make_engine(max_iterations=2)
        route_result = self._make_route_result()
        engine._route_skills = AsyncMock(return_value=route_result)

        vba_reply = "Sub MyMacro()\n  MsgBox \"Hello\"\nEnd Sub"
        engine._client.chat.completions.create = AsyncMock(
            return_value=types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(
                            content=vba_reply, tool_calls=None
                        )
                    )
                ]
            )
        )

        result = await engine.chat("查看这个文件的VBA代码")
        # VBA 豁免模式下，VBA 代码不应触发 execution_guard
        assert engine._vba_exempt is True
        assert result.reply == vba_reply

    @pytest.mark.asyncio
    async def test_no_vba_exempt_for_normal_request(self) -> None:
        """普通请求不应设置 _vba_exempt；纯文本直接结束。"""
        engine = self._make_engine(max_iterations=3)
        route_result = self._make_route_result()
        engine._route_skills = AsyncMock(return_value=route_result)

        vba_reply = "Sub MyMacro()\n  MsgBox \"Hello\"\nEnd Sub"
        engine._client.chat.completions.create = AsyncMock(
            return_value=types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(
                            content=vba_reply, tool_calls=None
                        )
                    )
                ]
            ),
        )

        result = await engine.chat("帮我汇总销售数据")
        assert engine._vba_exempt is False
        # 纯文本一律结束本轮，不再因内容形态被门禁拦截
        assert result.reply == vba_reply

    @pytest.mark.asyncio
    async def test_vba_exempt_resets_on_new_task(self) -> None:
        """新任务应重置 _vba_exempt。"""
        engine = self._make_engine(max_iterations=2)
        route_result = self._make_route_result()
        engine._route_skills = AsyncMock(return_value=route_result)

        engine._client.chat.completions.create = AsyncMock(
            return_value=types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(
                            content="ok", tool_calls=None
                        )
                    )
                ]
            )
        )

        await engine.chat("查看VBA宏")
        assert engine._vba_exempt is True

        await engine.chat("读取前10行数据")
        assert engine._vba_exempt is False
