"""One replayable request series. last_accepted updates only after HTTP success."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from excelmanus.request.types import REWRITE_EVENTS, RequestHeader


class RequestSeries:
    def __init__(
        self,
        *,
        series_id: str | None = None,
        last_accepted: RequestHeader | None = None,
        events: list[dict[str, Any]] | None = None,
        pending: list[str] | None = None,
    ) -> None:
        self.series_id = series_id or uuid4().hex
        self.last_accepted = last_accepted
        self.events: list[dict[str, Any]] = list(events or [])
        self._pending: list[str] = list(pending or [])

    def note(self, kind: str, **extra: Any) -> None:
        event = {"type": kind, **extra}
        self.events.append(event)
        if kind in REWRITE_EVENTS or kind == "transport/renew" or kind == "cache/policy":
            self._pending.append(kind)

    def start_new(self, reason: str) -> None:
        self.note(reason if reason in REWRITE_EVENTS else "series/start")
        if reason not in REWRITE_EVENTS:
            self.note("series/start", reason=reason)
        self.series_id = uuid4().hex
        self.last_accepted = None

    def allows_content_rewrite(self) -> bool:
        return any(kind in REWRITE_EVENTS for kind in self._pending)

    def check_prefix(self, header: RequestHeader) -> str | None:
        """Fail-closed on unexplained content rewrite. file_id / markers ignored."""
        prev = self.last_accepted
        if prev is None:
            return None
        if header.route_fingerprint != prev.route_fingerprint:
            if "route/change" in self._pending:
                return None
            self.start_new("route/change")
            return None
        if (
            header.catalog_digest != prev.catalog_digest
            or header.tools_digest != prev.tools_digest
        ):
            # 目录/工具集漂移（工作区文件族变化、技能集合、模式切换）：
            # digest 差异即可观测因，记注后允许重写——无需每个变更点上报。
            self.note("catalog/change")
            return None
        if self.allows_content_rewrite():
            return None
        if "transport/renew" in self._pending or "cache/policy" in self._pending:
            if header.content_identity == prev.content_identity:
                return None
            if header.content_payload.startswith(prev.content_payload):
                return None
            return (
                "请求内容前缀不变量破坏：transport/cache 事件不得改写内容身份"
                f"（series={self.series_id}）。"
            )
        if header.content_payload.startswith(prev.content_payload):
            return None
        return (
            "请求内容前缀不变量破坏：当前载荷不是上一成功请求的前缀延伸"
            f"（series={self.series_id}）。"
        )

    def accept(self, header: RequestHeader) -> None:
        self.last_accepted = header
        self.events.append({"type": "request/accepted", "content_identity": header.content_identity})
        self._pending.clear()

    def fail(self, reason: str) -> None:
        self.events.append({"type": "request/failed", "reason": reason})

    def cancel(self) -> None:
        self.events.append({"type": "request/cancelled"})

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "last_accepted": self.last_accepted.to_dict() if self.last_accepted else None,
            "events": list(self.events[-32:]),
            "pending": list(self._pending),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> RequestSeries:
        if not isinstance(raw, dict) or not raw:
            series = cls()
            series.note("restore/migrate")
            series.last_accepted = None
            return series
        header = RequestHeader.from_dict(raw.get("last_accepted"))
        pending = raw.get("pending")
        events = raw.get("events")
        series = cls(
            series_id=str(raw.get("series_id") or "") or None,
            last_accepted=header,
            events=list(events) if isinstance(events, list) else None,
            pending=[str(item) for item in pending] if isinstance(pending, list) else None,
        )
        if header is None:
            series.note("restore/migrate")
            series.series_id = uuid4().hex
        return series


def series_of(engine: Any) -> RequestSeries:
    current = getattr(engine, "_request_series", None)
    if isinstance(current, RequestSeries):
        return current
    series = RequestSeries()
    engine._request_series = series
    return series
