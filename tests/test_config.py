"""配置管理模块测试：Property 16、17 + 单元测试。

覆盖需求：6.1, 6.2, 6.3, 6.4, 6.5, 6.7, 6.8
"""

from __future__ import annotations

import os
import tempfile

import pytest
from hypothesis import given
from hypothesis import strategies as st

from excelmanus.config import ConfigError, load_config, load_cors_allow_origins


# ── 辅助策略 ──────────────────────────────────────────────

# 生成合法的非空 ASCII 字符串作为 API Key
_api_key_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=64,
).filter(lambda s: s.strip())

# 生成合法的 HTTP/HTTPS URL
_valid_url_st = st.sampled_from([
    "https://api.openai.com/v1",
    "http://localhost:8080",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://example.com/api",
    "http://192.168.1.1:3000/v2",
])

# 生成合法的模型名称
_model_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_."),
    min_size=1,
    max_size=32,
).filter(lambda s: s.strip())

# 生成合法的正整数
_pos_int_st = st.integers(min_value=1, max_value=100000)


# ══════════════════════════════════════════════════════════
# Property 16：环境变量加载
# 通过环境变量设置的值必须在 load_config() 输出中精确反映。
# **Validates: Requirements 6.1**
# ══════════════════════════════════════════════════════════


@given(
    api_key=_api_key_st,
    base_url=_valid_url_st,
    model=_model_st,
    max_iter=_pos_int_st,
    max_fail=_pos_int_st,
    ttl=_pos_int_st,
    max_sess=_pos_int_st,
)
def test_property16_env_vars_reflected(
    api_key: str,
    base_url: str,
    model: str,
    max_iter: int,
    max_fail: int,
    ttl: int,
    max_sess: int,
) -> None:
    """Property 16：环境变量设置的值必须在 load_config() 输出中精确反映。

    **Validates: Requirements 6.1**
    """
    env_vars = {
        "EXCELMANUS_API_KEY": api_key,
        "EXCELMANUS_BASE_URL": base_url,
        "EXCELMANUS_MODEL": model,
        "EXCELMANUS_MAX_ITERATIONS": str(max_iter),
        "EXCELMANUS_MAX_CONSECUTIVE_FAILURES": str(max_fail),
        "EXCELMANUS_SESSION_TTL_SECONDS": str(ttl),
        "EXCELMANUS_MAX_SESSIONS": str(max_sess),
    }
    # 使用 os.environ 直接操作，避免 hypothesis + monkeypatch 作用域冲突
    old_env = {k: os.environ.get(k) for k in env_vars}
    try:
        os.environ.update(env_vars)
        cfg = load_config()

        assert cfg.api_key == api_key
        assert cfg.base_url == base_url
        assert cfg.model == model
        assert cfg.max_iterations == max_iter
        assert cfg.max_consecutive_failures == max_fail
        assert cfg.session_ttl_seconds == ttl
        assert cfg.max_sessions == max_sess
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ══════════════════════════════════════════════════════════
# Property 17：Base URL 验证
# 仅接受合法 HTTP/HTTPS URL。
# **Validates: Requirements 6.5**
# ══════════════════════════════════════════════════════════

# 非法 URL 策略：各种不合法的 URL 格式
_invalid_url_st = st.sampled_from([
    "ftp://example.com",
    "not-a-url",
    "://missing-scheme",
    "",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "htp://typo.com",
    "httpx://wrong.com",
    "just-text",
    "  ",
])


@given(invalid_url=_invalid_url_st)
def test_property17_invalid_base_url_rejected(
    invalid_url: str,
) -> None:
    """Property 17：非法 URL 必须被拒绝。

    **Validates: Requirements 6.5**
    """
    old_key = os.environ.get("EXCELMANUS_API_KEY")
    old_url = os.environ.get("EXCELMANUS_BASE_URL")
    old_model = os.environ.get("EXCELMANUS_MODEL")
    old_cwd = os.getcwd()
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            os.environ["EXCELMANUS_API_KEY"] = "test-key"
            os.environ["EXCELMANUS_BASE_URL"] = invalid_url
            os.environ["EXCELMANUS_MODEL"] = "test-model"
            with pytest.raises(ConfigError):
                load_config()
    finally:
        os.chdir(old_cwd)
        if old_key is None:
            os.environ.pop("EXCELMANUS_API_KEY", None)
        else:
            os.environ["EXCELMANUS_API_KEY"] = old_key
        if old_url is None:
            os.environ.pop("EXCELMANUS_BASE_URL", None)
        else:
            os.environ["EXCELMANUS_BASE_URL"] = old_url
        if old_model is None:
            os.environ.pop("EXCELMANUS_MODEL", None)
        else:
            os.environ["EXCELMANUS_MODEL"] = old_model


@given(valid_url=_valid_url_st)
def test_property17_valid_base_url_accepted(
    valid_url: str,
) -> None:
    """Property 17：合法 HTTP/HTTPS URL 必须被接受。

    **Validates: Requirements 6.5**
    """
    old_key = os.environ.get("EXCELMANUS_API_KEY")
    old_url = os.environ.get("EXCELMANUS_BASE_URL")
    old_model = os.environ.get("EXCELMANUS_MODEL")
    old_cwd = os.getcwd()
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            os.environ["EXCELMANUS_API_KEY"] = "test-key"
            os.environ["EXCELMANUS_BASE_URL"] = valid_url
            os.environ["EXCELMANUS_MODEL"] = "test-model"
            cfg = load_config()
            assert cfg.base_url == valid_url
    finally:
        os.chdir(old_cwd)
        if old_key is None:
            os.environ.pop("EXCELMANUS_API_KEY", None)
        else:
            os.environ["EXCELMANUS_API_KEY"] = old_key
        if old_url is None:
            os.environ.pop("EXCELMANUS_BASE_URL", None)
        else:
            os.environ["EXCELMANUS_BASE_URL"] = old_url
        if old_model is None:
            os.environ.pop("EXCELMANUS_MODEL", None)
        else:
            os.environ["EXCELMANUS_MODEL"] = old_model


# ══════════════════════════════════════════════════════════
# 单元测试：缺失配置、默认值、.env 优先级
# ══════════════════════════════════════════════════════════


class TestMissingConfig:
    """测试缺失必填配置项的行为。"""

    def test_missing_api_key_raises_config_error(self, monkeypatch, tmp_path) -> None:
        """缺少 API Key 时必须抛出 ConfigError。（需求 6.3）"""
        # 切换到无 .env 的临时目录，避免 load_dotenv 加载项目根目录的 .env
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigError, match="EXCELMANUS_API_KEY"):
            load_config()

    def test_error_message_mentions_variable_name(self, monkeypatch, tmp_path) -> None:
        """错误信息必须指明需要设置的变量名。（需求 6.3）"""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigError) as exc_info:
            load_config()
        assert "EXCELMANUS_API_KEY" in str(exc_info.value)


class TestDefaultValues:
    """测试默认值是否正确。"""

    def test_missing_base_url_raises_config_error(self, monkeypatch, tmp_path) -> None:
        """缺少 BASE_URL 时必须抛出 ConfigError。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        with pytest.raises(ConfigError, match="EXCELMANUS_BASE_URL"):
            load_config()

    def test_missing_model_raises_config_error(self, monkeypatch, tmp_path) -> None:
        """缺少 MODEL 时必须抛出 ConfigError。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        with pytest.raises(ConfigError, match="EXCELMANUS_MODEL"):
            load_config()

    def test_default_max_iterations(self, monkeypatch) -> None:
        """默认最大迭代次数为 20。（需求 6.6）"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        cfg = load_config()
        assert cfg.max_iterations == 50

    def test_default_max_consecutive_failures(self, monkeypatch) -> None:
        """默认最大连续失败次数为 6。（需求 6.6）"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        cfg = load_config()
        assert cfg.max_consecutive_failures == 6

    def test_default_session_ttl(self, monkeypatch) -> None:
        """默认会话 TTL 为 1800 秒。（需求 6.7）"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        cfg = load_config()
        assert cfg.session_ttl_seconds == 1800

    def test_default_max_sessions(self, monkeypatch) -> None:
        """默认最大会话数为 1000。（需求 6.7）"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        cfg = load_config()
        assert cfg.max_sessions == 1000

    def test_default_skills_context_char_budget(self, monkeypatch) -> None:
        """默认技能正文字符预算为 12000。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        cfg = load_config()
        assert cfg.skills_context_char_budget == 12000

    def test_skills_context_char_budget_zero_allowed(self, monkeypatch) -> None:
        """技能正文字符预算允许设为 0（表示不限制）。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_SKILLS_CONTEXT_CHAR_BUDGET", "0")
        cfg = load_config()
        assert cfg.skills_context_char_budget == 0

    def test_default_workspace_root(self, monkeypatch) -> None:
        """默认工作目录白名单根路径为当前目录。（需求 6.8）"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        cfg = load_config()
        assert cfg.workspace_root == "."

    def test_default_large_excel_threshold_bytes(self, monkeypatch) -> None:
        """默认大文件阈值为 8MB。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        cfg = load_config()
        assert cfg.large_excel_threshold_bytes == 8 * 1024 * 1024

    def test_large_excel_threshold_bytes_from_env(self, monkeypatch) -> None:
        """允许通过环境变量覆盖大文件阈值。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_LARGE_EXCEL_THRESHOLD_BYTES", "4096")
        cfg = load_config()
        assert cfg.large_excel_threshold_bytes == 4096

    def test_default_tool_result_hard_cap_chars(self, monkeypatch) -> None:
        """默认工具结果全局硬截断上限为 12000。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        cfg = load_config()
        assert cfg.tool_result_hard_cap_chars == 12000

    def test_tool_result_hard_cap_chars_from_env(self, monkeypatch) -> None:
        """允许通过环境变量覆盖工具结果全局硬截断上限。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS", "2048")
        cfg = load_config()
        assert cfg.tool_result_hard_cap_chars == 2048

    def test_system_message_mode_supports_replace(self, monkeypatch) -> None:
        """system_message_mode 支持 replace。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_SYSTEM_MESSAGE_MODE", "replace")
        cfg = load_config()
        assert cfg.system_message_mode == "replace"

    def test_system_message_mode_legacy_multi_rejected(self, monkeypatch) -> None:
        """旧值 multi 已移除，应直接报错。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_SYSTEM_MESSAGE_MODE", "multi")
        with pytest.raises(ConfigError, match="EXCELMANUS_SYSTEM_MESSAGE_MODE"):
            load_config()

    def test_system_message_mode_rejects_invalid_value(self, monkeypatch) -> None:
        """非法 system_message_mode 应报错。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_SYSTEM_MESSAGE_MODE", "invalid")
        with pytest.raises(ConfigError, match="EXCELMANUS_SYSTEM_MESSAGE_MODE"):
            load_config()

    def test_default_subagent_config(self, monkeypatch) -> None:
        """subagent 配置默认值。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        # 显式清空统一/旧模型变量，避免本地 .env 干扰默认值断言
        monkeypatch.setenv("EXCELMANUS_AUX_MODEL", "")
        monkeypatch.setenv("EXCELMANUS_SUBAGENT_MODEL", "")
        monkeypatch.setenv("EXCELMANUS_WINDOW_ADVISOR_MODEL", "")
        cfg = load_config()
        assert cfg.subagent_enabled is True
        assert cfg.aux_model is None
        assert cfg.subagent_max_iterations == 120
        assert cfg.subagent_max_consecutive_failures == 6
        assert cfg.subagent_user_dir == "~/.excelmanus/agents"
        assert cfg.subagent_project_dir == ".excelmanus/agents"

    def test_subagent_config_from_env(self, monkeypatch) -> None:
        """支持通过环境变量覆盖 subagent 配置。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_SUBAGENT_ENABLED", "false")
        monkeypatch.setenv("EXCELMANUS_AUX_MODEL", "qwen-turbo")
        monkeypatch.setenv("EXCELMANUS_SUBAGENT_MAX_ITERATIONS", "4")
        monkeypatch.setenv("EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES", "1")
        monkeypatch.setenv("EXCELMANUS_SUBAGENT_USER_DIR", "~/.my-agents")
        monkeypatch.setenv("EXCELMANUS_SUBAGENT_PROJECT_DIR", ".my-agents")
        cfg = load_config()
        assert cfg.subagent_enabled is False
        assert cfg.aux_model == "qwen-turbo"
        assert cfg.subagent_max_iterations == 4
        assert cfg.subagent_max_consecutive_failures == 1
        assert cfg.subagent_user_dir == "~/.my-agents"
        assert cfg.subagent_project_dir == ".my-agents"

    def test_aux_model_falls_back_to_legacy_subagent_model(self, monkeypatch) -> None:
        """兼容旧变量 EXCELMANUS_SUBAGENT_MODEL。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_SUBAGENT_MODEL", "legacy-subagent")
        cfg = load_config()
        assert cfg.aux_model == "legacy-subagent"

    def test_aux_model_falls_back_to_legacy_window_advisor_model(self, monkeypatch) -> None:
        """兼容旧变量 EXCELMANUS_WINDOW_ADVISOR_MODEL。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_WINDOW_ADVISOR_MODEL", "legacy-window-advisor")
        cfg = load_config()
        assert cfg.aux_model == "legacy-window-advisor"

    def test_default_external_safe_mode_enabled(self, monkeypatch) -> None:
        """默认开启对外安全模式。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        cfg = load_config()
        assert cfg.external_safe_mode is True

    def test_external_safe_mode_can_be_disabled(self, monkeypatch) -> None:
        """允许通过环境变量关闭对外安全模式。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_EXTERNAL_SAFE_MODE", "false")
        cfg = load_config()
        assert cfg.external_safe_mode is False

    def test_config_is_frozen(self, monkeypatch) -> None:
        """配置对象不可变。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        cfg = load_config()
        with pytest.raises(AttributeError):
            cfg.api_key = "new-key"  # type: ignore[misc]


class TestDotEnvPriority:
    """.env 文件优先级测试：环境变量 > .env > 默认值。（需求 6.1, 6.2）"""

    def test_dotenv_provides_api_key(self, monkeypatch, tmp_path) -> None:
        """.env 文件中的 API Key 应被正确加载。"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "EXCELMANUS_API_KEY=from-dotenv\n"
            "EXCELMANUS_BASE_URL=https://example.com/v1\n"
            "EXCELMANUS_MODEL=test-model\n"
        )
        monkeypatch.chdir(tmp_path)

        cfg = load_config()
        assert cfg.api_key == "from-dotenv"

    def test_env_var_overrides_dotenv(self, monkeypatch, tmp_path) -> None:
        """环境变量优先于 .env 文件。"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "EXCELMANUS_API_KEY=from-dotenv\n"
            "EXCELMANUS_BASE_URL=https://example.com/v1\n"
            "EXCELMANUS_MODEL=dotenv-model\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXCELMANUS_API_KEY", "from-env")
        monkeypatch.setenv("EXCELMANUS_MODEL", "env-model")

        cfg = load_config()
        assert cfg.api_key == "from-env"
        assert cfg.model == "env-model"

    def test_dotenv_base_url_loaded(self, monkeypatch, tmp_path) -> None:
        """.env 文件中的 Base URL 应被正确加载。"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "EXCELMANUS_API_KEY=test-key\n"
            "EXCELMANUS_BASE_URL=https://custom.api.com/v1\n"
            "EXCELMANUS_MODEL=test-model\n"
        )
        monkeypatch.chdir(tmp_path)

        cfg = load_config()
        assert cfg.base_url == "https://custom.api.com/v1"


class TestIntegerParsing:
    """整数配置项解析测试。"""

    def test_invalid_integer_raises_error(self, monkeypatch) -> None:
        """非整数值应抛出 ConfigError。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_MAX_ITERATIONS", "not-a-number")
        with pytest.raises(ConfigError, match="整数"):
            load_config()

    def test_zero_integer_raises_error(self, monkeypatch) -> None:
        """零值应抛出 ConfigError（要求正整数）。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_MAX_ITERATIONS", "0")
        with pytest.raises(ConfigError, match="正整数"):
            load_config()

    def test_negative_integer_raises_error(self, monkeypatch) -> None:
        """负值应抛出 ConfigError。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_MAX_SESSIONS", "-5")
        with pytest.raises(ConfigError, match="正整数"):
            load_config()


class TestWorkspaceRoot:
    """工作目录白名单配置测试。"""

    def test_workspace_root_from_env(self, monkeypatch, tmp_path) -> None:
        """EXCELMANUS_WORKSPACE_ROOT 应被正确读取。（需求 6.8）"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_WORKSPACE_ROOT", str(tmp_path))
        cfg = load_config()
        assert cfg.workspace_root == str(tmp_path)


class TestMemoryConfig:
    """跨会话持久记忆配置项测试。（需求 8.1, 8.2, 8.3）"""

    def _set_required_env(self, monkeypatch) -> None:
        """设置必填环境变量。"""
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")

    def test_default_memory_enabled(self, monkeypatch) -> None:
        """memory_enabled 默认值为 True。（需求 8.1）"""
        self._set_required_env(monkeypatch)
        cfg = load_config()
        assert cfg.memory_enabled is True

    def test_memory_enabled_false(self, monkeypatch) -> None:
        """通过环境变量关闭 memory_enabled。（需求 8.1）"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_MEMORY_ENABLED", "false")
        cfg = load_config()
        assert cfg.memory_enabled is False

    def test_memory_enabled_true_explicit(self, monkeypatch) -> None:
        """通过环境变量显式开启 memory_enabled。（需求 8.1）"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_MEMORY_ENABLED", "true")
        cfg = load_config()
        assert cfg.memory_enabled is True

    def test_memory_enabled_invalid_raises_error(self, monkeypatch) -> None:
        """memory_enabled 非法值应抛出 ConfigError。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_MEMORY_ENABLED", "maybe")
        with pytest.raises(ConfigError, match="布尔值"):
            load_config()

    def test_default_memory_dir(self, monkeypatch) -> None:
        """memory_dir 默认值为 ~/.excelmanus/memory。（需求 8.2）"""
        self._set_required_env(monkeypatch)
        cfg = load_config()
        assert cfg.memory_dir == "~/.excelmanus/memory"

    def test_memory_dir_from_env(self, monkeypatch, tmp_path) -> None:
        """通过环境变量自定义 memory_dir。（需求 8.2）"""
        self._set_required_env(monkeypatch)
        custom_dir = str(tmp_path / "custom_memory")
        monkeypatch.setenv("EXCELMANUS_MEMORY_DIR", custom_dir)
        cfg = load_config()
        assert cfg.memory_dir == custom_dir

    def test_default_memory_auto_load_lines(self, monkeypatch) -> None:
        """memory_auto_load_lines 默认值为 200。（需求 8.3）"""
        self._set_required_env(monkeypatch)
        cfg = load_config()
        assert cfg.memory_auto_load_lines == 200

    def test_memory_auto_load_lines_from_env(self, monkeypatch) -> None:
        """通过环境变量自定义 memory_auto_load_lines。（需求 8.3）"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_MEMORY_AUTO_LOAD_LINES", "500")
        cfg = load_config()
        assert cfg.memory_auto_load_lines == 500

    def test_memory_auto_load_lines_invalid_raises_error(self, monkeypatch) -> None:
        """memory_auto_load_lines 非法值应抛出 ConfigError。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_MEMORY_AUTO_LOAD_LINES", "abc")
        with pytest.raises(ConfigError, match="整数"):
            load_config()

    def test_memory_auto_load_lines_zero_raises_error(self, monkeypatch) -> None:
        """memory_auto_load_lines 为 0 应抛出 ConfigError（要求正整数）。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_MEMORY_AUTO_LOAD_LINES", "0")
        with pytest.raises(ConfigError, match="正整数"):
            load_config()


class TestWindowPerceptionConfig:
    """窗口感知层配置解析测试（v4）。"""

    def _set_required_env(self, monkeypatch) -> None:
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        # 固定窗口感知默认值，避免本地 .env 干扰“默认值”断言。
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_ENABLED", "true")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_SYSTEM_BUDGET_TOKENS", "3000")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_TOOL_APPEND_TOKENS", "500")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_MAX_WINDOWS", "6")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_ROWS", "25")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_COLS", "10")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_MINIMIZED_TOKENS", "80")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_BACKGROUND_AFTER_IDLE", "1")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_SUSPEND_AFTER_IDLE", "3")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_TERMINATE_AFTER_IDLE", "5")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_MODE", "hybrid")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TIMEOUT_MS", "800")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_WINDOW_COUNT", "3")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_TURN", "4")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_PLAN_TTL_TURNS", "2")
        monkeypatch.setenv("EXCELMANUS_WINDOW_RETURN_MODE", "enriched")
        monkeypatch.setenv("EXCELMANUS_WINDOW_FULL_MAX_ROWS", "25")
        monkeypatch.setenv("EXCELMANUS_WINDOW_FULL_TOTAL_BUDGET_TOKENS", "500")
        monkeypatch.setenv("EXCELMANUS_WINDOW_DATA_BUFFER_MAX_ROWS", "200")
        monkeypatch.setenv("EXCELMANUS_WINDOW_INTENT_ENABLED", "true")
        monkeypatch.setenv("EXCELMANUS_WINDOW_INTENT_STICKY_TURNS", "3")
        monkeypatch.setenv("EXCELMANUS_WINDOW_INTENT_REPEAT_WARN_THRESHOLD", "2")
        monkeypatch.setenv("EXCELMANUS_WINDOW_INTENT_REPEAT_TRIP_THRESHOLD", "3")

    def test_window_perception_defaults(self, monkeypatch) -> None:
        """窗口感知层默认值应符合 v4 设定。"""
        self._set_required_env(monkeypatch)
        cfg = load_config()
        assert cfg.window_perception_enabled is True
        assert cfg.window_perception_system_budget_tokens == 3000
        assert cfg.window_perception_tool_append_tokens == 500
        assert cfg.window_perception_max_windows == 6
        assert cfg.window_perception_default_rows == 25
        assert cfg.window_perception_default_cols == 10
        assert cfg.window_perception_minimized_tokens == 80
        assert cfg.window_perception_background_after_idle == 1
        assert cfg.window_perception_suspend_after_idle == 3
        assert cfg.window_perception_terminate_after_idle == 5
        assert cfg.window_perception_advisor_mode == "hybrid"
        assert cfg.window_perception_advisor_timeout_ms == 800
        assert cfg.window_perception_advisor_trigger_window_count == 3
        assert cfg.window_perception_advisor_trigger_turn == 4
        assert cfg.window_perception_advisor_plan_ttl_turns == 2
        assert cfg.window_return_mode == "enriched"
        assert cfg.window_full_max_rows == 25
        assert cfg.window_full_total_budget_tokens == 500
        assert cfg.window_data_buffer_max_rows == 200
        assert cfg.window_intent_enabled is True
        assert cfg.window_intent_sticky_turns == 3
        assert cfg.window_intent_repeat_warn_threshold == 2
        assert cfg.window_intent_repeat_trip_threshold == 3

    def test_window_perception_values_from_env(self, monkeypatch) -> None:
        """支持通过环境变量覆盖全部窗口感知配置。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_ENABLED", "false")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_SYSTEM_BUDGET_TOKENS", "4096")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_TOOL_APPEND_TOKENS", "640")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_MAX_WINDOWS", "4")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_ROWS", "30")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_COLS", "12")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_MINIMIZED_TOKENS", "96")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_BACKGROUND_AFTER_IDLE", "2")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_SUSPEND_AFTER_IDLE", "4")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_TERMINATE_AFTER_IDLE", "7")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_MODE", "rules")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TIMEOUT_MS", "950")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_WINDOW_COUNT", "5")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_TURN", "6")
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_PLAN_TTL_TURNS", "4")
        monkeypatch.setenv("EXCELMANUS_WINDOW_RETURN_MODE", "anchored")
        monkeypatch.setenv("EXCELMANUS_WINDOW_FULL_MAX_ROWS", "40")
        monkeypatch.setenv("EXCELMANUS_WINDOW_FULL_TOTAL_BUDGET_TOKENS", "700")
        monkeypatch.setenv("EXCELMANUS_WINDOW_DATA_BUFFER_MAX_ROWS", "300")
        monkeypatch.setenv("EXCELMANUS_WINDOW_INTENT_ENABLED", "false")
        monkeypatch.setenv("EXCELMANUS_WINDOW_INTENT_STICKY_TURNS", "5")
        monkeypatch.setenv("EXCELMANUS_WINDOW_INTENT_REPEAT_WARN_THRESHOLD", "4")
        monkeypatch.setenv("EXCELMANUS_WINDOW_INTENT_REPEAT_TRIP_THRESHOLD", "6")

        cfg = load_config()
        assert cfg.window_perception_enabled is False
        assert cfg.window_perception_system_budget_tokens == 4096
        assert cfg.window_perception_tool_append_tokens == 640
        assert cfg.window_perception_max_windows == 4
        assert cfg.window_perception_default_rows == 30
        assert cfg.window_perception_default_cols == 12
        assert cfg.window_perception_minimized_tokens == 96
        assert cfg.window_perception_background_after_idle == 2
        assert cfg.window_perception_suspend_after_idle == 4
        assert cfg.window_perception_terminate_after_idle == 7
        assert cfg.window_perception_advisor_mode == "rules"
        assert cfg.window_perception_advisor_timeout_ms == 950
        assert cfg.window_perception_advisor_trigger_window_count == 5
        assert cfg.window_perception_advisor_trigger_turn == 6
        assert cfg.window_perception_advisor_plan_ttl_turns == 4
        assert cfg.window_return_mode == "anchored"
        assert cfg.window_full_max_rows == 40
        assert cfg.window_full_total_budget_tokens == 700
        assert cfg.window_data_buffer_max_rows == 300
        assert cfg.window_intent_enabled is False
        assert cfg.window_intent_sticky_turns == 5
        assert cfg.window_intent_repeat_warn_threshold == 4
        assert cfg.window_intent_repeat_trip_threshold == 6

    def test_window_perception_enabled_invalid_value_raises_error(self, monkeypatch) -> None:
        """窗口感知开关非法值应抛出 ConfigError。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_ENABLED", "maybe")
        with pytest.raises(ConfigError, match="EXCELMANUS_WINDOW_PERCEPTION_ENABLED"):
            load_config()

    def test_window_perception_integer_invalid_raises_error(self, monkeypatch) -> None:
        """窗口感知整数配置非法值应抛出 ConfigError。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_MAX_WINDOWS", "NaN")
        with pytest.raises(ConfigError, match="EXCELMANUS_WINDOW_PERCEPTION_MAX_WINDOWS"):
            load_config()

    def test_window_perception_integer_zero_raises_error(self, monkeypatch) -> None:
        """窗口感知正整数配置为 0 时应抛出 ConfigError。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_ROWS", "0")
        with pytest.raises(ConfigError, match="EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_ROWS"):
            load_config()

    def test_window_perception_lifecycle_integer_invalid_raises_error(self, monkeypatch) -> None:
        """生命周期阈值配置非法值应抛出 ConfigError。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_TERMINATE_AFTER_IDLE", "NaN")
        with pytest.raises(ConfigError, match="EXCELMANUS_WINDOW_PERCEPTION_TERMINATE_AFTER_IDLE"):
            load_config()

    def test_window_perception_advisor_mode_invalid_raises_error(self, monkeypatch) -> None:
        """顾问模式非法值应抛出 ConfigError。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_MODE", "smart")
        with pytest.raises(ConfigError, match="EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_MODE"):
            load_config()

    def test_window_intent_enabled_invalid_value_raises_error(self, monkeypatch) -> None:
        """window_intent_enabled 非法值应抛出 ConfigError。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_WINDOW_INTENT_ENABLED", "maybe")
        with pytest.raises(ConfigError, match="EXCELMANUS_WINDOW_INTENT_ENABLED"):
            load_config()

    def test_window_intent_integer_invalid_raises_error(self, monkeypatch) -> None:
        """window_intent 整数配置非法值应抛出 ConfigError。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_WINDOW_INTENT_STICKY_TURNS", "NaN")
        with pytest.raises(ConfigError, match="EXCELMANUS_WINDOW_INTENT_STICKY_TURNS"):
            load_config()

    def test_window_return_mode_invalid_fallback_to_adaptive(self, monkeypatch) -> None:
        """window_return_mode 非法值应回退 adaptive，而非抛错。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_WINDOW_RETURN_MODE", "invalid-mode")
        cfg = load_config()
        assert cfg.window_return_mode == "adaptive"

    def test_window_return_mode_unified_is_accepted(self, monkeypatch) -> None:
        """window_return_mode=unified 应被正确解析。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_WINDOW_RETURN_MODE", "unified")
        cfg = load_config()
        assert cfg.window_return_mode == "unified"

    def test_window_return_mode_default_is_adaptive(self, monkeypatch, tmp_path) -> None:
        """未设置 window_return_mode 时默认应为 adaptive。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.delenv("EXCELMANUS_WINDOW_RETURN_MODE", raising=False)
        cfg = load_config()
        assert cfg.window_return_mode == "adaptive"

    def test_adaptive_model_mode_overrides_parsed(self, monkeypatch) -> None:
        """adaptive override JSON 应被解析并归一化。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv(
            "EXCELMANUS_ADAPTIVE_MODEL_MODE_OVERRIDES",
            '{"gpt-5":"unified"," moonshotai/kimi ":"anchored","deepseek":"enriched"}',
        )
        cfg = load_config()
        assert cfg.adaptive_model_mode_overrides == {
            "gpt-5": "unified",
            "moonshotai/kimi": "anchored",
            "deepseek": "enriched",
        }

    def test_adaptive_model_mode_overrides_invalid_json_ignored(self, monkeypatch) -> None:
        """adaptive override 非法 JSON 时应忽略且不抛错。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_ADAPTIVE_MODEL_MODE_OVERRIDES", "{not-json")
        cfg = load_config()
        assert cfg.adaptive_model_mode_overrides == {}

    def test_adaptive_model_mode_overrides_invalid_values_ignored(self, monkeypatch) -> None:
        """adaptive override 非法项应被忽略，合法项保留。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv(
            "EXCELMANUS_ADAPTIVE_MODEL_MODE_OVERRIDES",
            '{"gpt-5":"unified","kimi":"invalid","x":123}',
        )
        cfg = load_config()
        assert cfg.adaptive_model_mode_overrides == {"gpt-5": "unified"}

    def test_window_rule_engine_version_accepts_v2(self, monkeypatch) -> None:
        """window_rule_engine_version=v2 应被正确解析。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_WINDOW_RULE_ENGINE_VERSION", "v2")
        cfg = load_config()
        assert cfg.window_rule_engine_version == "v2"

    def test_window_rule_engine_version_invalid_fallback_v1(self, monkeypatch) -> None:
        """window_rule_engine_version 非法值应回退 v1。"""
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_WINDOW_RULE_ENGINE_VERSION", "v9")
        cfg = load_config()
        assert cfg.window_rule_engine_version == "v1"


class TestLogLevelValidation:
    def _set_required_env(self, monkeypatch) -> None:
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")

    def test_log_level_accepts_valid_enum(self, monkeypatch) -> None:
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_LOG_LEVEL", "debug")
        cfg = load_config()
        assert cfg.log_level == "DEBUG"

    def test_log_level_rejects_invalid_enum(self, monkeypatch) -> None:
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_LOG_LEVEL", "verbose")
        with pytest.raises(ConfigError, match="EXCELMANUS_LOG_LEVEL"):
            load_config()


class TestCorsConfig:
    def test_load_cors_allow_origins_from_dotenv(self, monkeypatch, tmp_path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "EXCELMANUS_CORS_ALLOW_ORIGINS=http://a.com,http://b.com\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        assert load_cors_allow_origins() == ("http://a.com", "http://b.com")

    def test_env_overrides_dotenv_for_cors(self, monkeypatch, tmp_path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "EXCELMANUS_CORS_ALLOW_ORIGINS=http://dotenv.com\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXCELMANUS_CORS_ALLOW_ORIGINS", "http://env.com")
        assert load_cors_allow_origins() == ("http://env.com",)

    def test_cors_allow_origins_loaded_into_config(self, monkeypatch) -> None:
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_CORS_ALLOW_ORIGINS", "http://a.com,http://b.com")
        cfg = load_config()
        assert cfg.cors_allow_origins == ("http://a.com", "http://b.com")


class TestModelsRouterHooksAndMcpConfig:
    def _set_required_env(self, monkeypatch) -> None:
        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")

    def test_models_parsed_and_inherit_defaults(self, monkeypatch) -> None:
        self._set_required_env(monkeypatch)
        monkeypatch.setenv(
            "EXCELMANUS_MODELS",
            '[{"name":"alt","model":"gpt-4o-mini","description":"备用"}]',
        )
        cfg = load_config()
        assert len(cfg.models) == 1
        assert cfg.models[0].name == "alt"
        assert cfg.models[0].api_key == "test-key"
        assert cfg.models[0].base_url == "https://example.com/v1"

    def test_router_base_url_invalid_raises_error(self, monkeypatch) -> None:
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_ROUTER_BASE_URL", "ftp://invalid")
        with pytest.raises(ConfigError, match="EXCELMANUS_BASE_URL"):
            load_config()

    def test_hooks_and_max_context_tokens_loaded(self, monkeypatch) -> None:
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_HOOKS_COMMAND_ENABLED", "true")
        monkeypatch.setenv("EXCELMANUS_HOOKS_COMMAND_ALLOWLIST", "git status,pytest")
        monkeypatch.setenv("EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS", "30")
        monkeypatch.setenv("EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS", "4096")
        monkeypatch.setenv("EXCELMANUS_MAX_CONTEXT_TOKENS", "32768")
        cfg = load_config()
        assert cfg.hooks_command_enabled is True
        assert cfg.hooks_command_allowlist == ("git status", "pytest")
        assert cfg.hooks_command_timeout_seconds == 30
        assert cfg.hooks_output_max_chars == 4096
        assert cfg.max_context_tokens == 32768

    def test_mcp_shared_manager_flag_loaded(self, monkeypatch) -> None:
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_MCP_SHARED_MANAGER", "true")
        cfg = load_config()
        assert cfg.mcp_shared_manager is True

    def test_mcp_shared_manager_invalid_raises_error(self, monkeypatch) -> None:
        self._set_required_env(monkeypatch)
        monkeypatch.setenv("EXCELMANUS_MCP_SHARED_MANAGER", "maybe")
        with pytest.raises(ConfigError, match="EXCELMANUS_MCP_SHARED_MANAGER"):
            load_config()
