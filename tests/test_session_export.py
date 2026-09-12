"""Tests for excelmanus/session_export.py — Markdown / JSON session export."""

from __future__ import annotations

import json
import pytest

from excelmanus.session_export import (
    export_json,
    export_markdown,
    _extract_text_content,
    _escape_md_table_cell,
)


@pytest.fixture
def session_meta():
    return {
        "id": "test-session-001",
        "title": "测试会话",
        "created_at": "2026-03-01T00:00:00Z",
        "updated_at": "2026-03-01T01:00:00Z",
        "workspace_path": "/tmp/ws",
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


class TestExportJson:
    def test_structure(self, session_meta, sample_messages):
        data = export_json(session_meta, sample_messages)
        assert "exported_at" in data
        assert data["session"]["id"] == "test-session-001"
        assert data["session"]["title"] == "测试会话"
        assert data["session"]["workspace_path"] == "/tmp/ws"
        assert data["messages"] == sample_messages
        assert data["excel_diffs"] == []
        assert data["excel_previews"] == []
        assert data["affected_files"] == []

    def test_with_excel_data(self, session_meta, sample_messages, sample_excel_diffs, sample_excel_previews):
        data = export_json(
            session_meta, sample_messages,
            excel_diffs=sample_excel_diffs,
            excel_previews=sample_excel_previews,
            affected_files=["test.xlsx"],
        )
        assert len(data["excel_diffs"]) == 1
        assert len(data["excel_previews"]) == 1
        assert data["affected_files"] == ["test.xlsx"]

    def test_no_restore_fields(self, session_meta, sample_messages):
        data = export_json(session_meta, sample_messages)
        for key in (
            "format",
            "version",
            "workspace_files",
            "memories",
            "session_state",
            "task_list",
            "config_snapshot",
        ):
            assert key not in data

    def test_json_serializable(self, session_meta, sample_messages):
        data = export_json(session_meta, sample_messages)
        restored = json.loads(json.dumps(data, ensure_ascii=False))
        assert restored["session"]["id"] == "test-session-001"
        assert restored["messages"][1]["content"] == "请帮我分析这个表格"

    def test_missing_optional_meta_defaults(self, sample_messages):
        data = export_json({"id": "s1"}, sample_messages)
        assert data["session"]["title"] == ""
        assert data["session"]["workspace_path"] == ""


def test_old_export_symbols_removed():
    import excelmanus.session_export as mod

    for name in (
        "export_emx",
        "export_text",
        "parse_emx",
        "EMXImportError",
        "EMX_FORMAT_ID",
        "EMX_VERSION",
        "collect_workspace_files",
        "restore_workspace_files",
    ):
        assert not hasattr(mod, name)
    assert hasattr(mod, "export_json")
    assert hasattr(mod, "export_markdown")


def test_session_manager_has_no_emx_roundtrip():
    from excelmanus.session import SessionManager

    assert not hasattr(SessionManager, "export_full_session")
    assert not hasattr(SessionManager, "import_full_session")
