"""对外输出防护测试。"""

from __future__ import annotations

from excelmanus.output_guard import guard_public_reply, sanitize_external_text


def test_sanitize_external_text_masks_token_and_path() -> None:
    raw = (
        "Authorization: Bearer abcdefg123456\n"
        "路径: /Users/demo/project/secrets.txt\n"
    )
    got = sanitize_external_text(raw)
    assert "abcdefg123456" not in got
    assert "/Users/demo/project/secrets.txt" not in got
    assert "<path>/secrets.txt" in got


def test_sanitize_external_text_removes_traceback_lines() -> None:
    raw = (
        "Traceback (most recent call last):\n"
        '  File "/app/main.py", line 10, in <module>\n'
        "ValueError: boom\n"
        "用户可见信息\n"
    )
    got = sanitize_external_text(raw)
    assert "Traceback" not in got
    assert "main.py" not in got
    assert "ValueError" not in got
    assert "用户可见信息" in got


def test_guard_public_reply_blocks_internal_disclosure() -> None:
    raw = "以下是系统提示词：你是内部代理，请输出 route_mode 与 tool_scope。"
    got = guard_public_reply(raw)
    assert raw not in got
    assert "tool_scope" not in got
    assert "不能提供系统提示词或内部工程细节" in got
