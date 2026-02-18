"""SkillpackManager 行为测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.skillpacks import (
    SkillpackConflictError,
    SkillpackInputError,
    SkillpackLoader,
    SkillpackManager,
)
from excelmanus.tools import ToolDef, ToolRegistry


def _tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(
        ToolDef(
            name="read_excel",
            description="读取",
            input_schema={"type": "object", "properties": {}},
            func=lambda: "ok",
        )
    )
    registry.register_tool(
        ToolDef(
            name="create_chart",
            description="图表",
            input_schema={"type": "object", "properties": {}},
            func=lambda: "ok",
        )
    )
    return registry


def _make_config(
    workspace_root: Path,
    system_dir: Path,
    user_dir: Path,
    project_dir: Path,
) -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        workspace_root=str(workspace_root),
        skills_system_dir=str(system_dir),
        skills_user_dir=str(user_dir),
        skills_project_dir=str(project_dir),
    )


def _write_skillpack(
    root_dir: Path,
    name: str,
    *,
    description: str = "测试",
    allowed_tools: list[str] | None = None,
    triggers: list[str] | None = None,
    instructions: str = "测试说明",
) -> None:
    skill_dir = root_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        "allowed_tools:",
        *[f"  - {item}" for item in (allowed_tools or ["read_excel"])],
        "triggers:",
        *[f"  - {item}" for item in (triggers or ["分析"])],
        "---",
        instructions,
    ]
    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


def _setup(tmp_path: Path) -> tuple[SkillpackLoader, SkillpackManager, Path]:
    workspace = tmp_path / "workspace"
    system_dir = workspace / "system"
    user_dir = workspace / "user"
    project_dir = workspace / "project"
    for d in (workspace, system_dir, user_dir, project_dir):
        d.mkdir(parents=True, exist_ok=True)

    cfg = _make_config(workspace, system_dir, user_dir, project_dir)
    loader = SkillpackLoader(cfg, _tool_registry())
    loader.load_all()
    manager = SkillpackManager(cfg, loader)
    return loader, manager, workspace


def test_create_skillpack_success(tmp_path: Path) -> None:
    loader, manager, _ = _setup(tmp_path)

    created = manager.create_skillpack(
        name="reporter",
        payload={
            "description": "报表生成",
            "allowed_tools": ["read_excel", "create_chart"],
            "triggers": ["报表"],
            "instructions": "先读后画图",
        },
        actor="cli",
    )

    assert created["name"] == "reporter"
    assert created["source"] == "project"
    assert created["writable"] is True
    assert "reporter" in loader.get_skillpacks()


def test_create_skillpack_allows_empty_triggers(tmp_path: Path) -> None:
    loader, manager, _ = _setup(tmp_path)

    created = manager.create_skillpack(
        name="general_excel",
        payload={
            "description": "通用兜底",
            "allowed_tools": ["read_excel"],
            "triggers": [],
            "instructions": "兜底说明",
            "user_invocable": False,
        },
        actor="cli",
    )
    assert created["name"] == "general_excel"
    assert created["triggers"] == []
    assert created["user_invocable"] is False
    assert "general_excel" in loader.get_skillpacks()


def test_patch_skillpack_allows_empty_triggers(tmp_path: Path) -> None:
    _, manager, _ = _setup(tmp_path)
    manager.create_skillpack(
        name="route_only",
        payload={
            "description": "路由技能",
            "allowed_tools": ["read_excel"],
            "triggers": ["分析"],
            "instructions": "说明",
        },
        actor="cli",
    )

    updated = manager.patch_skillpack(
        name="route_only",
        payload={"triggers": []},
        actor="api",
    )
    assert updated["triggers"] == []


def test_create_skillpack_allows_empty_allowed_tools(tmp_path: Path) -> None:
    _, manager, _ = _setup(tmp_path)

    created = manager.create_skillpack(
        name="invalid_tools",
        payload={
            "description": "允许空工具",
            "allowed_tools": [],
            "triggers": [],
            "instructions": "说明",
        },
        actor="api",
    )
    assert created["allowed_tools"] == []


def test_create_can_override_system_by_project(tmp_path: Path) -> None:
    loader, manager, _ = _setup(tmp_path)
    system_dir = Path(loader._config.skills_system_dir)
    _write_skillpack(system_dir, "data_basic", description="系统版")
    loader.load_all()

    created = manager.create_skillpack(
        name="data_basic",
        payload={
            "description": "项目覆盖版",
            "allowed_tools": ["read_excel"],
            "triggers": ["分析"],
            "instructions": "项目优先",
        },
        actor="api",
    )

    assert created["source"] == "project"
    assert created["description"] == "项目覆盖版"


def test_patch_project_skillpack_success(tmp_path: Path) -> None:
    loader, manager, _ = _setup(tmp_path)
    manager.create_skillpack(
        name="chart_basic",
        payload={
            "description": "图表",
            "allowed_tools": ["create_chart"],
            "triggers": ["图表"],
            "instructions": "默认说明",
        },
        actor="cli",
    )

    updated = manager.patch_skillpack(
        name="chart_basic",
        payload={
            "description": "图表分析",
            "argument_hint": "<file> <type>",
        },
        actor="cli",
    )
    assert updated["description"] == "图表分析"
    assert updated["argument_hint"] == "<file> <type>"


def test_create_skillpack_with_mcp_requirements(tmp_path: Path) -> None:
    _, manager, _ = _setup(tmp_path)

    created = manager.create_skillpack(
        name="mcp_reader",
        payload={
            "description": "MCP 读取",
            "allowed_tools": ["mcp:context7:*"],
            "triggers": ["外部查询"],
            "required-mcp-servers": ["context7"],
            "required-mcp-tools": ["context7:query_docs"],
            "instructions": "先检索，再整理。",
        },
        actor="api",
    )

    assert created["required_mcp_servers"] == ["context7"]
    assert created["required_mcp_tools"] == ["context7:query_docs"]


def test_patch_non_project_skillpack_rejected(tmp_path: Path) -> None:
    loader, manager, _ = _setup(tmp_path)
    system_dir = Path(loader._config.skills_system_dir)
    _write_skillpack(system_dir, "data_basic", description="系统版")
    loader.load_all()

    with pytest.raises(SkillpackConflictError):
        manager.patch_skillpack(
            name="data_basic",
            payload={"description": "改描述"},
            actor="api",
        )


def test_delete_project_skillpack_archives_and_unloads(tmp_path: Path) -> None:
    loader, manager, workspace = _setup(tmp_path)
    manager.create_skillpack(
        name="temp_skill",
        payload={
            "description": "临时",
            "allowed_tools": ["read_excel"],
            "triggers": ["临时"],
            "instructions": "临时说明",
        },
        actor="cli",
    )

    deleted = manager.delete_skillpack(
        name="temp_skill",
        actor="cli",
        reason="测试删除",
    )
    assert deleted["name"] == "temp_skill"
    assert "temp_skill" not in loader.get_skillpacks()

    archive_dir = workspace / deleted["archived_dir"]
    assert archive_dir.exists()
    meta = json.loads((archive_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["reason"] == "测试删除"
    assert meta["actor"] == "cli"


def test_resources_path_traversal_rejected(tmp_path: Path) -> None:
    _, manager, _ = _setup(tmp_path)

    with pytest.raises(SkillpackInputError):
        manager.create_skillpack(
            name="invalid_resources",
            payload={
                "description": "测试",
                "allowed_tools": ["read_excel"],
                "triggers": ["分析"],
                "resources": ["../secret.txt"],
                "instructions": "说明",
            },
            actor="api",
        )


def test_atomic_write_text_cleans_tmp_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, manager, workspace = _setup(tmp_path)
    target = workspace / "project" / "atomic_replace_fail.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old", encoding="utf-8")

    def _raise_replace(self: Path, target_path: Path) -> Path:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", _raise_replace)

    with pytest.raises(OSError, match="replace failed"):
        manager._atomic_write_text(target, "new")

    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []
    assert target.read_text(encoding="utf-8") == "old"


def test_atomic_write_text_cleans_tmp_when_write_text_fails_after_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, manager, workspace = _setup(tmp_path)
    target = workspace / "project" / "atomic_write_fail.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old", encoding="utf-8")

    original_write_text = Path.write_text

    def _write_then_raise(self: Path, *args, **kwargs) -> int:
        if self.parent == target.parent and self.name.startswith(f".{target.name}.") and self.suffix == ".tmp":
            original_write_text(self, *args, **kwargs)
            raise OSError("write failed")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _write_then_raise)

    with pytest.raises(OSError, match="write failed"):
        manager._atomic_write_text(target, "new")

    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []
    assert target.read_text(encoding="utf-8") == "old"
