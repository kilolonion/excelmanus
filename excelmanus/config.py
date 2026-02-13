"""配置管理模块：加载环境变量、.env 文件和默认值。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    """配置缺失或校验失败时抛出的异常。"""


# Base URL 合法性正则：仅接受 http:// 或 https:// 开头的 URL
_URL_PATTERN = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)


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
    skills_prefilter_topk: int = 6
    skills_max_selected: int = 3
    skills_context_char_budget: int = 12000  # 技能正文字符预算，0 表示不限制
    skills_skip_llm_confirm: bool = False
    skills_fastpath_min_score: int = 6
    skills_fastpath_min_gap: int = 3
    system_message_mode: str = "auto"
    large_excel_threshold_bytes: int = 8 * 1024 * 1024
    external_safe_mode: bool = True
    cors_allow_origins: tuple[str, ...] = ("http://localhost:5173",)
    # 路由子代理配置（可选，默认回退到主模型）
    router_api_key: str | None = None
    router_base_url: str | None = None
    router_model: str | None = None
    # fork 子代理执行配置
    subagent_enabled: bool = True
    subagent_model: str | None = None
    subagent_max_iterations: int = 6
    subagent_max_consecutive_failures: int = 2
    # 跨会话持久记忆配置
    memory_enabled: bool = True
    memory_dir: str = "~/.excelmanus/memory"
    memory_auto_load_lines: int = 200


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


def _parse_choice(
    value: str | None, name: str, default: str, choices: set[str]
) -> str:
    """将字符串解析为枚举值。"""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized not in choices:
        raise ConfigError(
            f"配置项 {name} 必须是 {sorted(choices)} 之一，当前值: {value!r}"
        )
    return normalized


def load_cors_allow_origins() -> tuple[str, ...]:
    """解析 CORS 允许来源列表（逗号分隔，空字符串将被忽略）。"""
    cors_raw = os.environ.get("EXCELMANUS_CORS_ALLOW_ORIGINS")
    if cors_raw is not None:
        return tuple(o.strip() for o in cors_raw.split(",") if o.strip())
    return ("http://localhost:5173",)


def load_config() -> ExcelManusConfig:
    """加载配置。优先级：环境变量 > .env 文件 > 默认值。

    API Key 为必填项，缺失时抛出 ConfigError。
    """
    # 先加载 .env 文件（不覆盖已有环境变量）
    dotenv_path = Path.cwd() / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)

    # 读取各配置项
    api_key = os.environ.get("EXCELMANUS_API_KEY")
    if not api_key:
        raise ConfigError(
            "缺少必填配置项 EXCELMANUS_API_KEY。"
            "请通过环境变量或 .env 文件设置该值。"
        )

    base_url = os.environ.get("EXCELMANUS_BASE_URL")
    if not base_url:
        raise ConfigError(
            "缺少必填配置项 EXCELMANUS_BASE_URL。"
            "请通过环境变量或 .env 文件设置该值。"
        )
    _validate_base_url(base_url)

    model = os.environ.get("EXCELMANUS_MODEL")
    if not model:
        raise ConfigError(
            "缺少必填配置项 EXCELMANUS_MODEL。"
            "请通过环境变量或 .env 文件设置该值。"
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
    log_level = os.environ.get("EXCELMANUS_LOG_LEVEL", "INFO").upper()
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
    skills_prefilter_topk = _parse_int(
        os.environ.get("EXCELMANUS_SKILLS_PREFILTER_TOPK"),
        "EXCELMANUS_SKILLS_PREFILTER_TOPK",
        6,
    )
    skills_max_selected = _parse_int(
        os.environ.get("EXCELMANUS_SKILLS_MAX_SELECTED"),
        "EXCELMANUS_SKILLS_MAX_SELECTED",
        3,
    )
    skills_context_char_budget = _parse_int_allow_zero(
        os.environ.get("EXCELMANUS_SKILLS_CONTEXT_CHAR_BUDGET"),
        "EXCELMANUS_SKILLS_CONTEXT_CHAR_BUDGET",
        12000,
    )
    skills_skip_llm_confirm = _parse_bool(
        os.environ.get("EXCELMANUS_SKILLS_SKIP_LLM_CONFIRM"),
        "EXCELMANUS_SKILLS_SKIP_LLM_CONFIRM",
        False,
    )
    skills_fastpath_min_score = _parse_int(
        os.environ.get("EXCELMANUS_SKILLS_FASTPATH_MIN_SCORE"),
        "EXCELMANUS_SKILLS_FASTPATH_MIN_SCORE",
        6,
    )
    skills_fastpath_min_gap = _parse_int(
        os.environ.get("EXCELMANUS_SKILLS_FASTPATH_MIN_GAP"),
        "EXCELMANUS_SKILLS_FASTPATH_MIN_GAP",
        3,
    )
    system_message_mode = _parse_choice(
        os.environ.get("EXCELMANUS_SYSTEM_MESSAGE_MODE"),
        "EXCELMANUS_SYSTEM_MESSAGE_MODE",
        "auto",
        {"auto", "multi", "merge"},
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

    # 路由子代理配置（可选）
    router_api_key = os.environ.get("EXCELMANUS_ROUTER_API_KEY") or None
    router_base_url = os.environ.get("EXCELMANUS_ROUTER_BASE_URL") or None
    if router_base_url:
        _validate_base_url(router_base_url)
    router_model = os.environ.get("EXCELMANUS_ROUTER_MODEL") or None

    # fork 子代理执行配置
    subagent_enabled = _parse_bool(
        os.environ.get("EXCELMANUS_SUBAGENT_ENABLED"),
        "EXCELMANUS_SUBAGENT_ENABLED",
        True,
    )
    subagent_model = os.environ.get("EXCELMANUS_SUBAGENT_MODEL") or None
    subagent_max_iterations = _parse_int(
        os.environ.get("EXCELMANUS_SUBAGENT_MAX_ITERATIONS"),
        "EXCELMANUS_SUBAGENT_MAX_ITERATIONS",
        6,
    )
    subagent_max_consecutive_failures = _parse_int(
        os.environ.get("EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES"),
        "EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES",
        2,
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
        skills_prefilter_topk=skills_prefilter_topk,
        skills_max_selected=skills_max_selected,
        skills_context_char_budget=skills_context_char_budget,
        skills_skip_llm_confirm=skills_skip_llm_confirm,
        skills_fastpath_min_score=skills_fastpath_min_score,
        skills_fastpath_min_gap=skills_fastpath_min_gap,
        system_message_mode=system_message_mode,
        large_excel_threshold_bytes=large_excel_threshold_bytes,
        external_safe_mode=external_safe_mode,
        cors_allow_origins=cors_allow_origins,
        router_api_key=router_api_key,
        router_base_url=router_base_url,
        router_model=router_model,
        subagent_enabled=subagent_enabled,
        subagent_model=subagent_model,
        subagent_max_iterations=subagent_max_iterations,
        subagent_max_consecutive_failures=subagent_max_consecutive_failures,
        memory_enabled=memory_enabled,
        memory_dir=memory_dir,
        memory_auto_load_lines=memory_auto_load_lines,
    )
