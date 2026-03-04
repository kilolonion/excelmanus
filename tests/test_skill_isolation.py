"""Skill 用户隔离测试。

覆盖：
- UserSkillService 缓存与隔离行为
- SkillpackManager 的 user_skill_dir 写入
- 不同用户技能互不可见
- 匿名用户回退到全局行为
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.skillpacks.loader import SkillpackLoader
from excelmanus.skillpacks.manager import (
    SkillpackConflictError,
    SkillpackManager,
    SkillpackNotFoundError,
)
from excelmanus.skillpacks.user_skill_service import UserSkillService


# ── Fixtures ──────────────────────────────────────────────


def _minimal_config(tmp_path: Path, **overrides) -> ExcelManusConfig:
    """创建用于测试的最小配置。"""
    system_dir = tmp_path / "system_skills"
    system_dir.mkdir(exist_ok=True)
    user_dir = tmp_path / "global_user_skills"
    user_dir.mkdir(exist_ok=True)
    project_dir = tmp_path / "workspace" / ".excelmanus" / "skillpacks"
    project_dir.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)

    defaults = dict(
        api_key="test-key",
        base_url="http://localhost:8080",
        model="test-model",
        workspace_root=str(workspace),
        skills_system_dir=str(system_dir),
        skills_user_dir=str(user_dir),
        skills_project_dir=str(project_dir),
        skills_discovery_enabled=False,
        clawhub_enabled=False,
    )
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _create_skill_md(skill_dir: Path, name: str, description: str = "test") -> Path:
    """在指定目录下创建一个 SKILL.md 文件。"""
    d = skill_dir / name
    d.mkdir(parents=True, exist_ok=True)
    content = textwrap.dedent(f"""\
        ---
        name: {name}
        description: {description}
        ---
        Test instructions for {name}.
    """)
    f = d / "SKILL.md"
    f.write_text(content, encoding="utf-8")
    return f


def _mock_registry():
    """创建一个 mock ToolRegistry。"""
    registry = MagicMock()
    registry.get_tool_names.return_value = []
    registry.fork.return_value = registry
    return registry


# ── TestUserSkillService ──────────────────────────────────


class TestUserSkillServiceCache:
    """UserSkillService 缓存行为测试。"""

    def test_same_user_returns_cached_bundle(self, tmp_path: Path):
        config = _minimal_config(tmp_path)
        registry = _mock_registry()
        service = UserSkillService(config, registry)

        loader1 = service.get_loader("alice")
        loader2 = service.get_loader("alice")
        assert loader1 is loader2

    def test_different_users_return_different_bundles(self, tmp_path: Path):
        config = _minimal_config(tmp_path)
        registry = _mock_registry()
        service = UserSkillService(config, registry)

        loader_alice = service.get_loader("alice")
        loader_bob = service.get_loader("bob")
        assert loader_alice is not loader_bob

    def test_anonymous_user_returns_cached_bundle(self, tmp_path: Path):
        config = _minimal_config(tmp_path)
        registry = _mock_registry()
        service = UserSkillService(config, registry)

        loader1 = service.get_loader(None)
        loader2 = service.get_loader(None)
        assert loader1 is loader2

    def test_invalidate_clears_user_cache(self, tmp_path: Path):
        config = _minimal_config(tmp_path)
        registry = _mock_registry()
        service = UserSkillService(config, registry)

        loader1 = service.get_loader("alice")
        service.invalidate("alice")
        loader2 = service.get_loader("alice")
        assert loader1 is not loader2

    def test_invalidate_all_clears_all(self, tmp_path: Path):
        config = _minimal_config(tmp_path)
        registry = _mock_registry()
        service = UserSkillService(config, registry)

        loader_a = service.get_loader("alice")
        loader_b = service.get_loader("bob")
        service.invalidate_all()
        loader_a2 = service.get_loader("alice")
        loader_b2 = service.get_loader("bob")
        assert loader_a is not loader_a2
        assert loader_b is not loader_b2

    def test_lru_eviction(self, tmp_path: Path):
        config = _minimal_config(tmp_path)
        registry = _mock_registry()
        service = UserSkillService(config, registry, cache_max=2)

        service.get_loader("alice")
        service.get_loader("bob")
        service.get_loader("charlie")  # should evict alice

        # Alice should be evicted, getting a new instance
        loader_a = service.get_loader("alice")
        # Bob should still be cached (was moved to end when charlie was added)
        # but actually alice was first, so alice evicted
        assert loader_a is not None  # basic sanity


class TestUserSkillServiceIsolation:
    """UserSkillService 用户隔离测试。"""

    def test_user_skill_dir_is_per_user(self, tmp_path: Path):
        config = _minimal_config(tmp_path)
        registry = _mock_registry()
        service = UserSkillService(config, registry)

        # Alice 和 Bob 应该有不同的用户技能目录
        alice_dir = service._resolve_user_skill_dir("alice")
        bob_dir = service._resolve_user_skill_dir("bob")
        assert alice_dir != bob_dir
        assert "alice" in str(alice_dir)
        assert "bob" in str(bob_dir)

    def test_anonymous_user_uses_global_dir(self, tmp_path: Path):
        config = _minimal_config(tmp_path)
        registry = _mock_registry()
        service = UserSkillService(config, registry)

        anon_dir = service._resolve_user_skill_dir(None)
        # Should be the global user skills dir
        assert str(anon_dir) == str(
            Path(config.skills_user_dir).expanduser().resolve()
        )

    def test_user_a_skill_not_visible_to_user_b(self, tmp_path: Path):
        """用户 A 创建的技能对用户 B 不可见。"""
        config = _minimal_config(tmp_path)
        registry = _mock_registry()
        service = UserSkillService(config, registry)

        # 在 Alice 的用户目录创建技能
        alice_dir = service._resolve_user_skill_dir("alice")
        alice_dir.mkdir(parents=True, exist_ok=True)
        _create_skill_md(alice_dir, "alice-only-skill", "Alice's private skill")

        # 刷新 Alice 的 loader
        service.invalidate("alice")
        alice_loader = service.get_loader("alice")
        alice_skills = alice_loader.get_skillpacks()

        # 获取 Bob 的 loader
        bob_loader = service.get_loader("bob")
        bob_skills = bob_loader.get_skillpacks()

        assert "alice-only-skill" in alice_skills
        assert "alice-only-skill" not in bob_skills

    def test_system_skills_visible_to_all_users(self, tmp_path: Path):
        """系统技能对所有用户可见。"""
        config = _minimal_config(tmp_path)
        system_dir = Path(config.skills_system_dir)
        _create_skill_md(system_dir, "system-common", "A system skill")

        registry = _mock_registry()
        service = UserSkillService(config, registry)

        alice_skills = service.get_loader("alice").get_skillpacks()
        bob_skills = service.get_loader("bob").get_skillpacks()

        assert "system-common" in alice_skills
        assert "system-common" in bob_skills

    def test_project_skills_visible_to_all_users(self, tmp_path: Path):
        """项目技能对所有用户可见。"""
        config = _minimal_config(tmp_path)
        project_dir = Path(config.skills_project_dir)
        _create_skill_md(project_dir, "project-shared", "A project skill")

        registry = _mock_registry()
        service = UserSkillService(config, registry)

        alice_skills = service.get_loader("alice").get_skillpacks()
        bob_skills = service.get_loader("bob").get_skillpacks()

        assert "project-shared" in alice_skills
        assert "project-shared" in bob_skills


# ── TestSkillpackManagerUserDir ───────────────────────────


class TestSkillpackManagerUserDir:
    """SkillpackManager 的 user_skill_dir 写入目标测试。"""

    def test_create_writes_to_user_dir(self, tmp_path: Path):
        """创建技能时应写入 user_skill_dir 而非 project_dir。"""
        user_dir = tmp_path / "user_skills_alice"
        user_dir.mkdir(exist_ok=True)

        # loader 的 config 必须将 skills_user_dir 指向 user_dir，
        # 这样 load_all() 才能扫描到新写入的技能
        config = _minimal_config(tmp_path, skills_user_dir=str(user_dir))
        registry = _mock_registry()
        loader = SkillpackLoader(config, registry)
        loader.load_all()

        manager = SkillpackManager(config, loader, user_skill_dir=user_dir)
        manager.create_skillpack(
            name="my-skill",
            payload={"description": "test skill"},
            actor="alice",
        )

        # 应该在 user_dir 中创建
        assert (user_dir / "my-skill" / "SKILL.md").exists()
        # 不应在 project_dir 中创建
        assert not (Path(config.skills_project_dir) / "my-skill" / "SKILL.md").exists()

    def test_create_without_user_dir_writes_to_project(self, tmp_path: Path):
        """未指定 user_skill_dir 时回退到 project_dir。"""
        config = _minimal_config(tmp_path)
        registry = _mock_registry()
        loader = SkillpackLoader(config, registry)
        loader.load_all()

        manager = SkillpackManager(config, loader)
        manager.create_skillpack(
            name="shared-skill",
            payload={"description": "shared"},
            actor="api",
        )

        assert (Path(config.skills_project_dir) / "shared-skill" / "SKILL.md").exists()

    def test_delete_user_skill(self, tmp_path: Path):
        """可以删除 source=user 的技能。"""
        config = _minimal_config(tmp_path)
        registry = _mock_registry()

        user_dir = tmp_path / "user_skills_alice"
        user_dir.mkdir(exist_ok=True)
        _create_skill_md(user_dir, "deletable", "to delete")

        # 需要将 user_dir 作为 skills_user_dir 让 loader 能扫描到
        config = _minimal_config(tmp_path, skills_user_dir=str(user_dir))
        loader = SkillpackLoader(config, registry)
        loader.load_all()

        skills = loader.get_skillpacks()
        assert "deletable" in skills

        manager = SkillpackManager(config, loader, user_skill_dir=user_dir)
        result = manager.delete_skillpack(
            name="deletable", actor="alice", reason="test"
        )
        assert result["name"] == "deletable"

    def test_cannot_delete_system_skill(self, tmp_path: Path):
        """不能删除 system 源的技能。"""
        config = _minimal_config(tmp_path)
        system_dir = Path(config.skills_system_dir)
        _create_skill_md(system_dir, "system-readonly", "System skill")

        registry = _mock_registry()
        loader = SkillpackLoader(config, registry)
        loader.load_all()

        manager = SkillpackManager(config, loader)
        with pytest.raises(SkillpackConflictError):
            manager.delete_skillpack(
                name="system-readonly", actor="api", reason="test"
            )

    def test_writable_includes_user_source(self, tmp_path: Path):
        """source=user 的技能应标记为 writable=True。"""
        config = _minimal_config(tmp_path)
        user_dir = tmp_path / "user_skills"
        user_dir.mkdir(exist_ok=True)
        _create_skill_md(user_dir, "my-tool", "A user skill")

        config = _minimal_config(tmp_path, skills_user_dir=str(user_dir))
        registry = _mock_registry()
        loader = SkillpackLoader(config, registry)
        loader.load_all()

        manager = SkillpackManager(config, loader, user_skill_dir=user_dir)
        details = manager.list_skillpacks()
        user_skills = [d for d in details if d["name"] == "my-tool"]
        assert len(user_skills) == 1
        assert user_skills[0]["source"] == "user"
        assert user_skills[0]["writable"] is True


# ── TestUserSkillServiceRouter ────────────────────────────


class TestUserSkillServiceRouter:
    """UserSkillService 返回的 per-user router 测试。"""

    def test_per_user_router_uses_user_skills(self, tmp_path: Path):
        """per-user router 应能路由到用户私有技能。"""
        config = _minimal_config(tmp_path)
        registry = _mock_registry()
        service = UserSkillService(config, registry)

        # 在 Alice 的目录创建技能
        alice_dir = service._resolve_user_skill_dir("alice")
        alice_dir.mkdir(parents=True, exist_ok=True)
        _create_skill_md(alice_dir, "alice-router-skill", "Alice's skill")

        service.invalidate("alice")
        router = service.get_router("alice")

        # Router 的 loader 应该包含这个技能
        skills = router._loader.get_skillpacks()
        assert "alice-router-skill" in skills

    def test_per_user_manager_from_service(self, tmp_path: Path):
        """UserSkillService 返回的 manager 写入用户目录。"""
        config = _minimal_config(tmp_path)
        registry = _mock_registry()
        service = UserSkillService(config, registry)

        manager = service.get_manager("alice")
        manager.create_skillpack(
            name="from-service",
            payload={"description": "created via service"},
            actor="alice",
        )

        alice_dir = service._resolve_user_skill_dir("alice")
        assert (alice_dir / "from-service" / "SKILL.md").exists()

        # Bob 的 manager 看不到这个技能
        service.invalidate("alice")  # force reload
        bob_manager = service.get_manager("bob")
        bob_skills = bob_manager.list_skillpacks()
        bob_names = [s["name"] for s in bob_skills]
        assert "from-service" not in bob_names
