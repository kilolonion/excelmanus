"""技能安装速度优化测试。

覆盖：
- loader 增量加载 load_single()
- GitHub 并行文件下载
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestLoadSingle:
    """SkillpackLoader.load_single() 增量加载。"""

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

        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: v2\n---\nNew.\n",
            encoding="utf-8",
        )
        loader.load_single(skill_dir, source="project")
        assert loader.get_skillpacks()["my-skill"].description == "v2"


class TestGitHubParallelDownload:
    """_fetch_github_tree_recursive 并行下载。"""

    @pytest.mark.asyncio
    async def test_parallel_file_download(self):
        """多文件并行下载比串行快。"""
        from excelmanus.skillpacks.importer import _fetch_github_tree_recursive

        download_count = 0

        async def mock_get(url, **kwargs):
            nonlocal download_count
            await asyncio.sleep(0.05)
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
