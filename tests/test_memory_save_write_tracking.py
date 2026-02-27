"""memory_save 写入追踪单元测试（write_effect 语义）。"""

from excelmanus.engine_core.session_state import SessionState
from excelmanus.tools.memory_tools import get_tools as get_memory_tools


def _get_memory_tool_effect(name: str) -> str | None:
    """从 memory_tools 模块获取指定工具的 write_effect。"""
    for td in get_memory_tools():
        if td.name == name:
            return td.write_effect
    return None


class TestMemorySaveWriteEffect:
    """memory_save 声明为 external_write。"""

    def test_memory_save_declared_external_write(self):
        assert _get_memory_tool_effect("memory_save") == "external_write"

    def test_memory_read_topic_declared_none(self):
        assert _get_memory_tool_effect("memory_read_topic") == "none"


class TestMemorySaveWriteDetection:
    """external_write 工具触发写入检测但不触发 registry 刷新。"""

    def test_external_write_triggers_write_flag(self):
        state = SessionState()
        assert not state.has_write_tool_call
        # external_write 语义：记录写入但不刷新 registry
        effect = _get_memory_tool_effect("memory_save")
        assert effect == "external_write"
        state.record_write_action()
        assert state.has_write_tool_call is True
        assert state.current_write_hint == "may_write"


class TestMemoryReadTopicNoWriteDetection:
    """none 语义工具不触发写入检测。"""

    def test_memory_read_topic_is_none_effect(self):
        effect = _get_memory_tool_effect("memory_read_topic")
        assert effect == "none"

    def test_none_effect_does_not_trigger_write(self):
        state = SessionState()
        effect = _get_memory_tool_effect("memory_read_topic")
        # none 语义：不记录写入
        if effect in ("workspace_write", "external_write", "dynamic"):
            state.record_write_action()
        assert state.has_write_tool_call is False
