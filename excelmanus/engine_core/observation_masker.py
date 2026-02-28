"""ObservationMasker — 对旧轮次的工具返回值做结构化遮蔽。

保留完整的 reasoning + action 历史，仅对旧轮次的 observation
（工具返回值）做摘要/遮蔽，降低上下文占用。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 保留完整内容的最近 N 轮（用户消息计数）
FRESH_WINDOW = 4

# 遮蔽后的最大字符数
_MASKED_MAX_CHARS = 200

# 工具结果遮蔽模板
_MASK_TEMPLATES: dict[str, str] = {
    "read_excel": "[已读取 {file}/{sheet}, {summary}]",
    "inspect_excel_files": "[已探查 {n} 个文件: {files}]",
    "list_sheets": "[{file} 含 {n} 个 sheet: {sheets}]",
    "write_cells": "[已写入 {file}/{sheet}/{range}]",
    "write_excel": "[已写入 {file}/{sheet}]",
    "advanced_format": "[已格式化 {file}/{sheet}/{range}]",
    "create_sheet": "[已创建 sheet: {sheet}]",
    "delete_sheet": "[已删除 sheet: {sheet}]",
}


def _build_tool_call_name_map(messages: list[dict[str, Any]]) -> dict[str, str]:
    """从 assistant 的 tool_calls 中构建 tool_call_id → tool_name 映射。

    tool result 消息本身不携带 name 字段，需要从对应的 assistant
    tool_calls 中查找。
    """
    mapping: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            if isinstance(tc, dict):
                tc_id = tc.get("id", "")
                func = tc.get("function", {})
                name = func.get("name", "") if isinstance(func, dict) else ""
            else:
                tc_id = getattr(tc, "id", "")
                func = getattr(tc, "function", None)
                name = getattr(func, "name", "") if func else ""
            if tc_id and name:
                mapping[tc_id] = name
    return mapping


def mask_messages(
    messages: list[dict[str, Any]],
    *,
    fresh_window: int = FRESH_WINDOW,
) -> list[dict[str, Any]]:
    """对旧轮次的工具返回值做结构化遮蔽。

    规则：
    1. 最近 fresh_window 轮（按用户消息计数）的所有消息保持原样
    2. 更早轮次的 tool result 消息做遮蔽/摘要
    3. assistant 消息（推理文本）：完整保留
    4. user 消息：完整保留
    5. system 消息：完整保留

    Returns:
        新的消息列表（不修改原列表）
    """
    if not messages:
        return messages

    # 找到最近 fresh_window 个 user 消息的位置
    user_indices: list[int] = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            user_indices.append(i)

    if len(user_indices) <= fresh_window:
        # 总用户消息不超过窗口，全部保留
        return messages

    # 找到窗口边界：第 -fresh_window 个 user 消息的索引
    boundary_idx = user_indices[-fresh_window]

    # 构建 tool_call_id → tool_name 映射（仅 boundary 之前的部分需要）
    name_map = _build_tool_call_name_map(messages[:boundary_idx])

    # 构建结果：boundary 之前的做遮蔽，之后的保持原样
    result: list[dict[str, Any]] = []
    for i, msg in enumerate(messages):
        if i >= boundary_idx:
            # 窗口内，保持原样
            result.append(msg)
        else:
            role = msg.get("role", "")
            if role == "tool":
                # 工具返回值做遮蔽
                masked = _mask_tool_result(msg, name_map)
                result.append(masked)
            else:
                # user / assistant / system 保持原样
                result.append(msg)

    return result


def _mask_tool_result(
    msg: dict[str, Any],
    name_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """遮蔽单条工具返回值消息。"""
    # tool result 消息不携带 name，从 name_map 中查找
    tool_call_id = msg.get("tool_call_id", "")
    tool_name = (name_map or {}).get(tool_call_id, "") or msg.get("name", "")
    content = str(msg.get("content", ""))

    if not content or len(content) <= _MASKED_MAX_CHARS:
        return msg  # 短结果不遮蔽

    masked_content = _apply_mask(tool_name, content)

    # 构造新消息（不修改原对象）
    new_msg = dict(msg)
    new_msg["content"] = masked_content
    return new_msg


def _apply_mask(tool_name: str, content: str) -> str:
    """根据工具名应用遮蔽模板。"""
    if tool_name == "run_code":
        return _mask_run_code(content)
    elif tool_name == "read_excel":
        return _mask_read_excel(content)
    elif tool_name == "inspect_excel_files":
        return _mask_inspect(content)
    elif tool_name == "list_sheets":
        return _mask_list_sheets(content)
    elif tool_name in ("write_cells", "write_excel", "advanced_format",
                       "create_sheet", "delete_sheet"):
        return _mask_write_tool(tool_name, content)
    else:
        return _mask_generic(content)


def _mask_run_code(content: str) -> str:
    """run_code 结果：保留 stdout 前 200 字。"""
    # 尝试解析 JSON
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            stdout = str(parsed.get("stdout", ""))[:200]
            success = parsed.get("success", True)
            status = "成功" if success else "失败"
            return f"[run_code {status}] {stdout}" + (
                " [输出已截断]" if len(str(parsed.get("stdout", ""))) > 200 else ""
            )
    except (json.JSONDecodeError, TypeError):
        pass
    # 非 JSON：保留前 200 字
    truncated = content[:200]
    return f"{truncated}" + (" [输出已截断]" if len(content) > 200 else "")


def _mask_read_excel(content: str) -> str:
    """read_excel 结果：提取文件/sheet/行列信息。"""
    # 尝试提取行数
    row_match = re.search(r"(\d+)\s*(?:行|rows?)", content, re.IGNORECASE)
    col_match = re.search(r"(\d+)\s*(?:列|columns?)", content, re.IGNORECASE)
    rows = row_match.group(1) if row_match else "?"
    cols = col_match.group(1) if col_match else "?"

    # 提取首行/列名
    header_match = re.search(r"(?:列名|columns?|header)[:\s]*\[([^\]]+)\]", content, re.IGNORECASE)
    header = header_match.group(1)[:100] if header_match else ""

    summary = f"{rows}行×{cols}列"
    if header:
        summary += f", 列: [{header}]"

    return f"[已读取数据, {summary}]"


def _mask_inspect(content: str) -> str:
    """inspect_excel_files 结果：提取文件列表。"""
    # 简单计算文件数
    file_count = content.count(".xlsx") + content.count(".xls") + content.count(".csv")
    if file_count == 0:
        file_count = 1
    truncated = content[:150]
    return f"[已探查 {file_count} 个文件] {truncated}" + (
        " [已截断]" if len(content) > 150 else ""
    )


def _mask_list_sheets(content: str) -> str:
    """list_sheets 结果：提取 sheet 列表。"""
    truncated = content[:150]
    return f"[sheet 列表] {truncated}" + (
        " [已截断]" if len(content) > 150 else ""
    )


def _mask_write_tool(tool_name: str, content: str) -> str:
    """写入工具结果：简要标记。"""
    truncated = content[:100]
    return f"[{tool_name} 完成] {truncated}" + (
        " [已截断]" if len(content) > 100 else ""
    )


def _mask_generic(content: str) -> str:
    """通用遮蔽：保留前 100 字。"""
    return content[:100] + (" [已截断]" if len(content) > 100 else "")
