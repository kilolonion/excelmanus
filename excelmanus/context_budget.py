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

    __slots__ = ("_base_tokens", "_model_tokens", "_override_tokens", "_override_is_adaptive")

    _DEFAULT_TOKENS = 128_000

    def __init__(self, *, base_tokens: int = 0, model: str = "") -> None:
        self._base_tokens = max(0, base_tokens)
        self._model_tokens = (
            _infer_context_tokens_for_model(model) if model else 0
        )
        self._override_tokens = 0
        self._override_is_adaptive = False

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
        """切换模型时调用（同步），更新推断值并返回新的 max_tokens。"""
        old = self.max_tokens
        self._model_tokens = _infer_context_tokens_for_model(model)
        # 自适应缩减过的 override 在模型切换时应清除（新模型可能有不同的窗口）
        if self._override_tokens > 0:
            logger.info("模型切换，清除之前的自适应 override（%d tokens）", self._override_tokens)
            self._override_tokens = 0
        new = self.max_tokens
        if new != old:
            logger.info(
                "上下文窗口已更新: %d → %d tokens (model=%s)",
                old, new, model,
            )
        return new

    async def update_for_model_async(
        self, model: str, client: object = None, base_url: str = "",
    ) -> int:
        """切换模型时调用（异步），先尝试 API 查询再回退到静态推断。

        比 update_for_model 更精确：若 provider 支持 /models API，
        可获取真实的 context_window 值而非依赖硬编码映射表。
        """
        old = self.max_tokens
        # 清除之前的自适应 override
        if self._override_tokens > 0:
            logger.info("模型切换，清除之前的自适应 override（%d tokens）", self._override_tokens)
            self._override_tokens = 0

        api_tokens: int | None = None
        if client is not None:
            try:
                from excelmanus.model_probe import query_model_context_window
                api_tokens = await query_model_context_window(
                    client, model, base_url, timeout=8.0,
                )
            except Exception:
                logger.debug("API 元数据查询异常，回退到静态推断", exc_info=True)

        if api_tokens is not None and api_tokens > 0:
            self._model_tokens = api_tokens
        else:
            self._model_tokens = _infer_context_tokens_for_model(model)

        new = self.max_tokens
        if new != old:
            logger.info(
                "上下文窗口已更新: %d → %d tokens (model=%s%s)",
                old, new, model,
                ", 来源=API" if api_tokens else ", 来源=推断",
            )
        return new

    def set_override(self, tokens: int, *, adaptive: bool = False) -> None:
        """运行时临时覆盖。adaptive=True 表示系统自适应缩减（非用户显式锁定）。"""
        self._override_tokens = max(0, tokens)
        self._override_is_adaptive = adaptive

    def clear_override(self) -> None:
        self._override_tokens = 0
        self._override_is_adaptive = False

    @property
    def model_tokens(self) -> int:
        """当前模型推断的上下文窗口大小（仅供诊断）。"""
        return self._model_tokens

    @property
    def is_user_overridden(self) -> bool:
        """用户是否显式锁定了上下文大小（环境变量或手动 /context 命令）。

        系统自适应缩减（adaptive override）不算用户锁定。
        """
        if self._base_tokens > 0:
            return True
        if self._override_tokens > 0 and not self._override_is_adaptive:
            return True
        return False
