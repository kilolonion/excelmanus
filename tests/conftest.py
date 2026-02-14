"""pytest 全局配置与共享 fixtures。"""

import os
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
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试用例自动隔离环境变量，避免测试间互相污染。"""
    for key in (
        "EXCELMANUS_API_KEY",
        "EXCELMANUS_BASE_URL",
        "EXCELMANUS_MODEL",
        "EXCELMANUS_MODELS",
        "EXCELMANUS_LOG_LEVEL",
        "EXCELMANUS_MAX_ITERATIONS",
        "EXCELMANUS_MAX_CONSECUTIVE_FAILURES",
        "EXCELMANUS_SESSION_TTL_SECONDS",
        "EXCELMANUS_MAX_SESSIONS",
        "EXCELMANUS_WORKSPACE_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
