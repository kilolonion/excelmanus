"""会话导出与导入：支持 Markdown / 纯文本 / EMX (JSON) 三种格式。

EMX (ExcelManus eXport) 是自定义 JSON 格式，可重新导入为完整会话。

v2.0 新增：session_state、task_list、memories、config_snapshot、workspace_files，
支持完整会话状态导出与恢复。v1.x 格式向后兼容。
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EMX_FORMAT_ID = "excelmanus-session"
EMX_VERSION = "2.0.0"

# 工作区文件大小上限（单文件 50MB，总计 200MB）
_MAX_SINGLE_FILE_BYTES = 50 * 1024 * 1024
_MAX_TOTAL_FILE_BYTES = 200 * 1024 * 1024
# 排除的目录和文件模式
_EXCLUDE_DIRS = {".git", "__pycache__", ".excelmanus", "node_modules", ".venv", "venv"}
_EXCLUDE_EXTENSIONS = {".pyc", ".pyo", ".o", ".so", ".dll", ".exe"}


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
    *,
    session_state: dict | None = None,
    task_list: dict | None = None,
    memories: list[dict] | None = None,
    config_snapshot: dict | None = None,
    workspace_files: list[dict] | None = None,
) -> dict:
    """将会话导出为 EMX v2.0 (JSON) 格式，可重新导入。

    v2.0 新增字段（均为可选，缺失时导入端跳过恢复）：
    - session_state: SessionState.to_dict() 快照
    - task_list: TaskStore.to_dict() 快照
    - memories: [{category, content, source, created_at}, ...]
    - config_snapshot: {model, chat_mode, full_access_enabled}
    - workspace_files: [{path, content_b64, size}, ...]
    """
    data: dict[str, Any] = {
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
    # v2.0 扩展字段
    if session_state is not None:
        data["session_state"] = session_state
    if task_list is not None:
        data["task_list"] = task_list
    if memories is not None:
        data["memories"] = memories
    if config_snapshot is not None:
        data["config_snapshot"] = config_snapshot
    if workspace_files is not None:
        data["workspace_files"] = workspace_files
    return data


# ── EMX 导入 ──────────────────────────────────────────


class EMXImportError(ValueError):
    """EMX 导入格式校验失败。"""


def parse_emx(data: dict) -> dict:
    """解析并校验 EMX 数据，返回标准化结构。

    兼容 v1.x 和 v2.x 格式。v2 新增字段缺失时返回空默认值。

    Returns:
        dict with keys: session_meta, messages, excel_diffs, excel_previews,
        affected_files, session_state, task_list, memories, config_snapshot,
        workspace_files
    Raises:
        EMXImportError: 格式不合法
    """
    fmt = data.get("format")
    if fmt != EMX_FORMAT_ID:
        raise EMXImportError(
            f"不支持的格式: {fmt!r}，期望 {EMX_FORMAT_ID!r}"
        )

    version = data.get("version", "")
    if not (version.startswith("1.") or version.startswith("2.")):
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

    # v2 字段校验（非必须，缺失时返回 None/空）
    session_state = data.get("session_state")
    if session_state is not None and not isinstance(session_state, dict):
        raise EMXImportError("session_state 必须是 dict")

    task_list = data.get("task_list")
    if task_list is not None and not isinstance(task_list, dict):
        raise EMXImportError("task_list 必须是 dict")

    memories = data.get("memories")
    if memories is not None and not isinstance(memories, list):
        raise EMXImportError("memories 必须是 list")

    config_snapshot = data.get("config_snapshot")
    if config_snapshot is not None and not isinstance(config_snapshot, dict):
        raise EMXImportError("config_snapshot 必须是 dict")

    workspace_files = data.get("workspace_files")
    if workspace_files is not None and not isinstance(workspace_files, list):
        raise EMXImportError("workspace_files 必须是 list")

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
        # v2.0 扩展
        "session_state": session_state,
        "task_list": task_list,
        "memories": memories,
        "config_snapshot": config_snapshot,
        "workspace_files": workspace_files,
    }


# ── 工作区文件收集 ──────────────────────────────────────


def collect_workspace_files(
    workspace_root: str,
    *,
    affected_only: list[str] | None = None,
) -> list[dict]:
    """收集工作区文件列表（base64 编码），用于 EMX 导出。

    Args:
        workspace_root: 工作区根目录绝对路径。
        affected_only: 若非空，仅收集列表中的文件（相对路径）。
            为空时收集工作区内所有非排除文件。

    Returns:
        [{path: str, content_b64: str, size: int}, ...]
    """
    root = Path(workspace_root)
    if not root.is_dir():
        return []

    result: list[dict] = []
    total_bytes = 0

    if affected_only:
        # 仅收集指定文件
        for rel_path in affected_only:
            rel_clean = rel_path.lstrip("./").lstrip("/")
            full = root / rel_clean
            if not full.is_file():
                continue
            entry = _encode_file(root, full)
            if entry is not None:
                total_bytes += entry["size"]
                if total_bytes > _MAX_TOTAL_FILE_BYTES:
                    logger.warning("工作区文件总大小超限，停止收集")
                    break
                result.append(entry)
    else:
        # 收集所有文件
        for dirpath, dirnames, filenames in os.walk(root):
            # 过滤排除目录
            dirnames[:] = [
                d for d in dirnames
                if d not in _EXCLUDE_DIRS and not d.startswith(".")
            ]
            for fname in filenames:
                full = Path(dirpath) / fname
                entry = _encode_file(root, full)
                if entry is not None:
                    total_bytes += entry["size"]
                    if total_bytes > _MAX_TOTAL_FILE_BYTES:
                        logger.warning("工作区文件总大小超限，停止收集")
                        break
                    result.append(entry)
            if total_bytes > _MAX_TOTAL_FILE_BYTES:
                break

    return result


def _encode_file(root: Path, full_path: Path) -> dict | None:
    """读取并 base64 编码单个文件，返回 dict 或 None。"""
    if not full_path.is_file():
        return None
    ext = full_path.suffix.lower()
    if ext in _EXCLUDE_EXTENSIONS:
        return None
    try:
        size = full_path.stat().st_size
    except OSError:
        return None
    if size == 0 or size > _MAX_SINGLE_FILE_BYTES:
        return None
    try:
        raw = full_path.read_bytes()
        rel = full_path.relative_to(root).as_posix()
        return {
            "path": rel,
            "content_b64": base64.b64encode(raw).decode("ascii"),
            "size": size,
        }
    except Exception:
        logger.debug("编码文件失败: %s", full_path, exc_info=True)
        return None


def restore_workspace_files(
    workspace_root: str,
    files: list[dict],
) -> tuple[int, int]:
    """将 EMX 中的工作区文件恢复到磁盘。

    Args:
        workspace_root: 目标工作区根目录。
        files: [{path, content_b64, size}, ...]

    Returns:
        (restored_count, skipped_count)
    """
    root = Path(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    restored = 0
    skipped = 0

    for entry in files:
        rel_path = entry.get("path", "")
        content_b64 = entry.get("content_b64", "")
        if not rel_path or not content_b64:
            skipped += 1
            continue

        # 安全校验：防止路径穿越
        target = (root / rel_path).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            logger.warning("路径穿越攻击被阻止: %s", rel_path)
            skipped += 1
            continue

        try:
            raw = base64.b64decode(content_b64)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            restored += 1
        except Exception:
            logger.debug("恢复文件失败: %s", rel_path, exc_info=True)
            skipped += 1

    return restored, skipped
