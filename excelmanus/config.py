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


# 基础 URL 合法性正则：仅接受 http:// 或 https:// 开头的 URL
_URL_PATTERN = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)
_ALLOWED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_ALLOWED_WINDOW_RETURN_MODES = {"unified", "anchored", "enriched", "adaptive"}
_ALLOWED_WINDOW_RULE_ENGINE_VERSIONS = {"v1", "v2"}
DEFAULT_EMBEDDING_MODEL = "text-embedding-v3"
DEFAULT_EMBEDDING_DIMENSIONS = 1536
logger = logging.getLogger(__name__)

# ── 模型 → 上下文窗口大小映射（token 数） ──────────────────────────
# 键为模型标识符的前缀或完整名称，匹配时优先取最长前缀。
# 未匹配到的模型回退到 _DEFAULT_CONTEXT_TOKENS。
_DEFAULT_CONTEXT_TOKENS = 128_000

_MODEL_CONTEXT_WINDOW: dict[str, int] = {
    # OpenAI 提供商
    "gpt-5": 400_000,
    "gpt-5-pro": 400_000,
    "gpt-5-mini": 400_000,
    "gpt-5-nano": 400_000,
    "gpt-5.2": 400_000,
    "gpt-5.3": 400_000,
    "gpt-5-codex": 400_000,
    "gpt-5-codex-latest": 400_000,
    "gpt-5.2-codex": 400_000,
    "gpt-5.3-codex": 400_000,
    "gpt-5.3-codex-latest": 400_000,
    "gpt-5.1-codex-mini": 400_000,
    "gpt-5.1-codex-max": 400_000,
    "gpt-5.3-codex-spark": 128_000,
    "gpt-5.3-codex-spark-latest": 128_000,
    "gpt-5.1": 400_000,
    "gpt-5.1-codex": 400_000,
    "gpt-5-chat-latest": 128_000,
    "gpt-5.1-chat-latest": 128_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4.1-nano": 1_047_576,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o1-pro": 200_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "o3-pro": 200_000,
    "o3-deep-research": 200_000,
    "o4": 200_000,
    "o4-mini": 200_000,
    "o4-mini-deep-research": 200_000,
    "codex-mini-latest": 200_000,
    # Anthropic（Claude）提供商
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3.5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3.5-haiku": 200_000,
    "claude-3-7-sonnet": 200_000,
    "claude-3.7-sonnet": 200_000,
    "claude-4-sonnet": 200_000,
    "claude-4-opus": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-opus-4": 200_000,
    "claude-opus-4.1": 200_000,
    "claude-opus-4.6": 200_000,
    "claude-sonnet-4.6": 200_000,
    "claude-haiku-4.5": 200_000,
    # Google Gemini 提供商
    "gemini-2.5-pro": 1_048_576,
    "gemini-2.5-pro-preview": 1_048_576,
    "gemini-2.5-flash": 1_048_576,
    "gemini-2.5-flash-preview": 1_048_576,
    "gemini-2.5-flash-image-preview": 1_048_576,
    "gemini-2.5-flash-lite": 1_048_576,
    "gemini-2.5-flash-lite-preview": 1_048_576,
    "gemini-2.0-flash": 1_048_576,
    "gemini-2.0-flash-live": 1_048_576,
    "gemini-2.0-flash-thinking-exp": 1_048_576,
    "gemini-2.0-flash-lite": 1_048_576,
    "gemini-2.0-flash-001": 1_048_576,
    "gemini-2.0-flash-lite-001": 1_048_576,
    "gemini-1.5-pro": 2_097_152,
    "gemini-1.5-flash": 1_048_576,
    # 通义千问（Qwen）提供商
    "qwen-max": 262_144,
    "qwen-max-latest": 262_144,
    "qwen-plus": 1_000_000,
    "qwen-plus-us": 1_000_000,
    "qwen-plus-latest": 1_000_000,
    "qwen3.5-plus": 1_000_000,
    "qwq-plus": 131_072,
    "qwq-plus-latest": 131_072,
    "qwen-flash": 1_000_000,
    "qwen-flash-latest": 1_000_000,
    "qwen-turbo": 1_000_000,
    "qwen-long": 1_000_000,
    "qwen-long-latest": 10_000_000,
    "qwen-coder": 1_000_000,
    "qwen-coder-plus": 131_072,
    "qwen-coder-plus-latest": 131_072,
    "qwen-coder-turbo": 131_072,
    "qwen3-coder-plus": 1_000_000,
    "qwen3-235b": 131_072,
    "qwen3-30b": 131_072,
    "qwen3-32b": 131_072,
    "qvq-72b-preview": 32_768,
    "qwen-vl-ocr": 38_192,
    "qwen-vl-ocr-2025-08-28": 34_096,
    "qwen2.5-omni-7b": 32_768,
    "qwen2.5-72b": 131_072,
    "qwen2.5-32b": 131_072,
    # DeepSeek 提供商
    "deepseek-chat": 128_000,
    "deepseek-reasoner": 128_000,
    "deepseek-v3": 128_000,
    "deepseek-r1": 128_000,
    "deepseek-v3.2": 131_072,
    "deepseek-v3.2-exp": 131_072,
    # Mistral 提供商
    "mistral-large-2512": 256_000,
    "mistral-large-latest": 256_000,
    "mistral-medium-2508": 128_000,
    "mistral-medium-latest": 128_000,
    "mistral-small-2506": 128_000,
    "mistral-small-latest": 128_000,
    "devstral-2512": 256_000,
    "labs-devstral-small-2512": 256_000,
    "labs-devstral-small-latest": 256_000,
    "devstral-small-2505": 128_000,
    "codestral-2508": 128_000,
    "codestral-latest": 128_000,
    "magistral-small-2509": 128_000,
    "magistral-medium-2509": 128_000,
    "magistral-small-2507": 40_000,
    "magistral-small-2506": 40_000,
    "pixtral-large-2411": 128_000,
    "pixtral-large-latest": 128_000,
    "voxtral-mini-2507": 128_000,
    "voxtral-mini-latest": 128_000,
    "voxtral-small-2507": 32_000,
    "voxtral-small-latest": 32_000,
    "labs-mistral-small-creative": 32_000,
    "mistral-small-2503": 128_000,
    "ministral-14b-2512": 256_000,
    "ministral-8b-2512": 256_000,
    "ministral-3b-2512": 256_000,
    # AI21 Jamba 提供商
    "jamba-large": 256_000,
    "jamba-mini": 256_000,
    "jamba-3b": 256_000,
    # Amazon Nova 提供商
    "amazon.nova-premier": 1_000_000,
    "amazon.nova-pro": 300_000,
    "amazon.nova-lite": 300_000,
    "amazon.nova-micro": 128_000,
    "amazon.nova-sonic": 300_000,
    "us.amazon.nova-premier": 1_000_000,
    "us.amazon.nova-pro": 300_000,
    "us.amazon.nova-lite": 300_000,
    "us.amazon.nova-micro": 128_000,
    "us.amazon.nova-sonic": 300_000,
    "eu.amazon.nova-premier": 1_000_000,
    "eu.amazon.nova-pro": 300_000,
    "eu.amazon.nova-lite": 300_000,
    "eu.amazon.nova-micro": 128_000,
    "eu.amazon.nova-sonic": 300_000,
    "apac.amazon.nova-premier": 1_000_000,
    "apac.amazon.nova-pro": 300_000,
    "apac.amazon.nova-lite": 300_000,
    "apac.amazon.nova-micro": 128_000,
    "apac.amazon.nova-sonic": 300_000,
    "amazon.nova-2-lite": 1_000_000,
    "amazon.nova-2-sonic": 1_000_000,
    "nova-2-lite": 1_000_000,
    "nova-2-sonic": 1_000_000,
    # MiniMax 提供商
    "minimax-m2.5": 204_800,
    "minimax-m2.5-highspeed": 204_800,
    "minimax-m2.1": 204_800,
    "minimax-m2.1-highspeed": 204_800,
    "minimax-m2.1-lightning": 204_800,
    "minimax-m2": 204_800,
    "m2-her": 64_000,
    # Moonshot（Kimi）提供商
    "kimi-k2": 262_144,
    "kimi-k2-thinking": 262_144,
    "kimi-k2.5": 262_144,
    "kimi-k2.5-thinking": 262_144,
    "moonshot-kimi-k2.5": 262_144,
    "moonshot-kimi-k2.5-thinking": 262_144,
    "moonshot-kimi-k2-instruct": 131_072,
    "moonshotai/kimi-k2": 262_144,
    "moonshotai/kimi-k2-thinking": 262_144,
    "moonshotai/kimi-k2.5": 262_144,
    # Cohere 提供商
    "command-a": 256_000,
    "command-a-03-2025": 256_000,
    "command-a-reasoning": 256_000,
    "command-r-plus": 128_000,
    "command-r-plus-08-2024": 128_000,
    "command-r7b": 128_000,
    "command-r7b-12-2024": 128_000,
    "c4ai-command-r7b-12-2024": 128_000,
    "command-r": 128_000,
    "command-r-08-2024": 128_000,
    # xAI Grok 提供商
    "grok-4-fast-reasoning": 2_000_000,
    "grok-4-fast-non-reasoning": 2_000_000,
    "grok-4-1-fast-reasoning": 2_000_000,
    "grok-4-1-fast-non-reasoning": 2_000_000,
    "grok-code-fast-1": 256_000,
    "grok-4": 256_000,
    "xai.grok-4-fast-reasoning": 2_000_000,
    "xai.grok-4-fast-non-reasoning": 2_000_000,
    "xai.grok-4-1-fast-reasoning": 2_000_000,
    "xai.grok-4-1-fast-non-reasoning": 2_000_000,
    "xai.grok-code-fast-1": 256_000,
    "xai.grok-4": 256_000,
    # Meta Llama 提供商
    "llama-4-scout": 10_000_000,
    "llama-4-maverick": 1_000_000,
    "llama-3.3": 131_072,
    "llama-3.2": 131_072,
    "llama-3.1": 131_072,
    "meta-llama/llama-4-scout": 10_000_000,
    "meta-llama/llama-4-maverick": 1_000_000,
    "meta-llama/llama-3.3": 131_072,
    "meta-llama/llama-3.2": 131_072,
    "meta-llama/llama-3.1": 131_072,
    "moonshot-v1-128k": 128_000,
    "moonshot-v1-32k": 32_000,
    "moonshot-v1-8k": 8_000,
}


def _normalize_model_identifier(model: str) -> str:
    """归一化模型标识，兼容空格/下划线命名。"""
    normalized = model.strip().lower().replace("_", "-")
    normalized = re.sub(r"\s+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized)
    return normalized


def _infer_context_tokens_for_model(model: str) -> int:
    """根据模型名推断上下文窗口大小，最长前缀匹配优先。"""
    model_normalized = _normalize_model_identifier(model)
    candidates = [model_normalized]
    if "/" in model_normalized:
        tail = model_normalized.rsplit("/", 1)[-1]
        if tail and tail not in candidates:
            candidates.append(tail)

    best_key = ""
    best_val = _DEFAULT_CONTEXT_TOKENS
    best_candidate = ""
    for candidate in candidates:
        for key, val in _MODEL_CONTEXT_WINDOW.items():
            if candidate.startswith(key.lower()) and len(key) > len(best_key):
                best_key = key
                best_val = val
                best_candidate = candidate

    if not best_key:
        for candidate in candidates:
            # 兜底：未来 gpt-5.x-codex 变体，默认继承 GPT-5 Codex 400k。
            if candidate.startswith("gpt-5") and "codex" in candidate:
                best_key = "gpt-5*-codex(fallback)"
                best_val = 400_000
                best_candidate = candidate
                break

    if best_key:
        logger.debug(
            "模型 %r 匹配上下文窗口映射 %r（候选=%r）→ %d tokens",
            model, best_key, best_candidate or model_normalized, best_val,
        )
    else:
        logger.debug(
            "模型 %r 未匹配到已知映射，使用默认 %d tokens",
            model, _DEFAULT_CONTEXT_TOKENS,
        )
    return best_val


@dataclass(frozen=True)
class ExcelManusConfig:
    """不可变的全局配置对象。"""

    api_key: str
    base_url: str
    model: str
    max_iterations: int = 50
    max_consecutive_failures: int = 6
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
    skills_discovery_scan_external_tool_dirs: bool = True
    skills_discovery_extra_dirs: tuple[str, ...] = ()
    system_message_mode: str = "auto"
    tool_result_hard_cap_chars: int = 12000
    large_excel_threshold_bytes: int = 8 * 1024 * 1024
    external_safe_mode: bool = True
    cors_allow_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://localhost:5173",
    )
    mcp_shared_manager: bool = False
    # AUX 配置（统一用于路由小模型 + 子代理默认模型 + 窗口感知顾问模型）
    aux_api_key: str | None = None
    aux_base_url: str | None = None
    aux_model: str | None = None
    # subagent 执行配置
    subagent_enabled: bool = True
    subagent_max_iterations: int = 120
    subagent_max_consecutive_failures: int = 6
    # 同一轮次中相邻只读工具并发执行（asyncio.gather）
    parallel_readonly_tools: bool = True
    prefetch_explorer: bool = True
    subagent_user_dir: str = "~/.excelmanus/agents"
    subagent_project_dir: str = ".excelmanus/agents"
    # 跨会话持久记忆配置
    memory_enabled: bool = True
    memory_dir: str = "~/.excelmanus/memory"
    memory_auto_load_lines: int = 200
    memory_auto_extract_interval: int = 15  # 每 N 轮后台静默提取记忆（0 = 禁用）
    # 对话记忆上下文窗口大小（token 数），用于截断策略
    max_context_tokens: int = 128_000
    # 提示词缓存优化：向 OpenAI API 发送 prompt_cache_key 提升缓存命中率
    prompt_cache_key_enabled: bool = True
    # 对话历史摘要：超阈值时用辅助模型压缩早期对话（需配置 aux_model）
    summarization_enabled: bool = True
    summarization_threshold_ratio: float = 0.8
    summarization_keep_recent_turns: int = 3
    # 上下文自动压缩（Compaction）：增强版对话摘要，后台静默执行
    compaction_enabled: bool = True
    compaction_threshold_ratio: float = 0.85
    compaction_keep_recent_turns: int = 5
    compaction_max_summary_tokens: int = 1500
    # hooks 配置
    hooks_command_enabled: bool = False
    hooks_command_allowlist: tuple[str, ...] = ()
    hooks_command_timeout_seconds: int = 10
    hooks_output_max_chars: int = 32000
    # 窗口感知层配置
    window_perception_enabled: bool = True
    window_perception_system_budget_tokens: int = 3000
    window_perception_tool_append_tokens: int = 500
    window_perception_max_windows: int = 6
    window_perception_default_rows: int = 25
    window_perception_default_cols: int = 10
    window_perception_minimized_tokens: int = 80
    window_perception_background_after_idle: int = 2
    window_perception_suspend_after_idle: int = 5
    window_perception_terminate_after_idle: int = 8
    window_perception_advisor_mode: str = "rules"
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
    # VLM（视觉语言模型）独立模型配置（可选，未配置时回退到主模型）
    vlm_api_key: str | None = None
    vlm_base_url: str | None = None
    vlm_model: str | None = None
    vlm_timeout_seconds: int = 300
    vlm_max_retries: int = 1
    vlm_retry_base_delay_seconds: float = 5.0
    vlm_image_max_long_edge: int = 2048  # 图片长边上限（px），Qwen-VL 建议 4096
    vlm_image_jpeg_quality: int = 92  # JPEG 压缩质量
    vlm_enhance: bool = True  # B 通道总开关：VLM 增强描述，默认开启
    # 渐进式管线配置
    vlm_pipeline_uncertainty_threshold: int = 5  # 不确定项数量超过此值时暂停
    vlm_pipeline_uncertainty_confidence_floor: float = 0.3  # 任一项低于此置信度时暂停
    main_model_vision: str = "auto"  # 主模型视觉能力：auto/true/false
    # 备份沙盒模式：默认开启，所有文件操作重定向到 outputs/backups/ 副本
    backup_enabled: bool = True
    # 轮次 checkpoint 模式：每轮工具调用结束后自动快照被修改文件，支持按轮回退
    checkpoint_enabled: bool = False
    # 代码策略引擎配置
    code_policy_enabled: bool = True
    code_policy_green_auto_approve: bool = True
    code_policy_yellow_auto_approve: bool = True
    code_policy_extra_safe_modules: tuple[str, ...] = ()
    code_policy_extra_blocked_modules: tuple[str, ...] = ()
    # 工具参数 schema 校验（off/shadow/enforce）
    tool_schema_validation_mode: str = "off"
    tool_schema_validation_canary_percent: int = 100
    tool_schema_strict_path: bool = False
    # Embedding 语义检索配置（需独立配置 embedding API，未配置时功能关闭）
    embedding_enabled: bool = False
    embedding_api_key: str | None = None
    embedding_base_url: str | None = None
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS
    embedding_timeout_seconds: float = 30.0
    memory_semantic_top_k: int = 10
    memory_semantic_threshold: float = 0.3
    memory_semantic_fallback_recent: int = 5
    manifest_semantic_top_k: int = 5
    manifest_semantic_threshold: float = 0.25
    # 统一数据库路径（聊天记录、记忆、向量、审批均存于此）
    db_path: str = "~/.excelmanus/excelmanus.db"
    # PostgreSQL 连接 URL（设置后优先使用 PG，忽略 db_path）
    database_url: str = ""
    # 聊天记录持久化
    chat_history_enabled: bool = True
    chat_history_db_path: str = ""  # 废弃，运行时回退到 db_path
    # CLI 显示模式：dashboard（默认三段布局）或 classic（传统流式输出）
    cli_layout_mode: str = "dashboard"
    # 文本回复门禁模式：off（默认，完全关闭执行守卫和写入门禁）/ soft（降级为软提示后放行）
    guard_mode: str = "off"
    # 多模型配置档案（可选，通过 /model 命令切换）
    models: tuple[ModelProfile, ...] = ()


@dataclass(frozen=True)
class _ContextOptimizationConfig:
    """上下文预算与压缩策略配置（单一接线入口）。"""

    max_context_tokens: int
    prompt_cache_key_enabled: bool
    summarization_enabled: bool
    summarization_threshold_ratio: float
    summarization_keep_recent_turns: int
    compaction_enabled: bool
    compaction_threshold_ratio: float
    compaction_keep_recent_turns: int
    compaction_max_summary_tokens: int


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


def _parse_float_between_zero_and_one(value: str | None, name: str, default: float) -> float:
    """将字符串解析为 (0, 1) 区间内浮点数。"""
    if value is None:
        return default
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ConfigError(f"配置项 {name} 必须为浮点数，当前值: {value!r}")
    if not 0 < result < 1:
        raise ConfigError(f"配置项 {name} 必须在 (0, 1) 区间内，当前值: {result}")
    return result


def _parse_threshold(env_value: str | None, default: float) -> float:
    """解析语义阈值，非法值静默回退到默认值并记录警告。"""
    if env_value is None:
        return default
    try:
        result = float(env_value)
        if 0.0 <= result <= 1.0:
            return result
    except (ValueError, TypeError):
        pass
    logger.warning("阈值配置非法，使用默认值 %s（原值: %r）", default, env_value)
    return default


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
        return "rules"
    normalized = value.strip().lower()
    if normalized not in {"rules", "hybrid"}:
        raise ConfigError(
            "配置项 EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_MODE 必须是 "
            "['rules', 'hybrid'] 之一，当前值: "
            f"{value!r}"
        )
    return normalized


def _parse_window_return_mode(value: str | None) -> str:
    """解析工具返回模式，非法值自动回退 adaptive（与默认值一致）。"""
    if value is None:
        return "adaptive"
    normalized = value.strip().lower()
    if normalized in _ALLOWED_WINDOW_RETURN_MODES:
        return normalized
    logger.warning(
        "配置项 EXCELMANUS_WINDOW_RETURN_MODE 非法(%r)，已回退为 adaptive",
        value,
    )
    return "adaptive"


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


def _parse_tool_schema_validation_mode(value: str | None) -> str:
    """解析工具参数 schema 校验模式。"""
    if value is None:
        return "off"
    normalized = value.strip().lower()
    if normalized in {"off", "shadow", "enforce"}:
        return normalized
    raise ConfigError(
        "配置项 EXCELMANUS_TOOL_SCHEMA_VALIDATION_MODE 必须是 "
        "['off', 'shadow', 'enforce'] 之一，"
        f"当前值: {value!r}"
    )


_ALLOWED_CLI_LAYOUT_MODES = {"dashboard", "classic"}


def _parse_cli_layout_mode(value: str | None) -> str:
    """解析 CLI 布局模式，非法值自动回退 dashboard。"""
    if value is None:
        return "dashboard"
    normalized = value.strip().lower()
    if normalized in _ALLOWED_CLI_LAYOUT_MODES:
        return normalized
    logger.warning(
        "配置项 EXCELMANUS_CLI_LAYOUT_MODE 非法(%r)，已回退为 dashboard",
        value,
    )
    return "dashboard"


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


def _parse_cors_allow_origins() -> tuple[str, ...]:
    """从已加载的环境变量中解析 CORS 允许来源列表（不触发 .env 加载）。"""
    cors_raw = os.environ.get("EXCELMANUS_CORS_ALLOW_ORIGINS")
    if cors_raw is not None:
        return tuple(o.strip() for o in cors_raw.split(",") if o.strip())
    return ("http://localhost:3000", "http://localhost:5173")


def load_cors_allow_origins() -> tuple[str, ...]:
    """解析 CORS 允许来源列表（逗号分隔，空字符串将被忽略）。

    可独立调用（如 api.py 模块级），内部确保 .env 已加载。
    """
    load_runtime_env()
    return _parse_cors_allow_origins()


def _parse_csv_tuple(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _load_context_optimization_config(model: str = "") -> _ContextOptimizationConfig:
    """加载上下文优化相关配置，避免字段声明/解析/回填三处漂移。

    优先级：EXCELMANUS_MAX_CONTEXT_TOKENS 环境变量 > 模型自动推断 > 默认 128k。
    """
    env_max_ctx = os.environ.get("EXCELMANUS_MAX_CONTEXT_TOKENS")
    if env_max_ctx:
        # 用户显式配置，尊重用户意愿
        max_context_tokens = _parse_int(env_max_ctx, "EXCELMANUS_MAX_CONTEXT_TOKENS", _DEFAULT_CONTEXT_TOKENS)
    elif model:
        # 根据模型名自动推断
        max_context_tokens = _infer_context_tokens_for_model(model)
    else:
        max_context_tokens = _DEFAULT_CONTEXT_TOKENS
    return _ContextOptimizationConfig(
        max_context_tokens=max_context_tokens,
        prompt_cache_key_enabled=_parse_bool(
            os.environ.get("EXCELMANUS_PROMPT_CACHE_KEY_ENABLED"),
            "EXCELMANUS_PROMPT_CACHE_KEY_ENABLED",
            True,
        ),
        summarization_enabled=_parse_bool(
            os.environ.get("EXCELMANUS_SUMMARIZATION_ENABLED"),
            "EXCELMANUS_SUMMARIZATION_ENABLED",
            True,
        ),
        summarization_threshold_ratio=_parse_float_between_zero_and_one(
            os.environ.get("EXCELMANUS_SUMMARIZATION_THRESHOLD_RATIO"),
            "EXCELMANUS_SUMMARIZATION_THRESHOLD_RATIO",
            0.8,
        ),
        summarization_keep_recent_turns=_parse_int(
            os.environ.get("EXCELMANUS_SUMMARIZATION_KEEP_RECENT_TURNS"),
            "EXCELMANUS_SUMMARIZATION_KEEP_RECENT_TURNS",
            3,
        ),
        compaction_enabled=_parse_bool(
            os.environ.get("EXCELMANUS_COMPACTION_ENABLED"),
            "EXCELMANUS_COMPACTION_ENABLED",
            True,
        ),
        compaction_threshold_ratio=_parse_float_between_zero_and_one(
            os.environ.get("EXCELMANUS_COMPACTION_THRESHOLD_RATIO"),
            "EXCELMANUS_COMPACTION_THRESHOLD_RATIO",
            0.85,
        ),
        compaction_keep_recent_turns=_parse_int(
            os.environ.get("EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS"),
            "EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS",
            5,
        ),
        compaction_max_summary_tokens=_parse_int(
            os.environ.get("EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS"),
            "EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS",
            1500,
        ),
    )


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
        os.environ.get("EXCELMANUS_MAX_ITERATIONS"), "EXCELMANUS_MAX_ITERATIONS", 50
    )
    max_consecutive_failures = _parse_int(
        os.environ.get("EXCELMANUS_MAX_CONSECUTIVE_FAILURES"),
        "EXCELMANUS_MAX_CONSECUTIVE_FAILURES",
        6,
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
    skills_discovery_scan_external_tool_dirs = _parse_bool(
        os.environ.get("EXCELMANUS_SKILLS_DISCOVERY_SCAN_EXTERNAL_TOOL_DIRS"),
        "EXCELMANUS_SKILLS_DISCOVERY_SCAN_EXTERNAL_TOOL_DIRS",
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
    cors_allow_origins = _parse_cors_allow_origins()
    mcp_shared_manager = _parse_bool(
        os.environ.get("EXCELMANUS_MCP_SHARED_MANAGER"),
        "EXCELMANUS_MCP_SHARED_MANAGER",
        False,
    )

    # AUX 配置（统一配置）
    aux_api_key = os.environ.get("EXCELMANUS_AUX_API_KEY") or None
    aux_base_url = os.environ.get("EXCELMANUS_AUX_BASE_URL") or None
    if aux_base_url:
        _validate_base_url(aux_base_url)
    aux_model = os.environ.get("EXCELMANUS_AUX_MODEL") or None

    # subagent 执行配置
    subagent_enabled = _parse_bool(
        os.environ.get("EXCELMANUS_SUBAGENT_ENABLED"),
        "EXCELMANUS_SUBAGENT_ENABLED",
        True,
    )
    parallel_readonly_tools = _parse_bool(
        os.environ.get("EXCELMANUS_PARALLEL_READONLY_TOOLS"),
        "EXCELMANUS_PARALLEL_READONLY_TOOLS",
        True,
    )
    prefetch_explorer = _parse_bool(
        os.environ.get("EXCELMANUS_PREFETCH_EXPLORER"),
        "EXCELMANUS_PREFETCH_EXPLORER",
        True,
    )
    subagent_max_iterations = _parse_int(
        os.environ.get("EXCELMANUS_SUBAGENT_MAX_ITERATIONS"),
        "EXCELMANUS_SUBAGENT_MAX_ITERATIONS",
        120,
    )
    subagent_max_consecutive_failures = _parse_int(
        os.environ.get("EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES"),
        "EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES",
        6,
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
    memory_auto_extract_interval = _parse_int(
        os.environ.get("EXCELMANUS_MEMORY_AUTO_EXTRACT_INTERVAL"),
        "EXCELMANUS_MEMORY_AUTO_EXTRACT_INTERVAL",
        15,
    )
    context_optimization = _load_context_optimization_config(model=model)
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
        2,
    )
    window_perception_suspend_after_idle = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_SUSPEND_AFTER_IDLE"),
        "EXCELMANUS_WINDOW_PERCEPTION_SUSPEND_AFTER_IDLE",
        5,
    )
    window_perception_terminate_after_idle = _parse_int(
        os.environ.get("EXCELMANUS_WINDOW_PERCEPTION_TERMINATE_AFTER_IDLE"),
        "EXCELMANUS_WINDOW_PERCEPTION_TERMINATE_AFTER_IDLE",
        8,
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

    # VLM 独立模型配置（可选）
    vlm_api_key = os.environ.get("EXCELMANUS_VLM_API_KEY") or None
    vlm_base_url = os.environ.get("EXCELMANUS_VLM_BASE_URL") or None
    if vlm_base_url:
        _validate_base_url(vlm_base_url)
    vlm_model = os.environ.get("EXCELMANUS_VLM_MODEL") or None
    vlm_enhance = _parse_bool(
        os.environ.get("EXCELMANUS_VLM_ENHANCE"),
        "EXCELMANUS_VLM_ENHANCE",
        True,
    )
    main_model_vision = (
        os.environ.get("EXCELMANUS_MAIN_MODEL_VISION", "auto").strip().lower()
    )
    if main_model_vision not in ("auto", "true", "false"):
        logger.warning(
            "EXCELMANUS_MAIN_MODEL_VISION=%r 无效，回退到 'auto'",
            main_model_vision,
        )
        main_model_vision = "auto"

    # 备份沙盒模式
    backup_enabled = _parse_bool(
        os.environ.get("EXCELMANUS_BACKUP_ENABLED"),
        "EXCELMANUS_BACKUP_ENABLED",
        True,
    )

    # 轮次 checkpoint 模式
    checkpoint_enabled = _parse_bool(
        os.environ.get("EXCELMANUS_CHECKPOINT_ENABLED"),
        "EXCELMANUS_CHECKPOINT_ENABLED",
        False,
    )

    # 代码策略引擎配置
    code_policy_enabled = _parse_bool(
        os.environ.get("EXCELMANUS_CODE_POLICY_ENABLED"),
        "EXCELMANUS_CODE_POLICY_ENABLED",
        True,
    )
    code_policy_green_auto_approve = _parse_bool(
        os.environ.get("EXCELMANUS_CODE_POLICY_GREEN_AUTO"),
        "EXCELMANUS_CODE_POLICY_GREEN_AUTO",
        True,
    )
    code_policy_yellow_auto_approve = _parse_bool(
        os.environ.get("EXCELMANUS_CODE_POLICY_YELLOW_AUTO"),
        "EXCELMANUS_CODE_POLICY_YELLOW_AUTO",
        True,
    )
    code_policy_extra_safe_modules = _parse_csv_tuple(
        os.environ.get("EXCELMANUS_CODE_POLICY_EXTRA_SAFE")
    )
    code_policy_extra_blocked_modules = _parse_csv_tuple(
        os.environ.get("EXCELMANUS_CODE_POLICY_EXTRA_BLOCKED")
    )
    tool_schema_validation_mode = _parse_tool_schema_validation_mode(
        os.environ.get("EXCELMANUS_TOOL_SCHEMA_VALIDATION_MODE")
    )
    tool_schema_validation_canary_percent = _parse_int_allow_zero(
        os.environ.get("EXCELMANUS_TOOL_SCHEMA_VALIDATION_CANARY_PERCENT"),
        "EXCELMANUS_TOOL_SCHEMA_VALIDATION_CANARY_PERCENT",
        100,
    )
    if tool_schema_validation_canary_percent > 100:
        raise ConfigError(
            "配置项 EXCELMANUS_TOOL_SCHEMA_VALIDATION_CANARY_PERCENT 必须在 0..100，"
            f"当前值: {tool_schema_validation_canary_percent}"
        )
    tool_schema_strict_path = _parse_bool(
        os.environ.get("EXCELMANUS_TOOL_SCHEMA_STRICT_PATH"),
        "EXCELMANUS_TOOL_SCHEMA_STRICT_PATH",
        False,
    )

    # Embedding 语义检索配置（需独立配置 API，未配置时功能关闭）
    embedding_api_key = os.environ.get("EXCELMANUS_EMBEDDING_API_KEY") or None
    embedding_base_url = os.environ.get("EXCELMANUS_EMBEDDING_BASE_URL") or None
    if embedding_base_url:
        _validate_base_url(embedding_base_url)
    # 自动启用：配置了 API key 或 base_url 即视为启用；也可显式覆盖
    _embedding_explicit = os.environ.get("EXCELMANUS_EMBEDDING_ENABLED")
    if _embedding_explicit is not None:
        embedding_enabled = _parse_bool(
            _embedding_explicit, "EXCELMANUS_EMBEDDING_ENABLED", False,
        )
    else:
        embedding_enabled = bool(embedding_api_key or embedding_base_url)
    embedding_model = (
        os.environ.get("EXCELMANUS_EMBEDDING_MODEL")
        or DEFAULT_EMBEDDING_MODEL
    )
    embedding_dimensions = _parse_int(
        os.environ.get("EXCELMANUS_EMBEDDING_DIMENSIONS"),
        "EXCELMANUS_EMBEDDING_DIMENSIONS",
        DEFAULT_EMBEDDING_DIMENSIONS,
    )
    embedding_timeout_seconds = float(
        os.environ.get("EXCELMANUS_EMBEDDING_TIMEOUT_SECONDS", "30.0")
    )
    memory_semantic_top_k = _parse_int(
        os.environ.get("EXCELMANUS_MEMORY_SEMANTIC_TOP_K"),
        "EXCELMANUS_MEMORY_SEMANTIC_TOP_K",
        10,
    )
    memory_semantic_threshold = _parse_threshold(
        os.environ.get("EXCELMANUS_MEMORY_SEMANTIC_THRESHOLD"), 0.3
    )
    memory_semantic_fallback_recent = _parse_int(
        os.environ.get("EXCELMANUS_MEMORY_SEMANTIC_FALLBACK_RECENT"),
        "EXCELMANUS_MEMORY_SEMANTIC_FALLBACK_RECENT",
        5,
    )
    manifest_semantic_top_k = _parse_int(
        os.environ.get("EXCELMANUS_MANIFEST_SEMANTIC_TOP_K"),
        "EXCELMANUS_MANIFEST_SEMANTIC_TOP_K",
        5,
    )
    manifest_semantic_threshold = _parse_threshold(
        os.environ.get("EXCELMANUS_MANIFEST_SEMANTIC_THRESHOLD"), 0.25
    )

    # 聊天记录持久化
    chat_history_enabled = _parse_bool(
        os.environ.get("EXCELMANUS_CHAT_HISTORY_ENABLED"),
        "EXCELMANUS_CHAT_HISTORY_ENABLED",
        True,
    )
    db_path = os.environ.get(
        "EXCELMANUS_DB_PATH", "~/.excelmanus/excelmanus.db"
    )
    database_url = os.environ.get("EXCELMANUS_DATABASE_URL", "")
    chat_history_db_path = os.environ.get(
        "EXCELMANUS_CHAT_HISTORY_DB_PATH", ""
    )

    # CLI 布局模式
    cli_layout_mode = _parse_cli_layout_mode(
        os.environ.get("EXCELMANUS_CLI_LAYOUT_MODE")
    )

    # 文本回复门禁模式
    guard_mode = (os.environ.get("EXCELMANUS_GUARD_MODE", "off").strip().lower())
    if guard_mode not in ("off", "soft"):
        logger.warning("EXCELMANUS_GUARD_MODE=%r 无效，回退到 'off'", guard_mode)
        guard_mode = "off"

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
        skills_discovery_scan_external_tool_dirs=skills_discovery_scan_external_tool_dirs,
        skills_discovery_extra_dirs=skills_discovery_extra_dirs,
        system_message_mode=system_message_mode,
        tool_result_hard_cap_chars=tool_result_hard_cap_chars,
        large_excel_threshold_bytes=large_excel_threshold_bytes,
        external_safe_mode=external_safe_mode,
        cors_allow_origins=cors_allow_origins,
        mcp_shared_manager=mcp_shared_manager,
        aux_api_key=aux_api_key,
        aux_base_url=aux_base_url,
        aux_model=aux_model,
        subagent_enabled=subagent_enabled,
        parallel_readonly_tools=parallel_readonly_tools,
        prefetch_explorer=prefetch_explorer,
        subagent_max_iterations=subagent_max_iterations,
        subagent_max_consecutive_failures=subagent_max_consecutive_failures,
        subagent_user_dir=subagent_user_dir,
        subagent_project_dir=subagent_project_dir,
        memory_enabled=memory_enabled,
        memory_dir=memory_dir,
        memory_auto_load_lines=memory_auto_load_lines,
        memory_auto_extract_interval=memory_auto_extract_interval,
        max_context_tokens=context_optimization.max_context_tokens,
        prompt_cache_key_enabled=context_optimization.prompt_cache_key_enabled,
        summarization_enabled=context_optimization.summarization_enabled,
        summarization_threshold_ratio=context_optimization.summarization_threshold_ratio,
        summarization_keep_recent_turns=context_optimization.summarization_keep_recent_turns,
        compaction_enabled=context_optimization.compaction_enabled,
        compaction_threshold_ratio=context_optimization.compaction_threshold_ratio,
        compaction_keep_recent_turns=context_optimization.compaction_keep_recent_turns,
        compaction_max_summary_tokens=context_optimization.compaction_max_summary_tokens,
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
        vlm_api_key=vlm_api_key,
        vlm_base_url=vlm_base_url,
        vlm_model=vlm_model,
        vlm_enhance=vlm_enhance,
        main_model_vision=main_model_vision,
        backup_enabled=backup_enabled,
        checkpoint_enabled=checkpoint_enabled,
        code_policy_enabled=code_policy_enabled,
        code_policy_green_auto_approve=code_policy_green_auto_approve,
        code_policy_yellow_auto_approve=code_policy_yellow_auto_approve,
        code_policy_extra_safe_modules=code_policy_extra_safe_modules,
        code_policy_extra_blocked_modules=code_policy_extra_blocked_modules,
        tool_schema_validation_mode=tool_schema_validation_mode,
        tool_schema_validation_canary_percent=tool_schema_validation_canary_percent,
        tool_schema_strict_path=tool_schema_strict_path,
        embedding_enabled=embedding_enabled,
        embedding_api_key=embedding_api_key,
        embedding_base_url=embedding_base_url,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
        embedding_timeout_seconds=embedding_timeout_seconds,
        memory_semantic_top_k=memory_semantic_top_k,
        memory_semantic_threshold=memory_semantic_threshold,
        memory_semantic_fallback_recent=memory_semantic_fallback_recent,
        manifest_semantic_top_k=manifest_semantic_top_k,
        manifest_semantic_threshold=manifest_semantic_threshold,
        db_path=db_path,
        database_url=database_url,
        chat_history_enabled=chat_history_enabled,
        chat_history_db_path=chat_history_db_path,
        cli_layout_mode=cli_layout_mode,
        guard_mode=guard_mode,
        models=models,
    )
