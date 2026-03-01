"""会话导出与导入：支持 Markdown / 纯文本 / EMX (JSON) 三种格式。

EMX (ExcelManus eXport) 是自定义 JSON 格式，可重新导入为完整会话。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

EMX_FORMAT_ID = "excelmanus-session"
EMX_VERSION = "1.0.0"


# ── 工具函数 ─────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_text_content(msg: dict) -> str:
    """从消息 dict 中提取纯文本内容。"""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # OpenAI 多模态格式: [{type: "text", text: "..."}, ...]
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(part.get("text", ""))
                elif part.get("type") == "image_url":
                    parts.append("[图片]")
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return str(content) if content else ""


def _escape_md_table_cell(value: str) -> str:
    """转义 Markdown 表格单元格中的特殊字符（管道符、换行）。"""
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", "")


def _summarize_args(args: Any, max_len: int = 80) -> str:
    """将工具参数压缩为简短摘要。"""
    if not args:
        return ""
    if isinstance(args, str):
        s = args
    else:
        try:
            s = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            s = str(args)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


# ── Markdown 导出 ─────────────────────────────────────────


def export_markdown(
    session_meta: dict,
    messages: list[dict],
    excel_diffs: list[dict] | None = None,
    excel_previews: list[dict] | None = None,
    affected_files: list[str] | None = None,
) -> str:
    """将会话导出为 Markdown 报告。"""
    title = session_meta.get("title", "未命名会话")
    session_id = session_meta.get("id", "unknown")
    created_at = session_meta.get("created_at", "")
    exported_at = _now_iso()

    lines: list[str] = []
    lines.append(f"# 会话报告: {title}\n")
    lines.append(
        f"> **导出时间**: {exported_at}  \n"
        f"> **会话 ID**: `{session_id}`  \n"
        f"> **创建时间**: {created_at}  \n"
        f"> **消息数**: {len(messages)}\n"
    )

    # ── 对话记录 ──
    lines.append("## 对话记录\n")
    turn = 0
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            continue  # 跳过系统消息

        if role == "user":
            turn += 1
            text = _extract_text_content(msg)
            lines.append(f"### 👤 用户 (轮次 {turn})\n")
            lines.append(f"{text}\n")

        elif role == "assistant":
            text = _extract_text_content(msg)
            # 渲染工具调用
            tool_calls_in_msg = msg.get("tool_calls", [])
            lines.append(f"### 🤖 助手 (轮次 {turn})\n")
            if text.strip():
                lines.append(f"{text}\n")
            if tool_calls_in_msg:
                lines.append("#### 工具调用\n")
                for tc in tool_calls_in_msg:
                    if not isinstance(tc, dict):
                        continue
                    func = tc.get("function", {})
                    name = func.get("name", "unknown")
                    args_raw = func.get("arguments", "")
                    summary = _summarize_args(args_raw)
                    lines.append(f"- `{name}({summary})`\n")
                lines.append("")

        elif role == "tool":
            # 工具结果简要展示
            name = msg.get("name", "tool")
            content = _extract_text_content(msg)
            # 截断过长的工具输出
            if len(content) > 500:
                content = content[:497] + "..."
            lines.append(f"<details><summary>📎 {name} 结果</summary>\n")
            lines.append(f"```\n{content}\n```\n")
            lines.append("</details>\n")

    # ── 数据变更摘要 ──
    if excel_diffs:
        lines.append("---\n")
        lines.append("## 数据变更摘要\n")
        lines.append("| 文件 | 工作表 | 范围 | 变更数 |\n")
        lines.append("|------|--------|------|--------|\n")
        for d in excel_diffs:
            fp = d.get("file_path", "")
            sheet = d.get("sheet", "")
            rng = d.get("affected_range", "")
            changes = d.get("changes", [])
            lines.append(f"| `{fp}` | {sheet} | {rng} | {len(changes)} |\n")
        lines.append("")

    # ── 数据快照 ──
    if excel_previews:
        lines.append("## 数据快照\n")
        for p in excel_previews:
            fp = p.get("file_path", "")
            sheet = p.get("sheet", "")
            columns = p.get("columns", [])
            rows = p.get("rows", [])
            total = p.get("total_rows", len(rows))
            truncated = p.get("truncated", False)

            lines.append(f"### {fp} — {sheet}\n")
            if columns:
                lines.append("| " + " | ".join(str(c) for c in columns) + " |\n")
                lines.append("| " + " | ".join("---" for _ in columns) + " |\n")
                for row in rows[:30]:  # 最多 30 行
                    cells = [_escape_md_table_cell(str(c)) if c is not None else "" for c in row]
                    # 确保列数一致
                    while len(cells) < len(columns):
                        cells.append("")
                    lines.append("| " + " | ".join(cells[:len(columns)]) + " |\n")
                if truncated or total > 30:
                    lines.append(f"\n> *共 {total} 行，仅展示前 {min(30, len(rows))} 行*\n")
            lines.append("")

    # ── 涉及文件 ──
    if affected_files:
        lines.append("## 涉及文件\n")
        for f in affected_files:
            lines.append(f"- `{f}`\n")
        lines.append("")

    return "".join(lines)


# ── 纯文本导出 ─────────────────────────────────────────


def export_text(
    session_meta: dict,
    messages: list[dict],
) -> str:
    """将会话导出为纯文本。"""
    title = session_meta.get("title", "未命名会话")
    session_id = session_meta.get("id", "unknown")
    exported_at = _now_iso()

    lines: list[str] = []
    lines.append(f"会话报告: {title}")
    lines.append(f"导出时间: {exported_at}")
    lines.append(f"会话 ID: {session_id}")
    lines.append(f"消息数: {len(messages)}")
    lines.append("=" * 60)
    lines.append("")

    turn = 0
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            continue

        if role == "user":
            turn += 1
            text = _extract_text_content(msg)
            lines.append(f"[用户 - 轮次 {turn}]")
            lines.append(text)
            lines.append("")

        elif role == "assistant":
            text = _extract_text_content(msg)
            lines.append(f"[助手 - 轮次 {turn}]")
            if text.strip():
                lines.append(text)
            tool_calls_in_msg = msg.get("tool_calls", [])
            if tool_calls_in_msg:
                lines.append("  工具调用:")
                for tc in tool_calls_in_msg:
                    if not isinstance(tc, dict):
                        continue
                    func = tc.get("function", {})
                    name = func.get("name", "unknown")
                    lines.append(f"    - {name}")
            lines.append("")

        elif role == "tool":
            name = msg.get("name", "tool")
            content = _extract_text_content(msg)
            if len(content) > 300:
                content = content[:297] + "..."
            lines.append(f"  [{name} 结果]: {content}")
            lines.append("")

    return "\n".join(lines)


# ── EMX 导出 ──────────────────────────────────────────


def export_emx(
    session_meta: dict,
    messages: list[dict],
    excel_diffs: list[dict] | None = None,
    excel_previews: list[dict] | None = None,
    affected_files: list[str] | None = None,
) -> dict:
    """将会话导出为 EMX (JSON) 格式，可重新导入。"""
    return {
        "format": EMX_FORMAT_ID,
        "version": EMX_VERSION,
        "exported_at": _now_iso(),
        "session": {
            "id": session_meta.get("id", ""),
            "title": session_meta.get("title", ""),
            "created_at": session_meta.get("created_at", ""),
            "updated_at": session_meta.get("updated_at", ""),
        },
        "messages": messages,
        "excel_diffs": excel_diffs or [],
        "excel_previews": excel_previews or [],
        "affected_files": affected_files or [],
    }


# ── EMX 导入 ──────────────────────────────────────────


class EMXImportError(ValueError):
    """EMX 导入格式校验失败。"""


def parse_emx(data: dict) -> dict:
    """解析并校验 EMX 数据，返回标准化结构。

    Returns:
        dict with keys: session_meta, messages, excel_diffs, excel_previews, affected_files
    Raises:
        EMXImportError: 格式不合法
    """
    fmt = data.get("format")
    if fmt != EMX_FORMAT_ID:
        raise EMXImportError(
            f"不支持的格式: {fmt!r}，期望 {EMX_FORMAT_ID!r}"
        )

    version = data.get("version", "")
    if not version.startswith("1."):
        raise EMXImportError(f"不支持的版本: {version!r}")

    session_raw = data.get("session")
    if not isinstance(session_raw, dict):
        raise EMXImportError("缺少 session 元数据")

    messages = data.get("messages")
    if not isinstance(messages, list):
        raise EMXImportError("缺少 messages 列表")

    # 基本校验：每条消息至少有 role
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise EMXImportError(f"messages[{i}] 不是 dict")
        if "role" not in msg:
            raise EMXImportError(f"messages[{i}] 缺少 role 字段")

    return {
        "session_meta": {
            "id": session_raw.get("id", ""),
            "title": session_raw.get("title", "导入的会话"),
            "created_at": session_raw.get("created_at", ""),
            "updated_at": session_raw.get("updated_at", ""),
        },
        "messages": messages,
        "excel_diffs": data.get("excel_diffs", []),
        "excel_previews": data.get("excel_previews", []),
        "affected_files": data.get("affected_files", []),
    }
