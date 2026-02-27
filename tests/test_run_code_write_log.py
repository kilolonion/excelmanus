"""run_code 写入操作日志记录回归测试。

验证 run_code 通过 CodePolicyHandler 执行后，write_operations_log 正确记录，
确保 verifier playbook 能检测到 has_run_code 并注入针对性验证清单。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_session_state():
    """构建最小 SessionState mock，带真实 write_operations_log 行为。"""
    from excelmanus.engine_core.session_state import SessionState
    state = SessionState()
    return state


def _make_engine_with_state(*, code_policy_enabled: bool = True):
    """构建含真实 SessionState 的 mock engine。"""
    state = _make_session_state()
    e = MagicMock()
    e._state = state
    e.state = state
    e.config = SimpleNamespace(
        code_policy_enabled=code_policy_enabled,
        code_policy_extra_safe_modules=[],
        code_policy_extra_blocked_modules=[],
        code_policy_green_auto_approve=True,
        code_policy_yellow_auto_approve=False,
        workspace_root="/tmp/test_ws",
    )
    e.full_access_enabled = False
    e.record_write_action = state.record_write_action
    e.registry = MagicMock()
    e.registry.get_tool.return_value = None
    e.approval = MagicMock()
    e.approval.new_approval_id.return_value = "ap-1"
    e.approval.utc_now.return_value = "2026-01-01T00:00:00Z"
    e.window_perception = None
    e._context_builder = MagicMock()
    e.emit = MagicMock()
    return e, state


class TestCodePolicyHandlerWriteLog:
    """CodePolicyHandler 写入操作日志记录。"""

    @pytest.mark.asyncio
    async def test_ast_write_records_to_write_operations_log(self):
        """AST 检测到写入时，write_operations_log 应有 run_code 条目。"""
        from excelmanus.engine_core.tool_handlers import CodePolicyHandler

        e, state = _make_engine_with_state()
        dispatcher = MagicMock()
        dispatcher._snapshot_excel_for_diff.return_value = {}
        dispatcher._snapshot_uploads_dir.return_value = {}
        dispatcher._diff_uploads_snapshots.return_value = []
        dispatcher._emit_files_changed_from_audit.return_value = None
        dispatcher._extract_run_code_write_summary.return_value = "写入数据到 output.xlsx"

        handler = CodePolicyHandler(engine=e, dispatcher=dispatcher)

        audit_rec = MagicMock()
        audit_rec.changes = [{"file": "output.xlsx"}]
        e.execute_tool_with_audit = AsyncMock(
            return_value=('{"status": "ok", "stdout_tail": "done"}', audit_rec)
        )

        mock_target = MagicMock()
        mock_target.operation = "write"
        mock_target.file_path = "output.xlsx"

        from excelmanus.security.code_policy import CodeRiskTier
        mock_analysis = MagicMock()
        mock_analysis.tier = CodeRiskTier.GREEN
        mock_analysis.capabilities = set()

        with patch(
            "excelmanus.security.code_policy.extract_excel_targets",
            return_value=[mock_target],
        ):
            result = await handler._execute_code_with_policy(
                code='import openpyxl; wb.save("output.xlsx")',
                arguments={"code": 'import openpyxl; wb.save("output.xlsx")'},
                analysis=mock_analysis,
                tool_name="run_code",
                tool_call_id="tc-1",
                tool_scope=None,
                on_event=None,
                iteration=1,
            )

        assert result.success is True
        assert state.has_write_tool_call is True
        # 关键断言：write_operations_log 包含 run_code 条目
        assert len(state.write_operations_log) >= 1
        entry = state.write_operations_log[0]
        assert entry["tool_name"] == "run_code"
        assert "output.xlsx" in entry.get("file_path", "")

    @pytest.mark.asyncio
    async def test_cow_mapping_records_to_write_operations_log(self):
        """CoW 映射存在时，write_operations_log 应有 run_code 条目。"""
        from excelmanus.engine_core.tool_handlers import CodePolicyHandler

        e, state = _make_engine_with_state()
        dispatcher = MagicMock()
        dispatcher._snapshot_excel_for_diff.return_value = {}
        dispatcher._snapshot_uploads_dir.return_value = {}
        dispatcher._diff_uploads_snapshots.return_value = []
        dispatcher._emit_files_changed_from_audit.return_value = None
        dispatcher._extract_run_code_write_summary.return_value = "run_code 写入"

        handler = CodePolicyHandler(engine=e, dispatcher=dispatcher)

        cow_result = json.dumps({
            "status": "ok",
            "cow_mapping": {"/tmp/test_ws/data.xlsx": "/tmp/test_ws/.cow/data_abc.xlsx"},
            "stdout_tail": "",
        })
        audit_rec = MagicMock()
        audit_rec.changes = []
        e.execute_tool_with_audit = AsyncMock(return_value=(cow_result, audit_rec))

        from excelmanus.security.code_policy import CodeRiskTier
        mock_analysis = MagicMock()
        mock_analysis.tier = CodeRiskTier.GREEN
        mock_analysis.capabilities = set()

        with patch(
            "excelmanus.security.code_policy.extract_excel_targets",
            return_value=[],
        ):
            result = await handler._execute_code_with_policy(
                code='wb.save("data.xlsx")',
                arguments={"code": 'wb.save("data.xlsx")'},
                analysis=mock_analysis,
                tool_name="run_code",
                tool_call_id="tc-2",
                tool_scope=None,
                on_event=None,
                iteration=1,
            )

        assert state.has_write_tool_call is True
        assert len(state.write_operations_log) >= 1
        entry = state.write_operations_log[0]
        assert entry["tool_name"] == "run_code"
        assert ".cow/data_abc.xlsx" in entry.get("file_path", "")


class TestVerifierPlaybookRunCode:
    """验证 _select_verification_playbook 能正确识别 run_code。"""

    def test_playbook_includes_run_code_section(self):
        """write_operations_log 含 run_code 时，playbook 应包含 run_code 验证清单。"""
        from excelmanus.engine import AgentEngine

        write_ops = [
            {"tool_name": "run_code", "file_path": "output.xlsx", "summary": "写入数据"},
        ]
        playbook = AgentEngine._select_verification_playbook(write_ops)
        assert "run_code" in playbook
        assert "验证" in playbook

    def test_playbook_empty_without_run_code(self):
        """write_operations_log 为空时，playbook 应为空。"""
        from excelmanus.engine import AgentEngine

        playbook = AgentEngine._select_verification_playbook([])
        assert playbook == ""
