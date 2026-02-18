"""Plan Mode 数据模型与计划文档解析工具。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from excelmanus.skillpacks import SkillMatchResult

_JSON_FENCE_PATTERN = re.compile(
    r"```(?P<lang>[^\n`]*)\n(?P<body>.*?)```",
    re.DOTALL,
)
_TITLE_PATTERN = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
_TASK_SECTION_PATTERN = re.compile(r"^\s*##\s*任务清单\b.*$", re.MULTILINE)
_HEADING_PATTERN = re.compile(r"^\s*##\s+", re.MULTILINE)
_CHECKBOX_PATTERN = re.compile(r"^\s*[-*]\s*\[(?: |x|X)\]\s*(.+?)\s*$")


@dataclass(frozen=True)
class PlanDraft:
    """单份待审批的计划草案。"""

    plan_id: str
    markdown: str
    title: str
    subtasks: list[str]
    file_path: str
    source: Literal["plan_mode", "task_create_hook"]
    objective: str
    created_at_utc: str


@dataclass
class PendingPlanState:
    """当前会话待审批计划状态。"""

    draft: PlanDraft
    tool_call_id: str | None = None
    route_to_resume: SkillMatchResult | None = None


def new_plan_id() -> str:
    """生成计划 ID。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"pln_{stamp}_{secrets.token_hex(3)}"


def utc_now_iso() -> str:
    """返回 UTC ISO 时间。"""
    return datetime.now(timezone.utc).isoformat()


def plan_filename(plan_id: str) -> str:
    """根据计划 ID 生成落盘文件名。时间戳直接从 plan_id 中提取，保证一致性。"""
    parts = plan_id.split("_", 2)  # ["pln", "20240101T120000Z", "abc123"]
    stamp = parts[1] if len(parts) >= 3 else datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    token = parts[-1]
    return f"plan_{stamp}_{token}.md"


def save_plan_markdown(
    *,
    markdown: str,
    workspace_root: str,
    filename: str,
) -> str:
    """保存计划 Markdown，返回相对工作区路径。"""
    root = Path(workspace_root).expanduser().resolve()
    output_dir = root / ".excelmanus" / "plans"
    output_dir.mkdir(parents=True, exist_ok=True)

    path = output_dir / filename
    path.write_text(markdown, encoding="utf-8")
    return str(path.relative_to(root))


def parse_plan_markdown(markdown: str) -> tuple[str, list[str]]:
    """从 Markdown 中解析任务标题与子任务列表。"""
    text = (markdown or "").strip()
    if not text:
        raise ValueError("计划文档为空。")

    title = _extract_title(text)
    json_payload = _extract_tasklist_json_payload(text)

    if json_payload is not None:
        json_title = _safe_str(json_payload.get("title"))
        if json_title:
            title = json_title
        subtasks = _normalize_subtasks(json_payload.get("subtasks"))
    else:
        subtasks = _extract_checklist_subtasks(text)
        subtasks = _normalize_subtasks(subtasks)

    if not title:
        raise ValueError("计划文档缺少标题（需包含一级标题或 tasklist-json.title）。")
    if not subtasks:
        raise ValueError("计划文档缺少子任务（需包含 tasklist-json 或任务清单复选项）。")
    if len(subtasks) > 20:
        raise ValueError("子任务数量超过上限 20。")
    return title, subtasks


def _extract_title(text: str) -> str:
    match = _TITLE_PATTERN.search(text)
    if not match:
        return ""
    return match.group(1).strip()


def _extract_tasklist_json_payload(text: str) -> dict | None:
    payload_candidates: list[dict] = []
    for match in _JSON_FENCE_PATTERN.finditer(text):
        lang = (match.group("lang") or "").strip().lower()
        body = (match.group("body") or "").strip()
        if not body:
            continue

        is_target_fence = (
            lang in {"tasklist-json", "tasklist_json", "tasklistjson"}
            or ("tasklist-json" in lang)
            or lang == "json"
        )
        if not is_target_fence:
            continue

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "subtasks" in parsed:
            payload_candidates.append(parsed)

    if not payload_candidates:
        return None
    return payload_candidates[0]


def _extract_checklist_subtasks(text: str) -> list[str]:
    section_match = _TASK_SECTION_PATTERN.search(text)
    if section_match is None:
        return []

    section_start = section_match.end()
    tail = text[section_start:]
    next_heading = _HEADING_PATTERN.search(tail)
    block = tail[: next_heading.start()] if next_heading else tail

    subtasks: list[str] = []
    for line in block.splitlines():
        matched = _CHECKBOX_PATTERN.match(line)
        if matched:
            subtasks.append(matched.group(1).strip())
    return subtasks


def _normalize_subtasks(raw_subtasks: object) -> list[str]:
    if not isinstance(raw_subtasks, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_subtasks:
        text = _safe_str(item)
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _safe_str(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text
