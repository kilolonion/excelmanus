"""code_tools 工具函数测试：写文本文件与执行 Python 代码。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from excelmanus.security import SecurityViolationError
from excelmanus.tools import code_tools


def _bind_full_access(workspace: Path):
    from excelmanus.tools.context import (
        CallerCapability,
        SessionBinding,
        ToolCallContext,
        bind_call,
    )
    from excelmanus.workspace.refs import WorkspaceRef

    return bind_call(ToolCallContext(
        binding=SessionBinding(
            session_id="full-access-test",
            workspace=WorkspaceRef.from_root(workspace),
            capability=CallerCapability(approval="never", full_access=True),
        ),
        call_id="run-code-full-access",
        tool_name="run_code",
    ))


def _payload(result):
    from excelmanus.engine_core.tool_result import ToolResult

    if isinstance(result, ToolResult):
        data = dict(result.value) if isinstance(result.value, dict) else json.loads(result.model_text)
        if result.error is not None:
            data.setdefault("error", result.error.message)
            data.setdefault("message", result.error.message)
            data.setdefault("error_code", result.error.code)
            # 保留 run_code 已有 status（timed_out / failed），不要踩成 "error"
            if not data.get("status"):
                data["status"] = "error"
        return data
    if isinstance(result, str):
        return json.loads(result)
    return result


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """创建临时工作区并初始化 guard。"""
    code_tools.init_guard(str(tmp_path))
    (tmp_path / "scripts" / "temp").mkdir(parents=True, exist_ok=True)
    from excelmanus.workbook_commit import seed_seen_versions
    seed_seen_versions({})
    return tmp_path


class TestWriteTextFile:
    def test_write_success(self, workspace: Path) -> None:
        result = _payload(
            code_tools.write_text_file(
                "scripts/temp/job.py",
                "print('ok')\n",
            )
        )
        assert result["status"] == "success"
        assert result["file_path"] == "scripts/temp/job.py"
        assert (workspace / "scripts" / "temp" / "job.py").exists()
        assert result["content_version"].startswith("sha256:")

    def test_write_existing_requires_version(self, workspace: Path) -> None:
        target = workspace / "scripts" / "temp" / "job.py"
        target.write_text("old", encoding="utf-8")
        result = _payload(
            code_tools.write_text_file("scripts/temp/job.py", "new")
        )
        assert result.get("error_code") == "VERSION_CONFLICT"
        assert target.read_text(encoding="utf-8") == "old"

    def test_write_existing_with_version(self, workspace: Path) -> None:
        from excelmanus.workbook_commit import content_version_of_file

        target = workspace / "scripts" / "temp" / "job.py"
        target.write_text("old", encoding="utf-8")
        result = _payload(
            code_tools.write_text_file(
                "scripts/temp/job.py",
                "new",
                expected_version=content_version_of_file(target),
            )
        )
        assert result["status"] == "success"
        assert target.read_text(encoding="utf-8") == "new"

    def test_write_xlsx_rejected(self, workspace: Path) -> None:
        result = _payload(
            code_tools.write_text_file("book.xlsx", "not-excel")
        )
        assert result.get("error_code") == "PATH_INVALID"
        assert not (workspace / "book.xlsx").exists()

    def test_write_reject_when_overwrite_false(self, workspace: Path) -> None:
        target = workspace / "scripts" / "temp" / "job.py"
        target.write_text("old", encoding="utf-8")
        result = _payload(
            code_tools.write_text_file(
                "scripts/temp/job.py",
                "new",
                overwrite=False,
            )
        )
        assert result["status"] == "error"
        assert "已存在" in result["error"]
        assert target.read_text(encoding="utf-8") == "old"

    def test_write_path_traversal_rejected(self, workspace: Path) -> None:
        with pytest.raises(SecurityViolationError):
            code_tools.write_text_file("../escape.py", "print(1)")


class TestEditTextFile:
    def test_edit_existing_requires_version(self, workspace: Path) -> None:
        target = workspace / "scripts" / "temp" / "job.py"
        target.write_text("print('old')\n", encoding="utf-8")
        result = _payload(
            code_tools.edit_text_file(
                "scripts/temp/job.py",
                "print('old')",
                "print('new')",
            )
        )
        assert result.get("error_code") == "VERSION_CONFLICT"
        assert target.read_text(encoding="utf-8") == "print('old')\n"

    def test_edit_with_version(self, workspace: Path) -> None:
        from excelmanus.workbook_commit import content_version_of_file

        target = workspace / "scripts" / "temp" / "job.py"
        target.write_text("print('old')\n", encoding="utf-8")
        result = _payload(
            code_tools.edit_text_file(
                "scripts/temp/job.py",
                "print('old')",
                "print('new')",
                expected_version=content_version_of_file(target),
            )
        )
        assert result["status"] == "success"
        assert target.read_text(encoding="utf-8") == "print('new')\n"


class TestRunCodeInline:
    """run_code 内联模式测试。"""

    def test_inline_success(self, workspace: Path) -> None:
        result = _payload(
            code_tools.run_code(
                code="print('hello')\n",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert result["return_code"] == 0
        assert result["mode"] == "inline"
        assert "hello" in result["stdout_tail"]
        assert result["sandbox_tier"] == "RED"

    def test_full_access_promotes_yellow_to_network_capable_red(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from excelmanus.tools.context import reset_call

        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:8080")
        token = _bind_full_access(workspace)
        try:
            result = _payload(code_tools.run_code(
                code=(
                    "import socket\n"
                    "sock = socket.socket()\n"
                    "print('network-socket-enabled')\n"
                    "sock.close()\n"
                ),
                python_command=sys.executable,
                require_excel_deps=False,
                sandbox_tier="YELLOW",
            ))
        finally:
            reset_call(token)
        assert result["status"] == "success"
        assert result["sandbox_tier"] == "RED"
        assert "network-socket-enabled" in result["stdout_tail"]

    def test_full_access_allows_external_file_io(self, workspace: Path, tmp_path: Path) -> None:
        """完全访问放开普通外部文件；受限模式仍由 wrapper 拦截。"""
        external = tmp_path / "outside.txt"
        external.write_text("outside-ok", encoding="utf-8")
        from excelmanus.tools.context import reset_call

        token = _bind_full_access(workspace)
        try:
            result = _payload(code_tools.run_code(
                code=(
                    f"from pathlib import Path\n"
                    f"p = Path({str(external)!r})\n"
                    "print(p.read_text(encoding='utf-8'))\n"
                    "p.write_text('updated-ok', encoding='utf-8')\n"
                ),
                python_command=sys.executable,
                require_excel_deps=False,
            ))
        finally:
            reset_call(token)
        assert result["status"] == "success"
        assert "outside-ok" in result["stdout_tail"]
        assert external.read_text(encoding="utf-8") == "updated-ok"

    def test_inline_stdout_utf8_chinese(self, workspace: Path) -> None:
        result = _payload(
            code_tools.run_code(
                code="print('中文核对')\n",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert "中文核对" in result["stdout_tail"]

    def test_inline_cleans_temp_file(self, workspace: Path) -> None:
        code_tools.run_code(
            code="print(1)",
            python_command=sys.executable,
            require_excel_deps=False,
        )
        temp_dir = workspace / "scripts" / "temp"
        remaining = [f for f in temp_dir.iterdir() if f.name.startswith("_rc_")]
        assert remaining == []

    def test_inline_syntax_error(self, workspace: Path) -> None:
        result = _payload(
            code_tools.run_code(
                code="def(",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "failed"
        assert result["return_code"] != 0

    def test_inline_timeout(self, workspace: Path) -> None:
        packed = code_tools.run_code(
            code="import time; time.sleep(5)",
            timeout_seconds=1,
            python_command=sys.executable,
            require_excel_deps=False,
        )
        assert packed.success is False
        assert packed.error is not None
        assert packed.error.code == "RUN_CODE_TIMEOUT"
        result = _payload(packed)
        assert result["status"] == "timed_out"
        assert result["timed_out"] is True
        assert result["return_code"] == 124

    def test_inline_sandbox_env_whitelist(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXCELMANUS_TEST_SECRET", "TOP_SECRET")
        result = _payload(
            code_tools.run_code(
                code="import os; print(os.getenv('EXCELMANUS_TEST_SECRET'))",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert "None" in result["stdout_tail"]


class TestRunCodeFile:
    """文件模式测试（兼容旧脚本执行行为）。"""

    def test_file_success(self, workspace: Path) -> None:
        script = workspace / "scripts" / "temp" / "ok.py"
        script.write_text("print('hello')\n", encoding="utf-8")

        result = _payload(
            code_tools.run_code(
                script_path="scripts/temp/ok.py",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert result["mode"] == "file"
        assert "hello" in result["stdout_tail"]

    def test_file_auto_fallback_when_env_invalid(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = workspace / "scripts" / "temp" / "auto_ok.py"
        script.write_text("print('auto')\n", encoding="utf-8")
        monkeypatch.setenv("EXCELMANUS_RUN_PYTHON", "python_not_found_123")

        result = _payload(
            code_tools.run_code(
                script_path="scripts/temp/auto_ok.py",
                python_command="auto",
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"

    def test_file_path_traversal_rejected(self, workspace: Path) -> None:
        with pytest.raises(SecurityViolationError):
            code_tools.run_code(script_path="../outside.py")


class TestRunCodeValidation:
    """参数校验测试。"""

    def test_both_params_prefers_script_path(self, workspace: Path) -> None:
        """当 code 和 script_path 都传了非空值时，优先使用 script_path。"""
        script = workspace / "scripts" / "temp" / "both.py"
        script.write_text("print('from_file')\n", encoding="utf-8")
        result = _payload(
            code_tools.run_code(
                code="print('from_code')",
                script_path="scripts/temp/both.py",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert result["mode"] == "file"
        assert "from_file" in result["stdout_tail"]

    def test_neither_param_raises(self, workspace: Path) -> None:
        with pytest.raises(ValueError, match="必须指定"):
            code_tools.run_code()

    def test_empty_code_with_script_path(self, workspace: Path) -> None:
        """code 为空字符串 + script_path 有值 → 走文件模式（LLM 常见调用模式）。"""
        script = workspace / "scripts" / "temp" / "empty_code.py"
        script.write_text("print('script_ok')\n", encoding="utf-8")
        result = _payload(
            code_tools.run_code(
                code="",
                script_path="scripts/temp/empty_code.py",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert result["mode"] == "file"
        assert "script_ok" in result["stdout_tail"]

    def test_whitespace_code_with_script_path(self, workspace: Path) -> None:
        """code 为纯空白 + script_path 有值 → 走文件模式。"""
        script = workspace / "scripts" / "temp" / "ws.py"
        script.write_text("print('ws_ok')\n", encoding="utf-8")
        result = _payload(
            code_tools.run_code(
                code="   ",
                script_path="scripts/temp/ws.py",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert result["mode"] == "file"

    def test_both_empty_raises(self, workspace: Path) -> None:
        """code 和 script_path 都为空字符串 → 报错。"""
        with pytest.raises(ValueError, match="必须指定"):
            code_tools.run_code(code="", script_path="")

    def test_empty_script_path_with_code(self, workspace: Path) -> None:
        """script_path 为空字符串 + code 有值 → 走内联模式（LLM 常见调用模式）。"""
        result = _payload(
            code_tools.run_code(
                code="print('inline_ok')",
                script_path="",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert result["mode"] == "inline"
        assert "inline_ok" in result["stdout_tail"]

    def test_empty_stdout_stderr_file(self, workspace: Path) -> None:
        """stdout_file / stderr_file 为空字符串 → 视为未传，不写文件。"""
        result = _payload(
            code_tools.run_code(
                code="print('no_file')",
                stdout_file="",
                stderr_file="",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert result["stdout_file"] is None
        assert result["stderr_file"] is None

    def test_whitespace_only_params_raises(self, workspace: Path) -> None:
        """所有字符串参数都是纯空白 → 报错。"""
        with pytest.raises(ValueError, match="必须指定"):
            code_tools.run_code(code="   ", script_path="  ")

    """run_code 沙盒权限错误恢复提示测试。"""

    def test_bench_write_is_refused(self, workspace: Path) -> None:
        """Protected bench dirs refuse writes; no CoW copy is created."""
        bench_dir = workspace / "bench" / "external"
        bench_dir.mkdir(parents=True)
        target = bench_dir / "data.txt"
        target.write_text("original", encoding="utf-8")
        code = f"with open(r'{target}', 'w') as f:\n    f.write('updated')"
        result = _payload(
            code_tools.run_code(
                code=code,
                python_command=sys.executable,
                sandbox_tier="GREEN",
            )
        )
        assert result["status"] == "failed"
        blob = json.dumps(result, ensure_ascii=False)
        assert "禁止" in blob or "PermissionError" in blob
        assert "cow_mapping" not in result
        assert "cow_hint" not in result
        assert target.read_text(encoding="utf-8") == "original"
        assert not (workspace / "outputs" / "backups").exists()

    def test_no_cow_hint_on_plain_print(self, workspace: Path) -> None:
        result = _payload(
            code_tools.run_code(
                code="print('ok')",
                python_command=sys.executable,
                sandbox_tier="GREEN",
            )
        )
        assert result["status"] == "success"
        assert "cow_mapping" not in result
        assert "cow_hint" not in result

    def test_no_recovery_hint_on_success(self, workspace: Path) -> None:
        """成功执行不应有 recovery_hint。"""
        result = _payload(
            code_tools.run_code(
                code="print('ok')",
                python_command=sys.executable,
                sandbox_tier="GREEN",
            )
        )
        assert result["status"] == "success"
        assert "recovery_hint" not in result

    def test_no_recovery_hint_on_red_tier(self, workspace: Path) -> None:
        """RED 模式失败不应有 recovery_hint（RED 无沙盒保护）。"""
        result = _payload(
            code_tools.run_code(
                code="raise ValueError('test')",
                python_command=sys.executable,
                sandbox_tier="RED",
            )
        )
        assert result["status"] == "failed"
        assert "recovery_hint" not in result


class TestDetectTruncatedCode:
    """_detect_truncated_code 截断检测测试。"""

    def test_clean_code_no_warnings(self) -> None:
        warnings = code_tools._detect_truncated_code("print('hello')\nx = 1 + 2\n")
        assert warnings == []

    def test_ellipsis_in_function_stub_is_ok(self) -> None:
        code = "def foo():\n    ...\n\nprint('ok')\n"
        warnings = code_tools._detect_truncated_code(code)
        assert warnings == []

    def test_ellipsis_in_class_stub_is_ok(self) -> None:
        code = "class Foo:\n    ...\n\nprint('ok')\n"
        warnings = code_tools._detect_truncated_code(code)
        assert warnings == []

    def test_ellipsis_in_async_def_stub_is_ok(self) -> None:
        code = "async def bar():\n    ...\n\nprint('ok')\n"
        warnings = code_tools._detect_truncated_code(code)
        assert warnings == []

    def test_suspicious_ellipsis_in_try_block(self) -> None:
        code = "try:\n    import pandas\n    ...\nexcept:\n    ...\n"
        warnings = code_tools._detect_truncated_code(code)
        assert len(warnings) >= 1
        assert "Ellipsis" in warnings[0]

    def test_suspicious_ellipsis_standalone(self) -> None:
        code = "x = 1\n...\nprint('hi')\n"
        warnings = code_tools._detect_truncated_code(code)
        assert len(warnings) >= 1
        assert "占位符" in warnings[0]

    def test_code_ending_with_open_paren(self) -> None:
        code = "print(\n"
        warnings = code_tools._detect_truncated_code(code)
        assert any("截断" in w for w in warnings)

    def test_code_ending_with_comma(self) -> None:
        code = "x = [1, 2,\n"
        warnings = code_tools._detect_truncated_code(code)
        assert any("截断" in w for w in warnings)

    def test_code_ending_with_backslash(self) -> None:
        code = "x = 1 + \\\n"
        warnings = code_tools._detect_truncated_code(code)
        assert any("截断" in w for w in warnings)

    def test_normal_ending_no_warning(self) -> None:
        code = "print('done')\n"
        warnings = code_tools._detect_truncated_code(code)
        assert not any("截断" in w for w in warnings)


class TestTruncationWarningInRunCode:
    """run_code 中截断警告注入测试。"""

    def test_ellipsis_code_gets_truncation_warning(self, workspace: Path) -> None:
        code = "x = 1\n...\n"
        result = _payload(
            code_tools.run_code(
                code=code,
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert "truncation_warning" in result
        assert "Ellipsis" in result["truncation_warning"]

    def test_clean_code_no_truncation_warning(self, workspace: Path) -> None:
        result = _payload(
            code_tools.run_code(
                code="print('ok')",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert "truncation_warning" not in result


class TestEmptyOutputDiagnostic:
    """空输出诊断测试。"""

    def test_success_with_output_no_diagnostic(self, workspace: Path) -> None:
        result = _payload(
            code_tools.run_code(
                code="print('ok')",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert "empty_output_diagnostic" not in result

    def test_success_no_output_gets_diagnostic(self, workspace: Path) -> None:
        # Code that succeeds but produces no output
        result = _payload(
            code_tools.run_code(
                code="x = 1 + 2",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert "empty_output_diagnostic" in result
        assert "exit 0" in result["empty_output_diagnostic"]

    def test_failed_no_output_gets_diagnostic(self, workspace: Path) -> None:
        # Code that fails silently (try/except swallows error, no output)
        code = "import sys\ntry:\n    raise ValueError('x')\nexcept:\n    sys.exit(1)\n"
        result = _payload(
            code_tools.run_code(
                code=code,
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "failed"
        assert "empty_output_diagnostic" in result
        assert "失败" in result["empty_output_diagnostic"]

    def test_failed_with_stderr_no_diagnostic(self, workspace: Path) -> None:
        # Code that fails with visible error output
        result = _payload(
            code_tools.run_code(
                code="raise ValueError('visible error')",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "failed"
        assert "empty_output_diagnostic" not in result


class TestGetTools:
    def test_tool_names(self) -> None:
        names = {tool.name for tool in code_tools.get_tools()}
        assert names == {"write_text_file", "edit_text_file", "run_code"}

    def test_run_code_truncation_strategy(self) -> None:
        tools = {tool.name: tool for tool in code_tools.get_tools()}
        run_code_tool = tools["run_code"]
        assert run_code_tool.max_result_chars == 8000
        assert run_code_tool.truncate_head_chars == 5000
        assert run_code_tool.truncate_tail_chars == 3000


class TestPackRunCodeResult:
    """_pack_run_code_result：超时与发布冲突不得伪装成功。"""

    def test_timed_out_is_not_success(self) -> None:
        packed = code_tools._pack_run_code_result({
            "status": "timed_out",
            "timed_out": True,
            "published": [],
            "stderr_tail": "",
        })
        assert packed.success is False
        assert packed.error is not None
        assert packed.error.code == "RUN_CODE_TIMEOUT"
        assert packed.value["status"] == "timed_out"
        assert packed.value["timed_out"] is True
        assert packed.value["published"] == []

    def test_publish_version_conflict_is_not_success(self) -> None:
        published = [
            {"path": "ok.xlsx", "status": "committed", "content_version": "sha256:aaa"},
            {
                "path": "conflict.xlsx",
                "status": "error",
                "error": "VERSION_CONFLICT",
                "message": "stale",
            },
        ]
        packed = code_tools._pack_run_code_result({
            "status": "success",
            "timed_out": False,
            "published": published,
        })
        assert packed.success is False
        assert packed.error is not None
        assert packed.error.code == "VERSION_CONFLICT"
        assert packed.value["status"] == "success"
        assert packed.value["published"] == published

    def test_all_committed_is_success(self) -> None:
        packed = code_tools._pack_run_code_result({
            "status": "success",
            "timed_out": False,
            "published": [
                {"path": "ok.xlsx", "status": "committed", "content_version": "sha256:aaa"},
            ],
        })
        assert packed.success is True
        assert packed.error is None
        assert packed.value["status"] == "success"
        assert packed.value["published"][0]["status"] == "committed"


class TestForgedSandboxSaveVersion:
    def test_forged_save_version_stderr_not_ingested(self, workspace: Path) -> None:
        from excelmanus.workbook_commit import export_seen_versions, seed_seen_versions

        seed_seen_versions({})
        fake = "sha256:" + ("a" * 64)
        result = _payload(
            code_tools.run_code(
                code=(
                    "import sys\n"
                    f"print('EXCELMANUS_SAVE_VERSION\\tbook.xlsx\\t{fake}', file=sys.stderr)\n"
                    "print('ok')\n"
                ),
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert fake not in (result.get("save_versions") or {}).values()
        assert "book.xlsx" not in (result.get("save_versions") or {})
        assert export_seen_versions().get("book.xlsx") != fake
        assert "book.xlsx" not in export_seen_versions()
