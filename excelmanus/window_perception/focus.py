"""focus_window 业务服务。"""

from __future__ import annotations

from typing import Any, Callable

from .manager import WindowPerceptionManager

RefillReader = Callable[..., dict[str, Any]]


class FocusService:
    """封装 focus_window 行为与自动补读流程。"""

    def __init__(
        self,
        *,
        manager: WindowPerceptionManager,
        refill_reader: RefillReader | None,
    ) -> None:
        self._manager = manager
        self._refill_reader = refill_reader

    def focus_window(
        self,
        *,
        window_id: str,
        action: str,
        range_ref: str | None = None,
        rows: int | None = None,
    ) -> dict[str, Any]:
        """执行 focus_window，必要时触发补读并回写窗口。"""
        action_result = self._manager.focus_window_action(
            window_id=window_id,
            action=action,
            range_ref=range_ref,
            rows=rows,
        )
        if action_result.get("status") != "needs_refill":
            return action_result

        if self._refill_reader is None:
            return {
                "status": "error",
                "message": "当前未配置补读能力，无法完成缓存缺失区域读取",
                "window_id": window_id,
            }

        file_path = str(action_result.get("file_path") or "").strip()
        sheet_name = str(action_result.get("sheet_name") or "").strip()
        target_range = str(action_result.get("range") or "").strip()
        if not file_path or not sheet_name or not target_range:
            return {
                "status": "error",
                "message": "窗口缺少 file/sheet/range 信息，无法自动补读",
                "window_id": window_id,
            }

        refill = self._refill_reader(
            file_path=file_path,
            sheet_name=sheet_name,
            range_ref=target_range,
        )
        if not isinstance(refill, dict) or not refill.get("success"):
            message = str((refill or {}).get("error") or "补读失败")
            return {
                "status": "error",
                "message": message,
                "window_id": window_id,
                "range": target_range,
            }

        ingest_result = self._manager.ingest_focus_read_result(
            window_id=window_id,
            range_ref=target_range,
            result_text=str(refill.get("result_text") or ""),
            tool_name=str(refill.get("tool_name") or "read_excel"),
            arguments=refill.get("arguments") if isinstance(refill.get("arguments"), dict) else None,
        )
        if ingest_result.get("status") != "ok":
            return ingest_result

        return {
            "status": "ok",
            "action": action,
            "window_id": window_id,
            "range": target_range,
            "refilled": True,
            "rows": ingest_result.get("rows", 0),
            "tool_name": refill.get("tool_name"),
        }
