"""片 J：L4 wire = catalog ∩ (core + profile + loaded)。测试内临时签字启用 profile。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine_core.meta_tools import MetaToolBuilder
from excelmanus.prompt.envelope import compute_epoch_identity, epoch_changed
from excelmanus.system_one.host import (
    maybe_record_turn_exposure,
    turn_wire_profile,
)
from excelmanus.system_one.types import Decision
from excelmanus.tools.catalog import catalog_from_engine
from excelmanus.tools.registry import ToolDef, ToolRegistry


def _config(**overrides: object) -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        max_iterations=8,
        max_consecutive_failures=3,
        workspace_root=str(Path(__file__).resolve().parent),
        ai_gateway_api_key="vck_test",
        **overrides,
    )


def _tool(name: str, *, effect: str = "none") -> ToolDef:
    return ToolDef(
        name=name,
        description=f"test {name}",
        input_schema={"type": "object", "properties": {}},
        func=lambda: None,
        write_effect=effect,  # type: ignore[arg-type]
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tools(
        [
            _tool("inspect_spreadsheet"),
            _tool("analyze_spreadsheet"),
            _tool("ask_user"),
            _tool("edit_spreadsheet", effect="workspace_write"),
            _tool("write_text_file", effect="workspace_write"),
            _tool("run_code", effect="dynamic"),
            _tool("run_shell", effect="dynamic"),
            _tool("write_plan"),
        ]
    )
    from excelmanus.tools.introspection_tools import register_introspection_tools

    register_introspection_tools(registry)
    return registry


def _schema_names(schemas: list[dict]) -> set[str]:
    names: set[str] = set()
    for schema in schemas:
        func = schema.get("function") if isinstance(schema, dict) else None
        if isinstance(func, dict) and func.get("name"):
            names.add(str(func["name"]))
        elif isinstance(schema, dict) and schema.get("name"):
            names.add(str(schema["name"]))
    return names


def _inspect_record(*, applied: bool, sticky: str = "inspect") -> dict[str, object]:
    return {
        "profile": "inspect",
        "sticky_profile": sticky,
        "domain": "inspect_only",
        "mode_hint": "keep",
        "conf": 0.9,
        "latency_ms": 0.0,
        "wire_narrow": applied and sticky != "full",
        "gate": "enforce" if applied else "shadow",
        "applied": applied,
    }


def _engine(
    tmp_path: Path,
    *,
    config: ExcelManusConfig,
    exposure: dict[str, object] | None = None,
    child: bool = False,
) -> SimpleNamespace:
    registry = _registry()
    return SimpleNamespace(
        _registry=registry,
        registry=registry,
        _current_chat_mode="write",
        _turn_exposure=exposure,
        _exposure_sticky=None,
        _active_skills=[],
        _tools_cache=None,
        _tools_cache_key=None,
        _skill_router=None,
        _skill_resolver=None,
        _subagent_config=object() if child else None,
        _is_host_session=not child,
        _fixed_capability=None,
        config=replace(config, workspace_root=str(tmp_path)),
    )


def _sign_exposure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "excelmanus.system_one.calibration.SIGNED_ENFORCE_PACKS",
        frozenset({"exposure.turn"}),
    )


def _applied_decision(profile: str) -> Decision:
    return Decision(
        kind="noop",
        reason=f"domain:{profile}",
        extras={
            "profile": profile,
            "domain": "inspect_only" if profile == "inspect" else "spreadsheet_write",
            "domain_confidence": 0.9,
            "mode_hint": "keep",
            "wire_narrow": False,
        },
        applied=True,
    )


def _epoch(catalog_digest: str, tools: list[dict]) -> object:
    return compute_epoch_identity(
        session_id="s1",
        model="m",
        protocol="openai|https://x",
        call_config={"temperature": 0.2},
        tools=tools,
        system="SYS",
        catalog_digest=catalog_digest,
        wire_payload=[{"role": "user", "content": "a"}],
    )


def test_gate_off_keeps_core_wire_even_if_profile_inspect(tmp_path: Path) -> None:
    # 二态契约默认全 enforce；exposure 子闸 off 时即使有 applied 记录也不收窄。
    engine = _engine(
        tmp_path,
        config=_config(jev_exposure="off"),
        exposure=_inspect_record(applied=True, sticky="inspect"),
    )
    catalog = catalog_from_engine(engine)
    assert catalog is not None
    digest = catalog.digest()
    names = _schema_names(MetaToolBuilder(engine).build_v5_tools_impl())
    assert turn_wire_profile(engine) == "full"
    assert "edit_spreadsheet" in names
    assert "run_code" in names
    assert catalog_from_engine(engine) is not None
    assert catalog_from_engine(engine).digest() == digest


def test_signed_profiles_change_schema_not_digest_or_epoch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_exposure(monkeypatch)
    config = _config(jev_enabled="enforce", jev_exposure="enforce", jev_calibrated=True)
    full_engine = _engine(
        tmp_path, config=config,
        exposure={**_inspect_record(applied=True, sticky="file_code"), "profile": "file_code"},
    )
    catalog = catalog_from_engine(full_engine)
    assert catalog is not None
    digest = catalog.digest()
    full = _schema_names(MetaToolBuilder(full_engine).build_v5_tools_impl())
    assert "edit_spreadsheet" in full
    assert "inspect_spreadsheet" in full

    inspect_engine = _engine(
        tmp_path,
        config=config,
        exposure=_inspect_record(applied=True, sticky="inspect"),
    )
    inspect_catalog = catalog_from_engine(inspect_engine)
    assert inspect_catalog is not None
    assert inspect_catalog.digest() == digest
    inspect_wire = MetaToolBuilder(inspect_engine).build_v5_tools_impl()
    inspect_names = _schema_names(inspect_wire)
    assert "inspect_spreadsheet" in inspect_names
    assert "analyze_spreadsheet" in inspect_names
    assert "ask_user" in inspect_names
    assert "edit_spreadsheet" in inspect_names
    assert "run_shell" not in inspect_names
    assert "run_code" in inspect_names
    assert "write_plan" not in inspect_names
    assert "write_plan" not in full
    assert len(inspect_names) < len(full)

    edit_engine = _engine(
        tmp_path,
        config=config,
        exposure={
            **_inspect_record(applied=True, sticky="edit"),
            "profile": "edit",
        },
    )
    edit_names = _schema_names(MetaToolBuilder(edit_engine).build_v5_tools_impl())
    assert "edit_spreadsheet" in edit_names
    assert "inspect_spreadsheet" in edit_names
    assert "run_shell" not in edit_names
    assert catalog_from_engine(edit_engine).digest() == digest

    prev = _epoch(digest, MetaToolBuilder(full_engine).build_v5_tools_impl())
    curr = _epoch(digest, inspect_wire)
    assert epoch_changed(prev, curr) is False
    assert prev.tools_digest != curr.tools_digest


def test_undisclosed_authorized_tool_still_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_exposure(monkeypatch)
    engine = _engine(
        tmp_path,
        config=_config(jev_enabled="enforce", jev_exposure="enforce", jev_calibrated=True),
        exposure=_inspect_record(applied=True, sticky="inspect"),
    )
    names = _schema_names(MetaToolBuilder(engine).build_v5_tools_impl())
    assert "run_shell" not in names
    catalog = catalog_from_engine(engine)
    assert catalog is not None
    assert "run_shell" in catalog.name_set()
    assert engine.registry.call_tool("run_shell", {}).success


def test_profile_keeps_core_direct_and_programmatic_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_exposure(monkeypatch)
    engine = _engine(
        tmp_path,
        config=_config(jev_enabled="enforce", jev_exposure="enforce", jev_calibrated=True),
        exposure=_inspect_record(applied=True, sticky="inspect"),
    )
    names = _schema_names(MetaToolBuilder(engine).build_v5_tools_impl())
    assert {"run_code", "inspect_spreadsheet", "ask_user"} <= names
    assert "edit_spreadsheet" in names  # 常驻核心不受 profile 收窄。
    catalog = catalog_from_engine(engine)
    assert catalog is not None
    assert "inspect_spreadsheet" in catalog.name_set()


def test_loaded_tools_survive_profile_narrowing_without_expanding_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_exposure(monkeypatch)
    engine = _engine(
        tmp_path,
        config=_config(jev_enabled="enforce", jev_exposure="enforce", jev_calibrated=True),
        exposure=_inspect_record(applied=True, sticky="inspect"),
    )
    engine._loaded_tool_names = {"edit_spreadsheet", "not_registered"}
    names = _schema_names(MetaToolBuilder(engine).build_v5_tools_impl())
    assert "edit_spreadsheet" in names
    assert "not_registered" not in names

    engine._current_chat_mode = "read"
    names = _schema_names(MetaToolBuilder(engine).build_v5_tools_impl())
    assert "edit_spreadsheet" not in names
    assert "run_code" not in names


@pytest.mark.asyncio
async def test_new_turn_preserves_loaded_tools_even_when_jev_is_off(tmp_path: Path) -> None:
    engine = _engine(tmp_path, config=_config())
    old_loaded = {"edit_spreadsheet"}
    engine._loaded_tool_names = old_loaded
    engine._tools_cache = [{"function": {"name": "old"}}]
    await maybe_record_turn_exposure(engine, "next request")
    assert engine._loaded_tool_names == old_loaded
    assert engine._loaded_tool_names is old_loaded
    assert engine._tools_cache is None


def test_master_off_child_enforce_does_not_narrow(tmp_path: Path) -> None:
    # 二态契约：总闸 off 时子闸 enforce 也不收窄（原来 shadow 降级的位置）。
    engine = _engine(
        tmp_path,
        config=_config(jev_enabled="off", jev_exposure="enforce", jev_calibrated=True),
        exposure=_inspect_record(applied=True, sticky="inspect"),
    )
    names = _schema_names(MetaToolBuilder(engine).build_v5_tools_impl())
    assert turn_wire_profile(engine) == "full"
    assert "edit_spreadsheet" in names


# 原 test_calibrated_without_signed_packs_does_not_narrow 已删除：
# 二态契约下没有签字门禁，enforce + applied 即收窄，
# 与 test_signed_profiles_change_schema_not_digest_or_epoch 场景重复。


def test_child_session_does_not_narrow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_exposure(monkeypatch)
    engine = _engine(
        tmp_path,
        config=_config(jev_enabled="enforce", jev_exposure="enforce", jev_calibrated=True),
        exposure=_inspect_record(applied=True, sticky="inspect"),
        child=True,
    )
    names = _schema_names(MetaToolBuilder(engine).build_v5_tools_impl())
    assert turn_wire_profile(engine) == "full"
    assert "edit_spreadsheet" in names


@pytest.mark.asyncio
async def test_sticky_two_turns_then_narrow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_exposure(monkeypatch)
    engine = _engine(
        tmp_path,
        config=_config(jev_enabled="enforce", jev_exposure="enforce", jev_calibrated=True),
    )
    inspect = _applied_decision("inspect")
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=inspect)):
        await maybe_record_turn_exposure(engine, "看看这张表")
    assert engine._turn_exposure["sticky_profile"] == "full"
    assert engine._turn_exposure["wire_narrow"] is False
    assert engine._turn_exposure["applied"] is True
    assert turn_wire_profile(engine) == "full"
    assert "edit_spreadsheet" in _schema_names(MetaToolBuilder(engine).build_v5_tools_impl())

    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=inspect)):
        await maybe_record_turn_exposure(engine, "再看一眼结构")
    assert engine._turn_exposure["sticky_profile"] == "inspect"
    assert engine._turn_exposure["wire_narrow"] is True
    assert turn_wire_profile(engine) == "inspect"
    names = _schema_names(MetaToolBuilder(engine).build_v5_tools_impl())
    assert "inspect_spreadsheet" in names
    assert "edit_spreadsheet" in names
    assert "run_shell" not in names

    edit = _applied_decision("edit")
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=edit)):
        await maybe_record_turn_exposure(engine, "改几个单元格")
    assert engine._turn_exposure["profile"] == "edit"
    assert engine._turn_exposure["sticky_profile"] == "full"
    assert engine._turn_exposure["wire_narrow"] is False
    assert turn_wire_profile(engine) == "full"
    assert "edit_spreadsheet" in _schema_names(MetaToolBuilder(engine).build_v5_tools_impl())


@pytest.mark.asyncio
async def test_child_skips_evaluate_and_narrow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_exposure(monkeypatch)
    engine = _engine(
        tmp_path,
        config=_config(jev_enabled="enforce", jev_exposure="enforce", jev_calibrated=True),
        child=True,
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_record_turn_exposure(engine, "看看这张表")
        mocked.assert_not_called()
    assert engine._turn_exposure is None
    assert turn_wire_profile(engine) == "full"
