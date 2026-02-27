"""write_plan 工具单元测试。"""

from __future__ import annotations

import pytest
from pathlib import Path

from excelmanus.task_list import TaskStore, TaskStatus
from excelmanus.tools.plan_tools import write_plan, get_tools


# ── 辅助 ──

SAMPLE_PLAN_CHECKBOX = """\
# 数据汇总报告

## 背景分析
需要将 orders.xlsx 的订单按客户汇总金额。

## 方案设计
使用 pandas groupby 进行聚合，写入 report.xlsx。

## 任务清单
- [ ] 读取源数据并确认结构
- [ ] 数据清洗（去重、填充空值）
- [ ] 生成汇总透视表
- [ ] 写入报告文件
"""

SAMPLE_PLAN_JSON = """\
# 跨表匹配填充

## 背景
匹配 Sheet1 和 Sheet2 的数据。

```tasklist-json
{"subtasks": [
  {"title": "读取 Sheet1 结构", "verification": "行数 > 0"},
  {"title": "读取 Sheet2 结构"},
  "执行匹配填充",
  "验证填充结果"
]}
```
"""

SAMPLE_PLAN_NO_TASKS = """\
# 简单分析

## 说明
这是一个没有任务清单的计划文档。
"""


# ── write_plan 核心功能 ──

class TestWritePlan:
    """write_plan 工具核心功能测试。"""

    def test_checkbox_format(self, tmp_path: Path) -> None:
        """Markdown checkbox 格式自动解析并创建 TaskList。"""
        store = TaskStore()
        result = write_plan(
            title="数据汇总报告",
            content=SAMPLE_PLAN_CHECKBOX,
            store=store,
            workspace_root=str(tmp_path),
        )

        # 文件已写入
        plans_dir = tmp_path / "plans"
        assert plans_dir.exists()
        plan_files = list(plans_dir.glob("plan_*.md"))
        assert len(plan_files) == 1

        # TaskList 已创建
        task_list = store.current
        assert task_list is not None
        assert task_list.title == "数据汇总报告"
        assert len(task_list.items) == 4
        assert task_list.items[0].title == "读取源数据并确认结构"
        assert task_list.items[0].status == TaskStatus.PENDING

        # plan_file_path 已设置
        assert store.plan_file_path is not None
        assert store.plan_file_path.startswith("plans/")
        assert store.plan_file_path.endswith(".md")

        # 返回摘要包含关键信息
        assert "✅" in result
        assert "数据汇总报告" in result
        assert "4 个子任务" in result

    def test_json_format_with_verification(self, tmp_path: Path) -> None:
        """tasklist-json 格式支持验证条件。"""
        store = TaskStore()
        result = write_plan(
            title="跨表匹配",
            content=SAMPLE_PLAN_JSON,
            store=store,
            workspace_root=str(tmp_path),
        )

        task_list = store.current
        assert task_list is not None
        assert len(task_list.items) == 4
        # 第一个子任务有验证条件
        assert task_list.items[0].verification_criteria == "行数 > 0"
        # 第二个子任务没有验证条件
        assert task_list.items[1].verification_criteria is None

        assert store.plan_file_path is not None

    def test_no_tasks_returns_error(self, tmp_path: Path) -> None:
        """缺少任务清单时文件仍写入，返回错误提示。"""
        store = TaskStore()
        result = write_plan(
            title="简单分析",
            content=SAMPLE_PLAN_NO_TASKS,
            store=store,
            workspace_root=str(tmp_path),
        )

        # 文件已写入
        plan_files = list((tmp_path / "plans").glob("plan_*.md"))
        assert len(plan_files) == 1

        # TaskList 未创建
        assert store.current is None
        assert store.plan_file_path is None

        # 返回包含错误提示
        assert "⚠️" in result
        assert "解析失败" in result

    def test_empty_title_raises(self, tmp_path: Path) -> None:
        """空标题抛出 ValueError。"""
        store = TaskStore()
        with pytest.raises(ValueError, match="标题不能为空"):
            write_plan(
                title="",
                content=SAMPLE_PLAN_CHECKBOX,
                store=store,
                workspace_root=str(tmp_path),
            )

    def test_empty_content_raises(self, tmp_path: Path) -> None:
        """空内容抛出 ValueError。"""
        store = TaskStore()
        with pytest.raises(ValueError, match="内容不能为空"):
            write_plan(
                title="测试",
                content="",
                store=store,
                workspace_root=str(tmp_path),
            )

    def test_replaces_existing_task_list(self, tmp_path: Path) -> None:
        """write_plan 默认覆盖已有 TaskList。"""
        store = TaskStore()
        # 先创建一个已有的 TaskList
        store.create("旧计划", ["旧任务1", "旧任务2"])

        result = write_plan(
            title="新计划",
            content=SAMPLE_PLAN_CHECKBOX,
            store=store,
            workspace_root=str(tmp_path),
        )

        # 已被新计划覆盖
        assert store.current is not None
        assert store.current.title == "数据汇总报告"
        assert len(store.current.items) == 4

    def test_file_content_matches_input(self, tmp_path: Path) -> None:
        """写入的文件内容与 content 参数一致。"""
        store = TaskStore()
        write_plan(
            title="测试",
            content=SAMPLE_PLAN_CHECKBOX,
            store=store,
            workspace_root=str(tmp_path),
        )

        plan_files = list((tmp_path / "plans").glob("plan_*.md"))
        content = plan_files[0].read_text(encoding="utf-8")
        assert content == SAMPLE_PLAN_CHECKBOX.strip()

    def test_unique_filenames(self, tmp_path: Path) -> None:
        """多次调用生成不同的文件名。"""
        store = TaskStore()
        write_plan(
            title="计划1",
            content=SAMPLE_PLAN_CHECKBOX,
            store=store,
            workspace_root=str(tmp_path),
        )
        write_plan(
            title="计划2",
            content=SAMPLE_PLAN_CHECKBOX,
            store=store,
            workspace_root=str(tmp_path),
        )

        plan_files = list((tmp_path / "plans").glob("plan_*.md"))
        assert len(plan_files) == 2


# ── get_tools ──

class TestGetTools:
    """get_tools 返回正确的 ToolDef。"""

    def test_returns_write_plan_tool(self, tmp_path: Path) -> None:
        store = TaskStore()
        tools = get_tools(store, str(tmp_path))
        assert len(tools) == 1
        assert tools[0].name == "write_plan"
        assert tools[0].write_effect == "none"

    def test_tool_func_works(self, tmp_path: Path) -> None:
        """通过 get_tools 返回的闭包函数能正常工作。"""
        store = TaskStore()
        tools = get_tools(store, str(tmp_path))
        tool_func = tools[0].func

        result = tool_func(title="测试计划", content=SAMPLE_PLAN_CHECKBOX)
        assert store.current is not None
        assert store.plan_file_path is not None


# ── TaskStore.plan_file_path ──

class TestTaskStorePlanFilePath:
    """TaskStore.plan_file_path 属性测试。"""

    def test_default_none(self) -> None:
        store = TaskStore()
        assert store.plan_file_path is None

    def test_set_and_get(self) -> None:
        store = TaskStore()
        store.plan_file_path = "plans/plan_test.md"
        assert store.plan_file_path == "plans/plan_test.md"

    def test_clear_resets_plan_file_path(self) -> None:
        store = TaskStore()
        store.plan_file_path = "plans/plan_test.md"
        store.create("test", ["task1"])
        store.clear()
        assert store.plan_file_path is None
        assert store.current is None
