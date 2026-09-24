"""RC5 failure taxonomy：统一失败载荷与四类拒绝。

覆盖：
- canonical 形状含 failure_class / remediation
- 策略拒绝 / 只读守卫 / 审批拒绝 / 审批超时形状一致
- SubagentError 映射到具体 failure_class
- shadow 记录 violation 但不阻断；enforce 阻断
- 运行时发出的 error_code 全部在 ERROR_CODES 里
"""

from __future__ import annotations

import json
import re
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from excelmanus.approval import ApprovalManager
from excelmanus.config import ExcelManusConfig, _parse_tool_schema_validation_mode
from excelmanus.engine import AgentEngine
from excelmanus.engine_core.error_payload import (
    APPROVAL_DENIED,
    APPROVAL_TIMEOUT,
    ERROR_CODE_TO_FAILURE_CLASS,
    ERROR_CODES,
    FAILURE_CLASSES,
    PRE_EXECUTE_DENIED,
    REQUIRED_ERROR_KEYS,
    SUBAGENT_CODE_TO_TAXONOMY,
    dumps_error_payload,
    is_canonical_error_payload,
    make_error_payload,
    payload_from_subagent_error,
)
from excelmanus.engine_core.tool_result import (
    annotate_shadow_schema_violations,
    coerce_legacy_result,
    error_result,
)
from excelmanus.subagent.errors import SubagentError
from excelmanus.tools.registry import ToolDef, ToolRegistry
from excelmanus.tools.runtime import PRE_EXECUTE_DENIED as RUNTIME_PRE_EXECUTE_DENIED
from excelmanus.tools.runtime import ToolRuntime


def _assert_failure_shape(payload: dict, *, error_code: str, failure_class: str) -> None:
    assert is_canonical_error_payload(payload)
    assert REQUIRED_ERROR_KEYS <= payload.keys()
    assert payload["status"] == "error"
    assert payload["error_code"] == error_code
    assert payload["error_code"] in ERROR_CODES
    assert payload["failure_class"] == failure_class
    assert payload["failure_class"] in FAILURE_CLASSES
    assert isinstance(payload["remediation"], str) and payload["remediation"].strip()
    assert "error" not in payload
    assert "code" not in payload


def _parse_result_payload(result) -> dict:
    if hasattr(result, "value") and isinstance(result.value, dict):
        return result.value
    if hasattr(result, "structured") and result.structured is not None:
        value = result.structured.value
        if isinstance(value, dict):
            return value
    text = getattr(result, "result", None) or getattr(result, "model_text", None) or ""
    parsed = json.loads(text)
    assert isinstance(parsed, dict)
    return parsed


class TestCanonicalShape:
    def test_make_error_payload_includes_taxonomy(self) -> None:
        payload = make_error_payload("参数非法", error_code="INVALID_ARGS")
        _assert_failure_shape(payload, error_code="INVALID_ARGS", failure_class="invalid_args")

    def test_error_result_keeps_code_alias_on_structured_error(self) -> None:
        tr = error_result("工作表不存在", code="SHEET_NOT_FOUND")
        _assert_failure_shape(tr.value, error_code="SHEET_NOT_FOUND", failure_class="not_found")
        assert tr.error is not None
        assert tr.error.code == tr.value["error_code"]

    def test_every_error_code_has_failure_class(self) -> None:
        assert set(ERROR_CODE_TO_FAILURE_CLASS) == set(ERROR_CODES)
        assert set(ERROR_CODE_TO_FAILURE_CLASS.values()) <= set(FAILURE_CLASSES)

    def test_excel_semantic_codes_registered(self) -> None:
        for code in (
            "FORMULA_ERROR",
            "WORKBOOK_PROTECTED",
            "OUT_OF_RANGE",
            "NAMED_RANGE_NOT_FOUND",
            "TABLE_NOT_FOUND",
        ):
            assert code in ERROR_CODES
            payload = make_error_payload("x", error_code=code)
            assert is_canonical_error_payload(payload)


class TestFourDenialClasses:
    @pytest.mark.asyncio
    async def test_policy_denied_is_permission_denied(self, tmp_path: Path) -> None:
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
        payload = _parse_result_payload(result)
        _assert_failure_shape(
            payload,
            error_code="TOOL_NOT_ALLOWED",
            failure_class="permission_denied",
        )

    @pytest.mark.asyncio
    async def test_pre_execute_guard_denied(self) -> None:
        class _DummyDispatcher:
            def parse_arguments(self, raw):
                if isinstance(raw, dict):
                    return raw, None
                return {}, None

            async def execute(self, *args, **kwargs):
                raise AssertionError("deny 路径不得落到 dispatcher.execute")

        engine = SimpleNamespace(
            _current_chat_mode="write",
            config=SimpleNamespace(tool_result_hard_cap_chars=0),
            _config=SimpleNamespace(tool_result_hard_cap_chars=0),
            _registry=None,
            registry=None,
            _last_guard_deny="只读子代理拒绝写入：copy_file",
        )
        runtime = ToolRuntime(_DummyDispatcher(), engine=engine)
        runtime.add_pre_execute(lambda token: "deny")
        tc = SimpleNamespace(
            id="call_guard",
            function=SimpleNamespace(
                name="copy_file",
                arguments={"source": "a.xlsx", "destination": "b.xlsx"},
            ),
            parent_call_id="",
        )
        result = await runtime.execute(tc, ["copy_file"], None, 1)
        assert result.success is False
        assert result.error == PRE_EXECUTE_DENIED
        assert result.error == RUNTIME_PRE_EXECUTE_DENIED
        payload = _parse_result_payload(result)
        _assert_failure_shape(
            payload,
            error_code=PRE_EXECUTE_DENIED,
            failure_class="permission_denied",
        )
        assert "拒绝写入" in payload["message"] or "拒绝" in payload["message"]

    def test_approval_denied(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        pending = manager.create_pending(
            tool_name="write_text_file",
            arguments={"file_path": "a.py", "content": "x"},
            tool_scope=["write_text_file"],
        )
        raw = manager.reject_pending(pending.approval_id)
        payload = json.loads(raw)
        _assert_failure_shape(
            payload,
            error_code=APPROVAL_DENIED,
            failure_class="approval_denied",
        )
        assert "已拒绝" in payload["message"]
        assert manager.has_pending() is False

    def test_approval_timeout(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        pending = manager.create_pending(
            tool_name="write_text_file",
            arguments={"file_path": "a.py", "content": "x"},
            tool_scope=["write_text_file"],
        )
        raw = manager.reject_pending(pending.approval_id, timeout=True)
        payload = json.loads(raw)
        _assert_failure_shape(
            payload,
            error_code=APPROVAL_TIMEOUT,
            failure_class="approval_timeout",
        )
        assert "超时" in payload["message"]
        assert manager.has_pending() is False

    def test_four_denials_share_the_same_keys(self, tmp_path: Path) -> None:
        policy = make_error_payload(
            "工具 'add_numbers' 不在当前授权范围内。",
            error_code="TOOL_NOT_ALLOWED",
            tool="add_numbers",
        )
        guard = make_error_payload(
            "只读子代理拒绝写入：copy_file",
            error_code=PRE_EXECUTE_DENIED,
        )
        manager = ApprovalManager(str(tmp_path))
        pending = manager.create_pending(
            tool_name="run_shell",
            arguments={"command": "echo x"},
            tool_scope=["run_shell"],
        )
        denied = json.loads(manager.reject_pending(pending.approval_id))
        pending2 = manager.create_pending(
            tool_name="run_shell",
            arguments={"command": "echo y"},
            tool_scope=["run_shell"],
        )
        timeout = json.loads(manager.reject_pending(pending2.approval_id, timeout=True))
        payloads = [policy, guard, denied, timeout]
        key_sets = [frozenset(item.keys()) & REQUIRED_ERROR_KEYS for item in payloads]
        assert len(set(key_sets)) == 1
        classes = [item["failure_class"] for item in payloads]
        assert classes == [
            "permission_denied",
            "permission_denied",
            "approval_denied",
            "approval_timeout",
        ]


class TestSubagentErrorMapping:
    def test_config_errors_map_to_unsupported_or_invalid_args(self) -> None:
        disabled = payload_from_subagent_error(SubagentError("DISABLED", "subagent 关闭"))
        _assert_failure_shape(disabled, error_code="TOOL_NOT_ALLOWED", failure_class="unsupported")
        assert disabled["subagent_code"] == "DISABLED"

        empty = payload_from_subagent_error(SubagentError("EMPTY_TASK", "task 必须为非空字符串"))
        _assert_failure_shape(empty, error_code="INVALID_ARGS", failure_class="invalid_args")

        missing = payload_from_subagent_error(SubagentError("NOT_FOUND", "未找到子代理: foo"))
        _assert_failure_shape(missing, error_code="NOT_FOUND", failure_class="not_found")

        capability = payload_from_subagent_error(
            SubagentError("UNSUPPORTED_CAPABILITY", "背景子代理尚未实现"),
        )
        _assert_failure_shape(capability, error_code="TOOL_ERROR", failure_class="unsupported")

    def test_runtime_failure_maps_to_internal(self) -> None:
        payload = payload_from_subagent_error(SubagentError("LIFECYCLE_MISMATCH", "identity diverges"))
        _assert_failure_shape(payload, error_code="TOOL_ERROR", failure_class="internal")
        unknown = payload_from_subagent_error(SubagentError("SOME_NEW_CODE", "boom"))
        _assert_failure_shape(unknown, error_code="TOOL_ERROR", failure_class="internal")

    @pytest.mark.asyncio
    async def test_session_no_longer_swallows_as_empty_success(self, tmp_path: Path) -> None:
        cfg = ExcelManusConfig(
            api_key="test-key",
            base_url="https://test.example.com/v1",
            model="test-model",
            workspace_root=str(tmp_path),
        )
        engine = AgentEngine(config=cfg, registry=ToolRegistry())
        engine._subagent_enabled = False
        result = await engine._delegate_to_subagent(task="分析这个文件")
        assert result.success is False
        assert result.stop_reason == "error"
        payload = json.loads(result.output)
        _assert_failure_shape(
            payload,
            error_code="TOOL_NOT_ALLOWED",
            failure_class="unsupported",
        )
        assert payload["subagent_code"] == "DISABLED"
        assert payload.get("published") is False

    def test_subagent_taxonomy_covers_known_codes(self) -> None:
        for code, (error_code, failure_class) in SUBAGENT_CODE_TO_TAXONOMY.items():
            assert error_code in ERROR_CODES
            assert failure_class in FAILURE_CLASSES
            payload = payload_from_subagent_error(SubagentError(code, f"{code} happened"))
            _assert_failure_shape(payload, error_code=error_code, failure_class=failure_class)


def _schema_tool() -> ToolDef:
    return ToolDef(
        name="test_tool",
        description="测试工具",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "count": {"type": "integer", "minimum": 1},
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
        func=lambda **kw: {"status": "success", "ok": True, **kw},
    )


class TestSchemaValidationModes:
    def test_config_default_is_shadow(self) -> None:
        assert ExcelManusConfig.__dataclass_fields__["tool_schema_validation_mode"].default == "shadow"
        assert _parse_tool_schema_validation_mode(None) == "shadow"
        assert _parse_tool_schema_validation_mode("off") == "off"
        assert _parse_tool_schema_validation_mode("enforce") == "enforce"

    def test_shadow_records_violation_but_does_not_block(self) -> None:
        registry = ToolRegistry()
        registry.configure_schema_validation(mode="shadow", canary_percent=100, strict_path=False)
        registry.register_tool(_schema_tool())
        arguments = {"file_path": "a.xlsx", "unknown": 1}
        raw = registry.call_tool("test_tool", dict(arguments))
        result = coerce_legacy_result(raw)
        assert result.success is True
        annotated = annotate_shadow_schema_violations(
            result,
            registry=registry,
            tool_name="test_tool",
            arguments=arguments,
        )
        assert annotated.success is True
        assert annotated.value == result.value
        payload = json.loads(annotated.model_text)
        assert isinstance(payload, dict)
        assert payload["schema_validation"] == "shadow"
        assert payload["schema_violations"]
        assert any("unknown" in item for item in payload["schema_violations"])
        assert "remediation" in payload

    def test_shadow_does_not_claim_unparsed_call_executed(self) -> None:
        registry = ToolRegistry()
        registry.configure_schema_validation(mode="shadow", canary_percent=100, strict_path=False)
        registry.register_tool(_schema_tool())
        result = error_result(
            "工具参数解析错误",
            code="INVALID_ARGS",
            fields={"executed": False, "committed": False, "parse_error": True},
        )
        annotated = annotate_shadow_schema_violations(
            result,
            registry=registry,
            tool_name="test_tool",
            arguments={},
        )
        payload = json.loads(annotated.model_text)
        assert payload["executed"] is False
        assert payload["committed"] is False
        assert payload["parse_error"] is True
        assert "schema_validation" not in payload
        assert "本次已执行" not in annotated.model_text

    @pytest.mark.asyncio
    async def test_shadow_via_runtime_finalize_is_visible_to_model(self) -> None:
        from excelmanus.engine_types import ToolCallResult

        registry = ToolRegistry()
        registry.configure_schema_validation(mode="shadow", canary_percent=100, strict_path=False)
        registry.register_tool(_schema_tool())
        arguments = {"file_path": "a.xlsx", "unknown": 1}

        class _Dispatcher:
            def parse_arguments(self, raw):
                if isinstance(raw, dict):
                    return raw, None
                return {}, None

            async def execute(self, tc, tool_scope, on_event, iteration, **kwargs):
                structured = coerce_legacy_result(
                    registry.call_tool("test_tool", dict(tc.function.arguments))
                )
                return ToolCallResult(
                    tool_name="test_tool",
                    arguments=dict(tc.function.arguments),
                    result=structured.model_text,
                    success=True,
                    structured=structured,
                )

        engine = SimpleNamespace(
            _current_chat_mode="write",
            config=SimpleNamespace(tool_result_hard_cap_chars=0),
            _config=SimpleNamespace(tool_result_hard_cap_chars=0),
            _registry=registry,
            registry=registry,
            _last_guard_deny=None,
        )
        runtime = ToolRuntime(_Dispatcher(), engine=engine)
        tc = SimpleNamespace(
            id="call_shadow",
            function=SimpleNamespace(name="test_tool", arguments=arguments),
            parent_call_id="",
        )
        result = await runtime.execute(tc, ["test_tool"], None, 1)
        assert result.success is True
        payload = json.loads(result.result)
        assert payload["schema_validation"] == "shadow"
        assert any("unknown" in item for item in payload["schema_violations"])

    def test_enforce_blocks_with_invalid_args_class(self) -> None:
        registry = ToolRegistry()
        registry.configure_schema_validation(mode="enforce", canary_percent=100, strict_path=False)
        registry.register_tool(_schema_tool())
        result = registry.call_tool("test_tool", {"count": 1})
        assert result.success is False
        payload = result.value
        _assert_failure_shape(
            payload,
            error_code="TOOL_ARGUMENT_VALIDATION_ERROR",
            failure_class="invalid_args",
        )
        assert any("file_path" in item for item in payload["violations"])

    def test_off_does_not_annotate(self) -> None:
        registry = ToolRegistry()
        registry.configure_schema_validation(mode="off", canary_percent=100, strict_path=False)
        registry.register_tool(_schema_tool())
        arguments = {"file_path": "a.xlsx", "unknown": 1}
        raw = registry.call_tool("test_tool", dict(arguments))
        annotated = annotate_shadow_schema_violations(
            coerce_legacy_result(raw),
            registry=registry,
            tool_name="test_tool",
            arguments=arguments,
        )
        value = annotated.value
        if isinstance(value, dict):
            assert "schema_violations" not in value


class TestEmittedErrorCodesInVocab:
    _ASSIGN = re.compile(
        r'(?:error_code|code|error)\s*=\s*["\']([A-Z][A-Z0-9_]{3,})["\']'
    )
    _DENIED = re.compile(
        r'_denied_result\([^)]*?["\']([A-Z][A-Z0-9_]+)["\']'
    )

    def test_scanned_runtime_literals_are_in_error_codes(self) -> None:
        root = Path(__file__).resolve().parents[1] / "excelmanus"
        files = [
            root / "tools" / "runtime.py",
            root / "engine_core" / "tool_dispatcher.py",
            root / "engine_core" / "tool_result.py",
            root / "engine_core" / "error_payload.py",
            root / "approval.py",
            root / "agent" / "loop.py",
            root / "agent" / "session.py",
            root / "subagent" / "runtime.py",
        ]
        found: set[str] = set()
        for path in files:
            text = path.read_text(encoding="utf-8")
            found.update(self._ASSIGN.findall(text))
            found.update(self._DENIED.findall(text))
        found.add(PRE_EXECUTE_DENIED)
        found.add(APPROVAL_DENIED)
        found.add(APPROVAL_TIMEOUT)
        unknown = found - ERROR_CODES
        assert not unknown, f"运行时发出的 error_code 不在 ERROR_CODES: {sorted(unknown)}"

    def test_pre_execute_denied_is_no_longer_missing(self) -> None:
        runtime_src = (
            Path(__file__).resolve().parents[1] / "excelmanus" / "tools" / "runtime.py"
        ).read_text(encoding="utf-8")
        assert "PRE_EXECUTE_DENIED" in runtime_src
        assert PRE_EXECUTE_DENIED in ERROR_CODES

    def test_dumps_roundtrip_keeps_alias_contract(self) -> None:
        payload = make_error_payload("x", error_code="TOOL_NOT_ALLOWED", tool="t")
        dumped = dumps_error_payload(payload)
        parsed = json.loads(dumped)
        assert parsed["error_code"] == "TOOL_NOT_ALLOWED"
        assert "code" not in parsed
        tr = error_result("x", code="TOOL_NOT_ALLOWED", fields={"tool": "t"})
        assert tr.error is not None
        assert tr.error.code == parsed["error_code"]
