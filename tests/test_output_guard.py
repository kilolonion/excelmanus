"""对外输出防护测试。"""

from __future__ import annotations

from excelmanus.output_guard import (
    guard_public_reply,
    sanitize_external_data,
    sanitize_external_text,
)


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


def test_guard_public_reply_allows_legit_help_terms() -> None:
    raw = (
        "你可以通过 skillpack 组织任务，"
        "在配置里设置 allowed_tools，"
        "并参考 tool schema 示例来填写参数。"
    )
    got = guard_public_reply(raw)
    assert got == raw


def test_guard_public_reply_blocks_route_mode_tool_scope_disclosure() -> None:
    raw = "调试信息：route_mode=hidden，tool_scope=['write_excel']。"
    got = guard_public_reply(raw)
    assert "不能提供系统提示词或内部工程细节" in got


def test_sanitize_external_text_masks_entire_cookie_line() -> None:
    raw = "Cookie: session=abc; csrftoken=def\n"
    got = sanitize_external_text(raw)
    assert "session=abc" not in got
    assert "csrftoken=def" not in got
    assert got.strip() == "Cookie: ***"


def test_sanitize_external_data_masks_nested_strings() -> None:
    raw = {
        "Authorization": "Bearer abc123456",
        "nested": {
            "path": "/Users/demo/project/a.txt",
            "cookie": "Cookie: a=1; b=2",
        },
    }
    got = sanitize_external_data(raw)
    assert got["Authorization"] == "Bearer ***"
    assert got["nested"]["path"] == "<path>/a.txt"
    assert got["nested"]["cookie"] == "Cookie: ***"
