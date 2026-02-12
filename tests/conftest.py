"""pytest 全局配置与共享 fixtures。"""

import os
import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试用例自动隔离环境变量，避免测试间互相污染。"""
    for key in (
        "EXCELMANUS_API_KEY",
        "EXCELMANUS_BASE_URL",
        "EXCELMANUS_MODEL",
        "EXCELMANUS_LOG_LEVEL",
        "EXCELMANUS_MAX_ITERATIONS",
        "EXCELMANUS_MAX_CONSECUTIVE_FAILURES",
        "EXCELMANUS_SESSION_TTL_SECONDS",
        "EXCELMANUS_MAX_SESSIONS",
        "EXCELMANUS_WORKSPACE_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
