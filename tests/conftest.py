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
    """每个测试用例自动隔离环境变量，避免测试间互相污染。

    动态清理所有 EXCELMANUS_ 前缀的环境变量，无需手动维护列表。
    """
    for key in list(os.environ):
        if key.startswith("EXCELMANUS_"):
            monkeypatch.delenv(key, raising=False)
