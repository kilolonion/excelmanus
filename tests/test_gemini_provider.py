"""Gemini provider 单元测试。"""

from __future__ import annotations


def test_image_content_part_to_gemini():
    """image_url content part 转换为 Gemini inlineData。"""
    from excelmanus.providers.gemini import _openai_messages_to_gemini

    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "分析这张图"},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64,iVBORw0KGgo=",
                "detail": "high",
            }},
        ]},
    ]
    _, contents = _openai_messages_to_gemini(messages)
    assert len(contents) == 1
    parts = contents[0]["parts"]
    assert any(p.get("text") == "分析这张图" for p in parts)
    assert any("inlineData" in p for p in parts)
    inline = next(p for p in parts if "inlineData" in p)
    assert inline["inlineData"]["mimeType"] == "image/png"
    assert inline["inlineData"]["data"] == "iVBORw0KGgo="


def test_text_only_message_unchanged():
    """纯文本 user 消息保持不变。"""
    from excelmanus.providers.gemini import _openai_messages_to_gemini

    messages = [{"role": "user", "content": "hello"}]
    _, contents = _openai_messages_to_gemini(messages)
    assert len(contents) == 1
    assert contents[0]["parts"] == [{"text": "hello"}]


def test_normalize_thinking_level_clamps_minimal_on_gemini_38():
    from excelmanus.providers.gemini import _normalize_gemini_thinking_level

    assert _normalize_gemini_thinking_level("gemini-3.8-flash", "minimal") == "low"
    assert _normalize_gemini_thinking_level("gemini-3.8-flash", "high") == "high"
    assert _normalize_gemini_thinking_level("gemini-2.5-flash", "minimal") == "minimal"
