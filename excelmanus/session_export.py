"""会话导出：Markdown 阅读报告与 JSON 对话档案。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


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

    lines.append("## 对话记录\n")
    turn = 0
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            continue

        if role == "user":
            turn += 1
            text = _extract_text_content(msg)
            lines.append(f"### 👤 用户 (轮次 {turn})\n")
            lines.append(f"{text}\n")

        elif role == "assistant":
            text = _extract_text_content(msg)
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
            name = msg.get("name", "tool")
            content = _extract_text_content(msg)
            if len(content) > 500:
                content = content[:497] + "..."
            lines.append(f"<details><summary>📎 {name} 结果</summary>\n")
            lines.append(f"```\n{content}\n```\n")
            lines.append("</details>\n")

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
                for row in rows[:30]:
                    cells = [_escape_md_table_cell(str(c)) if c is not None else "" for c in row]
                    while len(cells) < len(columns):
                        cells.append("")
                    lines.append("| " + " | ".join(cells[:len(columns)]) + " |\n")
                if truncated or total > 30:
                    lines.append(f"\n> *共 {total} 行，仅展示前 {min(30, len(rows))} 行*\n")
            lines.append("")

    if affected_files:
        lines.append("## 涉及文件\n")
        for f in affected_files:
            lines.append(f"- `{f}`\n")
        lines.append("")

    return "".join(lines)


def export_json(
    session_meta: dict,
    messages: list[dict],
    excel_diffs: list[dict] | None = None,
    excel_previews: list[dict] | None = None,
    affected_files: list[str] | None = None,
) -> dict[str, Any]:
    """将会话导出为 JSON 对话档案（不可再导入还原）。"""
    return {
        "exported_at": _now_iso(),
        "session": {
            "id": session_meta.get("id", ""),
            "title": session_meta.get("title", ""),
            "created_at": session_meta.get("created_at", ""),
            "updated_at": session_meta.get("updated_at", ""),
            "workspace_path": session_meta.get("workspace_path", ""),
        },
        "messages": messages,
        "excel_diffs": excel_diffs or [],
        "excel_previews": excel_previews or [],
        "affected_files": affected_files or [],
    }
