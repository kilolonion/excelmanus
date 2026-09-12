"""LLMClientManager — 管理当前激活模型的唯一 LLM 客户端。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from excelmanus.logger import get_logger
from excelmanus.providers import create_client

if TYPE_CHECKING:
    from excelmanus.config import ExcelManusConfig

logger = get_logger("llm_client_manager")


class LLMClientManager:
    """只维护一个激活模型客户端。"""

    __slots__ = (
        "_client",
        "_active_model",
        "_active_api_key",
        "_active_base_url",
        "_active_protocol",
        "_active_model_name",
    )

    def __init__(self, config: "ExcelManusConfig") -> None:
        self._client = create_client(
            api_key=config.api_key,
            base_url=config.base_url,
            protocol=config.protocol,
            model=config.model,
        )
        self._active_model = config.model
        self._active_api_key = config.api_key
        self._active_base_url = config.base_url
        self._active_protocol = config.protocol
        self._active_model_name: str | None = None

    @property
    def client(self) -> Any:
        return self._client

    @property
    def active_model(self) -> str:
        return self._active_model

    @property
    def active_api_key(self) -> str:
        return self._active_api_key

    @property
    def active_base_url(self) -> str:
        return self._active_base_url

    @property
    def active_protocol(self) -> str:
        return self._active_protocol

    @property
    def active_model_name(self) -> str | None:
        return self._active_model_name

    @active_model_name.setter
    def active_model_name(self, value: str | None) -> None:
        self._active_model_name = value

    def switch_active_model(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        protocol: str,
        name: str | None = None,
    ) -> None:
        """切换激活模型并重建客户端。"""
        self._client = create_client(
            api_key=api_key,
            base_url=base_url,
            protocol=protocol,
            model=model,
        )
        self._active_model = model
        self._active_api_key = api_key
        self._active_base_url = base_url
        self._active_protocol = protocol
        self._active_model_name = name
