"""excelmanus.utils 公共辅助函数测试。"""

from __future__ import annotations

import pytest

from excelmanus.utils import mask_pii


class TestMaskPii:
    def test_phone(self):
        assert mask_pii("电话 13812345678") == "电话 138****5678"

    def test_id_card(self):
        assert mask_pii("身份证 320106199001011234") == "身份证 320106********1234"

    def test_bank_card(self):
        result = mask_pii("卡号 6222021234561234")
        assert "6222 **** **** 1234" in result

    def test_email(self):
        assert mask_pii("邮箱 test@example.com") == "邮箱 t***@example.com"

    def test_no_pii(self):
        text = "这是一段普通文本，没有敏感信息。"
        assert mask_pii(text) == text

    def test_mixed_pii(self):
        text = "张三 13812345678 user@test.com"
        result = mask_pii(text)
        assert "138****5678" in result
        assert "u***@test.com" in result
        assert "13812345678" not in result
