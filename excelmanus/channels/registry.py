"""渠道注册表：按名称注册和获取渠道适配器。"""

from __future__ import annotations

from typing import Type

from excelmanus.channels.base import ChannelAdapter
from excelmanus.logger import get_logger

logger = get_logger("channels.registry")


class ChannelRegistry:
    """渠道适配器工厂注册表。

    用法::

        registry = ChannelRegistry()
        registry.register("telegram", TelegramAdapter)
        adapter = registry.create("telegram", token="xxx", api_url="http://...")
    """

    def __init__(self) -> None:
        self._adapters: dict[str, Type[ChannelAdapter]] = {}

    def register(self, name: str, adapter_cls: Type[ChannelAdapter]) -> None:
        """注册渠道适配器类。"""
        if name in self._adapters:
            logger.warning("覆盖已注册的渠道适配器: %s", name)
        self._adapters[name] = adapter_cls
        logger.info("注册渠道适配器: %s -> %s", name, adapter_cls.__name__)

    def create(self, name: str, **kwargs) -> ChannelAdapter:
        """按名称创建渠道适配器实例。

        Raises:
            KeyError: 未注册的渠道名称。
        """
        cls = self._adapters.get(name)
        if cls is None:
            available = ", ".join(sorted(self._adapters.keys())) or "(无)"
            raise KeyError(
                f"未注册的渠道: {name!r}。可用渠道: {available}"
            )
        return cls(**kwargs)

    @property
    def available(self) -> list[str]:
        """已注册的渠道名称列表。"""
        return sorted(self._adapters.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._adapters


# 全局单例
_global_registry = ChannelRegistry()


def get_global_registry() -> ChannelRegistry:
    """获取全局渠道注册表。"""
    return _global_registry
