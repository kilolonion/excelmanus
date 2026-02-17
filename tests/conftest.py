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
        "EXCELMANUS_CORS_ALLOW_ORIGINS",
        "EXCELMANUS_MCP_SHARED_MANAGER",
        "EXCELMANUS_ROUTER_API_KEY",
        "EXCELMANUS_ROUTER_BASE_URL",
        "EXCELMANUS_ROUTER_MODEL",
        "EXCELMANUS_HOOKS_COMMAND_ENABLED",
        "EXCELMANUS_HOOKS_COMMAND_ALLOWLIST",
        "EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS",
        "EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS",
        "EXCELMANUS_WINDOW_PERCEPTION_ENABLED",
        "EXCELMANUS_WINDOW_PERCEPTION_SYSTEM_BUDGET_TOKENS",
        "EXCELMANUS_WINDOW_PERCEPTION_TOOL_APPEND_TOKENS",
        "EXCELMANUS_WINDOW_PERCEPTION_MAX_WINDOWS",
        "EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_ROWS",
        "EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_COLS",
        "EXCELMANUS_WINDOW_PERCEPTION_MINIMIZED_TOKENS",
        "EXCELMANUS_WINDOW_PERCEPTION_BACKGROUND_AFTER_IDLE",
        "EXCELMANUS_WINDOW_PERCEPTION_SUSPEND_AFTER_IDLE",
        "EXCELMANUS_WINDOW_PERCEPTION_TERMINATE_AFTER_IDLE",
        "EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_MODE",
        "EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TIMEOUT_MS",
        "EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_WINDOW_COUNT",
        "EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_TURN",
        "EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_PLAN_TTL_TURNS",
        "EXCELMANUS_WINDOW_RETURN_MODE",
        "EXCELMANUS_WINDOW_FULL_MAX_ROWS",
        "EXCELMANUS_WINDOW_FULL_TOTAL_BUDGET_TOKENS",
        "EXCELMANUS_WINDOW_DATA_BUFFER_MAX_ROWS",
        "EXCELMANUS_MAX_CONTEXT_TOKENS",
        "EXCELMANUS_SKILLS_DISCOVERY_ENABLED",
        "EXCELMANUS_SKILLS_DISCOVERY_SCAN_WORKSPACE_ANCESTORS",
        "EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_AGENTS",
        "EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_CLAUDE",
        "EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_OPENCLAW",
        "EXCELMANUS_SKILLS_DISCOVERY_EXTRA_DIRS",
        "EXCELMANUS_WINDOW_ADVISOR_API_KEY",
        "EXCELMANUS_WINDOW_ADVISOR_BASE_URL",
        "EXCELMANUS_WINDOW_ADVISOR_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
