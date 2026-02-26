"""CLI prompt 模块测试。"""

from __future__ import annotations

from excelmanus.cli.prompt import (
    apply_prompt_command_sync,
    build_prompt_badges,
    compute_inline_suggestion,
    is_interactive_terminal,
    _list_known_slash_commands,
    _SLASH_COMMAND_SUGGESTIONS,
    _DYNAMIC_SKILL_SLASH_COMMANDS,
    _COMMAND_ARGUMENT_MAP,
)
import excelmanus.cli.prompt as prompt_mod
from excelmanus.cli.commands import PromptCommandSyncPayload


class TestBuildPromptBadges:
    def test_empty(self):
        assert build_prompt_badges() == ""

    def test_model_only(self):
        assert build_prompt_badges(model_hint="gpt-4") == "gpt-4"

    def test_model_and_turn(self):
        result = build_prompt_badges(model_hint="gpt-4", turn_number=3)
        assert "gpt-4" in result
        assert "#3" in result

    def test_turn_zero_omitted(self):
        result = build_prompt_badges(model_hint="gpt-4", turn_number=0)
        assert "#" not in result


class TestApplyPromptCommandSync:
    def teardown_method(self):
        prompt_mod._SLASH_COMMAND_SUGGESTIONS = ()
        prompt_mod._DYNAMIC_SKILL_SLASH_COMMANDS = ()
        prompt_mod._COMMAND_ARGUMENT_MAP = {}

    def test_apply_payload_updates_prompt_command_surface(self):
        payload = PromptCommandSyncPayload(
            slash_command_suggestions=("/help", "/model"),
            dynamic_skill_slash_commands=("/excel_clean",),
            command_argument_map={"/model": ("list", "gpt-4o")},
        )

        apply_prompt_command_sync(payload)

        assert prompt_mod._SLASH_COMMAND_SUGGESTIONS == ("/help", "/model")
        assert prompt_mod._DYNAMIC_SKILL_SLASH_COMMANDS == ("/excel_clean",)
        assert prompt_mod._COMMAND_ARGUMENT_MAP == {"/model": ("list", "gpt-4o")}


class TestComputeInlineSuggestion:
    def setup_method(self):
        # Set up test slash commands
        prompt_mod._SLASH_COMMAND_SUGGESTIONS = (
            "/help", "/history", "/clear", "/save", "/skills",
            "/model", "/fullaccess", "/subagent",
        )
        prompt_mod._DYNAMIC_SKILL_SLASH_COMMANDS = ()
        prompt_mod._COMMAND_ARGUMENT_MAP = {
            "/fullaccess": ("on", "off", "status"),
            "/subagent": ("on", "off", "status", "list"),
        }

    def teardown_method(self):
        prompt_mod._SLASH_COMMAND_SUGGESTIONS = ()
        prompt_mod._DYNAMIC_SKILL_SLASH_COMMANDS = ()
        prompt_mod._COMMAND_ARGUMENT_MAP = {}

    def test_non_slash_returns_none(self):
        assert compute_inline_suggestion("hello") is None

    def test_exact_match_returns_none(self):
        assert compute_inline_suggestion("/help") is None

    def test_partial_command_suggests(self):
        result = compute_inline_suggestion("/hel")
        assert result == "p"

    def test_partial_command_with_multiple_matches(self):
        # /h 同时匹配 /help 与 /history，应返回首个匹配的后缀
        result = compute_inline_suggestion("/h")
        assert result is not None

    def test_argument_suggestion(self):
        result = compute_inline_suggestion("/fullaccess ")
        assert result == "on"

    def test_partial_argument(self):
        result = compute_inline_suggestion("/fullaccess st")
        assert result == "atus"

    def test_exact_argument_returns_none(self):
        assert compute_inline_suggestion("/fullaccess on") is None

    def test_unknown_command_no_args(self):
        assert compute_inline_suggestion("/unknown ") is None


class TestListKnownSlashCommands:
    def setup_method(self):
        prompt_mod._SLASH_COMMAND_SUGGESTIONS = ("/help", "/clear")
        prompt_mod._DYNAMIC_SKILL_SLASH_COMMANDS = ("/data_basic",)

    def teardown_method(self):
        prompt_mod._SLASH_COMMAND_SUGGESTIONS = ()
        prompt_mod._DYNAMIC_SKILL_SLASH_COMMANDS = ()

    def test_combines_static_and_dynamic(self):
        result = _list_known_slash_commands()
        assert "/help" in result
        assert "/clear" in result
        assert "/data_basic" in result

    def test_deduplication(self):
        prompt_mod._DYNAMIC_SKILL_SLASH_COMMANDS = ("/help",)
        result = _list_known_slash_commands()
        assert result.count("/help") == 1


class TestIsInteractiveTerminal:
    def test_returns_bool(self):
        result = is_interactive_terminal()
        assert isinstance(result, bool)
