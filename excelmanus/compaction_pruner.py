"""无模型 tool 结果修剪（L1）：压缩边界内先于 LLM 摘要运行。

两条规则，均不调用模型：

1. **error 载荷瘦身**：content 为 canonical error JSON 时，仅保留
   ``REQUIRED_ERROR_KEYS`` 与少量恢复线索（candidates/available_sheets/
   violations），剥掉 ``data``/``shape``/``columns`` 等数据大字段，
   ``message`` 截断到上限。
2. **head + marker + tail**：仍超阈值的内容切成
   ``head + […已省略 N 字符…] + tail``，按 code points 计（Python str
   切片天然 UTF-8 边界安全）。

幂等：含省略标记或 ``spill:`` 指针的结果不再处理——二次扫描零产出，
不会在重写边界反复 bump generation。

调用方只在压缩触发边界（pre-step 压力 / provider 溢出恢复）内把返回的
``{message_id: new_content}`` 落到 ``ConversationMemory``——
每条变更一条 ``tool/result`` replace 事件，与摘要共用同一次
series 重写，一次触发至多一次 cache miss。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from excelmanus.engine_core.error_payload import REQUIRED_ERROR_KEYS

logger = logging.getLogger(__name__)

# 与 dsh pruner 对齐的默认值（按 code points 计）。
PRUNE_THRESHOLD_CHARS = 8192
PRUNE_HEAD_CHARS = 4096
PRUNE_TAIL_CHARS = 1024
PRUNE_MESSAGE_MAX = 500

_PRUNE_MARK = "已省略"
_SPILL_PREFIX = "spill:"

# error 载荷里保留的恢复线索键（值本身也会被限量截断）。
_RECOVERY_HINT_KEYS = frozenset(
    {"candidates", "available_sheets", "available_excel_files", "violations"}
)
# 明确剥除的数据大字段（历史堆积的重头）。
_DATA_KEYS = frozenset(
    {"shape", "columns", "data", "sheets", "file", "result", "rows",
     "preview", "sample", "values", "output", "stdout", "stderr"}
)
_HINT_ITEM_MAX = 8


def _clip_hint_list(value: Any) -> Any:
    """恢复线索列表限量，防止瘦身后的载荷二次膨胀。"""
    if isinstance(value, list):
        return value[:_HINT_ITEM_MAX]
    return value


def slim_error_text(text: str) -> str | None:
    """若 text 是 canonical error JSON，返回瘦身后的 JSON 串；否则 None。

    无变化（无字段可剥）时返回 None——调用方据此判幂等。
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("status") or "") != "error":
        return None

    slim: dict[str, Any] = {
        k: payload[k] for k in REQUIRED_ERROR_KEYS if k in payload
    }
    message = str(slim.get("message") or "")
    if len(message) > PRUNE_MESSAGE_MAX:
        slim["message"] = message[:PRUNE_MESSAGE_MAX] + "…"
    for key in _RECOVERY_HINT_KEYS:
        if key in payload:
            slim[key] = _clip_hint_list(payload[key])
    dropped = [
        k for k in payload
        if k not in slim and k not in REQUIRED_ERROR_KEYS
    ]
    if dropped:
        slim["pruned_fields"] = sorted(dropped)

    out = json.dumps(slim, ensure_ascii=False, default=str)
    return out if out != text else None


def _is_pruned_or_external(content: str) -> bool:
    if content.startswith(_SPILL_PREFIX):
        return True
    if "已收起" in content:
        return True
    return _PRUNE_MARK in content


def _head_marker_tail(content: str, head: int, tail: int) -> str:
    omitted = len(content) - head - tail
    marker = f"\n[…{_PRUNE_MARK} {omitted} 字符，修剪于压缩边界；原文在会话事件日志中…]\n"
    return content[:head] + marker + content[len(content) - tail:]


def prune_messages(
    messages: list[Mapping[str, Any]],
    *,
    threshold: int = PRUNE_THRESHOLD_CHARS,
    head: int = PRUNE_HEAD_CHARS,
    tail: int = PRUNE_TAIL_CHARS,
) -> dict[int, str]:
    """扫描 ``role=="tool"`` 消息，返回 ``{index: new_content}``。

    只含需要变更的项；二次运行对已修剪内容零产出（幂等）。
    """
    edits: dict[int, str] = {}
    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if not isinstance(content, str) or len(content) <= threshold:
            continue
        if _is_pruned_or_external(content):
            continue
        slimmed = slim_error_text(content)
        candidate = slimmed if slimmed is not None else content
        if len(candidate) > threshold:
            candidate = _head_marker_tail(candidate, head, tail)
        if candidate != content:
            edits[i] = candidate
    return edits
