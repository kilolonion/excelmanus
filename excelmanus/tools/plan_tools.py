"""计划文档工具：通过 write_plan 让 Agent 撰写 Markdown 计划并自动创建 TaskList。"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from excelmanus.engine_core.tool_result import ToolResult
from excelmanus.logger import get_logger
from excelmanus.plan_mode import parse_plan_markdown
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
    rel_path = str(file_path.relative_to(root))

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


def get_tools(store: TaskStore, workspace_root: str) -> list[ToolDef]:
    """返回绑定到 TaskStore + workspace 的计划工具定义。"""

    def _write_plan(title: str, content: str) -> ToolResult:
        return write_plan(
            title=title,
            content=content,
            store=store,
            workspace_root=workspace_root,
        )

    return [
        ToolDef(
            name="write_plan",
            description=(
                "撰写 Markdown 计划文档并自动创建任务清单。"
                "将完整的分析方案写入 {workspace}/plans/ 目录，"
                "并从文档末尾自动解析子任务列表，创建可追踪的 TaskList。"
                "使用场景："
                "(1) plan 模式下必须使用此工具输出规划文档；"
                "(2) 复杂任务（5步以上）的全面规划。"
                "content 末尾必须包含可解析的任务清单，支持两种格式："
                "格式A — `## 任务清单` + checkbox（`- [ ] 子任务标题`）；"
                "格式B — tasklist-json 代码块（支持 verification 验证条件）。"
                "调用后自动创建 TaskList，无需再调用 task_create。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "计划标题（用于文件名和任务清单标题）",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Markdown 计划正文。末尾必须包含可解析的任务清单。"
                            "推荐结构：# 标题 → ## 背景分析 → ## 方案设计 → ## 任务清单（- [ ] 子任务）"
                        ),
                    },
                },
                "required": ["title", "content"],
                "additionalProperties": False,
            },
            func=_write_plan,
            write_effect="none",
        ),
    ]
