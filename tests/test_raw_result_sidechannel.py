"""测试 raw result 旁路通道 + dict 字段智能截断。

验证 P0 修复：
- Layer 1: 截断前的原始结果通过 _ToolExecOutcome.raw_result_str 传递
- Layer 2: _truncate_json_smart 支持 dict 字段缩减
"""
from __future__ import annotations
import json
from typing import Any
import pytest
from excelmanus.tools.registry import ToolDef

def _make_tool_def(max_chars: int=3000) -> ToolDef:
    return ToolDef(name='test_tool', description='test', input_schema={}, func=lambda: '', max_result_chars=max_chars)

class TestTruncateJsonSmartDictPhase:
    """验证 _truncate_json_smart 的 Phase 2: dict 字段缩减。"""

    def test_large_dict_field_gets_reduced(self):
        """当 list 缩减不够时，大 dict 字段被缩减。"""
        td = _make_tool_def(max_chars=500)
        data = {'shape': {'rows': 12, 'columns': 54}, 'file': 'test.xlsx', 'dtypes': {f'col_{i}': 'object' for i in range(100)}}
        result = td._truncate_json_smart(data, 500)
        assert result is not None
        parsed = json.loads(result)
        assert parsed['shape'] == {'rows': 12, 'columns': 54}
        assert parsed['file'] == 'test.xlsx'
        assert '__truncated__' in parsed['dtypes']
        assert len(parsed['dtypes']) < 100

    def test_dict_reduction_preserves_valid_json(self):
        """dict 缩减后结果仍是合法 JSON。"""
        td = _make_tool_def(max_chars=300)
        data = {'meta': 'ok', 'styles': {f'cell_{i}': {'font': 'Arial', 'size': 12} for i in range(50)}}
        result = td._truncate_json_smart(data, 300)
        assert result is not None
        parsed = json.loads(result)
        assert parsed['meta'] == 'ok'
        assert '__truncated__' in parsed['styles']

    def test_list_reduction_preferred_over_dict(self):
        """有可缩减的 list 时，优先缩减 list 而不是 dict。"""
        td = _make_tool_def(max_chars=500)
        data = {'shape': {'rows': 5, 'columns': 3}, 'preview': [{'a': 1, 'b': 2, 'c': 3}] * 20, 'dtypes': {'a': 'int', 'b': 'int', 'c': 'int'}}
        result = td._truncate_json_smart(data, 500)
        assert result is not None
        parsed = json.loads(result)
        assert len(parsed['preview']) < 20
        assert '__truncated__' not in parsed.get('dtypes', {})

    def test_no_dict_fields_to_reduce(self):
        """没有大 dict 字段时，dict 阶段跳过，回退到 string 阶段或返回 None。"""
        td = _make_tool_def(max_chars=50)
        data = {'huge_string': 'x' * 1000}
        result = td._truncate_json_smart(data, 200)
        assert result is not None
        parsed = json.loads(result)
        assert len(parsed['huge_string']) < 1000

    def test_wide_table_with_styles_scenario(self):
        """模拟宽表 + styles 的真实场景：54列 × 12行。"""
        td = _make_tool_def(max_chars=6000)
        data = {'file': '课表.xlsx', 'sheet': 'Sheet1', 'shape': {'rows': 12, 'columns': 54}, 'columns': [str(i) for i in range(54)], 'dtypes': {str(i): 'object' for i in range(54)}, 'styles': {'cell_style_map': {f'r{r}c{c}': {'font': '宋体', 'size': 11, 'bold': False} for r in range(12) for c in range(54)}, 'classes': [{'font': '宋体', 'size': 11, 'bold': False, 'color': '#000000'}] * 5}, 'preview': []}
        full_json = json.dumps(data, ensure_ascii=False)
        assert len(full_json) > 6000, f'测试数据应超过限制，实际: {len(full_json)}'
        result = td._truncate_json_smart(data, 6000)
        assert result is not None, '应该能通过 dict 缩减 fit 到限制内'
        assert len(result) <= 6000
        parsed = json.loads(result)
        assert parsed['shape'] == {'rows': 12, 'columns': 54}
        assert parsed['file'] == '课表.xlsx'

class TestRawResultSideChannel:
    """验证 raw_result_str 通过 _ToolExecOutcome 传递。"""

    def test_outcome_raw_result_str_field_exists(self):
        """_ToolExecOutcome 有 raw_result_str 字段。"""
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome
        outcome = _ToolExecOutcome(result_str='truncated', success=True, raw_result_str='full original result')
        assert outcome.raw_result_str == 'full original result'

    def test_outcome_raw_result_str_default_none(self):
        """raw_result_str 默认为 None（向后兼容）。"""
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome
        outcome = _ToolExecOutcome(result_str='ok', success=True)
        assert outcome.raw_result_str is None

class TestTruncateResultIntegration:
    """ToolDef.truncate_result 集成测试。"""

    def test_truncate_result_with_large_dict_and_list(self):
        """同时有大 list 和大 dict 时，都能被缩减。"""
        td = _make_tool_def(max_chars=2000)
        data = {'shape': {'rows': 50, 'columns': 30}, 'preview': [{'col': i} for i in range(50)], 'dtypes': {f'col_{i}': 'float64' for i in range(30)}}
        full = json.dumps(data, ensure_ascii=False)
        result = td.truncate_result(full)
        assert len(result) <= 2000
        parsed = json.loads(result)
        assert parsed['shape'] == {'rows': 50, 'columns': 30}
