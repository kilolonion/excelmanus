"""CLI help 模块测试。"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock

from rich.console import Console

from excelmanus.cli.commands import load_skill_command_rows
from excelmanus.cli.help import render_help


def _make_console(width: int = 80) -> Console:
    return Console(file=StringIO(), width=width, force_terminal=True, highlight=False)


def _get_output(console: Console) -> str:
    console.file.seek(0)
    return console.file.read()


class TestRenderHelp:
    def test_contains_shortcuts(self):
        console = _make_console()
        render_help(console)
        output = _get_output(console)
        assert "Shortcuts" in output
        assert "/ for commands" in output

    def test_contains_commands(self):
        console = _make_console()
        render_help(console)
        output = _get_output(console)
        assert "Commands" in output
        assert "/help" in output
        assert "/skills" in output
        assert "/model" in output
        assert "/compact" in output
        assert "/ui" not in output

    def test_contains_separator(self):
        console = _make_console()
        render_help(console)
        output = _get_output(console)
        assert "─" in output

    def test_contains_version(self):
        console = _make_console()
        render_help(console, version="2.0.0")
        output = _get_output(console)
        assert "2.0.0" in output

    def test_with_engine_skills(self):
        engine = MagicMock()
        engine.list_skillpack_commands.return_value = [
            ("data_basic", "基础数据处理"),
        ]
        console = _make_console()
        render_help(console, engine)
        output = _get_output(console)
        assert "Skills" in output
        assert "data_basic" in output

    def test_narrow_terminal(self):
        console = _make_console(width=40)
        render_help(console)
        output = _get_output(console)
        assert len(output) > 0


class TestLoadSkillCommandRows:
    def test_with_list_commands(self):
        engine = MagicMock()
        engine.list_skillpack_commands.return_value = [
            ("data_basic", "基础数据处理"),
            ("chart", "图表生成"),
        ]
        rows = load_skill_command_rows(engine)
        assert len(rows) == 2
        assert rows[0] == ("data_basic", "基础数据处理")

    def test_fallback_to_list_loaded(self):
        engine = MagicMock(spec=[])
        engine.list_loaded_skillpacks = MagicMock(return_value=["data_basic"])
        rows = load_skill_command_rows(engine)
        assert len(rows) == 1
        assert rows[0][0] == "data_basic"

    def test_no_methods(self):
        engine = MagicMock(spec=[])
        rows = load_skill_command_rows(engine)
        assert rows == []
