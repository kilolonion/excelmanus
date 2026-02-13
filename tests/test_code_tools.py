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
    """文件模式测试（兼容旧 run_python_script 行为）。"""

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
    """参数互斥校验测试。"""

    def test_both_params_raises(self, workspace: Path) -> None:
        with pytest.raises(ValueError, match="互斥"):
            code_tools.run_code(code="print(1)", script_path="x.py")

    def test_neither_param_raises(self, workspace: Path) -> None:
        with pytest.raises(ValueError, match="必须指定"):
            code_tools.run_code()


class TestGetTools:
    def test_tool_names(self) -> None:
        names = {tool.name for tool in code_tools.get_tools()}
        assert names == {"write_text_file", "run_code"}
