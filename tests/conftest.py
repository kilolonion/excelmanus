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
def _isolate_settings() -> None:
    """每个测试清空设置覆盖层与绑定的设置仓。"""
    from excelmanus.settings_runtime import reset_runtime_settings

    reset_runtime_settings()
    yield
    reset_runtime_settings()


@pytest.fixture(autouse=True)
def _reset_tool_guards() -> None:
    """每个测试结束后清空 ToolCallContext，防止 bind_workspace / init_guard 泄漏。"""
    yield
    from excelmanus.tools.context import clear_call
    from excelmanus.tools.introspection_tools import _call_catalog

    clear_call()
    _call_catalog.set(None)


@pytest.fixture(autouse=True)
def _isolate_api_runtime(monkeypatch):
    import sys
    from excelmanus.api_app_state import AppRuntime, bind_runtime, reset_runtime

    runtime = AppRuntime()
    module = sys.modules.get("excelmanus.api")
    if module is not None and hasattr(module, "app"):
        monkeypatch.setattr(module.app.state, "runtime", runtime)
    token = bind_runtime(runtime)
    try:
        yield runtime
    finally:
        reset_runtime(token)


def symlink_or_skip(link: "Path", target: "Path") -> None:
    """创建符号链接；Windows 无管理员/开发者模式特权时跳过测试。"""
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"当前环境无符号链接特权（WinError 1314）: {exc}")
