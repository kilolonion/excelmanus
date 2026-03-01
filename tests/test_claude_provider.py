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


# ── 内联 <thinking> 标签提取 ──────────────────────────────


def test_extract_inline_thinking_basic():
    """从 text 中提取 <thinking> 标签内容。"""
    from excelmanus.providers.stream_types import extract_inline_thinking as _extract_inline_thinking

    text = "<thinking>Let me think about this.</thinking>\n\nThe answer is 42."
    thinking, clean = _extract_inline_thinking(text)
    assert thinking == "Let me think about this."
    assert "<thinking>" not in clean
    assert "The answer is 42." in clean


def test_extract_inline_thinking_no_tags():
    """没有 <thinking> 标签时原文不变。"""
    from excelmanus.providers.stream_types import extract_inline_thinking as _extract_inline_thinking

    text = "The answer is 42."
    thinking, clean = _extract_inline_thinking(text)
    assert thinking == ""
    assert clean == text


def test_extract_inline_thinking_multiple():
    """多个 <thinking> 块合并提取。"""
    from excelmanus.providers.stream_types import extract_inline_thinking as _extract_inline_thinking

    text = "<thinking>Step 1</thinking>middle<thinking>Step 2</thinking>end"
    thinking, clean = _extract_inline_thinking(text)
    assert "Step 1" in thinking
    assert "Step 2" in thinking
    assert "<thinking>" not in clean
    assert "middle" in clean
    assert "end" in clean


# ── 非流式 _claude_response_to_openai ──────────────────────


def test_response_to_openai_standard_thinking_block():
    """标准 thinking content block 被正确提取到 message.thinking。"""
    from excelmanus.providers.claude import _claude_response_to_openai

    data = {
        "id": "msg_test",
        "model": "claude-sonnet-4-5-20250929",
        "content": [
            {"type": "thinking", "thinking": "I need to reason carefully."},
            {"type": "text", "text": "The answer is 9."},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    result = _claude_response_to_openai(data, "claude-sonnet-4-5-20250929")
    msg = result.choices[0].message
    assert msg.content == "The answer is 9."
    assert msg.thinking == "I need to reason carefully."
    assert msg.reasoning_content == "I need to reason carefully."


def test_response_to_openai_inline_thinking_tags():
    """<thinking> 内联标签从 text 中分离到 message.thinking。"""
    from excelmanus.providers.claude import _claude_response_to_openai

    data = {
        "id": "msg_test",
        "model": "claude-sonnet-4-5-20250929",
        "content": [
            {"type": "text", "text": "<thinking>This is a tricky question.\nLet me think.</thinking>\n\n9.8 is larger."},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    result = _claude_response_to_openai(data, "claude-sonnet-4-5-20250929")
    msg = result.choices[0].message
    assert "<thinking>" not in (msg.content or "")
    assert "9.8 is larger." in msg.content
    assert msg.thinking is not None
    assert "tricky question" in msg.thinking


def test_response_to_openai_empty_thinking_block():
    """空 thinking block（被中转站清空）不影响 message。"""
    from excelmanus.providers.claude import _claude_response_to_openai

    data = {
        "id": "msg_test",
        "model": "claude-sonnet-4-5-20250929",
        "content": [
            {"type": "thinking", "thinking": ""},
            {"type": "text", "text": "The answer."},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    result = _claude_response_to_openai(data, "claude-sonnet-4-5-20250929")
    msg = result.choices[0].message
    assert msg.content == "The answer."
    assert msg.thinking is None


def test_response_to_openai_combined_standard_and_inline():
    """标准 thinking block + 内联 <thinking> 标签同时存在时合并。"""
    from excelmanus.providers.claude import _claude_response_to_openai

    data = {
        "id": "msg_test",
        "model": "claude-sonnet-4-5-20250929",
        "content": [
            {"type": "thinking", "thinking": "Standard thinking."},
            {"type": "text", "text": "<thinking>Inline thinking.</thinking>\n\nFinal answer."},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    result = _claude_response_to_openai(data, "claude-sonnet-4-5-20250929")
    msg = result.choices[0].message
    assert "Standard thinking." in msg.thinking
    assert "Inline thinking." in msg.thinking
    assert "<thinking>" not in (msg.content or "")
    assert "Final answer." in msg.content


def test_response_to_openai_no_thinking():
    """无 thinking 时 message.thinking 为 None。"""
    from excelmanus.providers.claude import _claude_response_to_openai

    data = {
        "id": "msg_test",
        "model": "claude-sonnet-4-5-20250929",
        "content": [
            {"type": "text", "text": "Just a normal answer."},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 5},
    }
    result = _claude_response_to_openai(data, "claude-sonnet-4-5-20250929")
    msg = result.choices[0].message
    assert msg.content == "Just a normal answer."
    assert msg.thinking is None
    assert msg.reasoning is None


# ── InlineThinkingStateMachine ──────────────────────────


def test_state_machine_no_tags():
    """无标签时全部作为 content_delta 输出。"""
    from excelmanus.providers.stream_types import InlineThinkingStateMachine

    sm = InlineThinkingStateMachine()
    results = sm.feed("Hello world")
    assert len(results) == 1
    assert results[0].content_delta == "Hello world"
    assert results[0].thinking_delta == ""


def test_state_machine_single_chunk():
    """单 chunk 包含完整 <thinking> 标签。"""
    from excelmanus.providers.stream_types import InlineThinkingStateMachine

    sm = InlineThinkingStateMachine()
    results = sm.feed("<thinking>I need to think</thinking>The answer.")
    thinking = "".join(r.thinking_delta for r in results)
    content = "".join(r.content_delta for r in results)
    assert "I need to think" in thinking
    assert "The answer." in content
    assert "<thinking>" not in content


def test_state_machine_cross_chunk():
    """标签跨 chunk 边界。"""
    from excelmanus.providers.stream_types import InlineThinkingStateMachine

    sm = InlineThinkingStateMachine()
    all_results = []
    for chunk in ["<thin", "king>My thought</thi", "nking>Done"]:
        all_results.extend(sm.feed(chunk))
    all_results.extend(sm.flush())
    thinking = "".join(r.thinking_delta for r in all_results)
    content = "".join(r.content_delta for r in all_results)
    assert "My thought" in thinking
    assert "Done" in content


def test_state_machine_flush():
    """流结束时 flush 残余缓冲。"""
    from excelmanus.providers.stream_types import InlineThinkingStateMachine

    sm = InlineThinkingStateMachine()
    results = sm.feed("Hello<")
    flushed = sm.flush()
    all_content = "".join(r.content_delta for r in results + flushed)
    assert "Hello<" in all_content


# ── Gemini provider 内联 thinking ───────────────────────


def test_gemini_response_to_openai_inline_thinking():
    """内联 <thinking> 标签从 Gemini 响应中分离。"""
    from excelmanus.providers.gemini import _gemini_response_to_openai

    data = {
        "candidates": [{
            "content": {
                "parts": [{"text": "<thinking>Step 1: Analyze</thinking>\n\nThe result is 42."}],
            },
            "finishReason": "STOP",
        }],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20},
    }
    result = _gemini_response_to_openai(data, "gemini-2.5-flash")
    msg = result.choices[0].message
    assert msg.thinking is not None
    assert "Step 1: Analyze" in msg.thinking
    assert "<thinking>" not in (msg.content or "")
    assert "42" in msg.content


def test_gemini_response_to_openai_thought_part():
    """标准 Gemini thought part 被正确提取。"""
    from excelmanus.providers.gemini import _gemini_response_to_openai

    data = {
        "candidates": [{
            "content": {
                "parts": [
                    {"thought": "Let me reason about this."},
                    {"text": "The answer is 9."},
                ],
            },
            "finishReason": "STOP",
        }],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20},
    }
    result = _gemini_response_to_openai(data, "gemini-2.5-flash")
    msg = result.choices[0].message
    assert msg.thinking is not None
    assert "reason" in msg.thinking
    assert msg.content == "The answer is 9."


# ── OpenAI Responses provider 内联 thinking ─────────────


def test_responses_output_to_openai_inline_thinking():
    """内联 <thinking> 标签从 Responses API 响应中分离。"""
    from excelmanus.providers.openai_responses import _responses_output_to_openai

    data = {
        "id": "resp_test",
        "model": "gpt-5",
        "output": [
            {"type": "message", "content": [
                {"type": "output_text", "text": "<thinking>Hmm, tricky question.</thinking>\n\nThe answer is 7."},
            ]},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }
    result = _responses_output_to_openai(data, "gpt-5")
    msg = result.choices[0].message
    assert msg.thinking is not None
    assert "tricky" in msg.thinking
    assert "<thinking>" not in (msg.content or "")
    assert "7" in msg.content
