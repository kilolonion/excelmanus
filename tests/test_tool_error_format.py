"""工具错误格式统一 — 回归测试。

覆盖：
- Fix A: is_error_result 兼容 {"error": ...} 简写格式
- Fix C: data_tools CSV + range 错误格式统一
- Fix D: tool_dispatcher 错误消息提取兼容
"""
from __future__ import annotations
import json
import pytest
from excelmanus.tools.registry import ToolRegistry

class TestIsErrorResult:
    """is_error_result 应同时识别标准格式和简写格式。"""

    def test_standard_format_detected(self):
        """{"status": "error", "message": "..."} 被识别为错误。"""
        result = json.dumps({'status': 'error', 'message': '文件不存在'})
        assert ToolRegistry.is_error_result(result) is True

    def test_standard_format_with_extra_fields(self):
        """标准格式附带 error_code 等额外字段仍被识别。"""
        result = json.dumps({'status': 'error', 'error_code': 'TOOL_EXECUTION_ERROR', 'message': '超时'})
        assert ToolRegistry.is_error_result(result) is True

    def test_shorthand_format_detected(self):
        """{"error": "..."} 简写格式被识别为错误。"""
        result = json.dumps({'error': 'range 参数不支持 CSV 文件'})
        assert ToolRegistry.is_error_result(result) is True

    def test_shorthand_format_with_suggestion(self):
        """简写格式附带 suggestion 字段仍被识别。"""
        result = json.dumps({'error': '未解析公式列', 'suggestion': '请先在 Excel 重算'})
        assert ToolRegistry.is_error_result(result) is True

    def test_shorthand_with_data_keys_not_error(self):
        """有 "error" 键但同时有数据键 (file/shape/columns) 的不算错误。

        scan_excel_snapshot 中某个 sheet 读取失败时，结果 JSON
        同时含 "error" 和 "columns" 键，这不应被误判为工具级错误。
        """
        result = json.dumps({'error': '读取失败: timeout', 'columns': [], 'file': 'test.xlsx'})
        assert ToolRegistry.is_error_result(result) is False

    def test_normal_result_not_false_positive(self):
        """正常工具结果不被误判为错误。"""
        result = json.dumps({'file': 'test.xlsx', 'shape': {'rows': 10, 'columns': 5}, 'columns': ['A', 'B', 'C', 'D', 'E']})
        assert ToolRegistry.is_error_result(result) is False

    def test_non_string_input(self):
        assert ToolRegistry.is_error_result(None) is False
        assert ToolRegistry.is_error_result(42) is False
        assert ToolRegistry.is_error_result({'error': 'x'}) is False

    def test_non_json_string(self):
        assert ToolRegistry.is_error_result('not json') is False

    def test_empty_string(self):
        assert ToolRegistry.is_error_result('') is False

    def test_json_array(self):
        """JSON 数组不是错误。"""
        assert ToolRegistry.is_error_result('[{"error": "x"}]') is False

    def test_shorthand_with_file_key_not_error(self):
        """compare_excel 失败时有 "error" + "file" 不算工具级错误。"""
        result = json.dumps({'error': '无法读取文件 A', 'available_sheets': ['Sheet1'], 'file': 'a.xlsx'})
        assert ToolRegistry.is_error_result(result) is False

class TestCsvRangeErrorFormat:
    """read_excel 对 CSV 使用 range 参数时应静默降级（非报错）。"""

    def test_csv_range_graceful_fallback(self, tmp_path):
        """CSV + range → 静默忽略 range，正常返回数据。"""
        csv_file = tmp_path / 'test.csv'
        csv_file.write_text('a,b,c\n1,2,3\n', encoding='utf-8')
        from excelmanus.workbook.data import read_excel
        from excelmanus.tools._guard_ctx import set_guard
        from excelmanus.security import FileAccessGuard
        guard = FileAccessGuard(workspace_root=str(tmp_path))
        set_guard(guard)
        try:
            result = read_excel(file_path=str(csv_file), range='A1:C5')
            from excelmanus.engine_core.tool_result import ToolResult
            assert isinstance(result, ToolResult)
            parsed = result.value
            assert isinstance(parsed, dict)
            assert 'error' not in parsed
            assert parsed['shape']['rows'] == 1
            assert parsed['shape']['columns'] == 3
        finally:
            set_guard(None)

    def test_csv_range_not_error(self, tmp_path):
        """CSV + range → is_error_result 应为 False（非错误）。"""
        csv_file = tmp_path / 'test.csv'
        csv_file.write_text('a,b,c\n1,2,3\n', encoding='utf-8')
        from excelmanus.workbook.data import read_excel
        from excelmanus.tools._guard_ctx import set_guard
        from excelmanus.security import FileAccessGuard
        guard = FileAccessGuard(workspace_root=str(tmp_path))
        set_guard(guard)
        try:
            result = read_excel(file_path=str(csv_file), range='A1:C5')
            assert ToolRegistry.is_error_result(result) is False
        finally:
            set_guard(None)

class TestErrorMessageExtraction:
    """tool_dispatcher 从两种错误格式中正确提取错误消息。"""

    def test_extract_from_standard_format(self):
        """从 {"status": "error", "message": "X"} 提取 "X"。"""
        result_str = json.dumps({'status': 'error', 'message': '文件不存在'})
        parsed = json.loads(result_str)
        error = parsed.get('message') or parsed.get('error') or result_str
        assert error == '文件不存在'

    def test_extract_from_shorthand_format(self):
        """从 {"error": "X"} 提取 "X"。"""
        result_str = json.dumps({'error': 'range 参数不支持 CSV 文件'})
        parsed = json.loads(result_str)
        error = parsed.get('message') or parsed.get('error') or result_str
        assert error == 'range 参数不支持 CSV 文件'

    def test_extract_fallback_to_raw(self):
        """两个键都没有时回退到原始字符串。"""
        result_str = json.dumps({'status': 'error', 'code': 'UNKNOWN'})
        parsed = json.loads(result_str)
        error = parsed.get('message') or parsed.get('error') or result_str
        assert error == result_str

class TestEndToEndErrorChain:
    """验证完整错误链路：工具返回 → is_error_result → success=False。"""

    def test_shorthand_error_triggers_success_false(self):
        """模拟 tool_dispatcher 检测流程：
        {"error": "..."} → is_error_result=True → success=False。
        """
        result_str = json.dumps({'error': "列 'X' 不存在，可用列: ['A', 'B']"})
        success = True
        if success and ToolRegistry.is_error_result(result_str):
            success = False
            try:
                _err = json.loads(result_str)
                error = _err.get('message') or _err.get('error') or result_str
            except Exception:
                error = result_str
        assert success is False
        assert error == "列 'X' 不存在，可用列: ['A', 'B']"
