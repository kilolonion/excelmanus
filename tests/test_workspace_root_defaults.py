"""测试辅助配置的 workspace_root 默认值约束。

这些 helper 若默认指向仓库根目录，会触发 workspace manifest 大规模扫描，
导致属性测试和 engine 相关全量测试显著变慢。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("module_name", "factory_name"),
    [
        ("tests.test_engine", "_make_config"),
        ("tests.test_engine_events", "_make_config"),
        ("tests.test_mcp_integration", "_make_config"),
        ("tests.test_write_guard", "_make_config"),
        ("tests.test_verifier_advisory", "_make_config"),
        ("tests.test_subagent_auto_select", "_make_config"),
        ("tests.test_pbt_unauthorized_tool", "_make_config"),
    ],
)
def test_config_helpers_default_workspace_root_to_test_dir(
    module_name: str,
    factory_name: str,
) -> None:
    """测试 helper 的默认 workspace_root 应落在对应测试目录。"""
    module = importlib.import_module(module_name)
    factory = getattr(module, factory_name)
    config = factory()
    expected = Path(module.__file__).resolve().parent
    assert Path(config.workspace_root).resolve() == expected


def test_vba_engine_helper_defaults_workspace_root_to_test_dir() -> None:
    """VBA 测试中的 _make_engine 默认 workspace_root 也应使用测试目录。"""
    module = importlib.import_module("tests.test_vba_support")
    engine = module.TestVbaExemptEngineIntegration._make_engine()
    expected = Path(module.__file__).resolve().parent
    assert Path(engine._config.workspace_root).resolve() == expected
