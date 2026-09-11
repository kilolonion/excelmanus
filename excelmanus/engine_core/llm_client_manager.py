"""LLMClientManager — 统一管理 main / AUX LLM 客户端配置。

从 AgentEngine.__init__ 和 update_aux_config 提取，消除重复的客户端创建逻辑。
AUX 服务子代理默认模型与压缩等附属任务，不再另开顾问槽位。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from excelmanus.logger import get_logger
from excelmanus.providers import create_client

if TYPE_CHECKING:
    from excelmanus.config import ExcelManusConfig

logger = get_logger("llm_client_manager")


@dataclass
class _ClientSlot:
    """单个 LLM 客户端槽位。"""

    client: Any  # openai.AsyncOpenAI | openai.OpenAI
    model: str
    follow_active_model: bool = False


class LLMClientManager:
    """统一管理 main / AUX LLM 客户端。

    职责：
    - 根据 ExcelManusConfig 初始化客户端
    - 提供热更新 AUX 配置的方法（消除 __init__ 与 update_aux_config 的重复逻辑）
    - 提供热切换主模型的方法
    """

    __slots__ = (
        "_main_client", "_main_model", "_main_api_key", "_main_base_url", "_main_protocol",
        "_router_slot",
        "_active_model", "_active_api_key", "_active_base_url", "_active_protocol",
        "_active_model_name",
    )

    def __init__(self, config: "ExcelManusConfig") -> None:
        # ── 主模型 ──
        self._main_client = create_client(
            api_key=config.api_key,
            base_url=config.base_url,
            protocol=config.protocol,
            model=config.model,
        )
        self._main_model = config.model
        self._main_api_key = config.api_key
        self._main_base_url = config.base_url
        self._main_protocol = config.protocol

        # ── 活跃模型（可热切换） ──
        self._active_model = config.model
        self._active_api_key = config.api_key
        self._active_base_url = config.base_url
        self._active_protocol = config.protocol
        self._active_model_name: str | None = None

        # ── AUX（子代理默认模型、上下文压缩等） ──
        self._router_slot = self._build_aux_slot(
            config,
            main_client=self._main_client,
            active_model=config.model,
            active_protocol=config.protocol,
        )

    # ── 公共属性 ──────────────────────────────────────────

    @property
    def main_client(self) -> Any:
        return self._main_client

    @property
    def router_client(self) -> Any:
        return self._router_slot.client

    @property
    def router_model(self) -> str:
        return self._router_slot.model

    @property
    def router_follow_active_model(self) -> bool:
        return self._router_slot.follow_active_model

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

    # ── 热更新 ────────────────────────────────────────────

    def update_aux(
        self,
        config: "ExcelManusConfig",
        *,
        aux_enabled: bool = True,
        aux_model: str | None = None,
        aux_api_key: str | None = None,
        aux_base_url: str | None = None,
    ) -> None:
        """热更新 AUX 配置（子代理默认模型、上下文压缩等附属任务）。"""
        self._router_slot = self._build_aux_slot(
            config,
            main_client=self._main_client,
            active_model=self._active_model,
            active_protocol=self._active_protocol,
            aux_enabled_override=aux_enabled,
            aux_model_override=aux_model,
            aux_api_key_override=aux_api_key,
            aux_base_url_override=aux_base_url,
        )
        logger.info(
            "AUX 配置热更新: enabled=%s, model=%s, base_url=%s",
            aux_enabled,
            aux_model or "(跟随主模型)",
            aux_base_url or "(跟随主模型)",
        )

    def switch_active_model(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        protocol: str,
        name: str | None = None,
    ) -> None:
        """切换活跃模型，更新主客户端。"""
        self._main_client = create_client(
            api_key=api_key,
            base_url=base_url,
            protocol=protocol,
            model=model,
        )
        self._main_model = model
        self._main_api_key = api_key
        self._main_base_url = base_url
        self._main_protocol = protocol
        self._active_model = model
        self._active_api_key = api_key
        self._active_base_url = base_url
        self._active_protocol = protocol
        self._active_model_name = name

        # 跟随主模型的槽位需要更新
        if self._router_slot.follow_active_model:
            self._router_slot = _ClientSlot(
                client=self._main_client,
                model=model,
                follow_active_model=True,
            )

    # ── 内部构建方法 ──────────────────────────────────────

    @staticmethod
    def _build_aux_slot(
        config: "ExcelManusConfig",
        *,
        main_client: Any,
        active_model: str,
        active_protocol: str,
        aux_enabled_override: bool | None = None,
        aux_model_override: str | None = None,
        aux_api_key_override: str | None = None,
        aux_base_url_override: str | None = None,
    ) -> _ClientSlot:
        """构建 router 客户端槽位。"""
        _aux_enabled = aux_enabled_override if aux_enabled_override is not None else config.aux_enabled
        _aux_model = aux_model_override if aux_model_override is not None else config.aux_model
        _aux_effective = _aux_enabled and bool(_aux_model)

        _aux_api_key = (aux_api_key_override or config.aux_api_key) or config.api_key
        _aux_base_url = (aux_base_url_override or config.aux_base_url) or config.base_url
        _aux_protocol = config.aux_protocol if _aux_effective else active_protocol

        if _aux_effective:
            return _ClientSlot(
                client=create_client(
                    api_key=_aux_api_key,
                    base_url=_aux_base_url,
                    protocol=_aux_protocol,
                    model=_aux_model or active_model,
                ),
                model=_aux_model or active_model,
                follow_active_model=False,
            )
        return _ClientSlot(
            client=main_client,
            model=active_model,
            follow_active_model=True,
        )
