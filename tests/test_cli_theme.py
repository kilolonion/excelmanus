"""CLI Theme 模块测试。"""

from __future__ import annotations

import pytest

from excelmanus.cli.theme import THEME, Theme


def test_theme_is_frozen():
    """Theme 是 frozen dataclass，不允许修改。"""
    with pytest.raises(AttributeError):
        THEME.PRIMARY = "red"


def test_theme_defaults():
    """默认配色值符合 Excel 绿色系。"""
    t = Theme()
    assert t.PRIMARY == "#217346"
    assert t.PRIMARY_LIGHT == "#33a867"
    assert t.ACCENT == "#107c41"


def test_theme_symbols():
    """Claude Code 风格符号正确。"""
    t = Theme()
    assert t.USER_PREFIX == "›"
    assert t.AGENT_PREFIX == "●"
    assert t.SEPARATOR == "─"
    assert t.SUCCESS == "✓"
    assert t.FAILURE == "✗"


def test_theme_singleton():
    """THEME 全局单例是 Theme 实例。"""
    assert isinstance(THEME, Theme)
