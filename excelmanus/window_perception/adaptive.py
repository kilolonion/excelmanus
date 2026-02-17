"""WURM adaptive 模式选择与降级状态机。"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)

_MODE_ORDER = ("unified", "anchored", "enriched")
_DEFAULT_MODE = "anchored"


@dataclass
class AdaptiveModeSelector:
    """根据模型与会话状态选择运行模式，并维护降级状态。"""

    model_mode_overrides: dict[str, str] = field(default_factory=dict)
    current_mode: str | None = None
    consecutive_ingest_failures: int = 0
    requested_mode: str = _DEFAULT_MODE

    _DEFAULT_PREFIX_MAP: tuple[tuple[str, str], ...] = (
        ("gpt-5", "unified"),
        ("gpt-4", "unified"),
        ("moonshotai/kimi", "anchored"),
        ("kimi", "anchored"),
        ("claude-sonnet", "anchored"),
        ("sonnet", "anchored"),
        ("deepseek", "anchored"),
    )

    def __post_init__(self) -> None:
        self.model_mode_overrides = self._normalize_overrides(self.model_mode_overrides)

    def select_mode(self, *, model_id: str, requested_mode: str) -> str:
        """解析当前调用应使用的运行模式。"""
        normalized_requested = self._normalize_requested_mode(requested_mode)
        self.requested_mode = normalized_requested
        if normalized_requested != "adaptive":
            return normalized_requested

        if self.current_mode is None:
            self.current_mode = self._resolve_initial_mode(model_id)
            logger.info(
                "WURM adaptive 初次选模: model=%s -> mode=%s",
                model_id or "(empty)",
                self.current_mode,
            )
        return self.current_mode

    def downgrade(self, *, reason: str) -> str:
        """按 unified->anchored->enriched 链路降级。"""
        previous = self.current_mode or _DEFAULT_MODE
        if previous not in _MODE_ORDER:
            previous = _DEFAULT_MODE
        index = _MODE_ORDER.index(previous)
        target = _MODE_ORDER[min(index + 1, len(_MODE_ORDER) - 1)]
        self.current_mode = target
        logger.warning(
            "WURM adaptive 会话降级: %s -> %s (reason=%s)",
            previous,
            target,
            reason,
        )
        return target

    def mark_ingest_success(self) -> None:
        """ingest 成功后清零失败计数。"""
        self.consecutive_ingest_failures = 0

    def mark_ingest_failure(self) -> bool:
        """ingest 失败计数 +1，达到阈值时降级。"""
        self.consecutive_ingest_failures += 1
        if self.consecutive_ingest_failures < 2:
            return False
        self.consecutive_ingest_failures = 0
        self.downgrade(reason="ingest_failures")
        return True

    def mark_repeat_tripwire(self) -> str:
        """循环检测触发时立即降级。"""
        return self.downgrade(reason="repeat_tripwire")

    def reset(self) -> None:
        """重置会话状态。"""
        self.current_mode = None
        self.consecutive_ingest_failures = 0
        self.requested_mode = _DEFAULT_MODE

    def _resolve_initial_mode(self, model_id: str) -> str:
        normalized = str(model_id or "").strip().lower()
        if not normalized:
            return _DEFAULT_MODE

        # override 优先，按最长前缀匹配
        override_mode = self._match_prefix(self.model_mode_overrides, normalized)
        if override_mode:
            return override_mode

        # 默认映射，按最长前缀匹配
        default_map = {prefix: mode for prefix, mode in self._DEFAULT_PREFIX_MAP}
        matched = self._match_prefix(default_map, normalized)
        return matched or _DEFAULT_MODE

    @staticmethod
    def _match_prefix(prefix_map: dict[str, str], model_id: str) -> str:
        candidates = [
            (prefix, mode)
            for prefix, mode in prefix_map.items()
            if model_id.startswith(prefix)
        ]
        if not candidates:
            return ""
        candidates.sort(key=lambda item: len(item[0]), reverse=True)
        return candidates[0][1]

    @staticmethod
    def _normalize_requested_mode(requested_mode: str) -> str:
        value = str(requested_mode or _DEFAULT_MODE).strip().lower()
        if value in {"adaptive", "unified", "anchored", "enriched"}:
            return value
        return _DEFAULT_MODE

    @staticmethod
    def _normalize_overrides(raw: dict[str, str] | None) -> dict[str, str]:
        if not isinstance(raw, dict):
            return {}
        normalized: dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            normalized_key = key.strip().lower()
            normalized_value = value.strip().lower()
            if not normalized_key:
                continue
            if normalized_value not in {"unified", "anchored", "enriched"}:
                continue
            normalized[normalized_key] = normalized_value
        return normalized
