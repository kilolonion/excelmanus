"""睡眠/等待工具：sleep。

改进点：
- 分段睡眠（每 _TICK 秒一段），避免长时间不可中断
- 支持外部取消（通过 cancel_sleep() 设置 threading.Event）
- 等待期间输出进度日志，前端/日志不再"卡死"
"""

from __future__ import annotations

import contextvars
import threading

from excelmanus.logger import get_logger
from excelmanus.tools.registry import ToolDef

logger = get_logger(__name__)

# 允许的最大等待秒数
_MAX_SLEEP_SECONDS = 300

# 分段睡眠的粒度（秒）
_TICK = 5.0

# ── 每会话取消事件（通过 ContextVar 隔离） ──────────────────
# ToolDispatcher 在调用工具前通过 set_cancel_event() 注入会话专属的 Event，
# asyncio.to_thread 会自动将 contextvar 拷贝到工作线程。
_cancel_event_var: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "_sleep_cancel_event", default=None,
)


def set_cancel_event(event: threading.Event) -> contextvars.Token:
    """设置当前上下文的 sleep 取消事件，返回恢复 token。"""
    return _cancel_event_var.set(event)


def reset_cancel_event(token: contextvars.Token) -> None:
    """恢复 contextvar 到先前值。"""
    _cancel_event_var.reset(token)


def sleep(seconds: int | float, reason: str | None = None) -> str:
    """暂停执行指定秒数，用于等待外部操作完成。"""
    if seconds <= 0:
        return "⚠️ 等待时间必须大于 0 秒。"
    if seconds > _MAX_SLEEP_SECONDS:
        return f"⚠️ 等待时间不能超过 {_MAX_SLEEP_SECONDS} 秒（{_MAX_SLEEP_SECONDS // 60} 分钟）。"

    label = f"（原因: {reason}）" if reason else ""
    logger.info("sleep 开始: %.1f 秒%s", seconds, label)

    # 获取当前会话的取消事件（无则创建本地 event，CLI 模式回退）
    event = _cancel_event_var.get(None)
    if event is None:
        event = threading.Event()

    # 若进入时已被标记取消，立即返回
    if event.is_set():
        event.clear()
        logger.info("sleep 在启动前已被取消%s", label)
        return f"⏹️ 等待已取消{label}，未实际等待。"

    elapsed = 0.0
    remaining = float(seconds)

    while remaining > 0:
        chunk = min(_TICK, remaining)
        # wait 返回 True 表示事件已被 set（即取消）
        if event.wait(timeout=chunk):
            event.clear()
            logger.info("sleep 被取消: 已等待 %.1f/%.1f 秒", elapsed, seconds)
            return f"⏹️ 等待已取消{label}，已等待 {elapsed:.0f}/{seconds} 秒。"

        elapsed += chunk
        remaining -= chunk

        if remaining > 0:
            logger.debug("sleep 进度: %.0f/%.0f 秒", elapsed, seconds)

    logger.info("sleep 完成: %.1f 秒%s", seconds, label)
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
