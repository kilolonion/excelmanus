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
        assert state.current_write_hint == "unknown"
        assert state.has_write_tool_call is False
        assert state.execution_guard_fired is False
        assert state.vba_exempt is False
        assert state.turn_diagnostics == []
        assert state.session_diagnostics == []


class TestWriteHintTracking:
    """write_hint 状态追踪。"""

    def test_record_write_action_sets_may_write(self):
        state = SessionState()
        state.record_write_action()
        assert state.has_write_tool_call is True
        assert state.current_write_hint == "may_write"

    def test_set_write_hint_valid(self):
        state = SessionState()
        state.current_write_hint = "read_only"
        assert state.current_write_hint == "read_only"

    def test_record_write_action_idempotent(self):
        state = SessionState()
        state.record_write_action()
        state.record_write_action()
        assert state.has_write_tool_call is True
        assert state.current_write_hint == "may_write"


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

        state.reset_loop_stats()

        assert state.last_iteration_count == 0
        assert state.last_tool_call_count == 0
        assert state.last_success_count == 0
        assert state.last_failure_count == 0
        assert state.has_write_tool_call is False
        assert state.turn_diagnostics == []


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
        assert state.affected_files == ["a.xlsx", "b.xlsx"]

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
        state.current_write_hint = "may_write"
        state.has_write_tool_call = True
        state.execution_guard_fired = True
        state.vba_exempt = True
        state.turn_diagnostics = [{"iteration": 1}]
        state.session_diagnostics = [{"route": "test"}]

        state.reset_session()

        assert state.session_turn == 0
        assert state.last_iteration_count == 0
        assert state.last_tool_call_count == 0
        assert state.last_success_count == 0
        assert state.last_failure_count == 0
        assert state.current_write_hint == "unknown"
        assert state.has_write_tool_call is False
        assert state.execution_guard_fired is False
        assert state.vba_exempt is False
        assert state.turn_diagnostics == []
        assert state.session_diagnostics == []


class TestDiagnostics:
    """诊断数据管理。"""

    def test_session_diagnostics_append(self):
        state = SessionState()
        state.session_diagnostics.append({"route": "test", "iterations": 3})
        assert len(state.session_diagnostics) == 1
        assert state.session_diagnostics[0]["route"] == "test"
