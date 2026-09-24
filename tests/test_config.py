"""配置管理模块测试：Property 16、17 + 单元测试。

覆盖需求：6.1, 6.2, 6.3, 6.4, 6.5, 6.7, 6.8
"""
from __future__ import annotations
import os
import pytest
from hypothesis import given
from hypothesis import strategies as st
from excelmanus.config import (
    ConfigError,
    _normalize_base_url,
    _parse_cors_allow_origins,
    _parse_protocol,
    expand_cors_origins,
    format_deprecated_model_message,
    get_deprecated_model_replacement,
    load_config,
    load_cors_allow_origins,
    load_runtime_env,
    parse_frontend_ports,
)

_api_key_st = st.text(alphabet=st.characters(whitelist_categories=('L', 'N'), whitelist_characters='-_'), min_size=1, max_size=64).filter(lambda s: s.strip())
_valid_url_st = st.sampled_from(['https://api.openai.com/v1', 'http://localhost:8080', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'https://example.com/api', 'http://192.168.1.1:3000/v2'])
_model_st = st.text(alphabet=st.characters(whitelist_categories=('L', 'N'), whitelist_characters='-_.'), min_size=1, max_size=32).filter(lambda s: s.strip())
_pos_int_st = st.integers(min_value=1, max_value=100000)

_REQUIRED = {
    "EXCELMANUS_API_KEY": "test-key",
    "EXCELMANUS_BASE_URL": "https://example.com/v1",
    "EXCELMANUS_MODEL": "test-model",
}


def _load(**extra: str):
    values = dict(_REQUIRED)
    values.update(extra)
    return load_config(values=values)


@given(api_key=_api_key_st, base_url=_valid_url_st, model=_model_st, max_iter=_pos_int_st, max_fail=_pos_int_st, ttl=_pos_int_st, max_sess=_pos_int_st)
def test_property16_env_vars_reflected(api_key: str, base_url: str, model: str, max_iter: int, max_fail: int, ttl: int, max_sess: int) -> None:
    """Property 16：设置值必须在 load_config() 输出中精确反映。

    **验证：需求 6.1**
    """
    env_vars = {'EXCELMANUS_API_KEY': api_key, 'EXCELMANUS_BASE_URL': base_url, 'EXCELMANUS_MODEL': model, 'EXCELMANUS_MAX_ITERATIONS': str(max_iter), 'EXCELMANUS_MAX_CONSECUTIVE_FAILURES': str(max_fail), 'EXCELMANUS_SESSION_TTL_SECONDS': str(ttl), 'EXCELMANUS_MAX_SESSIONS': str(max_sess)}
    cfg = load_config(values=env_vars)
    expected_base = _normalize_base_url(
        base_url,
        protocol=_parse_protocol(env_vars.get("EXCELMANUS_PROTOCOL"), "EXCELMANUS_PROTOCOL"),
        env_name="EXCELMANUS_BASE_URL",
        model=model,
        api_key=api_key,
    )
    assert cfg.api_key == api_key
    assert cfg.base_url == expected_base
    assert cfg.model == model
    assert cfg.max_iterations == max_iter
    assert cfg.max_consecutive_failures == max_fail
    assert cfg.session_ttl_seconds == ttl
    assert cfg.max_sessions == max_sess
_invalid_url_st = st.sampled_from(['ftp://example.com', 'not-a-url', '://missing-scheme', '', 'file:///etc/passwd', 'javascript:alert(1)', 'htp://typo.com', 'httpx://wrong.com', 'just-text', '  '])

@given(invalid_url=_invalid_url_st)
def test_property17_invalid_base_url_rejected(invalid_url: str) -> None:
    """Property 17：非法 URL 必须被拒绝。

    **验证：需求 6.5**
    """
    with pytest.raises(ConfigError):
        load_config(values={
            'EXCELMANUS_API_KEY': 'test-key',
            'EXCELMANUS_BASE_URL': invalid_url,
            'EXCELMANUS_MODEL': 'test-model',
        })

@given(valid_url=_valid_url_st)
def test_property17_valid_base_url_accepted(valid_url: str) -> None:
    """Property 17：合法 HTTP/HTTPS URL 必须被接受。

    **验证：需求 6.5**
    """
    cfg = load_config(values={
        'EXCELMANUS_API_KEY': 'test-key',
        'EXCELMANUS_BASE_URL': valid_url,
        'EXCELMANUS_MODEL': 'test-model',
    })
    expected_base = _normalize_base_url(
        valid_url,
        protocol=_parse_protocol(None, "EXCELMANUS_PROTOCOL"),
        env_name="EXCELMANUS_BASE_URL",
        model='test-model',
        api_key='test-key',
    )
    assert cfg.base_url == expected_base

class TestMissingConfig:
    """测试缺失必填配置项的行为。"""

    def test_missing_api_key_raises_config_error(self, monkeypatch, tmp_path) -> None:
        """缺少 API Key 时必须抛出 ConfigError。（需求 6.3）"""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigError, match='EXCELMANUS_API_KEY'):
            load_config()

    def test_error_message_mentions_variable_name(self, monkeypatch, tmp_path) -> None:
        """错误信息必须指明需要设置的变量名。（需求 6.3）"""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigError) as exc_info:
            load_config()
        assert 'EXCELMANUS_API_KEY' in str(exc_info.value)

class TestDefaultValues:
    """测试默认值是否正确。"""

    def test_missing_base_url_raises_config_error(self, monkeypatch, tmp_path) -> None:
        """缺少 BASE_URL 时必须抛出 ConfigError。"""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigError, match='EXCELMANUS_BASE_URL'):
            load_config(values={'EXCELMANUS_API_KEY': 'test-key', 'EXCELMANUS_MODEL': 'test-model'})

    def test_missing_model_raises_config_error(self, monkeypatch, tmp_path) -> None:
        """缺少 MODEL 时必须抛出 ConfigError。"""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigError, match='EXCELMANUS_MODEL'):
            load_config(values={'EXCELMANUS_API_KEY': 'test-key', 'EXCELMANUS_BASE_URL': 'https://example.com/v1'})

    def test_default_max_iterations(self, monkeypatch) -> None:
        """默认不限制迭代次数。"""
        cfg = _load()
        assert cfg.max_iterations == 0

    def test_default_yellow_auto_approve_is_false(self, monkeypatch, tmp_path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv('EXCELMANUS_CODE_POLICY_YELLOW_AUTO', raising=False)
        cfg = _load()
        assert cfg.code_policy_yellow_auto_approve is False

    def test_default_max_consecutive_failures(self, monkeypatch) -> None:
        """默认最大连续失败次数为 6。（需求 6.6）"""
        cfg = _load()
        assert cfg.max_consecutive_failures == 6

    def test_default_session_ttl(self, monkeypatch) -> None:
        """默认会话 TTL 为 1800 秒。（需求 6.7）"""
        cfg = _load()
        assert cfg.session_ttl_seconds == 1800

    def test_default_max_sessions(self, monkeypatch) -> None:
        """默认最大会话数为 1000。（需求 6.7）"""
        cfg = _load()
        assert cfg.max_sessions == 1000

    def test_default_skills_context_char_budget(self, monkeypatch) -> None:
        """默认技能正文字符预算为 12000。"""
        cfg = _load()
        assert cfg.skills_context_char_budget == 12000

    def test_turn_budget_config(self, monkeypatch) -> None:
        cfg = _load(
            EXCELMANUS_TURN_TOKEN_BUDGET="1200",
            EXCELMANUS_TURN_COST_BUDGET_USD="0.25",
            EXCELMANUS_INPUT_COST_PER_1K_USD="0.01",
            EXCELMANUS_OUTPUT_COST_PER_1K_USD="0.02",
        )
        assert cfg.turn_token_budget == 1200
        assert cfg.turn_cost_budget_usd == 0.25
        assert cfg.input_cost_per_1k_usd == 0.01
        assert cfg.output_cost_per_1k_usd == 0.02

    def test_responses_continuation_config(self) -> None:
        assert _load(EXCELMANUS_RESPONSES_CONTINUATION_ENABLED="true").responses_continuation_enabled is True

    def test_responses_background_config(self) -> None:
        assert _load(EXCELMANUS_RESPONSES_BACKGROUND_ENABLED="true").responses_background_enabled is True

    def test_parallel_tool_concurrency_config(self) -> None:
        assert _load().parallel_tool_max == 4
        assert _load(EXCELMANUS_PARALLEL_TOOL_MAX="1").parallel_tool_max == 1
        assert _load(EXCELMANUS_PARALLEL_TOOL_MAX="32").parallel_tool_max == 32
        for value in ("0", "-1", "33", "invalid"):
            with pytest.raises(ConfigError):
                _load(EXCELMANUS_PARALLEL_TOOL_MAX=value)

    def test_skills_context_char_budget_zero_allowed(self, monkeypatch) -> None:
        """技能正文字符预算允许设为 0（表示不限制）。"""
        cfg = _load(EXCELMANUS_SKILLS_CONTEXT_CHAR_BUDGET='0')
        assert cfg.skills_context_char_budget == 0

    def test_default_workspace_root(self, monkeypatch) -> None:
        """默认工作区落在集中数据目录，不暴露启动目录的源码。"""
        from excelmanus.data_home import get_data_home
        cfg = _load()
        assert cfg.workspace_root == str(get_data_home())

    def test_default_tool_result_hard_cap_chars(self, monkeypatch) -> None:
        """默认工具结果全局硬截断上限为 12000。"""
        cfg = _load()
        assert cfg.tool_result_hard_cap_chars == 12000

    def test_jev_experimental_frontend_gate_defaults_off_and_reads_store_value(self) -> None:
        assert _load().jev_experimental_enabled is False
        assert _load(EXCELMANUS_JEV_EXPERIMENTAL_ENABLED="true").jev_experimental_enabled is True

    def test_tool_result_hard_cap_chars_from_env(self, monkeypatch) -> None:
        """允许覆盖工具结果全局硬截断上限。"""
        cfg = _load(EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS='2048')
        assert cfg.tool_result_hard_cap_chars == 2048

    def test_thinking_effort_options_are_filtered_and_ordered(self) -> None:
        cfg = _load(EXCELMANUS_THINKING_EFFORT_OPTIONS="max,low,unknown,low")
        assert cfg.thinking_effort_options == ("low", "max")

    def test_empty_thinking_effort_options_fall_back_to_all(self) -> None:
        cfg = _load(EXCELMANUS_THINKING_EFFORT_OPTIONS="unknown")
        assert cfg.thinking_effort_options == (
            "none", "minimal", "low", "medium", "high", "xhigh", "max",
        )

    def test_legacy_system_message_mode_env_is_ignored(self, monkeypatch) -> None:
        """旧 EXCELMANUS_SYSTEM_MESSAGE_MODE 配置已移除，不得再进入配置对象。"""
        cfg = _load(EXCELMANUS_SYSTEM_MESSAGE_MODE='merge')
        assert not hasattr(cfg, 'system_message_mode')

    def test_default_subagent_config(self, monkeypatch) -> None:
        """subagent 配置默认值。"""
        cfg = _load()
        assert cfg.subagent_enabled is True
        assert cfg.subagent_max_iterations == 0
        assert cfg.subagent_max_consecutive_failures == 6
        assert cfg.subagent_user_dir == '~/.excelmanus/agents'
        assert cfg.subagent_project_dir == os.path.join(cfg.workspace_root, '.excelmanus', 'agents')

    def test_subagent_config_from_env(self, monkeypatch) -> None:
        """支持覆盖 subagent 配置。"""
        cfg = _load(EXCELMANUS_SUBAGENT_ENABLED='false', EXCELMANUS_SUBAGENT_MAX_ITERATIONS='4', EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES='1', EXCELMANUS_SUBAGENT_USER_DIR='~/.my-agents', EXCELMANUS_SUBAGENT_PROJECT_DIR='.my-agents')
        assert cfg.subagent_enabled is False
        assert cfg.subagent_max_iterations == 4
        assert cfg.subagent_max_consecutive_failures == 1
        assert cfg.subagent_user_dir == '~/.my-agents'
        assert cfg.subagent_project_dir == '.my-agents'

    def test_legacy_subagent_model_env_is_ignored(self, monkeypatch) -> None:
        """旧变量 EXCELMANUS_SUBAGENT_MODEL 不再生效。"""
        cfg = _load(EXCELMANUS_SUBAGENT_MODEL='legacy-subagent')
        assert not hasattr(cfg, 'aux_model')

    def test_legacy_window_advisor_model_env_is_ignored(self, monkeypatch) -> None:
        """旧变量 EXCELMANUS_WINDOW_ADVISOR_MODEL 不再生效。"""
        cfg = _load(EXCELMANUS_WINDOW_ADVISOR_MODEL='legacy-window-advisor')
        assert not hasattr(cfg, 'aux_model')

    def test_config_is_frozen(self, monkeypatch) -> None:
        """配置对象不可变。"""
        cfg = _load()
        with pytest.raises(AttributeError):
            setattr(cfg, 'api_key', 'new-key')

class TestDotEnvIgnored:
    """磁盘上的 dotenv 文件不是设置源。"""

    def test_dotenv_settings_are_ignored(self, monkeypatch, tmp_path) -> None:
        env_file = tmp_path / '.env'
        env_file.write_text(
            'EXCELMANUS_API_KEY=from-dotenv\n'
            'EXCELMANUS_BASE_URL=https://example.com/v1\n'
            'EXCELMANUS_MODEL=test-model\n'
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv('EXCELMANUS_API_KEY', raising=False)
        monkeypatch.delenv('EXCELMANUS_BASE_URL', raising=False)
        monkeypatch.delenv('EXCELMANUS_MODEL', raising=False)
        with pytest.raises(ConfigError, match='EXCELMANUS_API_KEY'):
            load_config()

    def test_dotenv_does_not_set_home(self, monkeypatch, tmp_path) -> None:
        home = tmp_path / 'home'
        env_file = tmp_path / '.env'
        env_file.write_text(f'EXCELMANUS_HOME={home}\n')
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv('EXCELMANUS_HOME', raising=False)
        load_runtime_env()
        from excelmanus.data_home import get_excelmanus_home
        assert get_excelmanus_home() != home.resolve()

class TestIntegerParsing:
    """整数配置项解析测试。"""

    def test_invalid_integer_raises_error(self, monkeypatch) -> None:
        """非整数值应抛出 ConfigError。"""
        with pytest.raises(ConfigError, match='整数'):
            _load(EXCELMANUS_MAX_ITERATIONS='not-a-number')

    def test_zero_iteration_limits_are_unlimited(self, monkeypatch) -> None:
        cfg = _load(EXCELMANUS_MAX_ITERATIONS='0', EXCELMANUS_SUBAGENT_MAX_ITERATIONS='0')
        assert cfg.max_iterations == 0
        assert cfg.subagent_max_iterations == 0

    def test_negative_integer_raises_error(self, monkeypatch) -> None:
        """负值应抛出 ConfigError。"""
        with pytest.raises(ConfigError, match='正整数'):
            _load(EXCELMANUS_MAX_SESSIONS='-5')

    def test_turn_timeout_allows_zero_and_positive_values(self) -> None:
        assert _load(EXCELMANUS_TURN_TIMEOUT_SECONDS="0").turn_timeout_seconds == 0
        assert _load(EXCELMANUS_TURN_TIMEOUT_SECONDS="5").turn_timeout_seconds == 5

    def test_turn_timeout_rejects_negative_values(self) -> None:
        with pytest.raises(ConfigError, match="非负整数"):
            _load(EXCELMANUS_TURN_TIMEOUT_SECONDS="-1")

class TestWorkspaceRoot:
    """工作目录白名单配置测试。"""

    def test_workspace_root_from_env(self, monkeypatch, tmp_path) -> None:
        """EXCELMANUS_WORKSPACE_ROOT 应被正确读取。（需求 6.8）"""
        cfg = _load(EXCELMANUS_WORKSPACE_ROOT=str(tmp_path))
        assert cfg.workspace_root == str(tmp_path)

class TestMemoryConfig:
    """跨会话持久记忆配置项测试。（需求 8.1, 8.2, 8.3）"""


    def test_default_memory_enabled(self, monkeypatch) -> None:
        """memory_enabled 默认值为 True。（需求 8.1）"""
        cfg = _load()
        assert cfg.memory_enabled is True

    def test_memory_enabled_false(self, monkeypatch) -> None:
        """可关闭 memory_enabled。（需求 8.1）"""
        cfg = _load(EXCELMANUS_MEMORY_ENABLED='false')
        assert cfg.memory_enabled is False

    def test_memory_enabled_true_explicit(self, monkeypatch) -> None:
        """可显式开启 memory_enabled。（需求 8.1）"""
        cfg = _load(EXCELMANUS_MEMORY_ENABLED='true')
        assert cfg.memory_enabled is True

    def test_memory_enabled_invalid_raises_error(self, monkeypatch) -> None:
        """memory_enabled 非法值应抛出 ConfigError。"""
        with pytest.raises(ConfigError, match='布尔值'):
            _load(EXCELMANUS_MEMORY_ENABLED='maybe')

    def test_default_memory_dir(self, monkeypatch) -> None:
        """memory_dir 默认值为 ~/.excelmanus/memory。（需求 8.2）"""
        cfg = _load()
        assert cfg.memory_dir == '~/.excelmanus/memory'

    def test_memory_dir_from_env(self, monkeypatch, tmp_path) -> None:
        """可自定义 memory_dir。（需求 8.2）"""
        custom_dir = str(tmp_path / 'custom_memory')
        cfg = _load(EXCELMANUS_MEMORY_DIR=custom_dir)
        assert cfg.memory_dir == custom_dir

    def test_default_memory_auto_load_lines(self, monkeypatch) -> None:
        """memory_auto_load_lines 默认值为 200。（需求 8.3）"""
        cfg = _load()
        assert cfg.memory_auto_load_lines == 200

    def test_memory_auto_load_lines_from_env(self, monkeypatch) -> None:
        """可自定义 memory_auto_load_lines。（需求 8.3）"""
        cfg = _load(EXCELMANUS_MEMORY_AUTO_LOAD_LINES='500')
        assert cfg.memory_auto_load_lines == 500

    def test_memory_auto_load_lines_invalid_raises_error(self, monkeypatch) -> None:
        """memory_auto_load_lines 非法值应抛出 ConfigError。"""
        with pytest.raises(ConfigError, match='整数'):
            _load(EXCELMANUS_MEMORY_AUTO_LOAD_LINES='abc')

    def test_memory_auto_load_lines_zero_raises_error(self, monkeypatch) -> None:
        """memory_auto_load_lines 为 0 应抛出 ConfigError（要求正整数）。"""
        with pytest.raises(ConfigError, match='正整数'):
            _load(EXCELMANUS_MEMORY_AUTO_LOAD_LINES='0')

    def test_runtime_env_defaults(self, monkeypatch) -> None:
        """设置页会写回的运行时字段，冷启动必须读到 dataclass 默认值。"""
        cfg = _load()
        assert cfg.memory_expire_days == 90
        assert cfg.memory_maintenance_enabled is False
        assert cfg.memory_maintenance_min_entries == 10
        assert cfg.memory_maintenance_new_threshold == 5
        assert cfg.memory_maintenance_interval_hours == 4.0
        assert cfg.memory_maintenance_model is None
        assert cfg.llm_retry_max_attempts == 3
        assert cfg.llm_retry_base_delay_seconds == 2.0
        assert cfg.llm_retry_max_delay_seconds == 30.0
        assert cfg.image_pixel_budget == 640000
        assert cfg.image_max_bytes == 1048576
        assert cfg.image_files_api == "auto"
        assert cfg.friendly_error_messages is True

    def test_runtime_env_from_env(self, monkeypatch) -> None:
        """设置页写入的值冷启动后必须生效。"""
        cfg = _load(EXCELMANUS_MEMORY_EXPIRE_DAYS="0", EXCELMANUS_MEMORY_MAINTENANCE_ENABLED="true", EXCELMANUS_MEMORY_MAINTENANCE_MIN_ENTRIES="12", EXCELMANUS_MEMORY_MAINTENANCE_NEW_THRESHOLD="8", EXCELMANUS_MEMORY_MAINTENANCE_INTERVAL_HOURS="6.5", EXCELMANUS_MEMORY_MAINTENANCE_MODEL="gpt-test", EXCELMANUS_LLM_RETRY_MAX_ATTEMPTS="5", EXCELMANUS_LLM_RETRY_BASE_DELAY_SECONDS="1.5", EXCELMANUS_LLM_RETRY_MAX_DELAY_SECONDS="12", EXCELMANUS_IMAGE_PIXEL_BUDGET="low", EXCELMANUS_IMAGE_MAX_BYTES="512000", EXCELMANUS_IMAGE_FILES_API="true", EXCELMANUS_FRIENDLY_ERROR_MESSAGES="false")
        assert cfg.memory_expire_days == 0
        assert cfg.memory_maintenance_enabled is True
        assert cfg.memory_maintenance_min_entries == 12
        assert cfg.memory_maintenance_new_threshold == 8
        assert cfg.memory_maintenance_interval_hours == 6.5
        assert cfg.memory_maintenance_model == "gpt-test"
        assert cfg.llm_retry_max_attempts == 5
        assert cfg.llm_retry_base_delay_seconds == 1.5
        assert cfg.llm_retry_max_delay_seconds == 12.0
        assert cfg.image_pixel_budget == "low"
        assert cfg.image_max_bytes == 512000
        assert cfg.image_files_api == "true"
        assert cfg.friendly_error_messages is False

class TestLogLevelValidation:


    def test_log_level_accepts_valid_enum(self, monkeypatch) -> None:
        cfg = _load(EXCELMANUS_LOG_LEVEL='debug')
        assert cfg.log_level == 'DEBUG'

    def test_log_level_rejects_invalid_enum(self, monkeypatch) -> None:
        with pytest.raises(ConfigError, match='EXCELMANUS_LOG_LEVEL'):
            _load(EXCELMANUS_LOG_LEVEL='verbose')

class TestCorsConfig:

    def test_dotenv_does_not_set_cors(self, monkeypatch, tmp_path) -> None:
        env_file = tmp_path / '.env'
        env_file.write_text('EXCELMANUS_CORS_ALLOW_ORIGINS=http://a.com,http://b.com\n', encoding='utf-8')
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv('EXCELMANUS_CORS_ALLOW_ORIGINS', raising=False)
        assert load_cors_allow_origins() == ('http://localhost:3000', 'http://127.0.0.1:3000')

    def test_cors_allow_origins_loaded_into_config(self, monkeypatch) -> None:
        cfg = _load(EXCELMANUS_CORS_ALLOW_ORIGINS='http://a.com,http://b.com')
        assert cfg.cors_allow_origins == ('http://a.com', 'http://b.com')

    def test_default_cors_includes_127(self, monkeypatch) -> None:
        monkeypatch.delenv('EXCELMANUS_CORS_ALLOW_ORIGINS', raising=False)
        origins = _parse_cors_allow_origins()
        assert 'http://localhost:3000' in origins
        assert 'http://127.0.0.1:3000' in origins

    def test_parse_frontend_ports_defaults_and_csv(self) -> None:
        assert parse_frontend_ports(None) == ('3000',)
        assert parse_frontend_ports('') == ('3000',)
        assert parse_frontend_ports('3001, 4173') == ('3001', '4173')

    def test_expand_cors_origins_always_adds_loopback(self) -> None:
        origins = expand_cors_origins(('http://a.example',), frontend_ports=('3000',))
        assert origins == [
            'http://127.0.0.1:3000',
            'http://[::1]:3000',
            'http://a.example',
            'http://localhost:3000',
        ]

    def test_expand_cors_origins_skips_loopback_extra_hosts(self) -> None:
        origins = expand_cors_origins(
            (),
            frontend_ports=('3000',),
            extra_hosts=('127.0.0.1', '192.168.1.10', 'localhost'),
        )
        assert 'http://192.168.1.10:3000' in origins
        assert origins.count('http://127.0.0.1:3000') == 1

class TestModelsRouterHooksAndMcpConfig:


    def test_models_come_from_store_not_settings_json(self, monkeypatch) -> None:
        cfg = _load()
        assert cfg.models == ()

    def test_hooks_and_max_context_tokens_loaded(self, monkeypatch) -> None:
        cfg = _load(EXCELMANUS_HOOKS_COMMAND_ENABLED='true', EXCELMANUS_HOOKS_COMMAND_ALLOWLIST='git status,pytest', EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS='30', EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS='4096', EXCELMANUS_MAX_CONTEXT_TOKENS='32768')
        assert cfg.hooks_command_enabled is True
        assert cfg.hooks_command_allowlist == ('git status', 'pytest')
        assert cfg.hooks_command_timeout_seconds == 30
        assert cfg.hooks_output_max_chars == 4096
        assert cfg.max_context_tokens == 32768

    def test_context_optimization_flags_loaded(self, monkeypatch) -> None:
        """上下文优化相关配置应由设置源完整驱动。"""
        cfg = _load(EXCELMANUS_PROMPT_CACHE_KEY_ENABLED='false', EXCELMANUS_COMPACTION_ENABLED='false', EXCELMANUS_COMPACTION_THRESHOLD_RATIO='0.9', EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS='6', EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS='2000')
        assert cfg.prompt_cache_key_enabled is False
        assert cfg.compaction_enabled is False
        assert cfg.compaction_threshold_ratio == pytest.approx(0.9)
        assert cfg.compaction_keep_recent_turns == 6
        assert cfg.compaction_max_summary_tokens == 2000

    def test_context_optimization_invalid_values_raise_error(self, monkeypatch) -> None:
        with pytest.raises(ConfigError, match='EXCELMANUS_PROMPT_CACHE_KEY_ENABLED'):
            _load(EXCELMANUS_PROMPT_CACHE_KEY_ENABLED='maybe')
        with pytest.raises(ConfigError, match='EXCELMANUS_COMPACTION_THRESHOLD_RATIO'):
            _load(EXCELMANUS_PROMPT_CACHE_KEY_ENABLED='true', EXCELMANUS_COMPACTION_THRESHOLD_RATIO='1.5')

    def test_mcp_shared_manager_flag_loaded(self, monkeypatch) -> None:
        cfg = _load(EXCELMANUS_MCP_SHARED_MANAGER='true')
        assert cfg.mcp_shared_manager is True

    def test_mcp_shared_manager_invalid_raises_error(self, monkeypatch) -> None:
        with pytest.raises(ConfigError, match='EXCELMANUS_MCP_SHARED_MANAGER'):
            _load(EXCELMANUS_MCP_SHARED_MANAGER='maybe')

class TestContextWindowInference:

    def test_infers_mainstream_model_context_window(self, monkeypatch, tmp_path) -> None:
        monkeypatch.chdir(tmp_path)
        model_to_expected_tokens = {'gpt-5': 400000, 'gpt-5-chat-latest': 128000, 'gpt-5-codex-mini': 400000, 'gpt-5.2-codex': 400000, 'gpt-5.3-codex': 400000, 'gpt-5.3-codex-spark': 128000, 'gpt 5.3 codex': 400000, 'gpt_5_1_codex_max': 400000, 'openai/gpt-5.2-codex': 400000, 'o4': 200000, 'claude-opus-4.1': 200000, 'claude-sonnet-4.6': 200000, 'gemini-2.5-flash-lite': 1048576, 'gemini-live-2.5-flash-preview': 1048576, 'qwen-plus': 1000000, 'qwen3.5-plus': 1000000, 'qwen-flash': 1000000, 'qwen3-coder-plus': 1000000, 'qwen-coder-plus': 131072, 'qwen-long-latest': 10000000, 'qwq-plus': 131072, 'qvq-72b-preview': 32768, 'qwen-vl-ocr': 38192, 'qwen2.5-omni-7b': 32768, 'kimi-k2-turbo-preview': 262144, 'moonshot-kimi-k2-instruct-v1': 131072, 'moonshot-kimi-k2.5': 262144, 'deepseek-v3.2-exp': 131072, 'mistral-large-2512': 256000, 'mistral-medium-2508': 128000, 'ministral-8b-2512': 256000, 'mistral/mistral-large-2512': 256000, 'devstral-2512': 256000, 'labs-devstral-small-2512': 256000, 'magistral-small-2509': 128000, 'magistral-small-2507': 40000, 'voxtral-small-2507': 32000, 'labs-mistral-small-creative': 32000, 'jamba-mini': 256000, 'jamba-3b': 256000, 'amazon.nova-pro-v1:0': 300000, 'us.amazon.nova-premier-v1:0': 1000000, 'eu.amazon.nova-pro-v1:0': 300000, 'apac.amazon.nova-lite-v1:0': 300000, 'amazon.nova-sonic-v1:0': 300000, 'amazon.nova-2-sonic': 1000000, 'minimax-m2.5-highspeed': 204800, 'minimax-m2.1-lightning': 204800, 'm2-her': 64000, 'command-a-reasoning-08-2025': 256000, 'grok-4-fast-reasoning': 2000000, 'xai.grok-4-1-fast-reasoning': 2000000, 'xai.grok-code-fast-1': 256000, 'llama-4-scout-17b-16e-instruct': 10000000}
        for model, expected_tokens in model_to_expected_tokens.items():
            cfg = _load(EXCELMANUS_MODEL=model)
            assert cfg.max_context_tokens == expected_tokens

    def test_deprecated_models_fall_back_to_default_context(self, monkeypatch, tmp_path) -> None:
        monkeypatch.chdir(tmp_path)
        for model in ('gemini-2.0-flash', 'claude-3.5-sonnet', 'codex-mini-latest'):
            cfg = _load(EXCELMANUS_MODEL=model)
            assert cfg.max_context_tokens == 256000

    @pytest.mark.parametrize(('model', 'expected'), [('gemini-2.0-flash', ('gemini-2.0-flash', 'gemini-3.8-flash')), ('claude-3-5-sonnet', ('claude-3-5-sonnet', 'claude-sonnet-5')), ('openai-codex/codex-mini-latest', ('codex-mini-latest', 'gpt-6-luna')), ('mimo-v2-flash', ('mimo-v2-flash', 'mimo-v2.6-flash')), ('gpt-5', None)])
    def test_deprecated_model_replacement_lookup(self, model: str, expected) -> None:
        assert get_deprecated_model_replacement(model) == expected

    def test_deprecated_model_message_includes_replacement(self) -> None:
        message = format_deprecated_model_message('gemini-2.0-flash')
        assert message is not None
        assert 'gemini-3.8-flash' in message
