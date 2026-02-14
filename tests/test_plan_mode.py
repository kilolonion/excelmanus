"""Plan mode 解析与内置 planner 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.plan_mode import parse_plan_markdown, save_plan_markdown
from excelmanus.subagent.builtin import BUILTIN_SUBAGENTS


def test_parse_plan_markdown_from_tasklist_json() -> None:
    markdown = """# 周报自动化

## 任务清单
- [ ] 收集数据
- [ ] 生成图表

```tasklist-json
{"title":"周报自动化","subtasks":["收集数据","生成图表","发送邮件"]}
```
"""
    title, subtasks = parse_plan_markdown(markdown)
    assert title == "周报自动化"
    assert subtasks == ["收集数据", "生成图表", "发送邮件"]


def test_parse_plan_markdown_fallback_checklist() -> None:
    markdown = """# 数据清洗计划

## 任务清单
- [ ] 读取源文件
- [ ] 清洗空值
- [ ] 导出结果
"""
    title, subtasks = parse_plan_markdown(markdown)
    assert title == "数据清洗计划"
    assert subtasks == ["读取源文件", "清洗空值", "导出结果"]


def test_parse_plan_markdown_invalid_raises() -> None:
    markdown = """# 无效计划

## 说明
只有说明，没有任务。
"""
    with pytest.raises(ValueError, match="缺少子任务"):
        parse_plan_markdown(markdown)


def test_save_plan_markdown_under_excelmanus_plans(tmp_path: Path) -> None:
    relative = save_plan_markdown(
        markdown="# 计划\n\n## 任务清单\n- [ ] 子任务",
        workspace_root=str(tmp_path),
        filename="plan_20260213T120000Z_demo.md",
    )
    assert relative == ".excelmanus/plans/plan_20260213T120000Z_demo.md"
    path = tmp_path / relative
    assert path.exists()


def test_save_plan_markdown_does_not_migrate_legacy_plans(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "outputs" / "plans"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "legacy_plan.md"
    legacy_file.write_text("# 旧计划\n\n## 任务清单\n- [ ] 旧任务", encoding="utf-8")

    save_plan_markdown(
        markdown="# 新计划\n\n## 任务清单\n- [ ] 新任务",
        workspace_root=str(tmp_path),
        filename="plan_20260213T120001Z_new.md",
    )

    assert legacy_file.exists()
    assert legacy_dir.exists()


def test_builtin_planner_exists() -> None:
    planner = BUILTIN_SUBAGENTS.get("planner")
    assert planner is not None
    assert planner.permission_mode == "readOnly"
    assert "tasklist-json" in planner.system_prompt
