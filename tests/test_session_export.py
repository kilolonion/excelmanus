"""Tests for excelmanus/session_export.py — session export & import."""

from __future__ import annotations

import json
import pytest

from excelmanus.session_export import (
    EMX_FORMAT_ID,
    EMX_VERSION,
    EMXImportError,
    export_emx,
    export_markdown,
    export_text,
    parse_emx,
    _extract_text_content,
    _escape_md_table_cell,
)


# ── Fixtures ─────────────────────────────────────────


@pytest.fixture
def session_meta():
    return {
        "id": "test-session-001",
        "title": "测试会话",
        "created_at": "2026-03-01T00:00:00Z",
        "updated_at": "2026-03-01T01:00:00Z",
    }


@pytest.fixture
def sample_messages():
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "请帮我分析这个表格"},
        {
            "role": "assistant",
            "content": "好的，让我先读取表格内容。",
            "tool_calls": [
                {
                    "id": "tc_001",
                    "function": {
                        "name": "read_excel",
                        "arguments": '{"file_path": "test.xlsx"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc_001",
            "name": "read_excel",
            "content": "| A | B |\n| 1 | 2 |",
        },
        {"role": "assistant", "content": "表格包含 2 列数据。"},
    ]


@pytest.fixture
def sample_excel_diffs():
    return [
        {
            "file_path": "test.xlsx",
            "sheet": "Sheet1",
            "affected_range": "A1:B2",
            "changes": [{"cell": "A1", "old": 1, "new": 10}],
        }
    ]


@pytest.fixture
def sample_excel_previews():
    return [
        {
            "file_path": "test.xlsx",
            "sheet": "Sheet1",
            "columns": ["A", "B"],
            "rows": [[1, 2], [3, 4]],
            "total_rows": 2,
            "truncated": False,
        }
    ]


# ── _extract_text_content ────────────────────────────


class TestExtractTextContent:
    def test_string_content(self):
        assert _extract_text_content({"content": "hello"}) == "hello"

    def test_empty_content(self):
        assert _extract_text_content({}) == ""

    def test_multimodal_list(self):
        msg = {
            "content": [
                {"type": "text", "text": "看这张图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
            ]
        }
        result = _extract_text_content(msg)
        assert "看这张图" in result
        assert "[图片]" in result

    def test_none_content(self):
        assert _extract_text_content({"content": None}) == ""


# ── _escape_md_table_cell ─────────────────────────────


class TestEscapeMdTableCell:
    def test_pipe_escaped(self):
        assert _escape_md_table_cell("A|B") == "A\\|B"

    def test_newline_replaced(self):
        assert _escape_md_table_cell("line1\nline2") == "line1 line2"

    def test_carriage_return_stripped(self):
        assert _escape_md_table_cell("a\r\nb") == "a b"

    def test_plain_text_unchanged(self):
        assert _escape_md_table_cell("hello world") == "hello world"

    def test_combined(self):
        assert _escape_md_table_cell("a|b\nc") == "a\\|b c"


# ── Markdown export ──────────────────────────────────


class TestExportMarkdown:
    def test_basic_structure(self, session_meta, sample_messages):
        md = export_markdown(session_meta, sample_messages)
        assert "# 会话报告: 测试会话" in md
        assert "test-session-001" in md
        assert "👤 用户" in md
        assert "🤖 助手" in md
        assert "请帮我分析这个表格" in md

    def test_tool_calls_rendered(self, session_meta, sample_messages):
        md = export_markdown(session_meta, sample_messages)
        assert "`read_excel(" in md

    def test_tool_result_details(self, session_meta, sample_messages):
        md = export_markdown(session_meta, sample_messages)
        assert "📎 read_excel 结果" in md

    def test_with_excel_diffs(self, session_meta, sample_messages, sample_excel_diffs):
        md = export_markdown(session_meta, sample_messages, excel_diffs=sample_excel_diffs)
        assert "数据变更摘要" in md
        assert "test.xlsx" in md
        assert "Sheet1" in md

    def test_with_excel_previews(self, session_meta, sample_messages, sample_excel_previews):
        md = export_markdown(session_meta, sample_messages, excel_previews=sample_excel_previews)
        assert "数据快照" in md
        assert "| A | B |" in md

    def test_with_affected_files(self, session_meta, sample_messages):
        md = export_markdown(session_meta, sample_messages, affected_files=["test.xlsx"])
        assert "涉及文件" in md
        assert "`test.xlsx`" in md

    def test_system_messages_skipped(self, session_meta, sample_messages):
        md = export_markdown(session_meta, sample_messages)
        assert "You are a helpful assistant" not in md

    def test_pipe_in_preview_cells_escaped(self, session_meta, sample_messages):
        previews = [{
            "file_path": "t.xlsx", "sheet": "S1",
            "columns": ["A"], "rows": [["val|with|pipes"]],
            "total_rows": 1, "truncated": False,
        }]
        md = export_markdown(session_meta, sample_messages, excel_previews=previews)
        assert "val\\|with\\|pipes" in md


# ── Text export ──────────────────────────────────────


class TestExportText:
    def test_basic_structure(self, session_meta, sample_messages):
        txt = export_text(session_meta, sample_messages)
        assert "会话报告: 测试会话" in txt
        assert "[用户 - 轮次 1]" in txt
        assert "[助手 - 轮次 1]" in txt
        assert "请帮我分析这个表格" in txt

    def test_tool_names_listed(self, session_meta, sample_messages):
        txt = export_text(session_meta, sample_messages)
        assert "read_excel" in txt

    def test_no_markdown_syntax(self, session_meta, sample_messages):
        txt = export_text(session_meta, sample_messages)
        assert "##" not in txt
        assert "**" not in txt


# ── EMX export ───────────────────────────────────────


class TestExportEmx:
    def test_structure(self, session_meta, sample_messages):
        emx = export_emx(session_meta, sample_messages)
        assert emx["format"] == EMX_FORMAT_ID
        assert emx["version"] == EMX_VERSION
        assert "exported_at" in emx
        assert emx["session"]["id"] == "test-session-001"
        assert emx["session"]["title"] == "测试会话"
        assert len(emx["messages"]) == len(sample_messages)

    def test_with_excel_data(self, session_meta, sample_messages, sample_excel_diffs, sample_excel_previews):
        emx = export_emx(
            session_meta, sample_messages,
            excel_diffs=sample_excel_diffs,
            excel_previews=sample_excel_previews,
            affected_files=["test.xlsx"],
        )
        assert len(emx["excel_diffs"]) == 1
        assert len(emx["excel_previews"]) == 1
        assert emx["affected_files"] == ["test.xlsx"]

    def test_roundtrip_json_serializable(self, session_meta, sample_messages):
        emx = export_emx(session_meta, sample_messages)
        serialized = json.dumps(emx, ensure_ascii=False)
        restored = json.loads(serialized)
        assert restored["format"] == EMX_FORMAT_ID


# ── EMX import (parse_emx) ──────────────────────────


class TestParseEmx:
    def test_valid_roundtrip(self, session_meta, sample_messages):
        emx = export_emx(session_meta, sample_messages)
        parsed = parse_emx(emx)
        assert parsed["session_meta"]["id"] == "test-session-001"
        assert parsed["session_meta"]["title"] == "测试会话"
        assert len(parsed["messages"]) == len(sample_messages)

    def test_invalid_format(self):
        with pytest.raises(EMXImportError, match="不支持的格式"):
            parse_emx({"format": "wrong", "version": "1.0.0", "session": {}, "messages": []})

    def test_invalid_version(self):
        with pytest.raises(EMXImportError, match="不支持的版本"):
            parse_emx({"format": EMX_FORMAT_ID, "version": "2.0.0", "session": {}, "messages": []})

    def test_missing_session(self):
        with pytest.raises(EMXImportError, match="缺少 session"):
            parse_emx({"format": EMX_FORMAT_ID, "version": "1.0.0", "messages": []})

    def test_missing_messages(self):
        with pytest.raises(EMXImportError, match="缺少 messages"):
            parse_emx({"format": EMX_FORMAT_ID, "version": "1.0.0", "session": {}})

    def test_invalid_message_not_dict(self):
        with pytest.raises(EMXImportError, match="不是 dict"):
            parse_emx({
                "format": EMX_FORMAT_ID,
                "version": "1.0.0",
                "session": {"id": "x"},
                "messages": ["not a dict"],
            })

    def test_invalid_message_missing_role(self):
        with pytest.raises(EMXImportError, match="缺少 role"):
            parse_emx({
                "format": EMX_FORMAT_ID,
                "version": "1.0.0",
                "session": {"id": "x"},
                "messages": [{"content": "no role"}],
            })

    def test_defaults_for_missing_optional(self):
        parsed = parse_emx({
            "format": EMX_FORMAT_ID,
            "version": "1.0.0",
            "session": {"id": "s1"},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert parsed["excel_diffs"] == []
        assert parsed["excel_previews"] == []
        assert parsed["affected_files"] == []
        assert parsed["session_meta"]["title"] == "导入的会话"
