"""code_tools 工具函数测试：写文本文件与执行 Python 代码。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from excelmanus.security import SecurityViolationError
from excelmanus.tools import code_tools


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """创建临时工作区并初始化 guard。"""
    code_tools.init_guard(str(tmp_path))
    (tmp_path / "scripts" / "temp").mkdir(parents=True, exist_ok=True)
    return tmp_path


class TestWriteTextFile:
    def test_write_success(self, workspace: Path) -> None:
        result = json.loads(
            code_tools.write_text_file(
                "scripts/temp/job.py",
                "print('ok')\n",
            )
        )
        assert result["status"] == "success"
        assert result["file"] == "scripts/temp/job.py"
        assert (workspace / "scripts" / "temp" / "job.py").exists()

    def test_write_reject_when_overwrite_false(self, workspace: Path) -> None:
        target = workspace / "scripts" / "temp" / "job.py"
        target.write_text("old", encoding="utf-8")
        result = json.loads(
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


class TestRunCodeInline:
    """run_code 内联模式测试。"""

    def test_inline_success(self, workspace: Path) -> None:
        result = json.loads(
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
        assert result["sandbox"]["mode"] == "soft"
        assert result["sandbox"]["isolated_python"] is True

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
        result = json.loads(
            code_tools.run_code(
                code="def(",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "failed"
        assert result["return_code"] != 0

    def test_inline_timeout(self, workspace: Path) -> None:
        result = json.loads(
            code_tools.run_code(
                code="import time; time.sleep(5)",
                timeout_seconds=1,
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "timed_out"
        assert result["timed_out"] is True
        assert result["return_code"] == 124

    def test_inline_sandbox_env_whitelist(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXCELMANUS_TEST_SECRET", "TOP_SECRET")
        result = json.loads(
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

        result = json.loads(
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

        result = json.loads(
            code_tools.run_code(
                script_path="scripts/temp/auto_ok.py",
                python_command="auto",
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert result["python_resolve_mode"] == "auto"
        probes = result["python_probe_results"]
        assert probes[0]["status"] == "not_found"
        assert probes[-1]["status"] == "ok"

    def test_file_path_traversal_rejected(self, workspace: Path) -> None:
        with pytest.raises(SecurityViolationError):
            code_tools.run_code(script_path="../outside.py")


class TestRunCodeValidation:
    """参数校验测试。"""

    def test_both_params_prefers_script_path(self, workspace: Path) -> None:
        """当 code 和 script_path 都传了非空值时，优先使用 script_path。"""
        script = workspace / "scripts" / "temp" / "both.py"
        script.write_text("print('from_file')\n", encoding="utf-8")
        result = json.loads(
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
        result = json.loads(
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
        result = json.loads(
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
        result = json.loads(
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
        result = json.loads(
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


class TestRecoveryHint:
    """run_code 沙盒权限错误恢复提示测试。"""

    def test_bench_protection_recovery_hint(self, workspace: Path) -> None:
        """写入 bench 保护目录失败时应返回 recovery_hint。"""
        bench_dir = workspace / "bench" / "external"
        bench_dir.mkdir(parents=True)
        target = bench_dir / "data.txt"
        target.write_text("original", encoding="utf-8")
        code = f"with open(r'{target}', 'w') as f:\n    f.write('bad')"
        result = json.loads(
            code_tools.run_code(
                code=code,
                python_command=sys.executable,
                sandbox_tier="GREEN",
            )
        )
        assert result["status"] == "failed"
        assert "recovery_hint" in result
        assert "bench/external" in result["recovery_hint"]
        assert "mcp_excel" in result["recovery_hint"] or "delegate_to_subagent" in result["recovery_hint"]

    def test_no_recovery_hint_on_success(self, workspace: Path) -> None:
        """成功执行不应有 recovery_hint。"""
        result = json.loads(
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
        result = json.loads(
            code_tools.run_code(
                code="raise ValueError('test')",
                python_command=sys.executable,
                sandbox_tier="RED",
            )
        )
        assert result["status"] == "failed"
        assert "recovery_hint" not in result


class TestGetTools:
    def test_tool_names(self) -> None:
        names = {tool.name for tool in code_tools.get_tools()}
        assert names == {"write_text_file", "run_code"}
