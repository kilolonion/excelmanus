"""manage_skills 元工具测试。

覆盖 SkillManagementHandler 的 install / list / uninstall
以及边界条件。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from excelmanus.engine_core.tool_handlers import SkillManagementHandler


def _make_engine(
    *,
    has_manager: bool = True,
    skills: list[dict[str, Any]] | None = None,
    import_result: dict[str, Any] | None = None,
    import_error: Exception | None = None,
    delete_result: dict[str, Any] | None = None,
    delete_error: Exception | None = None,
    active_skills: list | None = None,
) -> MagicMock:
    """构造带 mock SkillpackManager 的 engine。"""
    engine = MagicMock()
    engine._tools_cache = {"cached": True}
    engine._config = SimpleNamespace()
    engine._active_skills = active_skills if active_skills is not None else []
    engine._loaded_skill_names = {}

    if has_manager:
        manager = MagicMock()

        if import_error:
            manager.import_skillpack_async = AsyncMock(side_effect=import_error)
        else:
            manager.import_skillpack_async = AsyncMock(
                return_value=import_result or {"name": "test-skill", "version": "1.0.0"}
            )

        manager.list_skillpacks = MagicMock(return_value=skills or [])

        if delete_error:
            manager.delete_skillpack = MagicMock(side_effect=delete_error)
        else:
            manager.delete_skillpack = MagicMock(
                return_value=delete_result or {"name": "test-skill"}
            )

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


class TestInstall:
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
        )

    @pytest.mark.asyncio
    async def test_install_local_path(self):
        path = "/tmp/skills/my-skill/SKILL.md"
        engine = _make_engine(
            import_result={"name": "my-skill", "version": "1.0.0", "description": "本地技能"},
        )
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "install", "slug": path})

        assert outcome.success is True
        assert "安装成功" in outcome.result_str
        engine._require_skillpack_manager().import_skillpack_async.assert_called_once_with(
            source="local_path", value=path, actor="agent", overwrite=False,
        )

    @pytest.mark.asyncio
    async def test_install_rejects_bare_slug(self):
        engine = _make_engine()
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "install", "slug": "data-cleaning"})

        assert outcome.success is False
        assert "GitHub URL" in outcome.result_str
        engine._require_skillpack_manager().import_skillpack_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_install_with_overwrite(self):
        url = "https://github.com/user/repo/blob/main/SKILL.md"
        engine = _make_engine(
            import_result={"name": "data-cleaning", "version": "2.0.0"},
        )
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "install", "slug": url, "overwrite": True})

        assert outcome.success is True
        engine._require_skillpack_manager().import_skillpack_async.assert_called_once_with(
            source="github_url", value=url, actor="agent", overwrite=True,
        )

    @pytest.mark.asyncio
    async def test_install_conflict_error(self):
        url = "https://github.com/user/repo/blob/main/SKILL.md"
        engine = _make_engine(
            import_error=RuntimeError("技能 'data-cleaning' 已存在"),
        )
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "install", "slug": url})

        assert outcome.success is False
        assert "已存在" in outcome.result_str
        assert "overwrite" in outcome.result_str

    @pytest.mark.asyncio
    async def test_install_general_error(self):
        url = "https://github.com/user/repo/blob/main/SKILL.md"
        engine = _make_engine(
            import_error=RuntimeError("下载失败"),
        )
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "install", "slug": url})

        assert outcome.success is False
        assert "安装失败" in outcome.result_str

    @pytest.mark.asyncio
    async def test_install_missing_slug(self):
        engine = _make_engine()
        handler = _make_handler(engine)
        outcome = await _call(handler, {"action": "install"})

        assert outcome.success is False
        assert "slug" in outcome.result_str


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

    @pytest.mark.asyncio
    async def test_removed_market_actions_rejected(self):
        engine = _make_engine()
        handler = _make_handler(engine)
        for action in ("search", "detail", "update"):
            outcome = await _call(handler, {"action": action, "query": "x", "slug": "x"})
            assert outcome.success is False
            assert "不支持的操作" in outcome.result_str


class TestMetaToolSchema:
    """验证 manage_skills 工具 schema 在 build_meta_tools 中正确生成。"""

    def test_manage_skills_schema_present(self):
        from excelmanus.engine_core.meta_tools import MetaToolBuilder

        engine = MagicMock()
        engine._skill_router = None
        engine._skill_resolver = MagicMock()
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.build_catalog.return_value = ("", [])
        engine._active_skills = []

        builder = MetaToolBuilder(engine)
        tools = builder.build_meta_tools()
        tool_names = [t["function"]["name"] for t in tools]

        assert "manage_skills" in tool_names

    def test_manage_skills_schema_structure(self):
        from excelmanus.engine_core.meta_tools import MetaToolBuilder

        engine = MagicMock()
        engine._skill_router = None
        engine._skill_resolver = MagicMock()
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.build_catalog.return_value = ("", [])
        engine._active_skills = []

        builder = MetaToolBuilder(engine)
        tools = builder.build_meta_tools()
        manage_skills = next(
            t for t in tools if t["function"]["name"] == "manage_skills"
        )

        params = manage_skills["function"]["parameters"]
        assert "action" in params["properties"]
        assert "query" not in params["properties"]
        assert "slug" in params["properties"]
        assert "overwrite" in params["properties"]
        assert params["properties"]["action"]["enum"] == [
            "install", "list", "uninstall",
        ]
        assert params["required"] == ["action"]


class TestToolCacheInvalidation:
    """验证安装/卸载后 tools_cache 被正确失效。"""

    @pytest.mark.asyncio
    async def test_install_invalidates_cache(self):
        engine = _make_engine()
        handler = _make_handler(engine)
        assert engine._tools_cache is not None

        await _call(handler, {
            "action": "install",
            "slug": "https://github.com/user/repo/blob/main/SKILL.md",
        })
        assert engine._tools_cache is None

    @pytest.mark.asyncio
    async def test_uninstall_invalidates_cache(self):
        engine = _make_engine()
        handler = _make_handler(engine)
        assert engine._tools_cache is not None

        await _call(handler, {"action": "uninstall", "slug": "test-skill"})
        assert engine._tools_cache is None

    @pytest.mark.asyncio
    async def test_failed_install_does_not_invalidate_cache(self):
        engine = _make_engine(import_error=RuntimeError("失败"))
        handler = _make_handler(engine)
        original_cache = engine._tools_cache

        await _call(handler, {
            "action": "install",
            "slug": "https://github.com/user/repo/blob/main/SKILL.md",
        })
        assert engine._tools_cache == original_cache


class TestUninstallCleanup:
    """验证卸载后清理 _active_skills。"""

    @pytest.mark.asyncio
    async def test_uninstall_removes_active_skill(self):
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
