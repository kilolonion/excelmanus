"""manage_skills 元工具测试。

覆盖 SkillManagementHandler 的全部 5 个操作：
search / detail / install / list / uninstall
以及安全门控和边界条件。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.engine_core.tool_handlers import SkillManagementHandler


# ── helpers ───────────────────────────────────────────────


def _make_engine(
    *,
    has_manager: bool = True,
    external_safe_mode: bool = False,
    skills: list[dict[str, Any]] | None = None,
    clawhub_search_results: list[dict[str, Any]] | None = None,
    clawhub_detail: dict[str, Any] | None = None,
    import_result: dict[str, Any] | None = None,
    import_error: Exception | None = None,
    delete_result: dict[str, Any] | None = None,
    delete_error: Exception | None = None,
    clawhub_search_error: Exception | None = None,
    clawhub_detail_error: Exception | None = None,
    clawhub_check_updates_result: list[dict[str, Any]] | None = None,
    clawhub_check_updates_error: Exception | None = None,
    clawhub_update_result: list[dict[str, Any]] | None = None,
    clawhub_update_error: Exception | None = None,
    active_skills: list | None = None,
    lockfile: Any = "auto",
) -> MagicMock:
    """构造带 mock SkillpackManager 的 engine。"""
    engine = MagicMock()
    engine._tools_cache = {"cached": True}
    engine._config = SimpleNamespace(external_safe_mode=external_safe_mode)
    engine._active_skills = active_skills if active_skills is not None else []
    engine._loaded_skill_names = {}

    if has_manager:
        manager = MagicMock()

        # clawhub_search
        if clawhub_search_error:
            manager.clawhub_search = AsyncMock(side_effect=clawhub_search_error)
        else:
            manager.clawhub_search = AsyncMock(
                return_value=clawhub_search_results or []
            )

        # clawhub_skill_detail
        if clawhub_detail_error:
            manager.clawhub_skill_detail = AsyncMock(side_effect=clawhub_detail_error)
        else:
            manager.clawhub_skill_detail = AsyncMock(
                return_value=clawhub_detail or {}
            )

        # import_skillpack_async
        if import_error:
            manager.import_skillpack_async = AsyncMock(side_effect=import_error)
        else:
            manager.import_skillpack_async = AsyncMock(
                return_value=import_result or {"name": "test-skill", "version": "1.0.0"}
            )

        # list_skillpacks
        manager.list_skillpacks = MagicMock(return_value=skills or [])

        # delete_skillpack
        if delete_error:
            manager.delete_skillpack = MagicMock(side_effect=delete_error)
        else:
            manager.delete_skillpack = MagicMock(
                return_value=delete_result or {"name": "test-skill"}
            )

        # clawhub_check_updates
        if clawhub_check_updates_error:
            manager.clawhub_check_updates = AsyncMock(side_effect=clawhub_check_updates_error)
        else:
            manager.clawhub_check_updates = AsyncMock(
                return_value=clawhub_check_updates_result or []
            )

        # clawhub_update
        if clawhub_update_error:
            manager.clawhub_update = AsyncMock(side_effect=clawhub_update_error)
        else:
            manager.clawhub_update = AsyncMock(
                return_value=clawhub_update_result or []
            )

        # lockfile mock
        if lockfile == "auto":
            lf = MagicMock()
            lf.remove = MagicMock(return_value=True)
            manager._clawhub_lockfile = lf
        elif lockfile is None:
            manager._clawhub_lockfile = None
        else:
            manager._clawhub_lockfile = lockfile

        engine._require_skillpack_manager = MagicMock(return_value=manager)
    else:
        engine._require_skillpack_manager = MagicMock(
            side_effect=RuntimeError("skillpack 管理器不可用。")
        )

    return engine


def _make_handler(engine: MagicMock) -> SkillManagementHandler:
    dispatcher = MagicMock()
    return SkillManagementHandler(engine, dispatcher)


async def _call(handler, arguments: dict[str, Any]):
    return await handler.handle(
        "manage_skills", "call_123", arguments,
    )


# ── TestSearch ────────────────────────────────────────────


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_success(self):
        engine = _make_engine(clawhub_search_results=[
            {"slug": "data-cleaning", "display_name": "Data Cleaning", "summary": "清洗数据", "version": "1.0.0"},
            {"slug": "pivot-table", "display_name": "Pivot Table", "summary": "数据透视", "version": "2.1.0"},
        ])
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "search", "query": "数据"})

        assert outcome.success is True
        assert "找到 2 个相关技能" in outcome.result_str
        assert "data-cleaning" in outcome.result_str
        assert "pivot-table" in outcome.result_str
        assert "清洗数据" in outcome.result_str

    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        engine = _make_engine(clawhub_search_results=[])
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "search", "query": "不存在的技能"})

        assert outcome.success is True
        assert "未找到" in outcome.result_str

    @pytest.mark.asyncio
    async def test_search_missing_query(self):
        engine = _make_engine()
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "search"})

        assert outcome.success is False
        assert "query" in outcome.result_str

    @pytest.mark.asyncio
    async def test_search_clawhub_error(self):
        engine = _make_engine(clawhub_search_error=RuntimeError("网络超时"))
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "search", "query": "test"})

        assert outcome.success is False
        assert "搜索失败" in outcome.result_str

    @pytest.mark.asyncio
    async def test_search_manager_unavailable(self):
        engine = _make_engine(has_manager=False)
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "search", "query": "test"})

        assert outcome.success is False
        assert "不可用" in outcome.result_str


# ── TestDetail ────────────────────────────────────────────


class TestDetail:
    @pytest.mark.asyncio
    async def test_detail_success(self):
        engine = _make_engine(clawhub_detail={
            "slug": "data-cleaning",
            "display_name": "Data Cleaning",
            "latest_version": "1.2.0",
            "summary": "自动清洗数据",
            "tags": ["excel", "data"],
            "owner_display_name": "ExcelManus",
            "latest_changelog": "修复了边界情况",
            "stats": {"downloads": 1500},
        })
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "detail", "slug": "data-cleaning"})

        assert outcome.success is True
        assert "Data Cleaning" in outcome.result_str
        assert "1.2.0" in outcome.result_str
        assert "自动清洗数据" in outcome.result_str
        assert "excel" in outcome.result_str
        assert "1500" in outcome.result_str

    @pytest.mark.asyncio
    async def test_detail_missing_slug(self):
        engine = _make_engine()
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "detail"})

        assert outcome.success is False
        assert "slug" in outcome.result_str

    @pytest.mark.asyncio
    async def test_detail_not_found(self):
        engine = _make_engine(clawhub_detail_error=RuntimeError("404 Not Found"))
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "detail", "slug": "nonexistent"})

        assert outcome.success is False
        assert "详情失败" in outcome.result_str


# ── TestInstall ───────────────────────────────────────────


class TestInstall:
    @pytest.mark.asyncio
    async def test_install_clawhub_success(self):
        engine = _make_engine(
            import_result={"name": "data-cleaning", "version": "1.0.0", "description": "清洗数据"},
        )
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "install", "slug": "data-cleaning"})

        assert outcome.success is True
        assert "安装成功" in outcome.result_str
        assert "data-cleaning" in outcome.result_str
        assert "activate_skill" in outcome.result_str
        # 验证 tools_cache 被失效
        assert engine._tools_cache is None
        # 验证调用参数
        engine._require_skillpack_manager().import_skillpack_async.assert_called_once_with(
            source="clawhub", value="data-cleaning", actor="agent", overwrite=False,
            version=None,
        )

    @pytest.mark.asyncio
    async def test_install_github_url(self):
        url = "https://github.com/user/repo/blob/main/skills/my-skill/SKILL.md"
        engine = _make_engine(
            import_result={"name": "my-skill", "version": "1.0.0"},
        )
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "install", "slug": url})

        assert outcome.success is True
        engine._require_skillpack_manager().import_skillpack_async.assert_called_once_with(
            source="github_url", value=url, actor="agent", overwrite=False,
            version=None,
        )

    @pytest.mark.asyncio
    async def test_install_with_overwrite(self):
        engine = _make_engine(
            import_result={"name": "data-cleaning", "version": "2.0.0"},
        )
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "install", "slug": "data-cleaning", "overwrite": True})

        assert outcome.success is True
        engine._require_skillpack_manager().import_skillpack_async.assert_called_once_with(
            source="clawhub", value="data-cleaning", actor="agent", overwrite=True,
            version=None,
        )

    @pytest.mark.asyncio
    async def test_install_blocked_by_safe_mode(self):
        engine = _make_engine(external_safe_mode=True)
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "install", "slug": "data-cleaning"})

        assert outcome.success is False
        assert "安全模式" in outcome.result_str

    @pytest.mark.asyncio
    async def test_install_conflict_error(self):
        engine = _make_engine(
            import_error=RuntimeError("技能 'data-cleaning' 已存在"),
        )
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "install", "slug": "data-cleaning"})

        assert outcome.success is False
        assert "已存在" in outcome.result_str
        assert "overwrite" in outcome.result_str

    @pytest.mark.asyncio
    async def test_install_general_error(self):
        engine = _make_engine(
            import_error=RuntimeError("下载失败"),
        )
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "install", "slug": "data-cleaning"})

        assert outcome.success is False
        assert "安装失败" in outcome.result_str

    @pytest.mark.asyncio
    async def test_install_missing_slug(self):
        engine = _make_engine()
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "install"})

        assert outcome.success is False
        assert "slug" in outcome.result_str


# ── TestList ──────────────────────────────────────────────


class TestList:
    @pytest.mark.asyncio
    async def test_list_with_skills(self):
        engine = _make_engine(skills=[
            {"name": "data-cleaning", "description": "清洗数据", "version": "1.0.0"},
            {"name": "pivot-table", "description": "数据透视表", "version": "2.1.0"},
        ])
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "list"})

        assert outcome.success is True
        assert "已安装 2 个技能" in outcome.result_str
        assert "data-cleaning" in outcome.result_str
        assert "pivot-table" in outcome.result_str

    @pytest.mark.asyncio
    async def test_list_empty(self):
        engine = _make_engine(skills=[])
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "list"})

        assert outcome.success is True
        assert "没有已安装" in outcome.result_str

    @pytest.mark.asyncio
    async def test_list_manager_unavailable(self):
        engine = _make_engine(has_manager=False)
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "list"})

        assert outcome.success is False
        assert "不可用" in outcome.result_str


# ── TestUninstall ─────────────────────────────────────────


class TestUninstall:
    @pytest.mark.asyncio
    async def test_uninstall_success(self):
        engine = _make_engine(
            delete_result={"name": "data-cleaning"},
        )
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "uninstall", "slug": "data-cleaning"})

        assert outcome.success is True
        assert "已卸载" in outcome.result_str
        assert engine._tools_cache is None
        engine._require_skillpack_manager().delete_skillpack.assert_called_once_with(
            name="data-cleaning", actor="agent",
        )

    @pytest.mark.asyncio
    async def test_uninstall_blocked_by_safe_mode(self):
        engine = _make_engine(external_safe_mode=True)
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "uninstall", "slug": "data-cleaning"})

        assert outcome.success is False
        assert "安全模式" in outcome.result_str

    @pytest.mark.asyncio
    async def test_uninstall_not_found(self):
        engine = _make_engine(
            delete_error=RuntimeError("未找到 Skillpack `nonexistent`。"),
        )
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "uninstall", "slug": "nonexistent"})

        assert outcome.success is False
        assert "卸载失败" in outcome.result_str

    @pytest.mark.asyncio
    async def test_uninstall_missing_slug(self):
        engine = _make_engine()
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "uninstall"})

        assert outcome.success is False
        assert "slug" in outcome.result_str


# ── TestInvalidAction ─────────────────────────────────────


class TestInvalidAction:
    @pytest.mark.asyncio
    async def test_unknown_action(self):
        engine = _make_engine()
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "upgrade"})

        assert outcome.success is False
        assert "不支持的操作" in outcome.result_str

    @pytest.mark.asyncio
    async def test_empty_action(self):
        engine = _make_engine()
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": ""})

        assert outcome.success is False
        assert "不支持的操作" in outcome.result_str


# ── TestMetaToolSchema ────────────────────────────────────


class TestMetaToolSchema:
    """验证 manage_skills 工具 schema 在 build_meta_tools 中正确生成。"""

    def test_manage_skills_schema_present(self):
        """manage_skills 工具应出现在 meta_tools 列表中。"""
        from excelmanus.engine_core.meta_tools import MetaToolBuilder

        engine = MagicMock()
        engine._skill_router = None
        engine._skill_resolver = MagicMock()
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.build_catalog.return_value = ("", [])
        engine._active_skills = []
        engine._bench_mode = False

        builder = MetaToolBuilder(engine)
        tools = builder.build_meta_tools()
        tool_names = [t["function"]["name"] for t in tools]

        assert "manage_skills" in tool_names

    def test_manage_skills_schema_structure(self):
        """验证 manage_skills 工具 schema 结构完整。"""
        from excelmanus.engine_core.meta_tools import MetaToolBuilder

        engine = MagicMock()
        engine._skill_router = None
        engine._skill_resolver = MagicMock()
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.build_catalog.return_value = ("", [])
        engine._active_skills = []
        engine._bench_mode = False

        builder = MetaToolBuilder(engine)
        tools = builder.build_meta_tools()
        manage_skills = next(
            t for t in tools if t["function"]["name"] == "manage_skills"
        )

        params = manage_skills["function"]["parameters"]
        assert "action" in params["properties"]
        assert "query" in params["properties"]
        assert "slug" in params["properties"]
        assert "overwrite" in params["properties"]
        assert params["properties"]["action"]["enum"] == [
            "search", "install", "detail", "list", "uninstall", "update",
        ]
        assert params["required"] == ["action"]


# ── TestToolCacheInvalidation ─────────────────────────────


class TestToolCacheInvalidation:
    """验证安装/卸载后 tools_cache 被正确失效。"""

    @pytest.mark.asyncio
    async def test_install_invalidates_cache(self):
        engine = _make_engine()
        handler = _make_handler(engine)
        assert engine._tools_cache is not None

        await _call(handler, {"action": "install", "slug": "test-skill"})
        assert engine._tools_cache is None

    @pytest.mark.asyncio
    async def test_uninstall_invalidates_cache(self):
        engine = _make_engine()
        handler = _make_handler(engine)
        assert engine._tools_cache is not None

        await _call(handler, {"action": "uninstall", "slug": "test-skill"})
        assert engine._tools_cache is None

    @pytest.mark.asyncio
    async def test_search_does_not_invalidate_cache(self):
        engine = _make_engine(clawhub_search_results=[
            {"slug": "x", "display_name": "X", "summary": "", "version": "1.0"},
        ])
        handler = _make_handler(engine)
        original_cache = engine._tools_cache

        await _call(handler, {"action": "search", "query": "test"})
        assert engine._tools_cache == original_cache

    @pytest.mark.asyncio
    async def test_failed_install_does_not_invalidate_cache(self):
        engine = _make_engine(import_error=RuntimeError("失败"))
        handler = _make_handler(engine)
        original_cache = engine._tools_cache

        await _call(handler, {"action": "install", "slug": "test"})
        assert engine._tools_cache == original_cache


# ── TestUninstallCleanup ───────────────────────────────


class TestUninstallCleanup:
    """验证卸载后清理 _active_skills 和 lockfile。"""

    @pytest.mark.asyncio
    async def test_uninstall_removes_active_skill(self):
        """_active_skills 中的已激活技能应被移除。"""
        skill_mock = SimpleNamespace(name="data-cleaning")
        engine = _make_engine(
            delete_result={"name": "data-cleaning"},
            active_skills=[skill_mock],
        )
        engine._loaded_skill_names = {"data-cleaning": 3}
        handler = _make_handler(engine)

        outcome = await _call(handler, {"action": "uninstall", "slug": "data-cleaning"})

        assert outcome.success is True
        assert len(engine._active_skills) == 0
        assert "data-cleaning" not in engine._loaded_skill_names

    @pytest.mark.asyncio
    async def test_uninstall_preserves_other_active_skills(self):
        """卸载一个技能不影响其他已激活技能。"""
        skill_a = SimpleNamespace(name="data-cleaning")
        skill_b = SimpleNamespace(name="pivot-table")
        engine = _make_engine(
            delete_result={"name": "data-cleaning"},
            active_skills=[skill_a, skill_b],
        )
        engine._loaded_skill_names = {"data-cleaning": 3, "pivot-table": 5}
        handler = _make_handler(engine)

        await _call(handler, {"action": "uninstall", "slug": "data-cleaning"})

        assert len(engine._active_skills) == 1
        assert engine._active_skills[0].name == "pivot-table"
        assert "pivot-table" in engine._loaded_skill_names

    @pytest.mark.asyncio
    async def test_uninstall_cleans_lockfile(self):
        """ClawHub lockfile 条目应被移除。"""
        engine = _make_engine(
            delete_result={"name": "data-cleaning"},
        )
        handler = _make_handler(engine)

        await _call(handler, {"action": "uninstall", "slug": "data-cleaning"})

        manager = engine._require_skillpack_manager()
        manager._clawhub_lockfile.remove.assert_called_once_with("data-cleaning")

    @pytest.mark.asyncio
    async def test_uninstall_no_lockfile_no_crash(self):
        """无 lockfile 时不崩溃。"""
        engine = _make_engine(
            delete_result={"name": "data-cleaning"},
            lockfile=None,
        )
        handler = _make_handler(engine)

        outcome = await _call(handler, {"action": "uninstall", "slug": "data-cleaning"})
        assert outcome.success is True


# ── TestUpdate ──────────────────────────────────────


class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_check_no_slug_shows_available(self):
        """无 slug → 检查可用更新。"""
        engine = _make_engine(clawhub_check_updates_result=[
            {"slug": "data-cleaning", "installed_version": "1.0.0", "latest_version": "1.2.0", "update_available": True},
            {"slug": "pivot-table", "installed_version": "2.0.0", "latest_version": "2.0.0", "update_available": False},
        ])
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "update"})

        assert outcome.success is True
        assert "发现 1 个可更新" in outcome.result_str
        assert "data-cleaning" in outcome.result_str
        assert "1.0.0" in outcome.result_str
        assert "1.2.0" in outcome.result_str

    @pytest.mark.asyncio
    async def test_update_check_all_up_to_date(self):
        engine = _make_engine(clawhub_check_updates_result=[
            {"slug": "x", "installed_version": "1.0", "latest_version": "1.0", "update_available": False},
        ])
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "update"})

        assert outcome.success is True
        assert "最新版本" in outcome.result_str

    @pytest.mark.asyncio
    async def test_update_check_empty(self):
        engine = _make_engine(clawhub_check_updates_result=[])
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "update"})

        assert outcome.success is True
        assert "最新版本" in outcome.result_str

    @pytest.mark.asyncio
    async def test_update_execute_success(self):
        """有 slug → 执行更新。"""
        engine = _make_engine(clawhub_update_result=[
            {"slug": "data-cleaning", "version": "1.2.0", "success": True},
        ])
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "update", "slug": "data-cleaning"})

        assert outcome.success is True
        assert "已更新" in outcome.result_str
        assert "1.2.0" in outcome.result_str
        assert engine._tools_cache is None

    @pytest.mark.asyncio
    async def test_update_execute_failure(self):
        engine = _make_engine(clawhub_update_result=[
            {"slug": "data-cleaning", "success": False, "error": "网络超时"},
        ])
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "update", "slug": "data-cleaning"})

        assert outcome.success is False
        assert "网络超时" in outcome.result_str

    @pytest.mark.asyncio
    async def test_update_blocked_by_safe_mode(self):
        engine = _make_engine(external_safe_mode=True)
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "update", "slug": "x"})

        assert outcome.success is False
        assert "安全模式" in outcome.result_str

    @pytest.mark.asyncio
    async def test_update_check_error(self):
        engine = _make_engine(clawhub_check_updates_error=RuntimeError("ClawHub 不可用"))
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "update"})

        assert outcome.success is False
        assert "检查更新失败" in outcome.result_str

    @pytest.mark.asyncio
    async def test_update_execute_exception(self):
        engine = _make_engine(clawhub_update_error=RuntimeError("下载失败"))
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "update", "slug": "x"})

        assert outcome.success is False
        assert "更新失败" in outcome.result_str
