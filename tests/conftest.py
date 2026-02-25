"""pytest 全局配置与共享 fixtures。"""

import os
from unittest.mock import AsyncMock

import pytest
from hypothesis import settings as hyp_settings, HealthCheck

# ---------------------------------------------------------------------------
# Hypothesis profiles: 本地开发默认 dev（快速），CI 通过
# --hypothesis-profile=ci 切换到完整模式
# ---------------------------------------------------------------------------
hyp_settings.register_profile(
    "dev",
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
hyp_settings.register_profile(
    "ci",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
hyp_settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))


@pytest.fixture(autouse=True)
def _disable_mid_discussion_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """禁用引擎的"中间讨论放行"机制，防止测试中 mock 响应列表被额外迭代耗尽。

    引擎在工具调用后收到短文本回复时会触发"中间讨论放行"继续迭代，
    导致测试中预设的有限 mock 响应被耗尽。此 fixture 通过 monkeypatch
    将 excelmanus.engine 模块中的 _MID_DISCUSSION_MAX_LEN 阈值设为 0，
    使该分支条件 `len(reply_text) < 0` 永远不成立。
    """
    try:
        import excelmanus.engine as engine_mod
        monkeypatch.setattr(engine_mod, "_MID_DISCUSSION_MAX_LEN", 0)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试用例自动隔离环境变量，避免测试间互相污染。

    动态清理所有 EXCELMANUS_ 前缀的环境变量，无需手动维护列表。
    """
    for key in list(os.environ):
        if key.startswith("EXCELMANUS_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _reset_tool_guards() -> None:
    """每个测试结束后重置所有工具模块的模块级 _guard 单例及 contextvar。

    防止 init_guard(tmp_path) 或 register_builtin_tools 设置的路径在测试结束后污染后续测试。
    """
    yield
    _TOOL_MODULES_WITH_GUARD = [
        "excelmanus.tools.worksheet_tools",
        "excelmanus.tools.cell_tools",
        "excelmanus.tools.data_tools",
        "excelmanus.tools.format_tools",
        "excelmanus.tools.advanced_format_tools",
        "excelmanus.tools.chart_tools",
        "excelmanus.tools.sheet_tools",
        "excelmanus.tools.file_tools",
        "excelmanus.tools.image_tools",
        "excelmanus.tools.macro_tools",
        "excelmanus.tools.code_tools",
        "excelmanus.tools.shell_tools",
    ]
    import sys
    for mod_name in _TOOL_MODULES_WITH_GUARD:
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "_guard"):
            mod._guard = None
    # 同时重置 contextvar，防止 register_builtin_tools 设置的 guard 跨测试污染
    _guard_ctx = sys.modules.get("excelmanus.tools._guard_ctx")
    if _guard_ctx is not None:
        _guard_ctx._current_guard.set(None)
