"""code_tools 工具函数测试：写脚本与执行脚本能力。"""

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


class TestRunPythonScript:
    def test_run_success_with_explicit_interpreter(self, workspace: Path) -> None:
        script = workspace / "scripts" / "temp" / "ok.py"
        script.write_text("print('hello')\n", encoding="utf-8")

        result = json.loads(
            code_tools.run_python_script(
                script_path="scripts/temp/ok.py",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert result["return_code"] == 0
        assert "hello" in result["stdout_tail"]
        assert result["sandbox"]["mode"] == "soft"
        assert result["sandbox"]["isolated_python"] is True
        assert isinstance(result["sandbox"]["limits_applied"], bool)
        assert isinstance(result["sandbox"]["warnings"], list)

    def test_run_uses_sandbox_env_whitelist(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        script = workspace / "scripts" / "temp" / "env_check.py"
        script.write_text(
            "import os\n"
            "print(os.getenv('EXCELMANUS_TEST_SECRET'))\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("EXCELMANUS_TEST_SECRET", "TOP_SECRET")

        result = json.loads(
            code_tools.run_python_script(
                script_path="scripts/temp/env_check.py",
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "success"
        assert "None" in result["stdout_tail"]
        assert result["sandbox"]["mode"] == "soft"

    def test_run_timeout(self, workspace: Path) -> None:
        script = workspace / "scripts" / "temp" / "slow.py"
        script.write_text(
            "import time\n"
            "time.sleep(2)\n"
            "print('done')\n",
            encoding="utf-8",
        )

        result = json.loads(
            code_tools.run_python_script(
                script_path="scripts/temp/slow.py",
                timeout_seconds=1,
                python_command=sys.executable,
                require_excel_deps=False,
            )
        )
        assert result["status"] == "timed_out"
        assert result["timed_out"] is True
        assert result["return_code"] == 124

    def test_run_auto_fallback_when_env_invalid(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        script = workspace / "scripts" / "temp" / "auto_ok.py"
        script.write_text("print('auto')\n", encoding="utf-8")
        monkeypatch.setenv("EXCELMANUS_RUN_PYTHON", "python_not_found_123")

        result = json.loads(
            code_tools.run_python_script(
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

    def test_run_path_traversal_rejected(self, workspace: Path) -> None:
        with pytest.raises(SecurityViolationError):
            code_tools.run_python_script("../outside.py")


class TestGetTools:
    def test_tool_names(self) -> None:
        names = {tool.name for tool in code_tools.get_tools()}
        assert names == {"write_text_file", "run_python_script"}
