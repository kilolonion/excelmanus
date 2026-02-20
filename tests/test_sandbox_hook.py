"""运行时沙盒钩子集成测试。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from excelmanus.security.sandbox_hook import generate_wrapper_script


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _run_in_sandbox(workspace: Path, script_content: str, tier: str) -> subprocess.CompletedProcess:
    """辅助函数：在沙盒 wrapper 中执行脚本。"""
    script = workspace / "test_script.py"
    script.write_text(script_content, encoding="utf-8")
    wrapper = generate_wrapper_script(tier, str(workspace))
    wrapper_path = workspace / "_wrapper.py"
    wrapper_path.write_text(wrapper, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(wrapper_path), str(script)],
        capture_output=True, text=True, timeout=10,
    )


class TestGreenSandbox:
    """GREEN 模式：禁止网络 + 子进程模块导入。"""

    def test_import_requests_blocked(self, workspace: Path) -> None:
        result = _run_in_sandbox(workspace, "import requests\nprint('should not reach')", "GREEN")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr

    def test_import_subprocess_blocked(self, workspace: Path) -> None:
        result = _run_in_sandbox(workspace, "import subprocess\nprint('should not reach')", "GREEN")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr

    def test_import_socket_blocked(self, workspace: Path) -> None:
        result = _run_in_sandbox(workspace, "import socket", "GREEN")
        assert result.returncode != 0

    def test_safe_import_allowed(self, workspace: Path) -> None:
        result = _run_in_sandbox(workspace, "import json\nprint(json.dumps({'ok': True}))", "GREEN")
        assert result.returncode == 0
        assert "ok" in result.stdout

    def test_pandas_import_allowed(self, workspace: Path) -> None:
        # pandas may not be in the subprocess env, test that import hook doesn't block it
        code = "try:\n    import pandas\n    print('import_ok')\nexcept ImportError:\n    print('not_installed_ok')"
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0
        assert "ok" in result.stdout

    def test_file_write_inside_workspace_allowed(self, workspace: Path) -> None:
        out = workspace / "output.txt"
        code = f"with open(r'{out}', 'w') as f:\n    f.write('hello')\nprint('done')"
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0
        assert out.read_text() == "hello"

    def test_file_write_outside_workspace_blocked(self, workspace: Path) -> None:
        import tempfile
        outside = Path(tempfile.mkdtemp())
        target = outside / "escape.txt"
        code = f"with open(r'{target}', 'w') as f:\n    f.write('evil')"
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode != 0
        assert not target.exists()

    def test_file_read_outside_workspace_allowed(self, workspace: Path) -> None:
        import tempfile
        outside = Path(tempfile.mkdtemp())
        outside_file = outside / "readable.txt"
        outside_file.write_text("safe_data", encoding="utf-8")
        code = f"with open(r'{outside_file}', 'r') as f:\n    print(f.read())"
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0
        assert "safe_data" in result.stdout


class TestYellowSandbox:
    """YELLOW 模式：允许网络模块，禁止子进程。"""

    def test_import_subprocess_blocked(self, workspace: Path) -> None:
        result = _run_in_sandbox(workspace, "import subprocess", "YELLOW")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr

    def test_import_ctypes_blocked(self, workspace: Path) -> None:
        result = _run_in_sandbox(workspace, "import ctypes", "YELLOW")
        assert result.returncode != 0

    def test_import_socket_allowed(self, workspace: Path) -> None:
        # socket should NOT be blocked in YELLOW
        result = _run_in_sandbox(workspace, "import socket\nprint('socket_ok')", "YELLOW")
        assert result.returncode == 0
        assert "socket_ok" in result.stdout

    def test_network_module_not_blocked(self, workspace: Path) -> None:
        # requests may not be installed, just verify the hook doesn't block it
        code = "try:\n    import requests\n    print('import_ok')\nexcept ImportError:\n    print('not_installed_ok')"
        result = _run_in_sandbox(workspace, code, "YELLOW")
        assert result.returncode == 0
        assert "ok" in result.stdout


class TestRedSandbox:
    """RED 模式：无钩子注入，所有操作允许。"""

    def test_no_restrictions(self, workspace: Path) -> None:
        code = "import json, os\nprint(json.dumps({'pid': os.getpid()}))"
        result = _run_in_sandbox(workspace, code, "RED")
        assert result.returncode == 0
        data = json.loads(result.stdout.strip())
        assert "pid" in data


class TestWrapperPreservesSemantics:
    """Wrapper 不应破坏 __file__ / __name__ 语义。"""

    def test_file_and_name(self, workspace: Path) -> None:
        script = workspace / "check_env.py"
        script.write_text(
            "import json\nprint(json.dumps({'file': __file__, 'name': __name__}))",
            encoding="utf-8",
        )
        wrapper = generate_wrapper_script("GREEN", str(workspace))
        wrapper_path = workspace / "_wrapper.py"
        wrapper_path.write_text(wrapper, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(wrapper_path), str(script)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout.strip())
        assert data["name"] == "__main__"
        assert str(script) in data["file"]
