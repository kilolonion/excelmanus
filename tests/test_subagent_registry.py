"""SubagentRegistry 单元测试。"""

from __future__ import annotations

from pathlib import Path

from excelmanus.config import ExcelManusConfig
from excelmanus.subagent import SubagentRegistry


def _make_config(
    tmp_path: Path,
    *,
    user_dir: Path,
    project_dir: Path,
    **overrides,
) -> ExcelManusConfig:
    defaults = dict(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        workspace_root=str(tmp_path),
        subagent_user_dir=str(user_dir),
        subagent_project_dir=str(project_dir),
    )
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _write_agent(
    root_dir: Path,
    filename: str,
    *,
    name: str,
    description: str,
    permission_mode: str = "default",
    tools: list[str] | None = None,
    max_iterations: int | None = None,
    max_consecutive_failures: int | None = None,
    memory_scope: str | None = None,
    extra_frontmatter_lines: list[str] | None = None,
    body: str = "你是测试子代理。",
) -> None:
    tools = tools or ["read_excel"]
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        f"permissionMode: {permission_mode}",
        "tools:",
        *[f"  - {tool}" for tool in tools],
    ]
    if max_iterations is not None:
        lines.append(f"max_iterations: {max_iterations}")
    if max_consecutive_failures is not None:
        lines.append(f"max_consecutive_failures: {max_consecutive_failures}")
    if memory_scope is not None:
        lines.append(f"memory_scope: {memory_scope}")
    if extra_frontmatter_lines:
        lines.extend(extra_frontmatter_lines)
    lines.extend(["---", body])
    content = "\n".join(lines)
    (root_dir / filename).write_text(content, encoding="utf-8")


def test_builtin_agents_loaded(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_agents"
    project_dir = tmp_path / "project_agents"
    user_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)
    registry = SubagentRegistry(_make_config(tmp_path, user_dir=user_dir, project_dir=project_dir))

    loaded = registry.load_all()
    assert "explorer" in loaded
    assert "analyst" in loaded
    assert "writer" in loaded
    assert "coder" in loaded


def test_project_overrides_user_and_builtin(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_agents"
    project_dir = tmp_path / "project_agents"
    user_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    _write_agent(
        user_dir,
        "explorer.md",
        name="explorer",
        description="用户覆盖版本",
        permission_mode="readOnly",
    )
    _write_agent(
        project_dir,
        "explorer.md",
        name="explorer",
        description="项目覆盖版本",
        permission_mode="default",
    )

    registry = SubagentRegistry(_make_config(tmp_path, user_dir=user_dir, project_dir=project_dir))
    loaded = registry.load_all()
    assert loaded["explorer"].description == "项目覆盖版本"
    assert loaded["explorer"].source == "project"


def test_invalid_frontmatter_skipped(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_agents"
    project_dir = tmp_path / "project_agents"
    user_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    # permissionMode 非法，应该被跳过
    _write_agent(
        project_dir,
        "bad.md",
        name="bad_agent",
        description="坏配置",
        permission_mode="unknown",
    )

    registry = SubagentRegistry(_make_config(tmp_path, user_dir=user_dir, project_dir=project_dir))
    loaded = registry.load_all()
    assert "bad_agent" not in loaded


def test_build_catalog_contains_agent_names(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_agents"
    project_dir = tmp_path / "project_agents"
    user_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    _write_agent(
        project_dir,
        "finance.md",
        name="finance_checker",
        description="财务校验子代理",
    )

    registry = SubagentRegistry(_make_config(tmp_path, user_dir=user_dir, project_dir=project_dir))
    catalog, names = registry.build_catalog()
    assert "finance_checker" in catalog
    assert "finance_checker" in names


def test_get_supports_ecosystem_aliases(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_agents"
    project_dir = tmp_path / "project_agents"
    user_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    registry = SubagentRegistry(_make_config(tmp_path, user_dir=user_dir, project_dir=project_dir))
    registry.load_all()

    assert registry.get("Explore") is not None
    assert registry.get("Explore").name == "explorer"  # type: ignore[union-attr]
    assert registry.get("Plan") is not None
    assert registry.get("Plan").name == "planner"  # type: ignore[union-attr]
    assert registry.get("General-purpose") is not None
    assert registry.get("General-purpose").name == "analyst"  # type: ignore[union-attr]


def test_external_agent_uses_global_threshold_defaults_when_not_declared(
    tmp_path: Path,
) -> None:
    user_dir = tmp_path / "user_agents"
    project_dir = tmp_path / "project_agents"
    user_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    _write_agent(
        project_dir,
        "finance.md",
        name="finance_checker",
        description="财务校验子代理",
    )
    registry = SubagentRegistry(
        _make_config(
            tmp_path,
            user_dir=user_dir,
            project_dir=project_dir,
            subagent_max_iterations=9,
            subagent_max_consecutive_failures=4,
        )
    )
    loaded = registry.load_all()
    finance = loaded["finance_checker"]
    assert finance.max_iterations == 9
    assert finance.max_consecutive_failures == 4


def test_external_agent_explicit_thresholds_override_global_defaults(
    tmp_path: Path,
) -> None:
    user_dir = tmp_path / "user_agents"
    project_dir = tmp_path / "project_agents"
    user_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    _write_agent(
        project_dir,
        "finance.md",
        name="finance_checker",
        description="财务校验子代理",
        max_iterations=3,
        max_consecutive_failures=1,
    )
    registry = SubagentRegistry(
        _make_config(
            tmp_path,
            user_dir=user_dir,
            project_dir=project_dir,
            subagent_max_iterations=9,
            subagent_max_consecutive_failures=4,
        )
    )
    loaded = registry.load_all()
    finance = loaded["finance_checker"]
    assert finance.max_iterations == 3
    assert finance.max_consecutive_failures == 1


def test_builtin_agents_keep_explicit_thresholds_when_global_defaults_change(
    tmp_path: Path,
) -> None:
    user_dir = tmp_path / "user_agents"
    project_dir = tmp_path / "project_agents"
    user_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    registry = SubagentRegistry(
        _make_config(
            tmp_path,
            user_dir=user_dir,
            project_dir=project_dir,
            subagent_max_iterations=99,
            subagent_max_consecutive_failures=99,
        )
    )
    loaded = registry.load_all()
    assert loaded["explorer"].max_iterations == 60
    assert loaded["explorer"].max_consecutive_failures == 2


def test_memory_scope_field_is_loaded_from_memory_scope_key(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_agents"
    project_dir = tmp_path / "project_agents"
    user_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    _write_agent(
        project_dir,
        "scope.md",
        name="scope_checker",
        description="scope 测试",
        memory_scope="project",
    )

    registry = SubagentRegistry(_make_config(tmp_path, user_dir=user_dir, project_dir=project_dir))
    loaded = registry.load_all()
    assert loaded["scope_checker"].memory_scope == "project"


def test_memory_scope_legacy_memory_key_is_compatible(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_agents"
    project_dir = tmp_path / "project_agents"
    user_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    _write_agent(
        project_dir,
        "legacy_scope.md",
        name="legacy_scope_checker",
        description="legacy scope 测试",
        extra_frontmatter_lines=["memory: user"],
    )

    registry = SubagentRegistry(_make_config(tmp_path, user_dir=user_dir, project_dir=project_dir))
    loaded = registry.load_all()
    assert loaded["legacy_scope_checker"].memory_scope == "user"


def test_memory_scope_conflict_is_rejected(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_agents"
    project_dir = tmp_path / "project_agents"
    user_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    _write_agent(
        project_dir,
        "bad_scope.md",
        name="bad_scope_checker",
        description="冲突 scope 测试",
        extra_frontmatter_lines=["memory_scope: user", "memory: project"],
    )

    registry = SubagentRegistry(_make_config(tmp_path, user_dir=user_dir, project_dir=project_dir))
    loaded = registry.load_all()
    assert "bad_scope_checker" not in loaded


def test_memory_scope_invalid_value_is_rejected(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_agents"
    project_dir = tmp_path / "project_agents"
    user_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    _write_agent(
        project_dir,
        "invalid_scope.md",
        name="invalid_scope_checker",
        description="非法 scope 测试",
        memory_scope="global",
    )

    registry = SubagentRegistry(_make_config(tmp_path, user_dir=user_dir, project_dir=project_dir))
    loaded = registry.load_all()
    assert "invalid_scope_checker" not in loaded
