"""CLI 斜杠命令下拉补全器回归测试。"""

from __future__ import annotations

import pytest

import excelmanus.cli.prompt as prompt_mod

# prompt_toolkit 可能未安装，跳过测试
pytestmark = pytest.mark.skipif(
    not prompt_mod._PROMPT_TOOLKIT_ENABLED,
    reason="prompt_toolkit 未安装",
)

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document


# ------------------------------------------------------------------
# SlashCommandCompleter 测试
# ------------------------------------------------------------------


class TestSlashCommandCompleter:
    """验证 _SlashCommandCompleter 的两阶段补全。"""

    def setup_method(self):
        self.completer = prompt_mod._SLASH_COMMAND_COMPLETER
        # 注入测试用命令数据
        prompt_mod._SLASH_COMMAND_SUGGESTIONS = (
            "/help", "/model", "/config", "/fullaccess",
        )
        prompt_mod._DYNAMIC_SKILL_SLASH_COMMANDS = ("/data_clean",)
        prompt_mod._COMMAND_ARGUMENT_MAP = {
            "/config": ("list", "set", "get", "delete"),
            "/model": ("list",),
            "/fullaccess": ("on", "off", "status"),
        }

    def teardown_method(self):
        prompt_mod._SLASH_COMMAND_SUGGESTIONS = ()
        prompt_mod._DYNAMIC_SKILL_SLASH_COMMANDS = ()
        prompt_mod._COMMAND_ARGUMENT_MAP = {}

    def _get_completions(self, text: str) -> list:
        doc = Document(text, len(text))
        return list(self.completer.get_completions(doc, CompleteEvent()))

    def test_slash_only_lists_all_commands(self):
        """输入 / 后应列出所有命令。"""
        items = self._get_completions("/")
        displays = [c.display[0][1] if isinstance(c.display, list) else str(c.display) for c in items]
        assert any("/help" in d for d in displays)
        assert any("/config" in d for d in displays)
        # 动态技能命令也应出现
        assert any("/data_clean" in d for d in displays)

    def test_partial_command_filters(self):
        """输入 /he 应只匹配 /help。"""
        items = self._get_completions("/he")
        texts = [c.text for c in items]
        assert any("/help" in t for t in texts)
        assert not any("/config" in t for t in texts)

    def test_command_with_args_has_trailing_space(self):
        """有参数的命令补全后应带尾空格（以触发参数补全）。"""
        items = self._get_completions("/con")
        config_item = [c for c in items if "/config" in c.text]
        assert len(config_item) == 1
        assert config_item[0].text.endswith(" ")

    def test_command_without_args_no_trailing_space(self):
        """无参数的命令补全后不带尾空格。"""
        items = self._get_completions("/he")
        help_item = [c for c in items if "/help" in c.text]
        assert len(help_item) == 1
        assert not help_item[0].text.endswith(" ")

    def test_argument_completions(self):
        """选中命令后空格应列出参数。"""
        items = self._get_completions("/config ")
        texts = [c.text for c in items]
        assert "list" in texts
        assert "set" in texts

    def test_partial_argument_filters(self):
        """输入参数部分文字应过滤。"""
        items = self._get_completions("/config s")
        texts = [c.text for c in items]
        assert "set" in texts
        assert "list" not in texts

    def test_no_completions_for_non_slash(self):
        """非 / 开头不应有补全。"""
        items = self._get_completions("hello")
        assert items == []

    def test_description_present(self):
        """补全项应携带 display_meta（描述）。"""
        items = self._get_completions("/")
        for c in items:
            assert c.display_meta is not None or c.display_meta_text


# ------------------------------------------------------------------
# MergedCompleter 测试
# ------------------------------------------------------------------


class TestMergedCompleter:
    """验证 _MergedCompleter 根据上下文分发到正确的补全器。"""

    def setup_method(self):
        self.merged = prompt_mod._MERGED_COMPLETER
        # 注入数据
        prompt_mod._SLASH_COMMAND_SUGGESTIONS = ("/help",)
        prompt_mod._DYNAMIC_SKILL_SLASH_COMMANDS = ()
        prompt_mod._COMMAND_ARGUMENT_MAP = {}

    def teardown_method(self):
        prompt_mod._SLASH_COMMAND_SUGGESTIONS = ()
        prompt_mod._DYNAMIC_SKILL_SLASH_COMMANDS = ()
        prompt_mod._COMMAND_ARGUMENT_MAP = {}

    def _get_completions(self, text: str) -> list:
        doc = Document(text, len(text))
        return list(self.merged.get_completions(doc, CompleteEvent()))

    def test_slash_dispatches_to_slash_completer(self):
        """/ 开头应走斜杠补全器。"""
        items = self._get_completions("/")
        assert len(items) > 0

    def test_non_slash_without_mention_returns_empty(self):
        """非 / 开头且无 mention completer 应返回空。"""
        old = self.merged.mention_completer
        self.merged.mention_completer = None
        try:
            items = self._get_completions("hello")
            assert items == []
        finally:
            self.merged.mention_completer = old

    def test_mention_completer_setter(self):
        """mention_completer 属性可动态设置。"""
        old = self.merged.mention_completer
        self.merged.mention_completer = None
        assert self.merged.mention_completer is None
        self.merged.mention_completer = old
        assert self.merged.mention_completer is old
