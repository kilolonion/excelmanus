"""技能安装速度优化测试。

覆盖 5 个优化点：
P0: ClawHubClient HTTP 连接池复用
P1: 版本并行解析
P2: search→install 版本缓存
P3: loader 增量加载 load_single()
P4: GitHub 并行文件下载
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.skillpacks.clawhub import (
    ClawHubClient,
    ClawHubError,
    ClawHubNotFoundError,
    ClawHubSearchResult,
    ClawHubSkillDetail,
    ClawHubVersionInfo,
)


# ═══════════════════════════════════════════════════════════
# P0: HTTP 连接池复用
# ═══════════════════════════════════════════════════════════


class TestP0ConnectionPool:
    """P0: ClawHubClient 共享 HTTP 连接池。"""

    def test_ensure_client_creates_once(self):
        """_ensure_client 懒初始化，多次调用返回同一实例。"""
        client = ClawHubClient()
        http1 = client._ensure_client()
        http2 = client._ensure_client()
        assert http1 is http2

    def test_ensure_client_recreates_after_close(self):
        """close() 后 _ensure_client 重建新实例。"""
        client = ClawHubClient()
        http1 = client._ensure_client()
        asyncio.get_event_loop().run_until_complete(client.close())
        http2 = client._ensure_client()
        assert http1 is not http2

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        """close() 多次调用不报错。"""
        client = ClawHubClient()
        client._ensure_client()
        await client.close()
        await client.close()  # 不应抛出
        assert client._http_client is None

    @pytest.mark.asyncio
    async def test_close_without_init(self):
        """未初始化时 close() 不报错。"""
        client = ClawHubClient()
        await client.close()  # _http_client is None

    @pytest.mark.asyncio
    async def test_http_get_reuses_client(self):
        """多次 _http_get 复用同一连接池。"""
        client = ClawHubClient(registry_url="https://example.com")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": "ok"}

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False

        client._http_client = mock_http

        await client._http_get("https://example.com/api/v1/search")
        await client._http_get("https://example.com/api/v1/skills/foo")

        assert mock_http.get.call_count == 2
        # 确认没有创建新的 client
        assert client._http_client is mock_http


# ═══════════════════════════════════════════════════════════
# P1: 版本并行解析
# ═══════════════════════════════════════════════════════════


class TestP1ParallelVersionResolve:
    """P1: _resolve_version_parallel 并行发起两个请求。"""

    @pytest.mark.asyncio
    async def test_resolve_version_uses_first_result(self):
        """resolve_version 成功时直接使用其结果。"""
        client = ClawHubClient()
        client.resolve_version = AsyncMock(
            return_value=ClawHubVersionInfo(match_version="1.0.0", latest_version="1.2.0")
        )
        client.get_skill = AsyncMock(
            return_value=ClawHubSkillDetail(
                slug="test", display_name="Test", summary="", tags=[],
                created_at=0, updated_at=0, latest_version="1.2.0",
            )
        )

        version = await client._resolve_version_parallel("test")
        assert version == "1.2.0"

    @pytest.mark.asyncio
    async def test_fallback_to_get_skill_when_resolve_fails(self):
        """resolve_version 失败时使用 get_skill 结果。"""
        client = ClawHubClient()
        client.resolve_version = AsyncMock(side_effect=ClawHubError("fail"))
        client.get_skill = AsyncMock(
            return_value=ClawHubSkillDetail(
                slug="test", display_name="Test", summary="", tags=[],
                created_at=0, updated_at=0, latest_version="2.0.0",
            )
        )

        version = await client._resolve_version_parallel("test")
        assert version == "2.0.0"

    @pytest.mark.asyncio
    async def test_both_fail_raises(self):
        """两者都失败时抛出 ClawHubNotFoundError。"""
        client = ClawHubClient()
        client.resolve_version = AsyncMock(side_effect=ClawHubError("fail1"))
        client.get_skill = AsyncMock(side_effect=ClawHubError("fail2"))

        with pytest.raises(ClawHubNotFoundError, match="无可用版本"):
            await client._resolve_version_parallel("test")

    @pytest.mark.asyncio
    async def test_parallel_execution(self):
        """两个请求确实并行执行（总时间 < 两者之和）。"""
        client = ClawHubClient()

        async def slow_resolve(slug, version=None):
            await asyncio.sleep(0.1)
            return ClawHubVersionInfo(match_version=None, latest_version="1.0.0")

        async def slow_get_skill(slug):
            await asyncio.sleep(0.1)
            return ClawHubSkillDetail(
                slug=slug, display_name="", summary="", tags=[],
                created_at=0, updated_at=0, latest_version="1.0.0",
            )

        client.resolve_version = slow_resolve
        client.get_skill = slow_get_skill

        start = time.monotonic()
        await client._resolve_version_parallel("test")
        elapsed = time.monotonic() - start

        # 并行应 < 0.2s（串行需 0.2s）
        assert elapsed < 0.18

    @pytest.mark.asyncio
    async def test_resolve_returns_none_uses_get_skill(self):
        """resolve_version 返回 None latest 时回退到 get_skill。"""
        client = ClawHubClient()
        client.resolve_version = AsyncMock(
            return_value=ClawHubVersionInfo(match_version=None, latest_version=None)
        )
        client.get_skill = AsyncMock(
            return_value=ClawHubSkillDetail(
                slug="test", display_name="Test", summary="", tags=[],
                created_at=0, updated_at=0, latest_version="3.0.0",
            )
        )

        version = await client._resolve_version_parallel("test")
        assert version == "3.0.0"


# ═══════════════════════════════════════════════════════════
# P2: search→install 版本缓存
# ═══════════════════════════════════════════════════════════


class TestP2VersionCache:
    """P2: SkillManagementHandler 版本缓存。"""

    def _make_handler(self, **kwargs):
        from excelmanus.engine_core.tool_handlers import SkillManagementHandler

        engine = MagicMock()
        engine._tools_cache = {"cached": True}
        engine._config = SimpleNamespace(external_safe_mode=False)
        engine._active_skills = []
        engine._loaded_skill_names = {}

        manager = MagicMock()
        manager.clawhub_search = AsyncMock(
            return_value=kwargs.get("search_results", [])
        )
        manager.import_skillpack_async = AsyncMock(
            return_value=kwargs.get("import_result", {"name": "test", "version": "1.0.0"})
        )
        manager.list_skillpacks = MagicMock(return_value=[])
        engine._require_skillpack_manager = MagicMock(return_value=manager)

        dispatcher = MagicMock()
        handler = SkillManagementHandler(engine, dispatcher)
        return handler, engine, manager

    def test_cache_version_stores(self):
        handler, _, _ = self._make_handler()
        handler._cache_version("my-skill", "2.1.0")
        assert handler._get_cached_version("my-skill") == "2.1.0"

    def test_cache_version_ignores_none(self):
        handler, _, _ = self._make_handler()
        handler._cache_version("my-skill", None)
        assert handler._get_cached_version("my-skill") is None

    def test_cache_version_ignores_empty_slug(self):
        handler, _, _ = self._make_handler()
        handler._cache_version("", "1.0.0")
        assert handler._get_cached_version("") is None

    def test_cache_ttl_expires(self):
        handler, _, _ = self._make_handler()
        handler._cache_version("my-skill", "1.0.0")
        # 手动过期
        handler._version_cache["my-skill"] = ("1.0.0", time.monotonic() - 400)
        assert handler._get_cached_version("my-skill") is None

    @pytest.mark.asyncio
    async def test_search_populates_cache(self):
        """search 操作将结果中的版本号缓存。"""
        handler, _, _ = self._make_handler(
            search_results=[
                {"slug": "skill-a", "display_name": "A", "version": "1.5.0", "summary": ""},
                {"slug": "skill-b", "display_name": "B", "version": "2.0.0", "summary": ""},
            ]
        )
        await handler.handle("manage_skills", "tc1", {"action": "search", "query": "test"})
        assert handler._get_cached_version("skill-a") == "1.5.0"
        assert handler._get_cached_version("skill-b") == "2.0.0"

    @pytest.mark.asyncio
    async def test_install_uses_cached_version(self):
        """install 操作使用缓存版本号。"""
        handler, _, manager = self._make_handler(
            search_results=[
                {"slug": "my-skill", "display_name": "My Skill", "version": "3.0.0", "summary": ""},
            ]
        )
        # 先搜索
        await handler.handle("manage_skills", "tc1", {"action": "search", "query": "test"})
        # 再安装
        await handler.handle("manage_skills", "tc2", {"action": "install", "slug": "my-skill"})

        call_kwargs = manager.import_skillpack_async.call_args
        assert call_kwargs.kwargs.get("version") == "3.0.0"

    @pytest.mark.asyncio
    async def test_install_no_cache_passes_none(self):
        """没有缓存时 version=None。"""
        handler, _, manager = self._make_handler()
        await handler.handle("manage_skills", "tc1", {"action": "install", "slug": "unknown-skill"})

        call_kwargs = manager.import_skillpack_async.call_args
        assert call_kwargs.kwargs.get("version") is None

    @pytest.mark.asyncio
    async def test_github_url_skips_cache(self):
        """GitHub URL 不使用版本缓存。"""
        handler, _, manager = self._make_handler()
        handler._cache_version("http-skill", "1.0.0")

        await handler.handle("manage_skills", "tc1", {
            "action": "install",
            "slug": "https://github.com/owner/repo/blob/main/SKILL.md",
        })

        call_kwargs = manager.import_skillpack_async.call_args
        assert call_kwargs.kwargs.get("version") is None


# ═══════════════════════════════════════════════════════════
# P3: loader 增量加载
# ═══════════════════════════════════════════════════════════


class TestP3LoadSingle:
    """P3: SkillpackLoader.load_single() 增量加载。"""

    def test_load_single_success(self, tmp_path):
        """成功增量加载单个技能。"""
        from excelmanus.skillpacks.loader import SkillpackLoader

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: test skill\n---\nInstructions here.\n",
            encoding="utf-8",
        )

        config = MagicMock()
        config.skills_system_dir = str(tmp_path / "system")
        config.skills_user_dir = str(tmp_path / "user")
        config.skills_project_dir = str(tmp_path)
        config.skills_discovery_enabled = False
        tool_registry = MagicMock()

        loader = SkillpackLoader(config, tool_registry)
        result = loader.load_single(skill_dir, source="project")

        assert result is not None
        assert result.name == "my-skill"
        assert result.description == "test skill"
        assert "my-skill" in loader.get_skillpacks()

    def test_load_single_missing_skill_md(self, tmp_path):
        """目录不存在 SKILL.md 时返回 None。"""
        from excelmanus.skillpacks.loader import SkillpackLoader

        config = MagicMock()
        config.skills_discovery_enabled = False
        tool_registry = MagicMock()
        loader = SkillpackLoader(config, tool_registry)

        result = loader.load_single(tmp_path / "nonexistent", source="project")
        assert result is None

    def test_load_single_invalid_frontmatter(self, tmp_path):
        """frontmatter 不合法时返回 None。"""
        from excelmanus.skillpacks.loader import SkillpackLoader

        skill_dir = tmp_path / "bad-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: \ndescription: \n---\nNo valid name.\n",
            encoding="utf-8",
        )

        config = MagicMock()
        config.skills_discovery_enabled = False
        tool_registry = MagicMock()
        loader = SkillpackLoader(config, tool_registry)

        result = loader.load_single(skill_dir, source="project")
        assert result is None

    def test_load_single_merges_into_existing(self, tmp_path):
        """增量加载 merge 到已有 _skillpacks 字典。"""
        from excelmanus.skillpacks.loader import SkillpackLoader

        config = MagicMock()
        config.skills_system_dir = str(tmp_path / "system")
        config.skills_user_dir = str(tmp_path / "user")
        config.skills_project_dir = str(tmp_path)
        config.skills_discovery_enabled = False
        config.workspace_root = str(tmp_path)
        tool_registry = MagicMock()
        loader = SkillpackLoader(config, tool_registry)

        # 创建两个技能
        for name in ("skill-a", "skill-b"):
            d = tmp_path / name
            d.mkdir()
            (d / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: desc {name}\n---\nInstructions.\n",
                encoding="utf-8",
            )

        loader.load_single(tmp_path / "skill-a", source="project")
        assert len(loader.get_skillpacks()) == 1

        loader.load_single(tmp_path / "skill-b", source="project")
        assert len(loader.get_skillpacks()) == 2
        assert "skill-a" in loader.get_skillpacks()
        assert "skill-b" in loader.get_skillpacks()

    def test_load_single_overwrites_existing(self, tmp_path):
        """增量加载覆盖已有同名技能。"""
        from excelmanus.skillpacks.loader import SkillpackLoader

        config = MagicMock()
        config.skills_discovery_enabled = False
        tool_registry = MagicMock()
        loader = SkillpackLoader(config, tool_registry)

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: v1\n---\nOld.\n",
            encoding="utf-8",
        )
        loader.load_single(skill_dir, source="project")
        assert loader.get_skillpacks()["my-skill"].description == "v1"

        # 更新
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: v2\n---\nNew.\n",
            encoding="utf-8",
        )
        loader.load_single(skill_dir, source="project")
        assert loader.get_skillpacks()["my-skill"].description == "v2"


# ═══════════════════════════════════════════════════════════
# P4: GitHub 并行文件下载
# ═══════════════════════════════════════════════════════════


class TestP4GitHubParallelDownload:
    """P4: _fetch_github_tree_recursive 并行下载。"""

    @pytest.mark.asyncio
    async def test_parallel_file_download(self):
        """多文件并行下载比串行快。"""
        from excelmanus.skillpacks.importer import _fetch_github_tree_recursive

        download_count = 0

        async def mock_get(url, **kwargs):
            nonlocal download_count
            await asyncio.sleep(0.05)  # 模拟网络延迟
            download_count += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.text = f"content of {url}"
            resp.json.return_value = []
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_get

        items = [
            {"name": f"file{i}.py", "type": "file", "size": 100,
             "download_url": f"https://raw.example.com/file{i}.py"}
            for i in range(5)
        ]

        start = time.monotonic()
        results = await _fetch_github_tree_recursive(
            mock_client, "owner", "repo", "main", items, "",
        )
        elapsed = time.monotonic() - start

        assert len(results) == 5
        # 并行: 5 × 0.05s = ~0.05-0.1s; 串行需 0.25s
        assert elapsed < 0.2
        assert download_count == 5

    @pytest.mark.asyncio
    async def test_max_files_limit(self):
        """max_files 限制生效。"""
        from excelmanus.skillpacks.importer import _fetch_github_tree_recursive

        async def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "content"
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_get

        items = [
            {"name": f"f{i}.py", "type": "file", "size": 10,
             "download_url": f"https://raw.example.com/f{i}.py"}
            for i in range(20)
        ]

        results = await _fetch_github_tree_recursive(
            mock_client, "owner", "repo", "main", items, "",
            max_files=3,
        )
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_skips_ignored_names(self):
        """忽略 .git, __pycache__ 等。"""
        from excelmanus.skillpacks.importer import _fetch_github_tree_recursive

        async def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "content"
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_get

        items = [
            {"name": ".git", "type": "dir", "url": "https://api.example.com/git"},
            {"name": "__pycache__", "type": "dir", "url": "https://api.example.com/pycache"},
            {"name": "good.py", "type": "file", "size": 10,
             "download_url": "https://raw.example.com/good.py"},
        ]

        results = await _fetch_github_tree_recursive(
            mock_client, "owner", "repo", "main", items, "",
        )
        assert len(results) == 1
        assert results[0]["path"] == "good.py"

    @pytest.mark.asyncio
    async def test_handles_download_failure(self):
        """单文件下载失败不影响其他文件。"""
        from excelmanus.skillpacks.importer import _fetch_github_tree_recursive

        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "fail" in url:
                raise Exception("network error")
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "ok"
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_get

        items = [
            {"name": "good.py", "type": "file", "size": 10,
             "download_url": "https://raw.example.com/good.py"},
            {"name": "bad.py", "type": "file", "size": 10,
             "download_url": "https://raw.example.com/fail.py"},
        ]

        results = await _fetch_github_tree_recursive(
            mock_client, "owner", "repo", "main", items, "",
        )
        assert len(results) == 1
        assert results[0]["path"] == "good.py"

    @pytest.mark.asyncio
    async def test_recursive_subdir(self):
        """递归下载子目录中的文件。"""
        from excelmanus.skillpacks.importer import _fetch_github_tree_recursive

        async def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "api" in url:
                # 子目录列表
                resp.json.return_value = [
                    {"name": "sub_file.py", "type": "file", "size": 10,
                     "download_url": "https://raw.example.com/sub/sub_file.py"},
                ]
            else:
                resp.text = "content"
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_get

        items = [
            {"name": "root.py", "type": "file", "size": 10,
             "download_url": "https://raw.example.com/root.py"},
            {"name": "sub", "type": "dir",
             "url": "https://api.example.com/sub"},
        ]

        results = await _fetch_github_tree_recursive(
            mock_client, "owner", "repo", "main", items, "",
        )
        paths = [r["path"] for r in results]
        assert "root.py" in paths
        assert "sub/sub_file.py" in paths


# ═══════════════════════════════════════════════════════════
# 集成: manager import_skillpack_async version 参数传递
# ═══════════════════════════════════════════════════════════


class TestManagerVersionPassthrough:
    """manager.import_skillpack_async 的 version 参数传递到 download_and_extract。"""

    @pytest.mark.asyncio
    async def test_clawhub_import_passes_version(self):
        """version 参数透传到 client.download_and_extract。"""
        from excelmanus.skillpacks.manager import SkillpackManager

        config = MagicMock()
        config.workspace_root = "/tmp/ws"
        config.skills_project_dir = "/tmp/ws/skills"
        config.clawhub_enabled = True
        config.clawhub_registry_url = "https://clawhub.ai"
        config.clawhub_prefer_cli = False

        loader = MagicMock()
        loader.load_single = MagicMock(return_value=None)
        loader.load_all = MagicMock(return_value={})

        manager = SkillpackManager(config, loader)

        mock_client = AsyncMock()
        mock_client.download_and_extract = AsyncMock(return_value=("3.0.0", ["SKILL.md"]))
        manager._clawhub_client = mock_client

        mock_lockfile = MagicMock()
        manager._clawhub_lockfile = mock_lockfile

        await manager.import_skillpack_async(
            source="clawhub", value="my-skill", actor="agent",
            version="3.0.0",
        )

        mock_client.download_and_extract.assert_called_once()
        call_kwargs = mock_client.download_and_extract.call_args
        assert call_kwargs.kwargs.get("version") == "3.0.0"

    @pytest.mark.asyncio
    async def test_clawhub_import_no_version(self):
        """不传 version 时透传 None。"""
        from excelmanus.skillpacks.manager import SkillpackManager

        config = MagicMock()
        config.workspace_root = "/tmp/ws"
        config.skills_project_dir = "/tmp/ws/skills"
        config.clawhub_enabled = True
        config.clawhub_registry_url = "https://clawhub.ai"
        config.clawhub_prefer_cli = False

        loader = MagicMock()
        loader.load_single = MagicMock(return_value=None)
        loader.load_all = MagicMock(return_value={})

        manager = SkillpackManager(config, loader)

        mock_client = AsyncMock()
        mock_client.download_and_extract = AsyncMock(return_value=("1.0.0", ["SKILL.md"]))
        manager._clawhub_client = mock_client
        manager._clawhub_lockfile = MagicMock()

        await manager.import_skillpack_async(
            source="clawhub", value="my-skill", actor="agent",
        )

        call_kwargs = mock_client.download_and_extract.call_args
        assert call_kwargs.kwargs.get("version") is None

    @pytest.mark.asyncio
    async def test_clawhub_import_uses_load_single(self):
        """ClawHub 安装使用 load_single 增量加载。"""
        from excelmanus.skillpacks.manager import SkillpackManager

        config = MagicMock()
        config.workspace_root = "/tmp/ws"
        config.skills_project_dir = "/tmp/ws/skills"
        config.clawhub_enabled = True
        config.clawhub_registry_url = "https://clawhub.ai"
        config.clawhub_prefer_cli = False

        mock_skillpack = MagicMock()
        mock_skillpack.description = "test desc"
        loader = MagicMock()
        loader.load_single = MagicMock(return_value=mock_skillpack)

        manager = SkillpackManager(config, loader)

        mock_client = AsyncMock()
        mock_client.download_and_extract = AsyncMock(return_value=("1.0.0", ["SKILL.md"]))
        manager._clawhub_client = mock_client
        manager._clawhub_lockfile = MagicMock()

        result = await manager.import_skillpack_async(
            source="clawhub", value="my-skill", actor="agent",
        )

        loader.load_single.assert_called_once()
        loader.load_all.assert_not_called()
        assert result["description"] == "test desc"

    @pytest.mark.asyncio
    async def test_clawhub_import_fallback_to_load_all(self):
        """load_single 返回 None 时回退到 load_all。"""
        from excelmanus.skillpacks.manager import SkillpackManager

        config = MagicMock()
        config.workspace_root = "/tmp/ws"
        config.skills_project_dir = "/tmp/ws/skills"
        config.clawhub_enabled = True
        config.clawhub_registry_url = "https://clawhub.ai"
        config.clawhub_prefer_cli = False

        loader = MagicMock()
        loader.load_single = MagicMock(return_value=None)
        loader.load_all = MagicMock(return_value={})

        manager = SkillpackManager(config, loader)

        mock_client = AsyncMock()
        mock_client.download_and_extract = AsyncMock(return_value=("1.0.0", ["SKILL.md"]))
        manager._clawhub_client = mock_client
        manager._clawhub_lockfile = MagicMock()

        await manager.import_skillpack_async(
            source="clawhub", value="my-skill", actor="agent",
        )

        loader.load_single.assert_called_once()
        loader.load_all.assert_called_once()
