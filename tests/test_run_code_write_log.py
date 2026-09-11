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
        dispatcher._snapshot_uploads_dir.return_value = {}
        dispatcher._diff_uploads_snapshots.return_value = []
        dispatcher._record_files_from_run_code.return_value = None
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
    async def test_published_records_to_write_operations_log(self):
        """宿主 pending 发布成功时，write_operations_log 应有 run_code 条目。"""
        from excelmanus.engine_core.tool_handlers import CodePolicyHandler

        e, state = _make_engine_with_state()
        dispatcher = MagicMock()
        dispatcher._snapshot_uploads_dir.return_value = {}
        dispatcher._diff_uploads_snapshots.return_value = []
        dispatcher._record_files_from_run_code.return_value = None
        dispatcher._extract_run_code_write_summary.return_value = "run_code 写入"

        handler = CodePolicyHandler(engine=e, dispatcher=dispatcher)

        published_result = json.dumps({
            "status": "ok",
            "published": [{"path": "data.xlsx", "status": "committed", "content_version": "sha256:abc"}],
            "stdout_tail": "",
        })
        audit_rec = MagicMock()
        audit_rec.changes = []
        e.execute_tool_with_audit = AsyncMock(return_value=(published_result, audit_rec))

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
        assert "data.xlsx" in entry.get("file_path", "")


