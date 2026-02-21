"""CLI commands 模块测试。"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock

from rich.console import Console

from excelmanus.cli.commands import (
    EXIT_COMMANDS,
    SLASH_COMMANDS,
    SLASH_COMMAND_SUGGESTIONS,
    SESSION_CONTROL_ALIASES,
    extract_slash_raw_args,
    load_skill_command_rows,
    mask_secret,
    parse_image_attachments,
    render_farewell,
    resolve_skill_slash_command,
    suggest_similar_commands,
    to_standard_skill_detail,
)


def _make_console(width: int = 80) -> Console:
    return Console(file=StringIO(), width=width, force_terminal=True, highlight=False)


def _get_output(console: Console) -> str:
    console.file.seek(0)
    return console.file.read()


class TestConstants:
    def test_exit_commands(self):
        assert "exit" in EXIT_COMMANDS
        assert "quit" in EXIT_COMMANDS

    def test_slash_commands_non_empty(self):
        assert len(SLASH_COMMANDS) > 10

    def test_suggestions_non_empty(self):
        assert len(SLASH_COMMAND_SUGGESTIONS) > 10

    def test_session_control_aliases(self):
        assert "/fullaccess" in SESSION_CONTROL_ALIASES
        assert "/subagent" in SESSION_CONTROL_ALIASES
        assert "/accept" in SESSION_CONTROL_ALIASES


class TestExtractSlashRawArgs:
    def test_no_args(self):
        assert extract_slash_raw_args("/help") == ""

    def test_with_args(self):
        assert extract_slash_raw_args("/skills list") == "list"

    def test_non_slash(self):
        assert extract_slash_raw_args("hello world") == ""

    def test_multiple_args(self):
        result = extract_slash_raw_args("/model set gpt-4")
        assert result == "set gpt-4"


class TestParseImageAttachments:
    def test_no_images(self):
        text, images = parse_image_attachments("hello world")
        assert text == "hello world"
        assert images == []

    def test_with_image(self):
        text, images = parse_image_attachments("analyze @img test.png")
        assert "test.png" in images
        assert "@img" not in text

    def test_multiple_images(self):
        text, images = parse_image_attachments("@img a.png @img b.jpg")
        assert len(images) == 2


class TestSuggestSimilarCommands:
    def test_exact_match(self):
        result = suggest_similar_commands("/help")
        assert "/help" in result

    def test_similar_command(self):
        result = suggest_similar_commands("/hel")
        assert "/help" in result

    def test_no_match(self):
        result = suggest_similar_commands("/zzzzzzz")
        assert result == []

    def test_empty_input(self):
        result = suggest_similar_commands("")
        assert result == []


class TestMaskSecret:
    def test_short_value(self):
        result = mask_secret("abc")
        assert "****" in result

    def test_medium_value(self):
        result = mask_secret("abcdefghij")
        assert "****" in result

    def test_long_value(self):
        result = mask_secret("sk-1234567890abcdef")
        assert "****" in result
        assert result.startswith("sk-1")
        assert result.endswith("cdef")


class TestToStandardSkillDetail:
    def test_empty_dict(self):
        assert to_standard_skill_detail({}) == {}

    def test_non_dict(self):
        assert to_standard_skill_detail("invalid") == {}

    def test_snake_to_kebab(self):
        result = to_standard_skill_detail({"file_patterns": ["*.xlsx"]})
        assert "file-patterns" in result
        assert "file_patterns" not in result


class TestResolveSkillSlashCommand:
    def test_no_resolver(self):
        engine = MagicMock(spec=[])
        assert resolve_skill_slash_command(engine, "/data_basic") is None

    def test_with_resolver(self):
        engine = MagicMock()
        engine.resolve_skill_command.return_value = "data_basic"
        assert resolve_skill_slash_command(engine, "/data_basic") == "data_basic"

    def test_resolver_returns_none(self):
        engine = MagicMock()
        engine.resolve_skill_command.return_value = None
        assert resolve_skill_slash_command(engine, "/unknown") is None


class TestLoadSkillCommandRows:
    def test_with_commands(self):
        engine = MagicMock()
        engine.list_skillpack_commands.return_value = [("data", "处理")]
        rows = load_skill_command_rows(engine)
        assert len(rows) == 1
        assert rows[0] == ("data", "处理")

    def test_no_methods(self):
        engine = MagicMock(spec=[])
        assert load_skill_command_rows(engine) == []


class TestRenderFarewell:
    def test_output(self):
        console = _make_console()
        render_farewell(console)
        output = _get_output(console)
        assert "Goodbye" in output
