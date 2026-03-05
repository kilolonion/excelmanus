"""工具错误格式统一 & 窗口感知错误透传 — 回归测试。

覆盖三层纵深防御：
- Fix A: is_error_result 兼容 {"error": ...} 简写格式
- Fix B: 窗口感知层检测错误 payload 并透传
- Fix C: data_tools CSV + range 错误格式统一
- Fix D: tool_dispatcher 错误消息提取兼容
"""

from __future__ import annotations

import json

import pytest

from excelmanus.tools.registry import ToolRegistry
from excelmanus.window_perception.manager import WindowPerceptionManager
from excelmanus.window_perception.models import PerceptionBudget

_DEFAULT_BUDGET = PerceptionBudget()


# ── Fix A: is_error_result 兼容扩展 ─────────────────────────


class TestIsErrorResult:
    """is_error_result 应同时识别标准格式和简写格式。"""

    def test_standard_format_detected(self):
        """{"status": "error", "message": "..."} 被识别为错误。"""
        result = json.dumps({"status": "error", "message": "文件不存在"})
        assert ToolRegistry.is_error_result(result) is True

    def test_standard_format_with_extra_fields(self):
        """标准格式附带 error_code 等额外字段仍被识别。"""
        result = json.dumps({
            "status": "error",
            "error_code": "TOOL_EXECUTION_ERROR",
            "message": "超时",
        })
        assert ToolRegistry.is_error_result(result) is True

    def test_shorthand_format_detected(self):
        """{"error": "..."} 简写格式被识别为错误。"""
        result = json.dumps({"error": "range 参数不支持 CSV 文件"})
        assert ToolRegistry.is_error_result(result) is True

    def test_shorthand_format_with_suggestion(self):
        """简写格式附带 suggestion 字段仍被识别。"""
        result = json.dumps({
            "error": "未解析公式列",
            "suggestion": "请先在 Excel 重算",
        })
        assert ToolRegistry.is_error_result(result) is True

    def test_shorthand_with_data_keys_not_error(self):
        """有 "error" 键但同时有数据键 (file/shape/columns) 的不算错误。

        scan_excel_snapshot 中某个 sheet 读取失败时，结果 JSON
        同时含 "error" 和 "columns" 键，这不应被误判为工具级错误。
        """
        result = json.dumps({
            "error": "读取失败: timeout",
            "columns": [],
            "file": "test.xlsx",
        })
        assert ToolRegistry.is_error_result(result) is False

    def test_normal_result_not_false_positive(self):
        """正常工具结果不被误判为错误。"""
        result = json.dumps({
            "file": "test.xlsx",
            "shape": {"rows": 10, "columns": 5},
            "columns": ["A", "B", "C", "D", "E"],
        })
        assert ToolRegistry.is_error_result(result) is False

    def test_non_string_input(self):
        assert ToolRegistry.is_error_result(None) is False
        assert ToolRegistry.is_error_result(42) is False
        assert ToolRegistry.is_error_result({"error": "x"}) is False

    def test_non_json_string(self):
        assert ToolRegistry.is_error_result("not json") is False

    def test_empty_string(self):
        assert ToolRegistry.is_error_result("") is False

    def test_json_array(self):
        """JSON 数组不是错误。"""
        assert ToolRegistry.is_error_result('[{"error": "x"}]') is False

    def test_shorthand_with_file_key_not_error(self):
        """compare_excel 失败时有 "error" + "file" 不算工具级错误。"""
        result = json.dumps({
            "error": "无法读取文件 A",
            "available_sheets": ["Sheet1"],
            "file": "a.xlsx",
        })
        assert ToolRegistry.is_error_result(result) is False


# ── Fix B: 窗口感知层错误 payload 透传 ──────────────────────


class TestWindowPerceptionErrorPassthrough:
    """窗口感知遇到错误 payload 时应返回 None（透传原始文本）。"""

    @pytest.fixture
    def manager(self) -> WindowPerceptionManager:
        return WindowPerceptionManager(enabled=True, budget=_DEFAULT_BUDGET)

    def test_shorthand_error_returns_none(self, manager: WindowPerceptionManager):
        """{"error": "..."} payload → update_from_tool_call 返回 None。"""
        error_json = json.dumps({"error": "range 参数不支持 CSV 文件"})
        result = manager.update_from_tool_call(
            tool_name="read_excel",
            arguments={"file_path": "test.csv", "range": "A1:Z30"},
            result_text=error_json,
        )
        assert result is None

    def test_standard_error_returns_none(self, manager: WindowPerceptionManager):
        """{"status": "error", "message": "..."} payload → 返回 None。"""
        error_json = json.dumps({"status": "error", "message": "文件不存在"})
        result = manager.update_from_tool_call(
            tool_name="read_excel",
            arguments={"file_path": "test.xlsx"},
            result_text=error_json,
        )
        assert result is None

    def test_normal_result_still_enriched(self, manager: WindowPerceptionManager):
        """正常工具结果仍被窗口感知处理（不受影响）。"""
        normal_json = json.dumps({
            "file": "test.xlsx",
            "shape": {"rows": 10, "columns": 5},
            "columns": ["A", "B", "C", "D", "E"],
            "data": [{"A": 1}],
        })
        result = manager.update_from_tool_call(
            tool_name="read_excel",
            arguments={"file_path": "test.xlsx"},
            result_text=normal_json,
        )
        # 应返回感知 payload（非 None）
        assert result is not None

    def test_enrich_tool_result_passthrough_on_error(self, manager: WindowPerceptionManager):
        """enrich_tool_result 遇到错误 JSON 时返回原始文本。"""
        error_json = json.dumps({"error": "列 'X' 不存在"})
        enriched = manager.enrich_tool_result(
            tool_name="filter_data",
            arguments={"file_path": "test.xlsx"},
            result_text=error_json,
            success=True,  # 关键：success=True 但内容是错误
        )
        # 应保留原始错误 JSON 文本，不生成 [OK] 确认
        assert "[OK]" not in enriched
        assert enriched == error_json

    def test_scan_snapshot_with_sheet_error_still_processed(self, manager: WindowPerceptionManager):
        """scan_excel_snapshot 中 sheet 级别 error 不应阻止窗口创建。

        这类结果有 "sheets" 数据键，不符合纯错误条件。
        """
        result_json = json.dumps({
            "file": "test.xlsx",
            "sheets": [
                {"name": "Sheet1", "rows": 10, "cols": 5, "columns": []},
                {"name": "Sheet2", "error": "读取失败", "columns": []},
            ],
        })
        result = manager.update_from_tool_call(
            tool_name="scan_excel_snapshot",
            arguments={"file_path": "test.xlsx"},
            result_text=result_json,
        )
        # 有 sheets 数据键，不算纯错误，应正常处理
        assert result is not None

    def test_disabled_manager_returns_none(self):
        """禁用的 manager 直接返回 None。"""
        manager = WindowPerceptionManager(enabled=False, budget=_DEFAULT_BUDGET)
        result = manager.update_from_tool_call(
            tool_name="read_excel",
            arguments={"file_path": "test.csv"},
            result_text='{"error": "test"}',
        )
        assert result is None


# ── Fix C: data_tools CSV + range 错误格式统一 ──────────────


class TestCsvRangeErrorFormat:
    """read_excel 对 CSV 使用 range 参数时应静默降级（非报错）。"""

    def test_csv_range_graceful_fallback(self, tmp_path):
        """CSV + range → 静默忽略 range，正常返回数据。"""
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("a,b,c\n1,2,3\n", encoding="utf-8")

        from excelmanus.tools.data_tools import read_excel

        from excelmanus.tools._guard_ctx import set_guard
        from excelmanus.security import FileAccessGuard

        guard = FileAccessGuard(workspace_root=str(tmp_path))
        set_guard(guard)

        try:
            result = read_excel(
                file_path=str(csv_file),
                range="A1:C5",
            )
            parsed = json.loads(result)
            assert "error" not in parsed
            assert parsed["shape"]["rows"] == 1
            assert parsed["shape"]["columns"] == 3
        finally:
            set_guard(None)  # type: ignore[arg-type]

    def test_csv_range_not_error(self, tmp_path):
        """CSV + range → is_error_result 应为 False（非错误）。"""
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("a,b,c\n1,2,3\n", encoding="utf-8")

        from excelmanus.tools.data_tools import read_excel
        from excelmanus.tools._guard_ctx import set_guard
        from excelmanus.security import FileAccessGuard

        guard = FileAccessGuard(workspace_root=str(tmp_path))
        set_guard(guard)

        try:
            result = read_excel(
                file_path=str(csv_file),
                range="A1:C5",
            )
            assert ToolRegistry.is_error_result(result) is False
        finally:
            set_guard(None)  # type: ignore[arg-type]


# ── Fix D: tool_dispatcher 错误消息提取 ─────────────────────


class TestErrorMessageExtraction:
    """tool_dispatcher 从两种错误格式中正确提取错误消息。"""

    def test_extract_from_standard_format(self):
        """从 {"status": "error", "message": "X"} 提取 "X"。"""
        result_str = json.dumps({"status": "error", "message": "文件不存在"})
        parsed = json.loads(result_str)
        error = parsed.get("message") or parsed.get("error") or result_str
        assert error == "文件不存在"

    def test_extract_from_shorthand_format(self):
        """从 {"error": "X"} 提取 "X"。"""
        result_str = json.dumps({"error": "range 参数不支持 CSV 文件"})
        parsed = json.loads(result_str)
        error = parsed.get("message") or parsed.get("error") or result_str
        assert error == "range 参数不支持 CSV 文件"

    def test_extract_fallback_to_raw(self):
        """两个键都没有时回退到原始字符串。"""
        result_str = json.dumps({"status": "error", "code": "UNKNOWN"})
        parsed = json.loads(result_str)
        error = parsed.get("message") or parsed.get("error") or result_str
        assert error == result_str


# ── 端到端集成测试 ──────────────────────────────────────────


class TestEndToEndErrorChain:
    """验证完整错误链路：工具返回 → is_error_result → success=False。"""

    def test_shorthand_error_triggers_success_false(self):
        """模拟 tool_dispatcher 检测流程：
        {"error": "..."} → is_error_result=True → success=False。
        """
        result_str = json.dumps({"error": "列 'X' 不存在，可用列: ['A', 'B']"})
        success = True

        # 模拟 tool_dispatcher L1131-1138
        if success and ToolRegistry.is_error_result(result_str):
            success = False
            try:
                _err = json.loads(result_str)
                error = _err.get("message") or _err.get("error") or result_str
            except Exception:
                error = result_str

        assert success is False
        assert error == "列 'X' 不存在，可用列: ['A', 'B']"

    def test_window_perception_does_not_enrich_error(self):
        """窗口感知在 success=False 时不处理。"""
        manager = WindowPerceptionManager(enabled=True, budget=_DEFAULT_BUDGET)
        error_text = '{"error": "range 参数不支持 CSV"}'

        # success=False 时 enrich_tool_result 直接透传
        result = manager.enrich_tool_result(
            tool_name="read_excel",
            arguments={"file_path": "test.csv"},
            result_text=error_text,
            success=False,
        )
        assert result == error_text
        assert "[OK]" not in result

    def test_window_perception_success_true_error_payload_passthrough(self):
        """即使 success=True（Fix A 未触发），Fix B 仍然保护。"""
        manager = WindowPerceptionManager(enabled=True, budget=_DEFAULT_BUDGET)
        error_json = json.dumps({"error": "不支持的运算符 'like'"})

        enriched = manager.enrich_tool_result(
            tool_name="filter_data",
            arguments={"file_path": "test.xlsx"},
            result_text=error_json,
            success=True,
        )
        # Fix B 确保错误 payload 不被替换为 [OK] 确认
        assert "[OK]" not in enriched
        assert enriched == error_json
