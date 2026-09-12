"""当前模型视觉能力推断：关键词 + probe 交叉验证。

图片只交给当前激活模型。probe=False 不覆盖已知视觉模型的关键词推断，
避免 Codex 等 backend-api 的探测误判把图片拦掉。
"""

from __future__ import annotations

from excelmanus.logger import get_logger

logger = get_logger("vision_capability")

_NON_VISION_KEYWORDS = (
    "o3-mini",
    "amazon.nova-micro", "amazon.nova-sonic",
    "gemini-embedding",
    "llama-3.2-1b", "llama-3.2-3b",
    "step-3.5-flash",
    "mistral-small-3.0", "mistral-small-3.1",
)

_VISION_KEYWORDS = (
    "gpt-4o", "gpt-4.1",
    "gpt-5",
    "gpt-6",
    "gpt-image-1",
    "o1", "o3", "o4",
    "grok-2-vision", "grok-4",
    "claude-opus-", "claude-sonnet-", "claude-haiku-",
    "claude-opus-4", "claude-sonnet-4", "claude-haiku-4",
    "claude-fable", "claude-mythos",
    "gemini",
    "amazon.nova", "nova-lite", "nova-pro", "nova-premier",
    "-vl", "-vision", "-multimodal",
    "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl", "qwen3.5-vl",
    "qwen-omni", "qwen2.5-omni", "qwen3-omni",
    "qwen3.8",
    "qwen3.7-plus", "qwen3.7-flash", "qwen3.7-max",
    "qwen3.6-plus", "qwen3.6-flash",
    "qwen3.5-plus", "qwen3.5-flash",
    "qvq-",
    "deepseek-flash",
    "deepseek-v4-flash",
    "deepseek-v4.1",
    "deepseek-vl",
    "janus-pro",
    "llama-3.2-", "llama3.2-vision",
    "llama-4-", "llama4-",
    "pixtral",
    "ministral-3b", "ministral-8b", "ministral-14b",
    "mistral-small-3", "mistral-medium-3", "mistral-large-3",
    "phi-3-vision", "phi-3.5-vision", "phi-4-multimodal",
    "glm-4v", "glm-4.1v", "glm-4.5v", "glm-4.6v", "glm-5",
    "internvl",
    "minicpm-v",
    "minicpm-o",
    "ernie-4.5-vl", "ernie-vl",
    "command-a-vision",
    "aya-vision",
    "moonshot-v1-vision", "kimi-vl", "kimi-k3", "kimi-k2.5", "kimi-k2.6", "kimi-k2.7",
    "yi-vl",
    "doubao-1.5-vision", "doubao-1.6-vision", "doubao-vision", "seed1.5-vl", "seed-vl",
    "doubao-seed-2",
    "hunyuan-vision",
    "minimax-vl", "minimax-m3",
    "step-1v", "step-1.5v", "step-3",
    "step-r1-v-mini", "step-1o-vision", "step-1o-turbo-vision",
    "llava",
)


def keyword_implies_vision(model: str) -> bool:
    """仅按模型 ID 关键词判断是否像视觉模型。"""
    model_lower = (model or "").lower()
    if any(kw in model_lower for kw in _NON_VISION_KEYWORDS):
        return False
    return any(kw in model_lower for kw in _VISION_KEYWORDS)


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

    model_lower = (model or "").lower()
    if any(kw in model_lower for kw in _NON_VISION_KEYWORDS):
        logger.info("视觉能力来自关键词推断 (NON_VISION): model=%s → False", model)
        return False

    keyword_vision = any(kw in model_lower for kw in _VISION_KEYWORDS)

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
