"""模型可见工具错误载荷：一套键、一套词表。

覆盖：
- canonical 构造器产出 {status, error_code, message}
- 代表性工具（sheet 不存在读/写、参数非法、未授权工具、二进制读取）
  载荷键集合一致，错误码属于词表
- structured.error.code 与载荷 error_code 一致
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest
from openpyxl import Workbook

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.engine_core.error_payload import (
    ERROR_CODES,
    FAILURE_CLASSES,
    REQUIRED_ERROR_KEYS,
    SHEET_NOT_FOUND,
    is_canonical_error_payload,
)
from excelmanus.engine_core.tool_result import error_result
from excelmanus.security import FileAccessGuard
from excelmanus.tools._guard_ctx import set_guard
from excelmanus.tools.file_tools import init_guard as init_file_guard
from excelmanus.tools.file_tools import read_text_file
from excelmanus.tools.workbook_tools import apply_spreadsheet_changes, init_guard as init_intent_guard
from excelmanus.tools.workbook_tools import observe_spreadsheet
from excelmanus.tools.registry import ToolDef, ToolRegistry
from excelmanus.workbook.data import init_guard as init_data_guard
from excelmanus.workbook_commit import content_version_of_file


REQUIRED_KEYS = frozenset({"status", "error_code", "message", "failure_class", "remediation"})
FORBIDDEN_ALIAS_KEYS = frozenset({"error", "code"})


def _assert_canonical_tool_error(
    result,
    *,
    expected_code: str | None = None,
    require_model_json: bool = True,
) -> dict:
    assert result.success is False
    assert result.error is not None
    payload = result.value
    assert isinstance(payload, dict)
    assert is_canonical_error_payload(payload)
    assert REQUIRED_ERROR_KEYS <= payload.keys()
    assert payload["status"] == "error"
    assert payload["error_code"] in ERROR_CODES
    assert payload["error_code"] == result.error.code
    assert payload["failure_class"] in FAILURE_CLASSES
    assert isinstance(payload["remediation"], str) and payload["remediation"].strip()
    assert FORBIDDEN_ALIAS_KEYS.isdisjoint(payload.keys())
    if require_model_json:
        parsed = json.loads(result.model_text)
        assert parsed["error_code"] == payload["error_code"]
        assert parsed["message"] == payload["message"]
    if expected_code is not None:
        assert payload["error_code"] == expected_code
    return payload


def _book(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Sheet1"
    ws["A1"] = 1
    wb.save(path)
    return path


def _bind(root: Path) -> None:
    workspace = str(root)
    set_guard(FileAccessGuard(workspace))
    init_intent_guard(workspace)
    init_file_guard(workspace)
    init_data_guard(workspace)


class TestErrorResultConstructor:
    def test_payload_keys_are_canonical(self) -> None:
        tr = error_result("参数非法", code="INVALID_ARGS")
        payload = _assert_canonical_tool_error(tr, expected_code="INVALID_ARGS")
        assert payload["message"] == "参数非法"

    def test_extra_fields_kept_aliases_dropped(self) -> None:
        tr = error_result(
            "工作表不存在",
            code=SHEET_NOT_FOUND,
            fields={
                "available_sheets": ["Sheet1"],
                "error": "should-not-leak",
                "code": "RANGE_INVALID",
            },
        )
        payload = _assert_canonical_tool_error(tr, expected_code=SHEET_NOT_FOUND)
        assert payload["available_sheets"] == ["Sheet1"]


class TestRepresentativeToolErrors:
    def test_missing_sheet_read_and_write_use_sheet_not_found(self, tmp_path: Path) -> None:
        _bind(tmp_path)
        path = _book(tmp_path / "book.xlsx")
        rel = path.name

        read = observe_spreadsheet(
            mode="range",
            file_path=rel,
            sheet="Nope",
            range="A1:A1",
        )
        _assert_canonical_tool_error(read, expected_code=SHEET_NOT_FOUND)

        written = apply_spreadsheet_changes(
            file_path=rel,
            operations=[{
                "kind": "write",
                "sheet": "Nope",
                "start_cell": "A1",
                "values": [[1]],
            }],
            expected_version=content_version_of_file(path),
        )
        _assert_canonical_tool_error(written, expected_code=SHEET_NOT_FOUND)

    def test_missing_named_file_lists_candidates_not_swap(self, tmp_path: Path) -> None:
        _bind(tmp_path)
        _book(tmp_path / "sales.xlsx")
        result = observe_spreadsheet(mode="overview", file_path="sale.xlsx")
        payload = _assert_canonical_tool_error(result, expected_code="PATH_INVALID")
        assert "sales.xlsx" in str(payload.get("available_excel_files") or [])
        assert "擅自" in payload["remediation"] or "替换" in payload["remediation"]
        assert "list_directory" in payload["remediation"] or "files" in payload["remediation"]

    def test_invalid_args_uses_vocab_code(self, tmp_path: Path) -> None:
        _bind(tmp_path)
        path = _book(tmp_path / "grid.xlsx")
        result = observe_spreadsheet(
            mode="range",
            file_path=path.name,
            range="A1:A1",
            facets=['unknown_facet'],
        )
        _assert_canonical_tool_error(result, expected_code="INVALID_ARGS")

    def test_binary_file_read_uses_vocab_code(self, tmp_path: Path) -> None:
        _bind(tmp_path)
        binary = tmp_path / "blob.bin"
        binary.write_bytes(bytes(range(256)))
        result = read_text_file(binary.name)
        payload = _assert_canonical_tool_error(result, expected_code="DECODE_ERROR")
        assert "二进制" in payload["message"] or "编码" in payload["message"]

    @pytest.mark.asyncio
    async def test_unauthorized_tool_payload_matches_vocab(self, tmp_path: Path) -> None:
        cfg = ExcelManusConfig(
            api_key="test-key",
            base_url="https://test.example.com/v1",
            model="test-model",
            workspace_root=str(tmp_path),
        )
        engine = AgentEngine(config=cfg, registry=ToolRegistry())

        def add_numbers(a: int, b: int) -> int:
            return a + b

        engine._registry.register_tool(
            ToolDef(
                name="add_numbers",
                description="两数相加",
                input_schema={
                    "type": "object",
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                    },
                    "required": ["a", "b"],
                },
                func=add_numbers,
            )
        )
        tc = types.SimpleNamespace(
            id="call_denied",
            function=types.SimpleNamespace(
                name="add_numbers",
                arguments='{"a":1,"b":2}',
            ),
        )
        result = await engine._execute_tool_call(
            tc, tool_scope=["observe_spreadsheet"], on_event=None, iteration=1,
        )
        assert result.success is False
        structured = result.structured
        assert structured is not None
        payload = _assert_canonical_tool_error(
            structured,
            expected_code="TOOL_NOT_ALLOWED",
            require_model_json=False,
        )
        assert payload["error_code"] == structured.error.code


class TestErrorCodeVocabulary:
    def test_required_keys_constant(self) -> None:
        assert REQUIRED_ERROR_KEYS == REQUIRED_KEYS

    def test_semantic_codes_are_in_vocab(self) -> None:
        for code in (
            "INVALID_ARGS",
            "NOT_FOUND",
            "SHEET_NOT_FOUND",
            "RANGE_INVALID",
            "PATH_INVALID",
            "DECODE_ERROR",
            "TOOL_NOT_ALLOWED",
            "VERSION_CONFLICT",
            "PRE_EXECUTE_DENIED",
            "APPROVAL_DENIED",
            "APPROVAL_TIMEOUT",
            "FORMULA_ERROR",
            "WORKBOOK_PROTECTED",
            "OUT_OF_RANGE",
            "NAMED_RANGE_NOT_FOUND",
            "TABLE_NOT_FOUND",
        ):
            assert code in ERROR_CODES
