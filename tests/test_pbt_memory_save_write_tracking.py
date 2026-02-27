"""write_effect 语义写入追踪属性基测试（Hypothesis）。"""

from hypothesis import given, settings, strategies as st

from excelmanus.engine_core.session_state import SessionState
from excelmanus.tools.registry import WriteEffect


# 写入语义枚举值
_WRITE_EFFECTS: list[WriteEffect] = ["none", "workspace_write", "external_write", "dynamic", "unknown"]
_TRIGGERING_EFFECTS: frozenset[str] = frozenset({"workspace_write", "external_write", "dynamic"})


def _simulate_write_effect_detection(effects: list[WriteEffect]) -> bool:
    """模拟基于 write_effect 的写入检测逻辑。"""
    state = SessionState()
    for effect in effects:
        if effect in _TRIGGERING_EFFECTS:
            state.record_write_action()
    return state.has_write_tool_call


class TestProperty1ExternalWriteAlwaysDetected:
    """Property 1: 包含 external_write 的工具调用历史 → 写入检测为 True。"""

    @given(
        other_effects=st.lists(
            st.sampled_from(_WRITE_EFFECTS),
            min_size=0,
            max_size=10,
        ),
        insert_pos=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=200)
    def test_external_write_always_detected(self, other_effects, insert_pos):
        """任何包含 external_write 的历史，写入检测结果为 True。"""
        pos = min(insert_pos, len(other_effects))
        effect_history = other_effects[:pos] + ["external_write"] + other_effects[pos:]
        assert _simulate_write_effect_detection(effect_history) is True


class TestProperty2NoneAndUnknownNeverTrigger:
    """Property 2: 仅包含 none/unknown 的工具调用历史 → 写入检测为 False。"""

    @given(
        effects=st.lists(
            st.sampled_from(["none", "unknown"]),
            min_size=0,
            max_size=10,
        ),
    )
    @settings(max_examples=200)
    def test_none_and_unknown_never_trigger_write(self, effects):
        """仅 none/unknown 时不触发写入检测。"""
        assert _simulate_write_effect_detection(effects) is False


class TestProperty3WorkspaceWriteAlwaysDetected:
    """Property 3: 包含 workspace_write 的工具调用历史 → 写入检测为 True。"""

    @given(
        other_effects=st.lists(
            st.sampled_from(_WRITE_EFFECTS),
            min_size=0,
            max_size=10,
        ),
        insert_pos=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=200)
    def test_workspace_write_always_detected(self, other_effects, insert_pos):
        pos = min(insert_pos, len(other_effects))
        effect_history = other_effects[:pos] + ["workspace_write"] + other_effects[pos:]
        assert _simulate_write_effect_detection(effect_history) is True
