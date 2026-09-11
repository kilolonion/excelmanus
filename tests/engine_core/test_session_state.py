"""SessionState 组件单元测试。"""

from excelmanus.engine_core.session_state import SessionState


class TestSessionStateInit:
    """初始化状态验证。"""

    def test_default_values(self):
        state = SessionState()
        assert state.session_turn == 0
        assert state.last_iteration_count == 0
        assert state.last_tool_call_count == 0
        assert state.last_success_count == 0
        assert state.last_failure_count == 0
        assert state.has_write_tool_call is False
        assert state.turn_diagnostics == []
        assert state.session_diagnostics == []


class TestWriteTracking:
    """真实写入追踪（不是 write_hint 猜测）。"""

    def test_record_write_action_sets_flag(self):
        state = SessionState()
        state.record_write_action()
        assert state.has_write_tool_call is True

    def test_record_write_action_idempotent(self):
        state = SessionState()
        state.record_write_action()
        state.record_write_action()
        assert state.has_write_tool_call is True


class TestTurnManagement:
    """轮次管理。"""

    def test_increment_turn(self):
        state = SessionState()
        state.increment_turn()
        assert state.session_turn == 1
        state.increment_turn()
        assert state.session_turn == 2

    def test_reset_loop_stats(self):
        state = SessionState()
        state.last_iteration_count = 5
        state.last_tool_call_count = 10
        state.last_success_count = 8
        state.last_failure_count = 2
        state.has_write_tool_call = True
        state.turn_diagnostics = [{"iteration": 1}]
        state.injected_context_fingerprint = "abc123"

        state.reset_loop_stats()

        assert state.last_iteration_count == 0
        assert state.last_tool_call_count == 0
        assert state.last_success_count == 0
        assert state.last_failure_count == 0
        assert state.has_write_tool_call is False
        assert state.turn_diagnostics == []
        assert state.injected_context_fingerprint == "abc123"

    def test_reset_session_clears_injected_fingerprint(self):
        state = SessionState()
        state.injected_context_fingerprint = "abc123"
        state.reset_session()
        assert state.injected_context_fingerprint is None


class TestToolCallStats:
    """工具调用统计。"""

    def test_record_tool_success(self):
        state = SessionState()
        state.record_tool_success()
        assert state.last_tool_call_count == 1
        assert state.last_success_count == 1
        assert state.last_failure_count == 0

    def test_record_tool_failure(self):
        state = SessionState()
        state.record_tool_failure()
        assert state.last_tool_call_count == 1
        assert state.last_success_count == 0
        assert state.last_failure_count == 1

    def test_mixed_stats(self):
        state = SessionState()
        state.record_tool_success()
        state.record_tool_success()
        state.record_tool_failure()
        assert state.last_tool_call_count == 3
        assert state.last_success_count == 2
        assert state.last_failure_count == 1


class TestFileContentVersions:
    def test_remember_and_peek_path_aliases(self):
        state = SessionState()
        state.remember_file_version("uploads/sales.xlsx", "sha256:abc")
        assert state.peek_file_version("uploads/sales.xlsx") == "sha256:abc"
        assert state.peek_file_version("./uploads/sales.xlsx") == "sha256:abc"
        assert state.peek_file_version("sales.xlsx") is None

    def test_reset_session_clears_versions(self):
        state = SessionState()
        state.remember_file_version("book.xlsx", "sha256:abc")
        state.reset_session()
        assert state.file_content_versions == {}


class TestAffectedFiles:
    """affected_files 自动追踪。"""

    def test_default_empty(self):
        state = SessionState()
        assert state.affected_files == []

    def test_record_affected_file(self):
        state = SessionState()
        state.record_affected_file("a.xlsx")
        state.record_affected_file("b.xlsx")
        state.record_affected_file("a.xlsx")
        assert state.affected_files == ["./a.xlsx", "./b.xlsx"]

    def test_record_affected_file_drops_backup_path(self):
        state = SessionState()
        state.record_affected_file("outputs/backups/foo_20260911T091344_abcd.xlsx")
        assert state.affected_files == []

    def test_reset_loop_stats_clears_affected_files(self):
        state = SessionState()
        state.record_affected_file("x.xlsx")
        state.reset_loop_stats()
        assert state.affected_files == []


class TestResetSession:
    """reset_session 全量重置。"""

    def test_resets_all_fields(self):
        state = SessionState()
        state.session_turn = 5
        state.last_iteration_count = 10
        state.last_tool_call_count = 20
        state.last_success_count = 15
        state.last_failure_count = 5
        state.has_write_tool_call = True
        state.turn_diagnostics = [{"iteration": 1}]
        state.session_diagnostics = [{"route": "test"}]

        state.reset_session()

        assert state.session_turn == 0
        assert state.last_iteration_count == 0
        assert state.last_tool_call_count == 0
        assert state.last_success_count == 0
        assert state.last_failure_count == 0
        assert state.has_write_tool_call is False
        assert state.turn_diagnostics == []
        assert state.session_diagnostics == []


class TestDiagnostics:
    """诊断数据管理。"""

    def test_session_diagnostics_append(self):
        state = SessionState()
        state.session_diagnostics.append({"route": "test", "iterations": 3})
        assert len(state.session_diagnostics) == 1
        assert state.session_diagnostics[0]["route"] == "test"


class TestLegacyCompat:
    """旧会话字段可反序列化，write_hint 状态机已忽略。"""

    def test_from_dict_ignores_current_write_hint(self):
        state = SessionState.from_dict({
            "session_turn": 3,
            "has_write_tool_call": True,
            "current_write_hint": "may_write",
            "stuck_warning_fired": True,
        })
        assert state.session_turn == 3
        assert state.has_write_tool_call is True
        assert not hasattr(state, "current_write_hint")

    def test_present_as_roundtrip_and_legacy_both(self):
        state = SessionState()
        state.present_as = "code"
        restored = SessionState.from_dict(state.to_dict())
        assert restored.present_as == "code"
        legacy = SessionState.from_dict({"present_as": "both"})
        assert legacy.present_as == "code"
        missing = SessionState.from_dict({})
        assert missing.present_as == "native"
