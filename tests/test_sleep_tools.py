"""sleep 工具单元测试。"""

from __future__ import annotations

import threading
import time

from excelmanus.tools.sleep_tools import (
    _MAX_SLEEP_SECONDS,
    _cancel_event_var,
    get_tools,
    set_cancel_event,
    sleep,
)


class TestSleepValidation:
    """参数校验测试。"""

    def test_zero_seconds_rejected(self) -> None:
        assert "必须大于 0" in sleep(0)

    def test_negative_seconds_rejected(self) -> None:
        assert "必须大于 0" in sleep(-5)

    def test_exceeds_max_rejected(self) -> None:
        result = sleep(_MAX_SLEEP_SECONDS + 1)
        assert "不能超过" in result

    def test_boundary_max_accepted(self) -> None:
        """最大值边界：不应被拒绝（但会实际 sleep，用 cancel 立即中断）。"""
        event = threading.Event()
        event.set()  # 立即取消以避免真正等待 300 秒
        set_cancel_event(event)
        result = sleep(_MAX_SLEEP_SECONDS)
        assert "已取消" in result
        assert "未实际等待" in result


class TestSleepExecution:
    """正常执行测试。"""

    def test_short_sleep_completes(self) -> None:
        t0 = time.monotonic()
        result = sleep(0.1)
        elapsed = time.monotonic() - t0
        assert "已等待" in result
        assert elapsed >= 0.09

    def test_reason_displayed(self) -> None:
        result = sleep(0.05, reason="等待文件同步")
        assert "等待文件同步" in result

    def test_no_reason_no_label(self) -> None:
        result = sleep(0.05)
        assert "原因" not in result


class TestSleepCancellation:
    """取消机制测试。"""

    def setup_method(self) -> None:
        self._event = threading.Event()
        self._token = set_cancel_event(self._event)

    def teardown_method(self) -> None:
        _cancel_event_var.reset(self._token)

    def test_cancel_interrupts_sleep(self) -> None:
        """从另一个线程取消 sleep，应在 _TICK 内返回。

        注意：threading.Thread 不会自动继承 ContextVar，
        需要在子线程内手动设置（模拟 asyncio.to_thread 的行为）。
        """
        results: list[str] = []
        event = self._event

        def _run() -> None:
            # 手动注入 contextvar（asyncio.to_thread 会自动完成此步骤）
            token = set_cancel_event(event)
            try:
                results.append(sleep(60))
            finally:
                _cancel_event_var.reset(token)

        t = threading.Thread(target=_run)
        t.start()

        # 等一小段时间让 sleep 开始，然后取消
        time.sleep(0.2)
        event.set()
        t.join(timeout=10)

        assert not t.is_alive(), "sleep 线程应已退出"
        assert len(results) == 1
        assert "已取消" in results[0]

    def test_cancel_before_sleep_starts(self) -> None:
        """先设置 cancel 再调 sleep，应立即返回。"""
        self._event.set()
        t0 = time.monotonic()
        result = sleep(60)
        elapsed = time.monotonic() - t0
        assert "已取消" in result
        assert "未实际等待" in result
        assert elapsed < 1.0

    def test_cancel_clears_after_use(self) -> None:
        """取消后 event 应被清除，后续 sleep 可正常执行。"""
        self._event.set()
        sleep(10)
        assert not self._event.is_set()
        result = sleep(0.05)
        assert "已等待" in result

    def test_no_contextvar_fallback(self) -> None:
        """未设置 contextvar 时，sleep 回退到本地 event，正常完成（CLI 模式）。"""
        # 临时清除 contextvar
        _cancel_event_var.reset(self._token)
        self._token = _cancel_event_var.set(None)
        result = sleep(0.05)
        assert "已等待" in result
        # 恢复
        _cancel_event_var.reset(self._token)
        self._token = set_cancel_event(self._event)


class TestGetTools:
    """工具定义测试。"""

    def test_returns_one_tool(self) -> None:
        tools = get_tools()
        assert len(tools) == 1

    def test_tool_name(self) -> None:
        tool = get_tools()[0]
        assert tool.name == "sleep"

    def test_schema_has_seconds(self) -> None:
        tool = get_tools()[0]
        props = tool.input_schema["properties"]
        assert "seconds" in props
        assert props["seconds"]["maximum"] == _MAX_SLEEP_SECONDS

    def test_write_effect_none(self) -> None:
        tool = get_tools()[0]
        assert tool.write_effect == "none"
