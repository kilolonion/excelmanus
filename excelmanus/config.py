"""配置管理模块：加载环境变量、.env 文件和默认值。"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    """配置缺失或校验失败时抛出的异常。"""


@dataclass(frozen=True)
class ModelProfile:
    """单个模型配置档案。"""

    name: str  # 用户可见的短名称，如 "gpt4", "qwen", "kimi"
    model: str  # 实际模型标识符
    api_key: str
    base_url: str
    description: str = ""  # 可选描述


# Base URL 合法性正则：仅接受 http:// 或 https:// 开头的 URL
_URL_PATTERN = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)
_ALLOWED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_ALLOWED_WINDOW_RETURN_MODES = {"unified", "anchored", "enriched", "adaptive"}
_ALLOWED_WINDOW_RULE_ENGINE_VERSIONS = {"v1", "v2"}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExcelManusConfig:
    """不可变的全局配置对象。"""

    api_key: str
    base_url: str
    model: str
    max_iterations: int = 20
    max_consecutive_failures: int = 3
    session_ttl_seconds: int = 1800
    max_sessions: int = 1000
    workspace_root: str = "."
    log_level: str = "INFO"
    skills_system_dir: str = "excelmanus/skillpacks/system"
    skills_user_dir: str = "~/.excelmanus/skillpacks"
    skills_project_dir: str = ".excelmanus/skillpacks"
    skills_context_char_budget: int = 12000  # 技能正文字符预算，0 表示不限制
    skills_discovery_enabled: bool = True
    skills_discovery_scan_workspace_ancestors: bool = True
    skills_discovery_include_agents: bool = True
    skills_discovery_include_claude: bool = True
    skills_discovery_include_openclaw: bool = True
    skills_discovery_extra_dirs: tuple[str, ...] = ()
    system_message_mode: str = "auto"
    tool_result_hard_cap_chars: int = 12000
    large_excel_threshold_bytes: int = 8 * 1024 * 1024
    external_safe_mode: bool = True
    cors_allow_origins: tuple[str, ...] = ("http://localhost:5173",)
    mcp_shared_manager: bool = False
    # 路由子代理配置（可选，默认回退到主模型）
    router_api_key: str | None = None
    router_base_url: str | None = None
    router_model: str | None = None
    # subagent 执行配置
    subagent_enabled: bool = True
    subagent_model: str | None = None
    subagent_max_iterations: int = 120
    subagent_max_consecutive_failures: int = 2
    subagent_user_dir: str = "~/.excelmanus/agents"
    subagent_project_dir: str = ".excelmanus/agents"
    # 跨会话持久记忆配置
    memory_enabled: bool = True
    memory_dir: str = "~/.excelmanus/memory"
    memory_auto_load_lines: int = 200
    # 对话记忆上下文窗口大小（token 数），用于截断策略
    max_context_tokens: int = 128_000
    # hooks 配置
    hooks_command_enabled: bool = False
    hooks_command_allowlist: tuple[str, ...] = ()
    hooks_command_timeout_seconds: int = 10
    hooks_output_max_chars: int = 32000
    # 窗口感知层配置（v4）
    window_perception_enabled: bool = True
    window_perception_system_budget_tokens: int = 3000
    window_perception_tool_append_tokens: int = 500
    window_perception_max_windows: int = 6
    window_perception_default_rows: int = 25
    window_perception_default_cols: int = 10
    window_perception_minimized_tokens: int = 80
    window_perception_background_after_idle: int = 1
    window_perception_suspend_after_idle: int = 3
    window_perception_terminate_after_idle: int = 5
    window_perception_advisor_mode: str = "hybrid"
    window_perception_advisor_timeout_ms: int = 800
    window_perception_advisor_trigger_window_count: int = 3
    window_perception_advisor_trigger_turn: int = 4
    window_perception_advisor_plan_ttl_turns: int = 2
    window_return_mode: str = "adaptive"
    adaptive_model_mode_overrides: dict[str, str] = field(default_factory=dict)
    window_full_max_rows: int = 25
    window_full_total_budget_tokens: int = 500
    window_data_buffer_max_rows: int = 200
    window_intent_enabled: bool = True
    window_intent_sticky_turns: int = 3
    window_intent_repeat_warn_threshold: int = 2
    window_intent_repeat_trip_threshold: int = 3
    window_rule_engine_version: str = "v1"
    # 多模型配置档案（可选，通过 /model 命令切换）
    models: tuple[ModelProfile, ...] = ()


def load_runtime_env() -> None:
    """加载当前工作目录 .env（不覆盖已存在环境变量）。"""
    dotenv_path = Path.cwd() / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)


def _parse_int(value: str | None, name: str, default: int) -> int:
    """将字符串解析为正整数，无效时抛出 ConfigError。"""
    if value is None:
        return default
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise ConfigError(f"配置项 {name} 必须为整数，当前值: {value!r}")
    if result <= 0:
        raise ConfigError(f"配置项 {name} 必须为正整数，当前值: {result}")
    return result


def _parse_int_allow_zero(value: str | None, name: str, default: int) -> int:
    """将字符串解析为非负整数，0 表示不限制。"""
    if value is None:
        return default
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise ConfigError(f"配置项 {name} 必须为整数，当前值: {value!r}")
    if result < 0:
        raise ConfigError(f"配置项 {name} 必须为非负整数，当前值: {result}")
    return result


def _validate_base_url(url: str) -> None:
    """校验 Base URL 为合法的 HTTP/HTTPS URL。"""
    if not _URL_PATTERN.match(url):
        raise ConfigError(
            f"EXCELMANUS_BASE_URL 必须为合法的 HTTP/HTTPS URL，当前值: {url!r}"
        )


def _parse_bool(value: str | None, name: str, default: bool) -> bool:
    """将字符串解析为布尔值。"""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"配置项 {name} 必须为布尔值，当前值: {value!r}")


def _parse_log_level(value: str | None) -> str:
    """解析日志级别。"""
    if value is None:
        return "INFO"
    normalized = value.strip().upper()
    if normalized not in _ALLOWED_LOG_LEVELS:
        raise ConfigError(
            "配置项 EXCELMANUS_LOG_LEVEL 必须是 "
            f"{sorted(_ALLOWED_LOG_LEVELS)} 之一，当前值: {value!r}"
        )
    return normalized


def _parse_system_message_mode(value: str | None) -> str:
    """解析 system_message_mode。"""
    if value is None:
        return "auto"
    normalized = value.strip().lower()
    if normalized not in {"auto", "merge", "replace"}:
        raise ConfigError(
            "配置项 EXCELMANUS_SYSTEM_MESSAGE_MODE 必须是 "
            "['auto', 'merge', 'replace'] 之一，当前值: "
            f"{value!r}"
        )
    return normalized


def _parse_window_perception_advisor_mode(value: str | None) -> str:
    """解析窗口感知顾问模式。"""
    if value is None:
        return "hybrid"
    normalized = value.strip().lower()
    if normalized not in {"rules", "hybrid"}:
        raise ConfigError(
            "配置项 EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_MODE 必须是 "
            "['rules', 'hybrid'] 之一，当前值: "
            f"{value!r}"
        )
    return normalized


def _parse_window_return_mode(value: str | None) -> str:
    """解析工具返回模式，非法值自动回退 enriched。"""
    if value is None:
        return "adaptive"
    normalized = value.strip().lower()
    if normalized in _ALLOWED_WINDOW_RETURN_MODES:
        return normalized
    logger.warning(
        "配置项 EXCELMANUS_WINDOW_RETURN_MODE 非法(%r)，已回退为 enriched",
        value,
    )
    return "enriched"


def _parse_window_rule_engine_version(value: str | None) -> str:
    """解析窗口规则引擎版本。"""
    if value is None:
        return "v1"
    normalized = value.strip().lower()
    if normalized in _ALLOWED_WINDOW_RULE_ENGINE_VERSIONS:
        return normalized
    logger.warning(
        "配置项 EXCELMANUS_WINDOW_RULE_ENGINE_VERSION 非法(%r)，已回退为 v1",
        value,
    )
    return "v1"


def _parse_adaptive_model_mode_overrides(value: str | None) -> dict[str, str]:
    """解析 adaptive 模型模式覆盖配置。"""
    if value is None or not value.strip():
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        logger.warning(
            "配置项 EXCELMANUS_ADAPTIVE_MODEL_MODE_OVERRIDES 非法 JSON，已忽略"
        )
        return {}

    if not isinstance(parsed, dict):
        logger.warning(
            "配置项 EXCELMANUS_ADAPTIVE_MODEL_MODE_OVERRIDES 必须为 JSON object，已忽略"
        )
        return {}

    normalized: dict[str, str] = {}
    for raw_key, raw_mode in parsed.items():
        if not isinstance(raw_key, str):
            logger.warning(
                "adaptive override key 非字符串(%r)，已忽略",
                raw_key,
            )
            continue
        if not isinstance(raw_mode, str):
            logger.warning(
                "adaptive override 模式非字符串(%r:%r)，已忽略",
                raw_key,
                raw_mode,
            )
            continue

        key = raw_key.strip().lower()
        mode = raw_mode.strip().lower()
        if not key:
            logger.warning("adaptive override key 为空，已忽略")
            continue
        if mode not in {"unified", "anchored", "enriched"}:
            logger.warning(
                "adaptive override 模式非法(%s=%s)，已忽略",
                raw_key,
                raw_mode,
            )
            continue
        normalized[key] = mode
    return normalized


def _extract_first_model(raw: str | None) -> dict | None:
    """从 EXCELMANUS_MODELS JSON 数组中提取第一个模型配置（用于继承默认值）。

    解析失败或为空时返回 None，不抛异常。
    """
    if not raw or not raw.strip():
        return None
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(items, list) and len(items) > 0 and isinstance(items[0], dict):
        return items[0]
    return None


def _parse_models(raw: str | None, default_api_key: str, default_base_url: str) -> tuple[ModelProfile, ...]:
    """解析 EXCELMANUS_MODELS 环境变量（JSON 数组）。

    每个元素必须包含 name 和 model，api_key/base_url 可省略（回退到主配置）。
    """
    if not raw or not raw.strip():
        return ()
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"EXCELMANUS_MODELS JSON 解析失败：{exc}")
    if not isinstance(items, list):
        raise ConfigError("EXCELMANUS_MODELS 必须为 JSON 数组。")

    profiles: list[ModelProfile] = []
    seen_names: set[str] = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigError(f"EXCELMANUS_MODELS[{i}] 必须为 JSON 对象。")
        name = item.get("name")
        model = item.get("model")
        if not name or not isinstance(name, str):
            raise ConfigError(f"EXCELMANUS_MODELS[{i}] 缺少 name 字段。")
        if not model or not isinstance(model, str):
            raise ConfigError(f"EXCELMANUS_MODELS[{i}] 缺少 model 字段。")
        name = name.strip()
        if name in seen_names:
            raise ConfigError(f"EXCELMANUS_MODELS 中 name 重复：{name!r}。")
        seen_names.add(name)
        api_key = item.get("api_key", "").strip() or default_api_key
        base_url = item.get("base_url", "").strip() or default_base_url
        _validate_base_url(base_url)
        description = item.get("description", "").strip()
        profiles.append(ModelProfile(
            name=name,
            model=model,
            api_key=api_key,
            base_url=base_url,
            description=description,
        ))
    return tuple(profiles)


def load_cors_allow_origins() -> tuple[str, ...]:
    """解析 CORS 允许来源列表（逗号分隔，空字符串将被忽略）。"""
    load_runtime_env()
    cors_raw = os.environ.get("EXCELMANUS_CORS_ALLOW_ORIGINS")
    if cors_raw is not None:
        return tuple(o.strip() for o in cors_raw.split(",") if o.strip())
    return ("http://localhost:5173",)


def _parse_csv_tuple(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def load_config() -> ExcelManusConfig:
    """加载配置。优先级：环境变量 > .env 文件 > 默认值。

    API Key 为必填项，缺失时抛出 ConfigError。
    """
    load_runtime_env()

    # 读取各配置项（允许从 EXCELMANUS_MODELS 的第一个模型继承）
    api_key = os.environ.get("EXCELMANUS_API_KEY") or ""
    base_url = os.environ.get("EXCELMANUS_BASE_URL") or ""
    model = os.environ.get("EXCELMANUS_MODEL") or ""

    # 当必填项缺失时，尝试从 EXCELMANUS_MODELS 的第一个模型继承
    if not api_key or not base_url or not model:
        first_model = _extract_first_model(os.environ.get("EXCELMANUS_MODELS"))
        if first_model is not None:
            api_key = api_key or first_model.get("api_key", "")
            base_url = base_url or first_model.get("base_url", "")
            model = model or first_model.get("model", "")

    if not api_key:
        raise ConfigError(
            "缺少必填配置项 EXCELMANUS_API_KEY。"
            "请通过环境变量、.env 文件或 EXCELMANUS_MODELS 设置该值。"
        )
    if not base_url:
        raise ConfigError(
            "缺少必填配置项 EXCELMANUS_BASE_URL。"
            "请通过环境变量、.env 文件或 EXCELMANUS_MODELS 设置该值。"
        )
    _validate_base_url(base_url)
    if not model:
        # 尝试从 Gemini 完整 URL 中提取模型名
        from excelmanus.providers.gemini import _extract_model_from_url
        extracted = _extract_model_from_url(base_url)
        if extracted:
            model = extracted
    if not model:
        raise ConfigError(
            "缺少必填配置项 EXCELMANUS_MODEL。"
            "请通过环境变量、.env 文件或 EXCELMANUS_MODELS 设置该值。"
            "（Gemini 用户也可在 BASE_URL 中包含模型名，如 .../models/gemini-2.5-flash:generateContent）"
        )

    max_iterations = _parse_int(
        os.environ.get("EXCELMANUS_MAX_ITERATIONS"), "EXCELMANUS_MAX_ITERATIONS", 20
    )
    max_consecutive_failures = _parse_int(
        os.environ.get("EXCELMANUS_MAX_CONSECUTIVE_FAILURES"),
        "EXCELMANUS_MAX_CONSECUTIVE_FAILURES",
        3,
    )
    session_ttl_seconds = _parse_int(
        os.environ.get("EXCELMANUS_SESSION_TTL_SECONDS"),
        "EXCELMANUS_SESSION_TTL_SECONDS",
        1800,
    )
    max_sessions = _parse_int(
        os.environ.get("EXCELMANUS_MAX_SESSIONS"), "EXCELMANUS_MAX_SESSIONS", 1000
    )

    workspace_root = os.environ.get("EXCELMANUS_WORKSPACE_ROOT", ".")
    log_level = _parse_log_level(os.environ.get("EXCELMANUS_LOG_LEVEL"))
    default_system_skill_dir = (
        Path(__file__).resolve().parent / "skillpacks" / "system"
    )
    default_project_skill_dir = Path(workspace_root) / ".excelmanus" / "skillpacks"
    skills_system_dir = os.environ.get(
        "EXCELMANUS_SKILLS_SYSTEM_DIR", str(default_system_skill_dir)
    )
    skills_user_dir = os.environ.get(
        "EXCELMANUS_SKILLS_USER_DIR", "~/.excelmanus/skillpacks"
    )
    skills_project_dir = os.environ.get(
        "EXCELMANUS_SKILLS_PROJECT_DIR", str(default_project_skill_dir)
    )
    skills_context_char_budget = _parse_int_allow_zero(
        os.environ.get("EXCELMANUS_SKILLS_CONTEXT_CHAR_BUDGET"),
        "EXCELMANUS_SKILLS_CONTEXT_CHAR_BUDGET",
        12000,
    )
    skills_discovery_enabled = _parse_bool(
        os.environ.get("EXCELMANUS_SKILLS_DISCOVERY_ENABLED"),
        "EXCELMANUS_SKILLS_DISCOVERY_ENABLED",
        True,
    )
    skills_discovery_scan_workspace_ancestors = _parse_bool(
        os.environ.get("EXCELMANUS_SKILLS_DISCOVERY_SCAN_WORKSPACE_ANCESTORS"),
        "EXCELMANUS_SKILLS_DISCOVERY_SCAN_WORKSPACE_ANCESTORS",
        True,
    )
    skills_discovery_include_agents = _parse_bool(
        os.environ.get("EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_AGENTS"),
        "EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_AGENTS",
        True,
    )
    skills_discovery_include_claude = _parse_bool(
        os.environ.get("EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_CLAUDE"),
        "EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_CLAUDE",
        True,
    )
    skills_discovery_include_openclaw = _parse_bool(
        os.environ.get("EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_OPENCLAW"),
        "EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_OPENCLAW",
        True,
    )
    skills_discovery_extra_dirs = _parse_csv_tuple(
        os.environ.get("EXCELMANUS_SKILLS_DISCOVERY_EXTRA_DIRS")
    )
    system_message_mode = _parse_system_message_mode(
        os.environ.get("EXCELMANUS_SYSTEM_MESSAGE_MODE")
    )
    tool_result_hard_cap_chars = _parse_int_allow_zero(
        os.environ.get("EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS"),
        "EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS",
        12000,
    )
    large_excel_threshold_bytes = _parse_int(
        os.environ.get("EXCELMANUS_LARGE_EXCEL_THRESHOLD_BYTES"),
        "EXCELMANUS_LARGE_EXCEL_THRESHOLD_BYTES",
        8 * 1024 * 1024,
    )
    external_safe_mode = _parse_bool(
        os.environ.get("EXCELMANUS_EXTERNAL_SAFE_MODE"),
        "EXCELMANUS_EXTERNAL_SAFE_MODE",
        True,
    )
    cors_allow_origins = load_cors_allow_origins()
    mcp_shared_manager = _parse_bool(
        os.environ.get("EXCELMANUS_MCP_SHARED_MANAGER"),
        "EXCELMANUS_MCP_SHARED_MANAGER",
        False,
    )

    # 路由子代理配置（可选）
    router_api_key = os.environ.get("EXCELMANUS_ROUTER_API_KEY") or None
    router_base_url = os.environ.get("EXCELMANUS_ROUTER_BASE_URL") or None
    if router_base_url:
        _validate_base_url(router_base_url)
    router_model = os.environ.get("EXCELMANUS_ROUTER_MODEL") or None

    # subagent 执行配置
    subagent_enabled = _parse_bool(
        os.environ.get("EXCELMANUS_SUBAGENT_ENABLED"),
        "EXCELMANUS_SUBAGENT_ENABLED",
        True,
    )
    subagent_model = os.environ.get("EXCELMANUS_SUBAGENT_MODEL") or None
    subagent_max_iterations = _parse_int(
        os.environ.get("EXCELMANUS_SUBAGENT_MAX_ITERATIONS"),
        "EXCELMANUS_SUBAGENT_MAX_ITERATIONS",
        120,
    )
    subagent_max_consecutive_failures = _parse_int(
        os.environ.get("EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES"),
        "EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES",
        2,
    )
    subagent_user_dir = os.environ.get(
        "EXCELMANUS_SUBAGENT_USER_DIR",
        "~/.excelmanus/agents",
    )
    subagent_project_dir = os.environ.get(
        "EXCELMANUS_SUBAGENT_PROJECT_DIR",
        str(Path(workspace_root) / ".excelmanus" / "agents"),
    )

    # 跨会话持久记忆配置
    memory_enabled = _parse_bool(
        os.environ.get("EXCELMANUS_MEMORY_ENABLED"),
        "EXCELMANUS_MEMORY_ENABLED",
        True,
    )
    memory_dir = os.environ.get("EXCELMANUS_MEMORY_DIR", "~/.excelmanus/memory")
    memory_auto_load_lines = _parse_int(
        os.environ.get("EXCELMANUS_MEMORY_AUTO_LOAD_LINES"),
        "EXCELMANUS_MEMORY_AUTO_LOAD_LINES",
        200,
    )
    max_context_tokens = _parse_int(
        os.environ.get("EXCELMANUS_MAX_CONTEXT_TOKENS"),
        "EXCELMANUS_MAX_CONTEXT_TOKENS",
        128_000,
    )
    hooks_command_enabled = _parse_bool(
        os.environ.get("EXCELMANUS_HOOKS_COMMAND_ENABLED"),
        "EXCELMANUS_HOOKS_COMMAND_ENABLED",
        False,
    )
    hooks_command_allowlist = _parse_csv_tuple(
        os.environ.get("EXCELMANUS_HOOKS_COMMAND_ALLOWLIST")
    )
    hooks_command_timeout_seconds = _parse_int(
        os.environ.get("EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS"),
        "EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS",
        10,
    )
    hooks_output_max_chars = _parse_int(
        os.environ.get("EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS"),
        "EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS",
        32000,
    )
    window_perception_enabled = _parse_bool(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_ENABLED"),
        "EXCELMANUS_WINDOW_PERCEPTION_ENABLED",
        True,
    )
    window_perception_system_budget_tokens = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_SYSTEM_BUDGET_TOKENS"),
        "EXCELMANUS_WINDOW_PERCEPTION_SYSTEM_BUDGET_TOKENS",
        3000,
    )
    window_perception_tool_append_tokens = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_TOOL_APPEND_TOKENS"),
        "EXCELMANUS_WINDOW_PERCEPTION_TOOL_APPEND_TOKENS",
        500,
    )
    window_perception_max_windows = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_MAX_WINDOWS"),
        "EXCELMANUS_WINDOW_PERCEPTION_MAX_WINDOWS",
        6,
    )
    window_perception_default_rows = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_ROWS"),
        "EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_ROWS",
        25,
    )
    window_perception_default_cols = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_COLS"),
        "EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_COLS",
        10,
    )
    window_perception_minimized_tokens = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_MINIMIZED_TOKENS"),
        "EXCELMANUS_WINDOW_PERCEPTION_MINIMIZED_TOKENS",
        80,
    )
    window_perception_background_after_idle = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_BACKGROUND_AFTER_IDLE"),
        "EXCELMANUS_WINDOW_PERCEPTION_BACKGROUND_AFTER_IDLE",
        1,
    )
    window_perception_suspend_after_idle = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_SUSPEND_AFTER_IDLE"),
        "EXCELMANUS_WINDOW_PERCEPTION_SUSPEND_AFTER_IDLE",
        3,
    )
    window_perception_terminate_after_idle = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_TERMINATE_AFTER_IDLE"),
        "EXCELMANUS_WINDOW_PERCEPTION_TERMINATE_AFTER_IDLE",
        5,
    )
    window_perception_advisor_mode = _parse_window_perception_advisor_mode(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_MODE")
    )
    window_perception_advisor_timeout_ms = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TIMEOUT_MS"),
        "EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TIMEOUT_MS",
        800,
    )
    window_perception_advisor_trigger_window_count = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_WINDOW_COUNT"),
        "EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_WINDOW_COUNT",
        3,
    )
    window_perception_advisor_trigger_turn = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_TURN"),
        "EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_TURN",
        4,
    )
    window_perception_advisor_plan_ttl_turns = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_PLAN_TTL_TURNS"),
        "EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_PLAN_TTL_TURNS",
        2,
    )
    window_return_mode = _parse_window_return_mode(
        os.environ.get("EXCELMANUS_WINDOW_RETURN_MODE")
    )
    adaptive_model_mode_overrides = _parse_adaptive_model_mode_overrides(
        os.environ.get("EXCELMANUS_ADAPTIVE_MODEL_MODE_OVERRIDES")
    )
    window_full_max_rows = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_FULL_MAX_ROWS"),
        "EXCELMANUS_WINDOW_FULL_MAX_ROWS",
        25,
    )
    window_full_total_budget_tokens = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_FULL_TOTAL_BUDGET_TOKENS"),
        "EXCELMANUS_WINDOW_FULL_TOTAL_BUDGET_TOKENS",
        500,
    )
    window_data_buffer_max_rows = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_DATA_BUFFER_MAX_ROWS"),
        "EXCELMANUS_WINDOW_DATA_BUFFER_MAX_ROWS",
        200,
    )
    window_intent_enabled = _parse_bool(
        os.environ.get("EXCELMANUS_WINDOW_INTENT_ENABLED"),
        "EXCELMANUS_WINDOW_INTENT_ENABLED",
        True,
    )
    window_intent_sticky_turns = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_INTENT_STICKY_TURNS"),
        "EXCELMANUS_WINDOW_INTENT_STICKY_TURNS",
        3,
    )
    window_intent_repeat_warn_threshold = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_INTENT_REPEAT_WARN_THRESHOLD"),
        "EXCELMANUS_WINDOW_INTENT_REPEAT_WARN_THRESHOLD",
        2,
    )
    window_intent_repeat_trip_threshold = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_INTENT_REPEAT_TRIP_THRESHOLD"),
        "EXCELMANUS_WINDOW_INTENT_REPEAT_TRIP_THRESHOLD",
        3,
    )
    window_rule_engine_version = _parse_window_rule_engine_version(
        os.environ.get("EXCELMANUS_WINDOW_RULE_ENGINE_VERSION")
    )

    # 多模型配置档案
    models = _parse_models(
        os.environ.get("EXCELMANUS_MODELS"),
        default_api_key=api_key,
        default_base_url=base_url,
    )

    return ExcelManusConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        max_iterations=max_iterations,
        max_consecutive_failures=max_consecutive_failures,
        session_ttl_seconds=session_ttl_seconds,
        max_sessions=max_sessions,
        workspace_root=workspace_root,
        log_level=log_level,
        skills_system_dir=skills_system_dir,
        skills_user_dir=skills_user_dir,
        skills_project_dir=skills_project_dir,
        skills_context_char_budget=skills_context_char_budget,
        skills_discovery_enabled=skills_discovery_enabled,
        skills_discovery_scan_workspace_ancestors=skills_discovery_scan_workspace_ancestors,
        skills_discovery_include_agents=skills_discovery_include_agents,
        skills_discovery_include_claude=skills_discovery_include_claude,
        skills_discovery_include_openclaw=skills_discovery_include_openclaw,
        skills_discovery_extra_dirs=skills_discovery_extra_dirs,
        system_message_mode=system_message_mode,
        tool_result_hard_cap_chars=tool_result_hard_cap_chars,
        large_excel_threshold_bytes=large_excel_threshold_bytes,
        external_safe_mode=external_safe_mode,
        cors_allow_origins=cors_allow_origins,
        mcp_shared_manager=mcp_shared_manager,
        router_api_key=router_api_key,
        router_base_url=router_base_url,
        router_model=router_model,
        subagent_enabled=subagent_enabled,
        subagent_model=subagent_model,
        subagent_max_iterations=subagent_max_iterations,
        subagent_max_consecutive_failures=subagent_max_consecutive_failures,
        subagent_user_dir=subagent_user_dir,
        subagent_project_dir=subagent_project_dir,
        memory_enabled=memory_enabled,
        memory_dir=memory_dir,
        memory_auto_load_lines=memory_auto_load_lines,
        max_context_tokens=max_context_tokens,
        hooks_command_enabled=hooks_command_enabled,
        hooks_command_allowlist=hooks_command_allowlist,
        hooks_command_timeout_seconds=hooks_command_timeout_seconds,
        hooks_output_max_chars=hooks_output_max_chars,
        window_perception_enabled=window_perception_enabled,
        window_perception_system_budget_tokens=window_perception_system_budget_tokens,
        window_perception_tool_append_tokens=window_perception_tool_append_tokens,
        window_perception_max_windows=window_perception_max_windows,
        window_perception_default_rows=window_perception_default_rows,
        window_perception_default_cols=window_perception_default_cols,
        window_perception_minimized_tokens=window_perception_minimized_tokens,
        window_perception_background_after_idle=window_perception_background_after_idle,
        window_perception_suspend_after_idle=window_perception_suspend_after_idle,
        window_perception_terminate_after_idle=window_perception_terminate_after_idle,
        window_perception_advisor_mode=window_perception_advisor_mode,
        window_perception_advisor_timeout_ms=window_perception_advisor_timeout_ms,
        window_perception_advisor_trigger_window_count=window_perception_advisor_trigger_window_count,
        window_perception_advisor_trigger_turn=window_perception_advisor_trigger_turn,
        window_perception_advisor_plan_ttl_turns=window_perception_advisor_plan_ttl_turns,
        window_return_mode=window_return_mode,
        adaptive_model_mode_overrides=adaptive_model_mode_overrides,
        window_full_max_rows=window_full_max_rows,
        window_full_total_budget_tokens=window_full_total_budget_tokens,
        window_data_buffer_max_rows=window_data_buffer_max_rows,
        window_intent_enabled=window_intent_enabled,
        window_intent_sticky_turns=window_intent_sticky_turns,
        window_intent_repeat_warn_threshold=window_intent_repeat_warn_threshold,
        window_intent_repeat_trip_threshold=window_intent_repeat_trip_threshold,
        window_rule_engine_version=window_rule_engine_version,
        models=models,
    )
