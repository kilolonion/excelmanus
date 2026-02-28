"""单元测试：ObservationMasker 观测遮蔽。"""

from __future__ import annotations

from excelmanus.engine_core.observation_masker import (
    FRESH_WINDOW,
    _build_tool_call_name_map,
    mask_messages,
    _mask_run_code,
    _mask_read_excel,
    _mask_generic,
    _mask_tool_result,
)


def _msg(role: str, content: str = "test", **kwargs) -> dict:
    d = {"role": role, "content": content}
    d.update(kwargs)
    return d


def _tool_msg(name: str, content: str) -> dict:
    return {"role": "tool", "name": name, "content": content}


class TestMaskMessages:
    """mask_messages 核心逻辑。"""

    def test_empty_messages(self) -> None:
        assert mask_messages([]) == []

    def test_within_window_no_masking(self) -> None:
        """用户消息数 <= FRESH_WINDOW 时不遮蔽。"""
        msgs = [
            _msg("user", "q1"),
            _msg("assistant", "a1"),
            _tool_msg("read_excel", "x" * 500),
            _msg("user", "q2"),
            _msg("assistant", "a2"),
        ]
        result = mask_messages(msgs, fresh_window=4)
        assert result == msgs  # 原样返回

    def test_old_tool_results_masked(self) -> None:
        """超出窗口的 tool 结果应被遮蔽。"""
        msgs = []
        # 生成 6 轮对话
        for i in range(6):
            msgs.append(_msg("user", f"问题{i}"))
            msgs.append(_msg("assistant", f"回答{i}"))
            msgs.append(_tool_msg("read_excel", "x" * 500))

        result = mask_messages(msgs, fresh_window=2)

        # 最后 2 轮的 user 消息之后的内容应保留原样
        # 前面的 tool 结果应被遮蔽
        old_tool_msgs = [m for m in result[:12] if m.get("role") == "tool"]
        for m in old_tool_msgs:
            assert len(m["content"]) < 500  # 已遮蔽

    def test_user_messages_never_masked(self) -> None:
        """user 消息无论多旧都不遮蔽。"""
        msgs = []
        for i in range(6):
            msgs.append(_msg("user", f"长消息{'x' * 500}"))
            msgs.append(_tool_msg("read_excel", "y" * 500))

        result = mask_messages(msgs, fresh_window=2)
        user_msgs = [m for m in result if m.get("role") == "user"]
        for m in user_msgs:
            assert len(m["content"]) > 400  # 未遮蔽

    def test_assistant_messages_never_masked(self) -> None:
        """assistant 消息无论多旧都不遮蔽。"""
        msgs = []
        for i in range(6):
            msgs.append(_msg("user", f"q{i}"))
            msgs.append(_msg("assistant", f"长回答{'x' * 500}"))
            msgs.append(_tool_msg("read_excel", "y" * 500))

        result = mask_messages(msgs, fresh_window=2)
        asst_msgs = [m for m in result if m.get("role") == "assistant"]
        for m in asst_msgs:
            assert len(m["content"]) > 400  # 未遮蔽

    def test_short_tool_results_not_masked(self) -> None:
        """短 tool 结果（≤200 字）不遮蔽。"""
        msgs = []
        for i in range(6):
            msgs.append(_msg("user", f"q{i}"))
            msgs.append(_tool_msg("read_excel", "short result"))

        result = mask_messages(msgs, fresh_window=2)
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        for m in tool_msgs:
            assert m["content"] == "short result"

    def test_does_not_mutate_original(self) -> None:
        """遮蔽不修改原消息列表。"""
        original_content = "x" * 500
        msgs = [
            _msg("user", "q1"),
            _tool_msg("read_excel", original_content),
            _msg("user", "q2"),
            _msg("user", "q3"),
            _msg("user", "q4"),
            _msg("user", "q5"),
            _msg("user", "q6"),
        ]
        original_len = len(msgs[1]["content"])
        mask_messages(msgs, fresh_window=2)
        assert len(msgs[1]["content"]) == original_len  # 未修改


class TestBuildToolCallNameMap:
    """_build_tool_call_name_map 工具名映射。"""

    def test_extracts_from_assistant_tool_calls(self) -> None:
        msgs = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc_1", "type": "function", "function": {"name": "read_excel", "arguments": "{}"}},
                {"id": "tc_2", "type": "function", "function": {"name": "run_code", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "tc_1", "content": "data..."},
        ]
        name_map = _build_tool_call_name_map(msgs)
        assert name_map["tc_1"] == "read_excel"
        assert name_map["tc_2"] == "run_code"

    def test_empty_messages(self) -> None:
        assert _build_tool_call_name_map([]) == {}

    def test_no_tool_calls(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        assert _build_tool_call_name_map(msgs) == {}


class TestMaskToolResultWithNameMap:
    """_mask_tool_result 使用 name_map 正确路由到工具特定遮蔽。"""

    def test_routes_to_run_code_mask(self) -> None:
        msg = {"role": "tool", "tool_call_id": "tc_1", "content": "x" * 500}
        name_map = {"tc_1": "run_code"}
        result = _mask_tool_result(msg, name_map)
        assert "[run_code" in result["content"] or "输出已截断" in result["content"]

    def test_routes_to_read_excel_mask(self) -> None:
        msg = {"role": "tool", "tool_call_id": "tc_2", "content": "共 500 行, 12 列" + "x" * 300}
        name_map = {"tc_2": "read_excel"}
        result = _mask_tool_result(msg, name_map)
        assert "已读取" in result["content"]

    def test_falls_back_to_generic_without_name_map(self) -> None:
        msg = {"role": "tool", "tool_call_id": "tc_3", "content": "x" * 500}
        result = _mask_tool_result(msg, None)
        assert "已截断" in result["content"]
        assert len(result["content"]) < 500


class TestMaskRunCode:
    """run_code 遮蔽逻辑。"""

    def test_json_output(self) -> None:
        import json
        content = json.dumps({"success": True, "stdout": "结果: " + "x" * 300})
        masked = _mask_run_code(content)
        assert "[run_code 成功]" in masked
        assert "输出已截断" in masked

    def test_json_failure(self) -> None:
        import json
        content = json.dumps({"success": False, "stdout": "Error: something"})
        masked = _mask_run_code(content)
        assert "[run_code 失败]" in masked

    def test_non_json(self) -> None:
        content = "plain text output " + "x" * 300
        masked = _mask_run_code(content)
        assert len(masked) < len(content)
        assert "输出已截断" in masked


class TestMaskReadExcel:
    """read_excel 遮蔽逻辑。"""

    def test_with_row_count(self) -> None:
        content = "Sheet1: 共 500 行, 12 列, columns: [A, B, C] " + "x" * 300
        masked = _mask_read_excel(content)
        assert "500" in masked
        assert "已读取" in masked

    def test_without_metadata(self) -> None:
        content = "some data " + "x" * 300
        masked = _mask_read_excel(content)
        assert "已读取" in masked


class TestMaskGeneric:
    """通用遮蔽。"""

    def test_long_content_truncated(self) -> None:
        content = "x" * 500
        masked = _mask_generic(content)
        assert len(masked) <= 110  # 100 + "[已截断]"
        assert "已截断" in masked

    def test_short_content_unchanged(self) -> None:
        content = "short"
        masked = _mask_generic(content)
        assert masked == "short"
