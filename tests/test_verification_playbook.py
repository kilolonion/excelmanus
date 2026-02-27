"""_select_verification_playbook 单元测试。

覆盖：
- 空 write_ops → 返回空
- 单一工具类型（write_cells / create_sheet / delete_sheet / insert_rows / run_code）
- 公式检测（summary 含关键词）
- 跨 sheet 操作（多 sheet / create_sheet）
- 混合操作（多种工具+公式+跨表）
- verifier prompt 包含 playbook 注入
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.engine import AgentEngine
from excelmanus.subagent.models import SubagentResult


class TestSelectVerificationPlaybook:
    """_select_verification_playbook 纯逻辑测试。"""

    def test_empty_write_ops(self):
        assert AgentEngine._select_verification_playbook([]) == ""

    def test_write_cells_only(self):
        ops = [{"tool_name": "write_cells", "file_path": "a.xlsx", "sheet": "S1"}]
        result = AgentEngine._select_verification_playbook(ops)
        assert "数据写入验证" in result
        assert "公式验证" not in result

    def test_create_sheet_triggers_cross_sheet(self):
        ops = [{"tool_name": "create_sheet", "file_path": "a.xlsx", "summary": "创建 sheet「汇总」"}]
        result = AgentEngine._select_verification_playbook(ops)
        assert "跨表一致性" in result

    def test_delete_sheet(self):
        ops = [{"tool_name": "delete_sheet", "file_path": "a.xlsx", "summary": "删除 sheet「临时」"}]
        result = AgentEngine._select_verification_playbook(ops)
        assert "删除验证" in result

    def test_insert_rows(self):
        ops = [{"tool_name": "insert_rows", "file_path": "a.xlsx"}]
        result = AgentEngine._select_verification_playbook(ops)
        assert "插入行/列验证" in result

    def test_insert_columns(self):
        ops = [{"tool_name": "insert_columns", "file_path": "a.xlsx"}]
        result = AgentEngine._select_verification_playbook(ops)
        assert "插入行/列验证" in result

    def test_run_code_write(self):
        ops = [{"tool_name": "run_code", "file_path": "a.xlsx", "summary": "pandas 写入 500 行"}]
        result = AgentEngine._select_verification_playbook(ops)
        assert "run_code 写入验证" in result

    def test_formula_detected_from_summary_cn(self):
        ops = [{"tool_name": "write_cells", "summary": "写入 VLOOKUP 公式"}]
        result = AgentEngine._select_verification_playbook(ops)
        assert "公式验证" in result
        # 公式模式下不应有"数据写入验证"
        assert "数据写入验证" not in result

    def test_formula_detected_from_vlookup(self):
        ops = [{"tool_name": "write_cells", "summary": "写入 vlookup 引用"}]
        result = AgentEngine._select_verification_playbook(ops)
        assert "公式验证" in result

    def test_formula_detected_from_english(self):
        ops = [{"tool_name": "write_cells", "summary": "write formula to cells"}]
        result = AgentEngine._select_verification_playbook(ops)
        assert "公式验证" in result

    def test_cross_sheet_multi_sheets(self):
        ops = [
            {"tool_name": "write_cells", "sheet": "Sheet1"},
            {"tool_name": "write_cells", "sheet": "Sheet2"},
        ]
        result = AgentEngine._select_verification_playbook(ops)
        assert "跨表一致性" in result

    def test_single_sheet_no_cross(self):
        ops = [
            {"tool_name": "write_cells", "sheet": "Sheet1"},
            {"tool_name": "write_cells", "sheet": "Sheet1"},
        ]
        result = AgentEngine._select_verification_playbook(ops)
        assert "跨表一致性" not in result

    def test_mixed_operations(self):
        ops = [
            {"tool_name": "run_code", "file_path": "a.xlsx", "summary": "pandas groupby 写入"},
            {"tool_name": "write_cells", "sheet": "Sheet1", "summary": "写入 VLOOKUP 公式"},
            {"tool_name": "create_sheet", "summary": "创建 sheet「汇总」"},
        ]
        result = AgentEngine._select_verification_playbook(ops)
        assert "公式验证" in result
        assert "跨表一致性" in result
        assert "run_code 写入验证" in result
        # 有公式时不出现通用数据写入
        assert "数据写入验证" not in result

    def test_unknown_tool_only_returns_empty(self):
        ops = [{"tool_name": "some_unknown_tool"}]
        result = AgentEngine._select_verification_playbook(ops)
        assert result == ""

    def test_playbook_has_header(self):
        ops = [{"tool_name": "write_cells", "sheet": "S1"}]
        result = AgentEngine._select_verification_playbook(ops)
        assert "针对性验证清单" in result


class TestVerifierPromptPlaybookInjection:
    """验证 playbook 注入到 verifier prompt。"""

    @pytest.mark.asyncio
    async def test_playbook_injected_when_write_ops_present(self):
        from excelmanus.config import ExcelManusConfig
        from excelmanus.tools.registry import ToolRegistry

        cfg = ExcelManusConfig(
            api_key="test-key",
            base_url="https://test.example.com/v1",
            model="test-model",
            max_iterations=20,
            max_consecutive_failures=3,
            workspace_root=str(Path(__file__).resolve().parent),
            backup_enabled=False,
        )
        engine = AgentEngine(config=cfg, registry=ToolRegistry())
        engine._subagent_enabled = True

        # 模拟跨表 + 公式写入
        engine._state.record_write_operation(
            tool_name="write_cells", sheet="Sheet1", summary="写入 VLOOKUP 公式",
        )
        engine._state.record_write_operation(
            tool_name="create_sheet", summary="创建 sheet「汇总」",
        )

        mock_result = SubagentResult(
            success=True,
            summary=json.dumps({"verdict": "pass", "checks": ["ok"]}),
            subagent_name="verifier",
            permission_mode="readOnly",
            conversation_id="v1",
        )
        captured: list[str] = []

        async def _cap(*, agent_name, prompt, on_event=None):
            captured.append(prompt)
            return mock_result

        with patch.object(engine, "run_subagent", side_effect=_cap):
            await engine._run_finish_verifier_advisory(
                report={"operations": "跨表操作"},
                summary="",
            )

        assert len(captured) == 1
        p = captured[0]
        assert "针对性验证清单" in p
        assert "公式验证" in p
        assert "跨表一致性" in p

    @pytest.mark.asyncio
    async def test_playbook_omitted_when_no_write_ops(self):
        from excelmanus.config import ExcelManusConfig
        from excelmanus.tools.registry import ToolRegistry

        cfg = ExcelManusConfig(
            api_key="test-key",
            base_url="https://test.example.com/v1",
            model="test-model",
            max_iterations=20,
            max_consecutive_failures=3,
            workspace_root=str(Path(__file__).resolve().parent),
            backup_enabled=False,
        )
        engine = AgentEngine(config=cfg, registry=ToolRegistry())
        engine._subagent_enabled = True

        mock_result = SubagentResult(
            success=True,
            summary=json.dumps({"verdict": "pass", "checks": ["ok"]}),
            subagent_name="verifier",
            permission_mode="readOnly",
            conversation_id="v1",
        )
        captured: list[str] = []

        async def _cap(*, agent_name, prompt, on_event=None):
            captured.append(prompt)
            return mock_result

        with patch.object(engine, "run_subagent", side_effect=_cap):
            await engine._run_finish_verifier_advisory(
                report={"operations": "读取数据"},
                summary="",
            )

        assert len(captured) == 1
        assert "针对性验证清单" not in captured[0]
