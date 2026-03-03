"""上下文预算管理器 — 系统唯一的 max_context_tokens 来源。

切换模型时自动更新上下文窗口大小，所有消费者通过此对象读取。
"""

from __future__ import annotations

import logging

from excelmanus.config import _infer_context_tokens_for_model

logger = logging.getLogger(__name__)


class ContextBudget:
    """可变的上下文预算管理器。

    优先级：override > base(用户环境变量) > model(推断) > 128k 默认。
    """

    __slots__ = ("_base_tokens", "_model_tokens", "_override_tokens")

    _DEFAULT_TOKENS = 128_000

    def __init__(self, *, base_tokens: int = 0, model: str = "") -> None:
        self._base_tokens = max(0, base_tokens)
        self._model_tokens = (
            _infer_context_tokens_for_model(model) if model else 0
        )
        self._override_tokens = 0

    @property
    def max_tokens(self) -> int:
        """有效的上下文窗口大小。"""
        if self._override_tokens > 0:
            return self._override_tokens
        if self._base_tokens > 0:
            return self._base_tokens
        if self._model_tokens > 0:
            return self._model_tokens
        return self._DEFAULT_TOKENS

    def update_for_model(self, model: str) -> int:
        """切换模型时调用，更新推断值并返回新的 max_tokens。"""
        old = self.max_tokens
        self._model_tokens = _infer_context_tokens_for_model(model)
        new = self.max_tokens
        if new != old:
            logger.info(
                "上下文窗口已更新: %d → %d tokens (model=%s)",
                old, new, model,
            )
        return new

    def set_override(self, tokens: int) -> None:
        """运行时临时覆盖（如 /context 命令）。0 表示清除覆盖。"""
        self._override_tokens = max(0, tokens)

    def clear_override(self) -> None:
        self._override_tokens = 0

    @property
    def model_tokens(self) -> int:
        """当前模型推断的上下文窗口大小（仅供诊断）。"""
        return self._model_tokens

    @property
    def is_user_overridden(self) -> bool:
        """用户是否通过环境变量或 override 显式指定了上下文大小。"""
        return self._base_tokens > 0 or self._override_tokens > 0
