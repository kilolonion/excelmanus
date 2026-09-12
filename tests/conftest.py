"""pytest 全局配置与共享 fixtures。"""

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from hypothesis import settings as hyp_settings, HealthCheck

# ---------------------------------------------------------------------------
# Hypothesis 配置：本地开发默认 dev（快速），CI 使用完整模式；
# 可通过 --hypothesis-profile=ci 切换。
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
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """每个测试用例自动隔离环境变量，避免测试间互相污染。

    动态清理所有 EXCELMANUS_ 前缀的环境变量，无需手动维护列表。
    同时把 EXCELMANUS_HOME 指到临时目录，避免读写开发者本机 ~/.excelmanus。
    """
    for key in list(os.environ):
        if key.startswith("EXCELMANUS_"):
            monkeypatch.delenv(key, raising=False)
    home = tmp_path / "excelmanus-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)


@pytest.fixture(autouse=True)
def _reset_tool_guards() -> None:
    """每个测试结束后重置所有工具模块的模块级 _guard 单例及 contextvar。

    防止 init_guard(tmp_path) 或 register_builtin_tools 设置的路径在测试结束后污染后续测试。
    """
    yield
    _TOOL_MODULES_WITH_GUARD = [
        "excelmanus.workbook.cells",
        "excelmanus.workbook.data",
        "excelmanus.workbook.styles",
        "excelmanus.workbook.charts",
        "excelmanus.workbook.sheets",
        "excelmanus.tools.file_tools",
        "excelmanus.tools.image_tools",
        "excelmanus.tools.code_tools",
        "excelmanus.tools.shell_tools",
        "excelmanus.tools.intent_tools",
        "excelmanus.tools.reference_tools",
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
