"""睡眠/等待工具：sleep。"""

from __future__ import annotations

import time

from excelmanus.tools.registry import ToolDef

# 允许的最大等待秒数
_MAX_SLEEP_SECONDS = 300


def sleep(seconds: int | float, reason: str | None = None) -> str:
    """暂停执行指定秒数，用于等待外部操作完成。"""
    if seconds <= 0:
        return "⚠️ 等待时间必须大于 0 秒。"
    if seconds > _MAX_SLEEP_SECONDS:
        return f"⚠️ 等待时间不能超过 {_MAX_SLEEP_SECONDS} 秒（{_MAX_SLEEP_SECONDS // 60} 分钟）。"

    time.sleep(seconds)

    label = f"（原因: {reason}）" if reason else ""
    return f"✅ 已等待 {seconds} 秒{label}，继续执行。"


def get_tools() -> list[ToolDef]:
    """返回 sleep 工具定义。"""
    return [
        ToolDef(
            name="sleep",
            description=(
                "暂停等待指定秒数后继续执行。"
                "适用场景："
                "(1) 等待用户在 Excel 中完成手动操作后再继续；"
                "(2) 等待外部系统、文件同步或其他异步过程完成；"
                "(3) 需要给用户留出检查/确认中间结果的时间。"
                "注意：最大等待时间为 300 秒（5 分钟）。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "number",
                        "description": "等待秒数（最大 300）",
                        "minimum": 1,
                        "maximum": 300,
                    },
                    "reason": {
                        "type": "string",
                        "description": "等待原因说明（可选，会在等待结束后回显）",
                    },
                },
                "required": ["seconds"],
                "additionalProperties": False,
            },
            func=sleep,
            write_effect="none",
        ),
    ]
