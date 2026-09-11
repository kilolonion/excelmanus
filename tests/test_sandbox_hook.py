"""运行时沙盒钩子集成测试。"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from excelmanus.security.sandbox_hook import generate_wrapper_script


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _sha256_version(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_save_versions(stderr: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in stderr.splitlines():
        if not line.startswith("EXCELMANUS_SAVE_VERSION\t"):
            continue
        parts = line.split("\t")
        assert len(parts) == 3, line
        versions[parts[1]] = parts[2]
    return versions


def _run_in_sandbox(
    workspace: Path,
    script_content: str,
    tier: str,
    *,
    env_override: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """辅助函数：在沙盒 wrapper 中执行脚本。"""
    script = workspace / "test_script.py"
    script.write_text(script_content, encoding="utf-8")
    wrapper = generate_wrapper_script(tier, str(workspace))
    wrapper_path = workspace / "_wrapper.py"
    wrapper_path.write_text(wrapper, encoding="utf-8")
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [sys.executable, str(wrapper_path), str(script)],
        capture_output=True, text=True, timeout=10, env=env,
    )


class TestGreenSandbox:
    """GREEN 模式：禁止网络模块导入 + subprocess 函数级拦截。"""

    def test_import_requests_blocked(self, workspace: Path) -> None:
        result = _run_in_sandbox(workspace, "import requests\nprint('should not reach')", "GREEN")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr

    def test_import_subprocess_allowed(self, workspace: Path) -> None:
        """subprocess 模块允许导入（pandas/matplotlib 内部依赖）。"""
        result = _run_in_sandbox(workspace, "import subprocess\nprint('import_ok')", "GREEN")
        assert result.returncode == 0
        assert "import_ok" in result.stdout

    def test_subprocess_run_blocked(self, workspace: Path) -> None:
        """subprocess.run() 等进程创建函数被拦截。"""
        result = _run_in_sandbox(workspace, "import subprocess\nsubprocess.run(['echo','hi'])", "GREEN")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr

    def test_subprocess_popen_blocked(self, workspace: Path) -> None:
        result = _run_in_sandbox(workspace, "import subprocess\nsubprocess.Popen(['echo','hi'])", "GREEN")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr

    def test_subprocess_check_output_blocked(self, workspace: Path) -> None:
        result = _run_in_sandbox(workspace, "import subprocess\nsubprocess.check_output(['echo','hi'])", "GREEN")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr

    def test_from_os_import_execv_blocked(self, workspace: Path) -> None:
        """回归测试：from os import execv 不应绕过进程创建拦截。"""
        code = "from os import execv\nexecv('/bin/echo', ('echo', 'hi'))"
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr

    def test_import_socket_allowed(self, workspace: Path) -> None:
        """socket 模块允许导入（matplotlib.pyplot 内部依赖）。"""
        result = _run_in_sandbox(workspace, "import socket\nprint('socket_ok')", "GREEN")
        assert result.returncode == 0
        assert "socket_ok" in result.stdout

    def test_socket_create_blocked(self, workspace: Path) -> None:
        """创建 socket 实例被拦截（禁止实际网络通信）。"""
        result = _run_in_sandbox(workspace, "import socket\ns = socket.socket()", "GREEN")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr

    def test_raw_socket_create_blocked(self, workspace: Path) -> None:
        """回归测试：import _socket 不应绕过网络拦截。"""
        result = _run_in_sandbox(workspace, "import _socket\ns = _socket.socket()", "GREEN")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr

    def test_socket_gethostname_allowed(self, workspace: Path) -> None:
        """只读信息函数仍可用。"""
        result = _run_in_sandbox(workspace, "import socket\nprint('host:', socket.gethostname())", "GREEN")
        assert result.returncode == 0
        assert "host:" in result.stdout

    def test_matplotlib_pyplot_allowed(self, workspace: Path) -> None:
        """回归测试：matplotlib.pyplot 依赖 socket，确保 GREEN 沙盒不拦截。"""
        code = "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt; print('pyplot_ok')"
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0
        assert "pyplot_ok" in result.stdout

    def test_safe_import_allowed(self, workspace: Path) -> None:
        result = _run_in_sandbox(workspace, "import json\nprint(json.dumps({'ok': True}))", "GREEN")
        assert result.returncode == 0
        assert "ok" in result.stdout

    def test_pandas_import_allowed(self, workspace: Path) -> None:
        """回归测试：pandas 3.x 内部依赖 subprocess，确保 GREEN 沙盒不拦截。"""
        code = "import pandas\nprint('pandas_version:', pandas.__version__)"
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0
        assert "pandas_version:" in result.stdout

    def test_file_write_inside_workspace_allowed(self, workspace: Path) -> None:
        out = workspace / "output.txt"
        code = f"with open(r'{out}', 'w') as f:\n    f.write('hello')\nprint('done')"
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0
        assert out.read_text() == "hello"

    def test_file_write_outside_workspace_blocked(self, workspace: Path) -> None:
        # 写入一个不存在的路径（不在工作区内，也不在系统临时目录下）
        target = Path("/tmp/_sandbox_test_should_not_exist/escape.txt")
        code = (
            "import os\n"
            f"os.makedirs(os.path.dirname(r'{target}'), exist_ok=True)\n"
            f"with open(r'{target}', 'w') as f:\n"
            f"    f.write('evil')\n"
        )
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
    """YELLOW 模式：允许网络模块，subprocess 允许导入但函数被拦截。"""

    def test_import_subprocess_allowed(self, workspace: Path) -> None:
        """subprocess 模块允许导入。"""
        result = _run_in_sandbox(workspace, "import subprocess\nprint('import_ok')", "YELLOW")
        assert result.returncode == 0
        assert "import_ok" in result.stdout

    def test_subprocess_run_blocked(self, workspace: Path) -> None:
        """subprocess.run() 仍被拦截。"""
        result = _run_in_sandbox(workspace, "import subprocess\nsubprocess.run(['echo','hi'])", "YELLOW")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr

    def test_import_ctypes_allowed(self, workspace: Path) -> None:
        """ctypes 已从 YELLOW 禁止列表移除（pandas 等数据处理库间接依赖）。"""
        result = _run_in_sandbox(workspace, "import ctypes\nprint('ctypes_ok')", "YELLOW")
        assert result.returncode == 0
        assert "ctypes_ok" in result.stdout

    def test_import_socket_allowed(self, workspace: Path) -> None:
        # YELLOW 模式下不应拦截 socket
        result = _run_in_sandbox(workspace, "import socket\nprint('socket_ok')", "YELLOW")
        assert result.returncode == 0
        assert "socket_ok" in result.stdout

    def test_raw_socket_create_blocked(self, workspace: Path) -> None:
        result = _run_in_sandbox(workspace, "import _socket\ns = _socket.socket()", "YELLOW")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr

    def test_network_module_not_blocked(self, workspace: Path) -> None:
        # requests 可能未安装，仅验证 hook 不拦截其导入
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


class TestAutoCoW:
    """自动 Copy-on-Write 行为测试。"""

    def test_auto_cow_on_protected_dir(self, workspace: Path) -> None:
        import os
        
        bench_dir = workspace / "bench" / "external"
        bench_dir.mkdir(parents=True, exist_ok=True)
        target = bench_dir / "protected.txt"
        target.write_text("original_data", encoding="utf-8")
        
        outputs_dir = workspace / "outputs"
        
        # 默认 EXCELMANUS_BENCH_PROTECTED_DIRS="bench/external"
        code = (
            "import os\n"
            f"with open(r'{target}', 'w') as f:\n"
            f"    f.write('new_data')\n"
            f"with open(r'{target}', 'r') as f:\n"
            f"    print('READ:', f.read())\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0
        assert "READ: new_data" in result.stdout
        
        # 原文件未被修改
        assert target.read_text(encoding="utf-8") == "original_data"
        
        # 副本已生成并被修改
        cow_file = outputs_dir / "backups" / "protected.txt"
        assert cow_file.exists()
        assert cow_file.read_text(encoding="utf-8") == "new_data"
        
    def test_auto_cow_openpyxl_save(self, workspace: Path) -> None:
        import os
        
        bench_dir = workspace / "bench" / "external"
        bench_dir.mkdir(parents=True, exist_ok=True)
        target = bench_dir / "protected.xlsx"
        
        # 创建一个合法的空 excel
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"] = "original"
        wb.save(target)
        
        outputs_dir = workspace / "outputs"
        
        code = (
            "import openpyxl\n"
            f"wb = openpyxl.load_workbook(r'{target}')\n"
            "ws = wb.active\n"
            "ws['A1'] = 'new_data'\n"
            f"wb.save(r'{target}')\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0
        
        # 原文件未被修改
        wb_orig = openpyxl.load_workbook(target)
        assert wb_orig.active["A1"].value == "original"
        
        # 副本已生成并被修改
        cow_file = outputs_dir / "backups" / "protected.xlsx"
        assert cow_file.exists()
        wb_cow = openpyxl.load_workbook(cow_file)
        assert wb_cow.active["A1"].value == "new_data"


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


class TestSaveContentVersion:
    """P3：sandbox Workbook.save 记录 sha256 content_version。

    覆盖已有文件时，若宿主传入 EXCELMANUS_EXPECTED_VERSIONS 则比较哈希。
    不导入 workbook_commit。未传环境变量时行为与原先一致。
    """

    def test_wrapper_has_save_versions_without_workbook_commit(self) -> None:
        for tier in ("GREEN", "YELLOW", "RED"):
            src = generate_wrapper_script(tier, "/tmp/ws")
            assert "_SAVE_VERSIONS" in src
            assert "EXCELMANUS_SAVE_VERSION" in src
            code_lines = [
                ln.strip() for ln in src.splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            assert not any(
                ln.startswith("import excelmanus.workbook_commit")
                or ln.startswith("from excelmanus.workbook_commit")
                for ln in code_lines
            )
            assert "expected_version" in src
            assert "EXCELMANUS_EXPECTED_VERSIONS" in src
            assert "_check_expected_version" in src

    def test_existing_file_save_conflicts_when_expected_stale(self, workspace: Path) -> None:
        from openpyxl import Workbook

        xlsx = workspace / "outputs"
        xlsx.mkdir()
        target = xlsx / "existing.xlsx"
        wb = Workbook()
        wb.active["A1"] = "initial"
        wb.save(str(target))
        wb.close()
        seen = _sha256_version(target)

        outsider = Workbook()
        outsider.active["A1"] = "external"
        outsider.save(str(target))
        outsider.close()

        code = (
            "from openpyxl import load_workbook\n"
            f"wb = load_workbook(r'{target}')\n"
            "wb.active['A1'] = 'from-sandbox'\n"
            f"wb.save(r'{target}')\n"
            "print('saved')\n"
        )
        result = _run_in_sandbox(
            workspace,
            code,
            "GREEN",
            env_override={
                "EXCELMANUS_EXPECTED_VERSIONS": json.dumps(
                    {"outputs/existing.xlsx": seen}
                ),
            },
        )
        assert result.returncode != 0
        assert "VERSION_CONFLICT" in result.stderr
        assert _sha256_version(target) != seen
        from openpyxl import load_workbook
        wb2 = load_workbook(str(target))
        assert wb2.active["A1"].value == "external"
        wb2.close()

    def test_new_file_save_emits_sha256_on_stderr(self, workspace: Path) -> None:
        target = workspace / "outputs"
        target.mkdir()
        xlsx = target / "new.xlsx"
        code = (
            "from openpyxl import Workbook\n"
            "wb = Workbook()\n"
            "wb.active['A1'] = 'hello'\n"
            f"wb.save(r'{xlsx}')\n"
            "print('saved')\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0, result.stderr
        assert "saved" in result.stdout
        versions = _parse_save_versions(result.stderr)
        resolved = os.path.realpath(str(xlsx))
        assert versions[resolved] == _sha256_version(xlsx)

    def test_existing_file_save_emits_sha256_on_stderr(self, workspace: Path) -> None:
        from openpyxl import Workbook

        xlsx = workspace / "outputs"
        xlsx.mkdir()
        target = xlsx / "existing.xlsx"
        wb = Workbook()
        wb.active["A1"] = "initial"
        wb.save(str(target))
        wb.close()

        code = (
            "from openpyxl import load_workbook\n"
            f"wb = load_workbook(r'{target}')\n"
            "wb.active['A1'] = 'updated'\n"
            f"wb.save(r'{target}')\n"
            "print('saved')\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0, result.stderr
        versions = _parse_save_versions(result.stderr)
        resolved = os.path.realpath(str(target))
        assert versions[resolved] == _sha256_version(target)
        assert versions[resolved].startswith("sha256:")

    def test_cow_save_versions_the_copy(self, workspace: Path) -> None:
        from openpyxl import Workbook

        bench_dir = workspace / "bench" / "external"
        bench_dir.mkdir(parents=True)
        target = bench_dir / "protected.xlsx"
        wb = Workbook()
        wb.active["A1"] = "original"
        wb.save(str(target))
        wb.close()
        orig_ver = _sha256_version(target)

        code = (
            "from openpyxl import load_workbook\n"
            f"wb = load_workbook(r'{target}')\n"
            "wb.active['A1'] = 'new_data'\n"
            f"wb.save(r'{target}')\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0, result.stderr
        cow_file = workspace / "outputs" / "backups" / "protected.xlsx"
        assert cow_file.exists()
        versions = _parse_save_versions(result.stderr)
        cow_resolved = os.path.realpath(str(cow_file))
        orig_resolved = os.path.realpath(str(target))
        assert orig_resolved not in versions
        assert versions[cow_resolved] == _sha256_version(cow_file)
        assert _sha256_version(target) == orig_ver

    def test_save_versions_log_when_env_set(self, workspace: Path) -> None:
        log = workspace / "save_versions.log"
        out = workspace / "outputs"
        out.mkdir()
        xlsx = out / "logged.xlsx"
        code = (
            "from openpyxl import Workbook\n"
            "wb = Workbook()\n"
            "wb.active['A1'] = 'x'\n"
            f"wb.save(r'{xlsx}')\n"
        )
        result = _run_in_sandbox(
            workspace, code, "GREEN",
            env_override={"EXCELMANUS_SAVE_VERSIONS_LOG": str(log)},
        )
        assert result.returncode == 0, result.stderr
        assert log.exists()
        line = log.read_text(encoding="utf-8").strip()
        resolved = os.path.realpath(str(xlsx))
        assert line == f"{resolved}\t{_sha256_version(xlsx)}"

    def test_failed_new_file_save_emits_no_version(self, workspace: Path) -> None:
        """save 失败不得写出 content_version。"""
        missing_dir = workspace / "no_such_dir"
        target = missing_dir / "fail.xlsx"
        code = (
            "from openpyxl import Workbook\n"
            "wb = Workbook()\n"
            f"wb.save(r'{target}')\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode != 0
        assert _parse_save_versions(result.stderr) == {}
        assert not target.exists()
