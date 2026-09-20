"""当前模型视觉能力推断：关键词 + probe 交叉验证。

图片只交给当前激活模型。probe=False 不覆盖已知视觉模型的关键词推断，
避免 Codex 等 backend-api 的探测误判把图片拦掉。

关键词按 token 序列匹配（归一化后以 "-" 为边界），
支持 provider/ 前缀与 Bedrock 命名空间写法。
"""

from __future__ import annotations

from excelmanus.logger import get_logger
from excelmanus.model_identity import (
    matches_token_sequence,
    token_sequence_pattern,
)

logger = get_logger("vision_capability")

# 命中即判定为非视觉模型（优先级高于视觉关键词）
_NON_VISION_KEYWORDS = (
    "o1-mini", "o1-preview", "o3-mini",
    "nova-micro", "nova-sonic", "nova-2-sonic",
    "gemini-embedding",
    "llama-3.2-1b", "llama-3.2-3b",
    "step-3.5-flash",
    "mistral-small-3.0", "mistral-small-3.1",
    "gemma-3-1b",
    "embedding", "embed", "rerank", "tts", "whisper",
    "transcribe", "realtime", "moderation", "audio",
)

# 命中即疑似视觉模型
_VISION_KEYWORDS = (
    "gpt-4o", "gpt-4.1", "gpt-4-turbo", "chatgpt-4o",
    "gpt-5", "gpt-6",
    "gpt-image-1",
    "o1", "o3", "o4",
    "grok-4",
    "claude-opus", "claude-sonnet", "claude-haiku",
    "claude-fable", "claude-mythos",
    "gemini",
    "nova-lite", "nova-pro", "nova-premier", "nova-2-lite",
    # 通用 token：覆盖各家的 -vl / -vision / -multimodal / -omni 变体
    "vl", "vision", "multimodal", "omni",
    "qwen3.8",
    "qwen3.7-plus", "qwen3.7-flash", "qwen3.7-max",
    "qwen3.6-plus", "qwen3.6-flash", "qwen3.6-max",
    "qwen3.5-plus", "qwen3.5-flash",
    "qwen3-max",
    "qvq",
    "deepseek-flash", "deepseek-v4-flash", "deepseek-v4.1",
    "janus-pro",
    "llama-3.2", "llama-4",
    "pixtral",
    "ministral-3b", "ministral-8b", "ministral-14b",
    "mistral-small-3", "mistral-medium-3", "mistral-large-3",
    "glm-4v", "glm-4.1v", "glm-4.5v", "glm-4.6v", "glm-5v", "glm-5",
    "internvl",
    "minicpm-v", "minicpm-o",
    "kimi-k3", "kimi-k2.5", "kimi-k2.6", "kimi-k2.7",
    "doubao-seed-1.6", "doubao-seed-2",
    "minimax-m3",
    "step-1v", "step-1.5v", "step-3", "step-r1-v-mini",
    "llava",
    "gemma-3", "gemma-3n",
)

_NON_VISION_PATTERN = token_sequence_pattern(_NON_VISION_KEYWORDS)
_VISION_PATTERN = token_sequence_pattern(_VISION_KEYWORDS)


def _keyword_hint(model: str) -> bool | None:
    """关键词命中：NON→False，VISION→True，未命中→None。"""
    if matches_token_sequence(model, _NON_VISION_PATTERN):
        return False
    if matches_token_sequence(model, _VISION_PATTERN):
        return True
    return None


def keyword_implies_vision(model: str) -> bool:
    """仅按模型 ID 关键词判断是否像视觉模型。"""
    return _keyword_hint(model) is True


def infer_vision_capable(
    model: str,
    *,
    override: str = "auto",
    probe: bool | None = None,
) -> bool:
    """推断当前模型是否支持视觉输入。

    优先级：手动覆盖 > 关键词+probe 交叉验证 > 关键词推断。
    """
    if override == "true":
        return True
    if override == "false":
        return False

    hint = _keyword_hint(model)
    if hint is False:
        logger.info("视觉能力来自关键词推断 (NON_VISION): model=%s → False", model)
        return False
    keyword_vision = hint is True

    if probe is True:
        logger.info("视觉能力来自 probe 检测结果: model=%s, vision=True", model)
        return True
    if probe is False:
        if not keyword_vision:
            logger.info("视觉能力来自 probe 检测结果: model=%s, vision=False", model)
            return False
        logger.warning(
            "probe 标记 vision=False 但关键词推断为 True，"
            "信任关键词推断: model=%s。"
            "若确实不支持视觉，请设置 EXCELMANUS_MAIN_MODEL_VISION=false",
            model,
        )
        return True

    logger.info("视觉能力来自关键词推断: model=%s → %s", model, keyword_vision)
    return keyword_vision
