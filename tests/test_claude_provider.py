"""Claude provider 单元测试。"""

from __future__ import annotations


def test_image_content_part_to_claude():
    """image_url content part 转换为 Claude image block。"""
    from excelmanus.providers.claude import _openai_messages_to_claude

    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": [
            {"type": "text", "text": "分析这张图"},
            {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64,/9j/4AAQ=",
            }},
        ]},
    ]
    system_text, claude_msgs = _openai_messages_to_claude(messages)
    assert len(claude_msgs) == 1
    content = claude_msgs[0]["content"]
    assert isinstance(content, list)
    assert any(b.get("type") == "text" for b in content)
    assert any(b.get("type") == "image" for b in content)
    img_block = next(b for b in content if b.get("type") == "image")
    assert img_block["source"]["type"] == "base64"
    assert img_block["source"]["media_type"] == "image/jpeg"
    assert img_block["source"]["data"] == "/9j/4AAQ="


def test_text_only_message_unchanged():
    """纯文本 user 消息保持不变。"""
    from excelmanus.providers.claude import _openai_messages_to_claude

    messages = [{"role": "user", "content": "hello"}]
    _, claude_msgs = _openai_messages_to_claude(messages)
    assert len(claude_msgs) == 1
    assert claude_msgs[0]["content"] == "hello"
