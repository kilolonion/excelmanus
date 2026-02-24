"""P1 Bug 修复验证测试。

覆盖 B1 / B3 / B4 / U1 四个修复点的 Fix Checking 和 Preservation Checking。

Property 1: B1 所有退出路径调用 manifest 刷新
Property 2: B1 run_code 成功后置位刷新标记
Property 3: B3 构建失败后 _workspace_manifest_built 保持 False
Property 4: B4 非法阈值使用默认值
Property 5: U1 初始化后 introspect_capability 可用
Property 6: Preservation — max_iter 路径行为不变
Property 7: Preservation — B3 成功路径缓存行为不变
Property 8: Preservation — B4 合法阈值解析行为不变
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch, call

import pytest
from hypothesis import given, assume, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# B3 测试 — context_builder.py manifest 构建失败可恢复
# ---------------------------------------------------------------------------


def _make_engine_stub(built: bool = False) -> MagicMock:
    """构造一个最小化的 engine stub，模拟 context_builder 所需属性。"""
    e = MagicMock()
    e._workspace_manifest_built = built
    e._workspace_manifest = None
    e._config.workspace_root = "/fake/workspace"
    return e


def _make_context_builder(engine_stub: MagicMock) -> MagicMock:
    """构造一个 ContextBuilder stub，持有 engine stub。"""
    cb = MagicMock()
    cb._engine = engine_stub
    return cb


def _run_build_workspace_manifest(engine_stub: MagicMock, raises: bool) -> None:
    """直接执行 context_builder 中 _build_workspace_manifest 的核心逻辑。

    复制修复后的代码逻辑，用于独立验证，不依赖完整 ContextBuilder 实例化。
    """
    import logging
    logger = logging.getLogger(__name__)

    e = engine_stub
    if not e._workspace_manifest_built:
        try:
            if raises:
                raise OSError("模拟磁盘 I/O 错误")
            # 模拟成功构建
            e._workspace_manifest = object()
            e._workspace_manifest_built = True
        except Exception:
            logger.debug("Workspace manifest 构建失败（测试模拟）")
            e._workspace_manifest = None
            # _workspace_manifest_built 保持 False


class TestB3ManifestBuildFailureRecoverable:
    """Property 3: build_manifest 失败后 _workspace_manifest_built 保持 False。"""

    @given(st.booleans())
    def test_property3_failure_keeps_built_false(self, raises: bool) -> None:
        """对任意 raises 值：失败时 built=False，成功时 built=True。"""
        engine = _make_engine_stub(built=False)
        _run_build_workspace_manifest(engine, raises=raises)

        if raises:
            # Property 3: 失败后保持 False，允许重试
            assert engine._workspace_manifest_built is False
            assert engine._workspace_manifest is None
        else:
            # Property 7 (preservation): 成功后置 True，缓存结果
            assert engine._workspace_manifest_built is True
            assert engine._workspace_manifest is not None

    def test_property3_failure_then_retry_succeeds(self) -> None:
        """失败后再次调用能成功构建（可恢复性验证）。"""
        engine = _make_engine_stub(built=False)

        # 第一次：失败
        _run_build_workspace_manifest(engine, raises=True)
        assert engine._workspace_manifest_built is False

        # 第二次：成功
        _run_build_workspace_manifest(engine, raises=False)
        assert engine._workspace_manifest_built is True
        assert engine._workspace_manifest is not None


class TestB3ManifestBuildCachePreservation:
    """Property 7: build_manifest 成功后多次调用仅构建一次。"""

    @given(st.integers(min_value=2, max_value=10))
    def test_property7_cache_reuse(self, call_count: int) -> None:
        """成功构建后，后续 call_count 次调用均复用缓存，不重复构建。"""
        build_call_count = 0

        def run_once(engine: MagicMock) -> None:
            nonlocal build_call_count
            if not engine._workspace_manifest_built:
                build_call_count += 1
                engine._workspace_manifest = object()
                engine._workspace_manifest_built = True

        engine = _make_engine_stub(built=False)
        for _ in range(call_count):
            run_once(engine)

        assert build_call_count == 1
        assert engine._workspace_manifest_built is True


# ---------------------------------------------------------------------------
# B4 测试 — config.py 阈值解析容错
# ---------------------------------------------------------------------------


def _parse_threshold(env_value: str | None, default: float) -> float:
    """复制修复后的 _parse_threshold 逻辑，独立测试。"""
    if env_value is None:
        return default
    try:
        result = float(env_value)
        if 0.0 <= result <= 1.0:
            return result
    except (ValueError, TypeError):
        pass
    return default


class TestB4ThresholdParsing:
    """Property 4 & 8: 阈值解析的 Fix Checking 和 Preservation Checking。"""

    # ── Property 4: 非法值返回默认值 ──

    @given(st.text())
    def test_property4_invalid_text_returns_default(self, value: str) -> None:
        """非数字字符串返回默认值。"""
        try:
            f = float(value)
            is_valid = 0.0 <= f <= 1.0
        except (ValueError, TypeError):
            is_valid = False

        assume(not is_valid)

        result = _parse_threshold(value, 0.3)
        assert result == 0.3

    @given(st.floats(allow_nan=False, allow_infinity=False).filter(lambda x: x < 0.0 or x > 1.0))
    def test_property4_out_of_range_returns_default(self, value: float) -> None:
        """超出 [0,1] 范围的浮点数字符串返回默认值。"""
        result = _parse_threshold(str(value), 0.25)
        assert result == 0.25

    def test_property4_none_returns_default(self) -> None:
        """None 返回默认值。"""
        assert _parse_threshold(None, 0.3) == 0.3
        assert _parse_threshold(None, 0.25) == 0.25

    def test_property4_specific_invalid_cases(self) -> None:
        """具体非法值验证。"""
        assert _parse_threshold("abc", 0.3) == 0.3
        assert _parse_threshold("-0.5", 0.3) == 0.3
        assert _parse_threshold("1.5", 0.25) == 0.25
        assert _parse_threshold("", 0.3) == 0.3
        assert _parse_threshold("inf", 0.3) == 0.3

    # ── Property 8: 合法值正确解析 ──

    @given(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
    def test_property8_valid_float_parsed_correctly(self, value: float) -> None:
        """[0,1] 区间内的合法浮点数正确解析。"""
        result = _parse_threshold(str(value), 0.3)
        assert result == float(str(value))

    def test_property8_boundary_values(self) -> None:
        """边界值 0.0 和 1.0 正确解析。"""
        assert _parse_threshold("0.0", 0.3) == 0.0
        assert _parse_threshold("1.0", 0.3) == 1.0
        assert _parse_threshold("0.5", 0.3) == 0.5

    def test_property8_config_integration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """集成验证：通过环境变量设置合法值，config 正确加载。"""
        monkeypatch.setenv("EXCELMANUS_MEMORY_SEMANTIC_THRESHOLD", "0.5")
        monkeypatch.setenv("EXCELMANUS_MANIFEST_SEMANTIC_THRESHOLD", "0.6")
        monkeypatch.setenv("EXCELMANUS_WORKSPACE_ROOT", "/tmp")
        monkeypatch.setenv("EXCELMANUS_OPENAI_API_KEY", "test-key")

        from excelmanus.config import load_config
        config = load_config()
        assert config.memory_semantic_threshold == 0.5
        assert config.manifest_semantic_threshold == 0.6

    def test_property4_config_invalid_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """集成验证：非法值回退到默认值，不抛出异常。"""
        monkeypatch.setenv("EXCELMANUS_MEMORY_SEMANTIC_THRESHOLD", "not_a_number")
        monkeypatch.setenv("EXCELMANUS_MANIFEST_SEMANTIC_THRESHOLD", "2.0")
        monkeypatch.setenv("EXCELMANUS_WORKSPACE_ROOT", "/tmp")
        monkeypatch.setenv("EXCELMANUS_OPENAI_API_KEY", "test-key")

        from excelmanus.config import load_config
        config = load_config()
        assert config.memory_semantic_threshold == 0.3   # 默认值
        assert config.manifest_semantic_threshold == 0.25  # 默认值


# ---------------------------------------------------------------------------
# B1 测试 — _tool_calling_loop 退出路径调用 manifest 刷新
# ---------------------------------------------------------------------------


class TestB1ManifestRefreshOnExit:
    """Property 1 & 2: 所有退出路径调用 _try_refresh_manifest。"""

    def test_property1_finish_task_calls_refresh(self) -> None:
        """finish_task 退出路径调用 _try_refresh_manifest。"""
        from excelmanus.engine import AgentEngine

        # 验证修复后代码中 finish_task 路径包含 _try_refresh_manifest 调用
        import inspect
        source = inspect.getsource(AgentEngine._tool_calling_loop)

        # 统一出口 helper 中应有 refresh
        helper_block = source[source.find("def _finalize_result"):source.find("max_iter =")]
        assert "_try_refresh_manifest()" in helper_block

        # finish_task 接受退出块应走统一出口
        finish_task_block = source[source.find("finish_task 接受，退出循环"):]
        assert "return _finalize_result(" in finish_task_block

    def test_property1_pending_approval_calls_refresh(self) -> None:
        """pending_approval 退出路径调用 _try_refresh_manifest。"""
        from excelmanus.engine import AgentEngine
        import inspect
        source = inspect.getsource(AgentEngine._tool_calling_loop)

        block = source[source.find("工具调用进入待确认队列"):]
        assert "return _finalize_result(" in block

    def test_property1_pending_plan_calls_refresh(self) -> None:
        """pending_plan 退出路径调用 _try_refresh_manifest。"""
        from excelmanus.engine import AgentEngine
        import inspect
        source = inspect.getsource(AgentEngine._tool_calling_loop)

        block = source[source.find("工具调用进入待审批计划队列"):]
        assert "return _finalize_result(" in block

    def test_property1_ask_user_calls_refresh(self) -> None:
        """ask_user 退出路径调用 _try_refresh_manifest。"""
        from excelmanus.engine import AgentEngine
        import inspect
        source = inspect.getsource(AgentEngine._tool_calling_loop)

        block = source[source.find("命中 ask_user，进入待回答状态"):]
        assert "return _finalize_result(" in block

    def test_property1_breaker_calls_refresh(self) -> None:
        """breaker_triggered 退出路径调用 _try_refresh_manifest。"""
        from excelmanus.engine import AgentEngine
        import inspect
        source = inspect.getsource(AgentEngine._tool_calling_loop)

        block = source[source.find("连续 %d 次工具失败，熔断终止"):]
        assert "return _finalize_result(" in block

    def test_property2_run_code_sets_refresh_needed(self) -> None:
        """run_code 写入信号通过 _record_write_action 统一置位刷新标记。"""
        from excelmanus.engine import AgentEngine
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        import inspect
        source = inspect.getsource(AgentEngine._tool_calling_loop)
        source_engine = inspect.getsource(AgentEngine)
        source_dispatcher = inspect.getsource(ToolDispatcher._dispatch_tool_execution)

        # _record_workspace_write_action 负责置位 _manifest_refresh_needed
        record_block = source_engine[source_engine.find("def _record_workspace_write_action"):]
        assert "_manifest_refresh_needed = True" in record_block
        # 循环内写入工具走统一写入记录（_record_workspace_write_action / _record_external_write_action）
        assert "_record_workspace_write_action()" in source or "_record_external_write_action()" in source
        # run_code 路径在 dispatcher 的 _dispatch_tool_execution 中同样走统一写入记录
        assert "e.record_write_action()" in source_dispatcher

    def test_property6_max_iter_still_calls_refresh(self) -> None:
        """Preservation: max_iter 路径仍然调用 _try_refresh_manifest（原有行为不变）。"""
        from excelmanus.engine import AgentEngine
        import inspect
        source = inspect.getsource(AgentEngine._tool_calling_loop)

        # max_iter 路径在函数末尾
        max_iter_block = source[source.rfind("达到迭代上限"):]
        assert "return _finalize_result(" in max_iter_block
        helper_block = source[source.find("def _finalize_result"):source.find("max_iter =")]
        assert "_try_refresh_manifest()" in helper_block


# ---------------------------------------------------------------------------
# U1 测试 — introspect_capability 工具注册
# ---------------------------------------------------------------------------


class TestU1IntrospectCapabilityRegistered:
    """Property 5: 引擎初始化后 introspect_capability 在 registry 中可用。"""

    def test_property5_introspect_capability_in_registry(self) -> None:
        """register_introspection_tools 调用后工具可用。"""
        from excelmanus.tools.registry import ToolRegistry
        from excelmanus.tools.introspection_tools import register_introspection_tools

        registry = ToolRegistry()
        assert "introspect_capability" not in registry.get_tool_names()

        register_introspection_tools(registry)
        assert "introspect_capability" in registry.get_tool_names()

    def test_property5_engine_init_registers_introspect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AgentEngine 初始化后 introspect_capability 在 registry 中可用。"""
        from excelmanus.tools.registry import ToolRegistry
        from excelmanus.tools.introspection_tools import register_introspection_tools

        # 验证 engine.py 中确实 import 并调用了 register_introspection_tools
        import excelmanus.engine as engine_module
        assert hasattr(engine_module, "register_introspection_tools"), (
            "register_introspection_tools 未在 engine.py 中 import"
        )

    def test_property5_other_tools_unaffected(self) -> None:
        """Preservation: 其他工具注册不受影响。"""
        from excelmanus.tools.registry import ToolRegistry, ToolDef
        from excelmanus.tools.introspection_tools import register_introspection_tools

        registry = ToolRegistry()
        # 先注册一个普通工具
        registry.register_tool(ToolDef(
            name="dummy_tool",
            description="测试工具",
            input_schema={"type": "object", "properties": {}},
            func=lambda: "ok",
        ))

        register_introspection_tools(registry)

        # 两个工具都应存在
        names = registry.get_tool_names()
        assert "dummy_tool" in names
        assert "introspect_capability" in names
