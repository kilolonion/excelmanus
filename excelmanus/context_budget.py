"""上下文预算管理器 — 系统唯一的 max_context_tokens 来源。

切换模型时自动更新上下文窗口大小，所有消费者通过此对象读取。
"""

from __future__ import annotations

import logging

from excelmanus.config import _DEFAULT_CONTEXT_TOKENS, _infer_context_tokens_for_model
from excelmanus.model_catalog import local_context_budget

logger = logging.getLogger(__name__)


class ContextBudget:
    """可变的上下文预算管理器。

    优先级：override > profile(档案手动值) > base(全局设置) > model(推断) > 默认。
    """

    __slots__ = ("_base_tokens", "_profile_tokens", "_model_tokens", "_override_tokens", "_override_is_adaptive")

    _DEFAULT_TOKENS = _DEFAULT_CONTEXT_TOKENS

    def __init__(
        self, *, base_tokens: int = 0, model: str = "", canonical_model: str = "",
    ) -> None:
        self._base_tokens = max(0, base_tokens)
        self._profile_tokens = 0
        infer_key = model
        self._model_tokens = (
            _infer_context_tokens_for_model(infer_key) if infer_key else 0
        )
        self._override_tokens = 0
        self._override_is_adaptive = False

    @property
    def max_tokens(self) -> int:
        """有效的上下文窗口大小。"""
        if self._override_tokens > 0:
            return self._override_tokens
        if self._profile_tokens > 0:
            return self._profile_tokens
        if self._base_tokens > 0:
            return self._base_tokens
        if self._model_tokens > 0:
            return self._model_tokens
        return self._DEFAULT_TOKENS

    def update_for_model(self, model: str, canonical_model: str = "", profile_tokens: int = 0) -> int:
        """切换模型时调用（同步），更新推断值并返回新的 max_tokens。"""
        old = self.max_tokens
        self._profile_tokens = max(0, profile_tokens)
        self._model_tokens = local_context_budget(model)
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
        canonical_model: str = "",
        profile_tokens: int = 0,
    ) -> int:
        """切换模型时调用（异步），先尝试 API 查询再回退到静态推断。

        比 update_for_model 更精确：若 provider 支持 /models API，
        可获取真实的 context_window 值而非依赖硬编码映射表。
        静态推断回退优先使用 canonical_model（智能匹配绑定的规范模型名）。
        """
        old = self.max_tokens
        self._profile_tokens = max(0, profile_tokens)
        # 清除之前的自适应 override
        if self._override_tokens > 0:
            logger.info("模型切换，清除之前的自适应 override（%d tokens）", self._override_tokens)
            self._override_tokens = 0

        api_tokens: int | None = None
        if client is not None and not self._profile_tokens:
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
            self._model_tokens = local_context_budget(model, base_url)

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

    def set_base_tokens(self, tokens: int) -> int:
        """用户显式锁定上下文窗口（设置页 / 环境变量）。

        清除运行时 override（含自适应缩减）；档案手动值仍优先于全局设置。
        """
        self._base_tokens = max(0, tokens)
        self._override_tokens = 0
        self._override_is_adaptive = False
        return self.max_tokens

    def clear_override(self) -> None:
        self._override_tokens = 0
        self._override_is_adaptive = False

    @property
    def model_tokens(self) -> int:
        """当前模型推断的上下文窗口大小（仅供诊断）。"""
        return self._model_tokens

    @property
    def is_user_overridden(self) -> bool:
        """用户是否显式锁定了上下文大小（档案、全局设置或 /context 命令）。

        系统自适应缩减（adaptive override）不算用户锁定。
        """
        if self._base_tokens > 0 or self._profile_tokens > 0:
            return True
        if self._override_tokens > 0 and not self._override_is_adaptive:
            return True
        return False
