"""CLI welcome 模块测试。"""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from excelmanus.cli.welcome import render_welcome


def _make_console(width: int = 80) -> Console:
    return Console(file=StringIO(), width=width, force_terminal=True, highlight=False)


def _get_output(console: Console) -> str:
    console.file.seek(0)
    return console.file.read()


def _make_config(**kwargs) -> SimpleNamespace:
    defaults = {
        "model": "gpt-4o",
        "workspace_root": "/tmp/test",
        "subagent_enabled": True,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestRenderWelcome:
    def test_contains_title(self):
        console = _make_console()
        render_welcome(console, _make_config(), version="1.0.0")
        output = _get_output(console)
        assert "ExcelManus" in output
        assert "1.0.0" in output

    def test_contains_model(self):
        console = _make_console()
        render_welcome(console, _make_config(model="claude-3.5"))
        output = _get_output(console)
        assert "claude-3.5" in output

    def test_contains_tips(self):
        console = _make_console()
        render_welcome(console, _make_config())
        output = _get_output(console)
        assert "Tips" in output

    def test_contains_separator(self):
        console = _make_console()
        render_welcome(console, _make_config())
        output = _get_output(console)
        assert "─" in output

    def test_contains_ascii_art(self):
        console = _make_console()
        render_welcome(console, _make_config())
        output = _get_output(console)
        assert "A1" in output
        assert "B1" in output

    def test_narrow_terminal_no_crash(self):
        console = _make_console(width=40)
        render_welcome(console, _make_config())
        output = _get_output(console)
        assert len(output) > 0
