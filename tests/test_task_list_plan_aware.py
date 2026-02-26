"""任务清单 + 计划文档注入 system prompt 测试。"""

from __future__ import annotations

import pytest

from excelmanus.task_list import TaskStore, TaskStatus


class TestBuildTaskPlanNotice:
    """测试 _build_task_plan_notice 生成的 system prompt 注入内容。"""

    def _make_builder(self):
        """创建最小化的 ContextBuilder mock。"""
        from unittest.mock import MagicMock
        from excelmanus.engine_core.context_builder import ContextBuilder

        engine = MagicMock()
        engine._task_store = TaskStore()
        builder = ContextBuilder.__new__(ContextBuilder)
        builder._engine = engine
        return builder, engine._task_store

    def test_no_task_list_returns_empty(self) -> None:
        """无 TaskList 时返回空字符串。"""
        builder, store = self._make_builder()
        assert builder._build_task_plan_notice() == ""

    def test_task_list_without_plan_path(self) -> None:
        """有 TaskList 但无 plan_file_path 时，不显示计划文档引用。"""
        builder, store = self._make_builder()
        store.create("测试计划", ["任务A", "任务B"])

        notice = builder._build_task_plan_notice()
        assert "## 当前计划与任务清单" in notice
        assert "任务清单状态「测试计划」" in notice
        assert "任务A" in notice
        assert "任务B" in notice
        assert "📄 计划文档" not in notice

    def test_task_list_with_plan_path(self) -> None:
        """有 TaskList + plan_file_path 时，显示计划文档引用。"""
        builder, store = self._make_builder()
        store.create("数据汇总", ["读取源数据", "清洗", "汇总"])
        store.plan_file_path = "plans/plan_20260226T1530_abc123.md"

        notice = builder._build_task_plan_notice()
        assert "## 当前计划与任务清单" in notice
        assert "📄 计划文档: `plans/plan_20260226T1530_abc123.md`" in notice
        assert "任务清单状态「数据汇总」" in notice
        assert "读取源数据" in notice

    def test_status_icons_rendered(self) -> None:
        """任务状态图标正确渲染。"""
        builder, store = self._make_builder()
        store.create("测试", ["已完成", "进行中", "待做"])
        # 模拟状态变更
        store.update_item(0, TaskStatus.IN_PROGRESS)
        store.update_item(0, TaskStatus.COMPLETED)
        store.update_item(1, TaskStatus.IN_PROGRESS)

        notice = builder._build_task_plan_notice()
        assert "✅" in notice  # completed
        assert "🟡" in notice  # in_progress
        assert "🔵" in notice  # pending

    def test_notice_updates_after_task_update(self) -> None:
        """task_update 后 notice 内容实时更新。"""
        builder, store = self._make_builder()
        store.create("测试", ["步骤1", "步骤2"])

        notice_before = builder._build_task_plan_notice()
        assert "pending" in notice_before

        store.update_item(0, TaskStatus.IN_PROGRESS)
        notice_after = builder._build_task_plan_notice()
        assert "in_progress" in notice_after

    def test_clear_removes_notice(self) -> None:
        """clear 后 notice 恢复为空。"""
        builder, store = self._make_builder()
        store.create("测试", ["任务1"])
        store.plan_file_path = "plans/test.md"

        assert builder._build_task_plan_notice() != ""

        store.clear()
        assert builder._build_task_plan_notice() == ""
