"""CLI approval 模块测试。"""

from __future__ import annotations

from excelmanus.cli.approval import (
    APPROVAL_ACCEPT,
    APPROVAL_FULLACCESS,
    APPROVAL_REJECT,
    _APPROVAL_OPTIONS,
    _is_interactive,
)


class TestApprovalConstants:
    def test_constants_are_strings(self):
        assert isinstance(APPROVAL_ACCEPT, str)
        assert isinstance(APPROVAL_REJECT, str)
        assert isinstance(APPROVAL_FULLACCESS, str)

    def test_options_count(self):
        assert len(_APPROVAL_OPTIONS) == 3

    def test_options_have_three_elements(self):
        for label, desc, value in _APPROVAL_OPTIONS:
            assert isinstance(label, str)
            assert isinstance(desc, str)
            assert value in (APPROVAL_ACCEPT, APPROVAL_REJECT, APPROVAL_FULLACCESS)


class TestIsInteractive:
    def test_returns_bool(self):
        result = _is_interactive()
        assert isinstance(result, bool)
