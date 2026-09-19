"""计划文档工具：通过 write_plan 让 Agent 撰写 Markdown 计划并自动创建 TaskList。"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from collections.abc import Callable

from excelmanus.engine_core.tool_result import ToolResult, error_result
from excelmanus.logger import get_logger
from excelmanus.plan_mode import parse_plan_markdown
from excelmanus.prompt.canonical import TOOL_DESCRIPTIONS
from excelmanus.task_list import TaskStore
from excelmanus.tools.registry import ToolDef

logger = get_logger("plan_tools")


def _generate_plan_filename() -> str:
    """生成唯一的计划文件名：plan_{timestamp}_{token}.md"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    token = secrets.token_hex(3)
    return f"plan_{stamp}_{token}.md"


def write_plan(
    title: str,
    content: str,
    *,
    store: TaskStore,
    workspace_root: str,
) -> ToolResult:
    """写入 Markdown 计划文档到 {workspace}/plans/，自动从末尾解析任务清单。

    工作流程：
    1. 写入 Markdown 文件
    2. 调用 parse_plan_markdown 解析子任务
    3. 调用 TaskStore.create 创建 TaskList
    4. 设置 TaskStore.plan_file_path
    """
    if not title or not title.strip():
        raise ValueError("计划标题不能为空。")
    if not content or not content.strip():
        raise ValueError("计划内容不能为空。")

    title = title.strip()
    content = content.strip()

    # ── 写入文件 ──
    root = Path(workspace_root).expanduser().resolve()
    plans_dir = root / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    filename = _generate_plan_filename()
    file_path = plans_dir / filename
    file_path.write_text(content, encoding="utf-8")
    rel_path = file_path.relative_to(root).as_posix()

    logger.info("计划文档已写入: %s", rel_path)

    # ── 解析任务清单 ──
    try:
        parsed_title, subtasks = parse_plan_markdown(content)
    except ValueError as exc:
        # 文件已写入但解析失败 → 返回错误提示，agent 可修正后重试
        return ToolResult.from_text(
            (
                f"⚠️ 计划文档已保存到 `{rel_path}`，但任务清单解析失败：{exc}\n"
                "请确保 content 末尾包含 `## 任务清单` + checkbox 子任务，"
                "或 tasklist-json 代码块。"
            ),
            success=False,
        )

    # 使用解析出的标题（如有），否则用 tool 参数的 title
    effective_title = parsed_title or title

    # ── 创建 TaskList（覆盖已有） ──
    task_list = store.create(effective_title, subtasks, replace_existing=True)
    store.plan_file_path = rel_path

    # ── 构建返回摘要 ──
    lines = [
        f"✅ 计划文档已保存: `{rel_path}`",
        f"📋 已创建任务清单「{task_list.title}」，共 {len(task_list.items)} 个子任务：",
    ]
    for idx, item in enumerate(task_list.items):
        v_tag = f"  [验证: {item.verification_criteria}]" if item.verification_criteria else ""
        lines.append(f"  {idx}. {item.title}{v_tag}")

    return ToolResult.from_text("\n".join(lines))


def exit_plan_mode(
    plan: str = "",
    *,
    is_plan_active: Callable[[], bool],
    on_submitted: Callable[[str], None] | None = None,
) -> ToolResult:
    """呈交计划。plan 外失败；真正退出要用户批准。"""
    if not is_plan_active():
        return error_result(
            "exit_plan_mode 仅在计划模式内有效。",
            code="PLAN_INACTIVE",
        )
    text = (plan or "").strip()
    if not text:
        return error_result("需要完整计划正文。", code="INVALID_ARGS")
    if on_submitted is not None:
        on_submitted(text)
    return ToolResult.from_text("计划已呈交，等待用户批准后退出计划模式。")


def get_tools(
    store: TaskStore,
    workspace_root: str,
    *,
    is_plan_active: Callable[[], bool] | None = None,
    on_exit_submitted: Callable[[str], None] | None = None,
) -> list[ToolDef]:
    """返回绑定到 TaskStore + workspace 的计划工具定义。"""

    def _is_plan_active() -> bool:
        if is_plan_active is None:
            return False
        return bool(is_plan_active())

    def _write_plan(title: str, content: str) -> ToolResult:
        return write_plan(
            title=title,
            content=content,
            store=store,
            workspace_root=workspace_root,
        )

    def _exit_plan_mode(plan: str = "") -> ToolResult:
        return exit_plan_mode(
            plan=plan,
            is_plan_active=_is_plan_active,
            on_submitted=on_exit_submitted,
        )

    return [
        ToolDef(
            name="write_plan",
            description=TOOL_DESCRIPTIONS["write_plan"],
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "计划标题",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown 计划正文",
                    },
                },
                "required": ["title", "content"],
                "additionalProperties": False,
            },
            func=_write_plan,
            write_effect="none",
        ),
        ToolDef(
            name="exit_plan_mode",
            description=TOOL_DESCRIPTIONS["exit_plan_mode"],
            input_schema={
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "string",
                        "description": "完整计划正文，须含标题",
                    },
                },
                "required": ["plan"],
                "additionalProperties": False,
            },
            func=_exit_plan_mode,
            write_effect="none",
        ),
    ]
