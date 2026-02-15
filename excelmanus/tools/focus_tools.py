"""窗口聚焦工具：focus_window。"""

from __future__ import annotations

import json
from typing import Any, Callable

from excelmanus.tools.registry import ToolDef
from excelmanus.window_perception.focus import FocusService
from excelmanus.window_perception.manager import WindowPerceptionManager

_focus_service: FocusService | None = None


def init_focus_manager(
    *,
    manager: WindowPerceptionManager,
    refill_reader: Callable[..., dict[str, Any]] | None = None,
) -> None:
    """注入窗口管理器与自动补读回调。"""
    global _focus_service
    _focus_service = FocusService(
        manager=manager,
        refill_reader=refill_reader,
    )


def focus_window(
    window_id: str,
    action: str,
    range: str | None = None,
    rows: int | None = None,
) -> str:
    """聚焦窗口视口并按需自动补读缓存缺失区域。"""
    if _focus_service is None:
        return json.dumps(
            {
                "status": "error",
                "message": "focus_window 未初始化",
            },
            ensure_ascii=False,
            indent=2,
        )

    payload = _focus_service.focus_window(
        window_id=window_id,
        action=action,
        range_ref=range,
        rows=rows,
    )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def get_tools() -> list[ToolDef]:
    """返回 focus_window 工具定义。"""
    return [
        ToolDef(
            name="focus_window",
            description=(
                "聚焦指定数据窗口并切换视口，支持 scroll/clear_filter/expand/restore。"
                "当目标范围不在缓存中时会自动补读并更新窗口。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "window_id": {
                        "type": "string",
                        "description": "目标窗口 ID（如 sheet_1）",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["scroll", "clear_filter", "expand", "restore"],
                        "description": "窗口动作类型",
                    },
                    "range": {
                        "type": "string",
                        "description": "scroll/expand 的目标区域（如 A20:F60）",
                    },
                    "rows": {
                        "type": "integer",
                        "description": "expand 时向下扩展的行数，默认使用系统窗口行数",
                    },
                },
                "required": ["window_id", "action"],
                "additionalProperties": False,
            },
            func=focus_window,
        ),
    ]
