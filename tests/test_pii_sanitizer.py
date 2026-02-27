"""PII 脱敏回归测试。"""

from __future__ import annotations

import pytest

from excelmanus.security.sanitizer import (
    _sanitize_pii,
    sanitize_sensitive_text,
)


# ── 手机号 ──────────────────────────────────────────────────


class TestPhoneMasking:
    def test_standard_phone(self):
        assert _sanitize_pii("电话 13812345678") == "电话 138****5678"

    def test_phone_in_sentence(self):
        result = _sanitize_pii("联系人张三，手机号13912345678，请联系。")
        assert "139****5678" in result
        assert "13912345678" not in result

    def test_multiple_phones(self):
        result = _sanitize_pii("13812345678 和 15987654321")
        assert "138****5678" in result
        assert "159****4321" in result

    def test_non_phone_number_not_masked(self):
        """12位以上数字不应被手机号规则匹配。"""
        assert _sanitize_pii("订单号 123456789012") == "订单号 123456789012"

    def test_phone_boundary(self):
        """手机号前后有数字不应匹配。"""
        assert _sanitize_pii("编号213812345678X") == "编号213812345678X"


# ── 身份证号 ────────────────────────────────────────────────


class TestIDCardMasking:
    def test_standard_id_card(self):
        result = _sanitize_pii("身份证 320106199001011234")
        assert result == "身份证 320106********1234"

    def test_id_card_with_x(self):
        result = _sanitize_pii("320106199001011X23")
        # 18位末位X
        assert "320106********1X23" not in result  # 这个不是合法身份证格式
        # 正确的18位身份证
        result2 = _sanitize_pii("32010619900101123X")
        assert result2 == "320106********123X"

    def test_id_card_boundary(self):
        """前后有数字不应匹配。"""
        assert "320106********" not in _sanitize_pii("1320106199001011234")


# ── 银行卡号 ────────────────────────────────────────────────


class TestBankCardMasking:
    def test_16_digit_card(self):
        result = _sanitize_pii("卡号 6222021234561234")
        assert "6222 **** **** 1234" in result

    def test_19_digit_card(self):
        result = _sanitize_pii("6222021234567891234")
        assert "6222 **** **** 1234" in result


# ── 邮箱 ────────────────────────────────────────────────────


class TestEmailMasking:
    def test_standard_email(self):
        result = _sanitize_pii("邮箱 user@example.com")
        assert result == "邮箱 u***@example.com"

    def test_single_char_local(self):
        result = _sanitize_pii("u@test.cn")
        assert result == "u***@test.cn"


# ── 集成测试 ────────────────────────────────────────────────


class TestSanitizeSensitiveTextPII:
    def test_pii_enabled_by_default(self):
        result = sanitize_sensitive_text("电话 13812345678")
        assert "138****5678" in result

    def test_pii_disabled(self):
        result = sanitize_sensitive_text("电话 13812345678", mask_pii=False)
        assert "13812345678" in result

    def test_api_key_still_masked(self):
        result = sanitize_sensitive_text("EXCELMANUS_API_KEY=sk-abc123xyz456")
        assert "sk-abc123xyz456" not in result

    def test_combined_pii_and_api_key(self):
        text = "key=sk-test123456 phone=13812345678"
        result = sanitize_sensitive_text(text)
        assert "sk-test123456" not in result
        assert "138****5678" in result
