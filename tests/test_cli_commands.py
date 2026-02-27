"""CLI commands 模块测试。"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock

from rich.console import Console

from excelmanus.cli.commands import (
    COMMAND_ARGUMENTS_BY_ALIAS,
    DECLARED_SLASH_COMMAND_ALIASES,
    EXIT_COMMANDS,
    HELP_COMMAND_ENTRIES,
    HELP_SHORTCUT_ENTRIES,
    MODEL_ALIASES,
    SHORTCUT_ACTION_SHOW_HELP,
    SLASH_COMMANDS,
    SLASH_COMMAND_SUGGESTIONS,
    SESSION_CONTROL_ALIASES,
    build_command_argument_map,
    build_prompt_command_sync_payload,
    extract_slash_raw_args,
    load_skill_command_rows,
    mask_secret,
    parse_image_attachments,
    render_farewell,
    resolve_shortcut_action,
    resolve_skill_slash_command,
    suggest_similar_commands,
    to_standard_skill_detail,
)
from excelmanus.control_commands import CONTROL_COMMAND_SPECS


def _make_console(width: int = 80) -> Console:
    return Console(file=StringIO(), width=width, force_terminal=True, highlight=False)


def _get_output(console: Console) -> str:
    console.file.seek(0)
    return console.file.read()


class TestConstants:
    def test_exit_commands(self):
        assert "exit" in EXIT_COMMANDS
        assert "quit" in EXIT_COMMANDS

    def test_declared_aliases_keep_backward_compatible_membership_surface(self):
        assert SLASH_COMMANDS == frozenset(DECLARED_SLASH_COMMAND_ALIASES)

    def test_suggestions_non_empty(self):
        assert len(SLASH_COMMAND_SUGGESTIONS) > 10

    def test_ui_removed_from_all_command_surfaces(self):
        assert "/ui" not in SLASH_COMMANDS
        assert "/ui" not in SLASH_COMMAND_SUGGESTIONS
        help_commands = [cmd for cmd, _ in HELP_COMMAND_ENTRIES]
        assert "/ui" not in help_commands
        assert "/ui" not in COMMAND_ARGUMENTS_BY_ALIAS

    def test_suggestions_are_subset_of_declared_commands(self):
        assert set(SLASH_COMMAND_SUGGESTIONS).issubset(SLASH_COMMANDS)

    def test_argument_map_contains_aliases(self):
        assert COMMAND_ARGUMENTS_BY_ALIAS["/fullaccess"] == ("status", "on", "off")
        assert COMMAND_ARGUMENTS_BY_ALIAS["/full_access"] == ("status", "on", "off")
        assert COMMAND_ARGUMENTS_BY_ALIAS["/subagent"] == ("status", "on", "off", "list", "run")
        assert COMMAND_ARGUMENTS_BY_ALIAS["/sub_agent"] == ("status", "on", "off", "list", "run")
        assert COMMAND_ARGUMENTS_BY_ALIAS["/compact"] == ("status", "on", "off")
        assert COMMAND_ARGUMENTS_BY_ALIAS["/registry"] == ("status", "scan")

    def test_session_control_aliases(self):
        assert "/fullaccess" in SESSION_CONTROL_ALIASES
        assert "/subagent" in SESSION_CONTROL_ALIASES
        assert "/accept" in SESSION_CONTROL_ALIASES
        assert "/compact" in SESSION_CONTROL_ALIASES
        assert "/registry" in SESSION_CONTROL_ALIASES

    def test_session_control_aliases_match_shared_registry(self):
        expected_aliases = {
            alias for spec in CONTROL_COMMAND_SPECS for alias in spec.all_aliases
        }
        assert SESSION_CONTROL_ALIASES == expected_aliases


class TestShortcutRegistry:
    def test_help_shortcuts_contains_question_mark_entry(self):
        assert ("? for shortcuts", "ctrl+c to exit") in HELP_SHORTCUT_ENTRIES

    def test_resolve_shortcut_action_for_question_mark(self):
        assert resolve_shortcut_action("?") == SHORTCUT_ACTION_SHOW_HELP
        assert resolve_shortcut_action("？") == SHORTCUT_ACTION_SHOW_HELP

    def test_resolve_shortcut_action_for_non_shortcut(self):
        assert resolve_shortcut_action("普通输入") is None


class TestPromptCommandSyncContract:
    def test_build_command_argument_map_injects_model_names_for_all_aliases(self):
        arg_map = build_command_argument_map(
            model_names=["gpt-4o", "  ", "gpt-4o", "claude-3.7-sonnet"]
        )
        expected = ("list", "gpt-4o", "claude-3.7-sonnet")
        for alias in MODEL_ALIASES:
            assert arg_map[alias] == expected

    def test_build_prompt_command_sync_payload_contains_dynamic_skills_and_model_args(self):
        engine = MagicMock()
        engine.list_skillpack_commands.return_value = [
            ("data_basic", ""),
            ("data_basic", "重复"),
            ("excel_formula", ""),
        ]
        engine.model_names.return_value = ["gpt-4o-mini", "claude-3.7-sonnet"]

        payload = build_prompt_command_sync_payload(engine)

        assert payload.slash_command_suggestions == SLASH_COMMAND_SUGGESTIONS
        assert payload.dynamic_skill_slash_commands == (
            "/data_basic",
            "/excel_formula",
        )
        for alias in MODEL_ALIASES:
            assert payload.command_argument_map[alias] == (
                "list",
                "gpt-4o-mini",
                "claude-3.7-sonnet",
            )


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
