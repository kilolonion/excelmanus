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


def _parse_pending_writes(stderr: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for line in stderr.splitlines():
        if not line.startswith("EXCELMANUS_PENDING_WRITE\t"):
            continue
        parts = line.split("\t")
        assert len(parts) == 3, line
        found.append((parts[1], parts[2]))
    return found


def _parse_save_versions(stderr: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in stderr.splitlines():
        if not line.startswith("EXCELMANUS_SAVE_VERSION\t"):
            continue
        parts = line.split("\t")
        assert len(parts) == 3, line
        versions[parts[1]] = parts[2]
    return versions


def _require_symlink_privilege(tmp_dir: Path) -> None:
    """Windows 上创建符号链接需要特权；无权限时跳过用例。"""
    target = tmp_dir / ".symlink_probe_target"
    link = tmp_dir / ".symlink_probe_link"
    target.touch()
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("当前环境无创建符号链接权限")
    finally:
        link.unlink(missing_ok=True)
        target.unlink(missing_ok=True)


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
    # 与 _execute_script 同口径：临时目录注入工作区 .tmp，保证 openpyxl 等
    # 库创建的临时文件（tmpdir 写后再读回）不触碰工作区外路径。
    sandbox_tmp = workspace / ".tmp"
    sandbox_tmp.mkdir(exist_ok=True)
    env.setdefault("TMPDIR", str(sandbox_tmp))
    env["TMPDIR"] = str(sandbox_tmp)
    env["TMP"] = str(sandbox_tmp)
    env["TEMP"] = str(sandbox_tmp)
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

    @pytest.mark.parametrize("tier", ["GREEN", "YELLOW"])
    def test_asyncio_import_and_popen_subclass_guard(self, workspace: Path, tier: str) -> None:
        # Windows asyncio subclasses subprocess.Popen at import time. Libraries
        # may import it, but neither the guard nor a subclass may launch a child.
        result = _run_in_sandbox(workspace, '''import asyncio
import subprocess
class ChildProcess(subprocess.Popen):
    pass
print('asyncio_import_ok')
ChildProcess(['echo', 'must-not-run'])
''', tier)
        assert "asyncio_import_ok" in result.stdout, result.stderr
        assert result.returncode != 0
        assert "subprocess.Popen() 被安全策略禁止" in result.stderr

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
        assert result.returncode == 0, result.stderr
        assert not out.exists()
        pending = list((workspace / ".excelmanus" / "pending").rglob("*_output.txt"))
        assert pending
        assert pending[0].read_text(encoding="utf-8") == "hello"

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

    def test_file_read_outside_workspace_denied(self, workspace: Path) -> None:
        """数据路径与 Native FileAccessGuard 同口径：区外文件不可读。"""
        import tempfile
        outside = Path(tempfile.mkdtemp())
        outside_file = outside / "readable.txt"
        outside_file.write_text("safe_data", encoding="utf-8")
        code = f"with open(r'{outside_file}', 'r') as f:\n    print(f.read())"
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode != 0
        assert "PATH_OUTSIDE_WORKSPACE" in result.stderr

    def test_pandas_read_outside_workspace_denied(self, workspace: Path) -> None:
        """pandas 直读区外文件同样被拒绝。"""
        import tempfile
        from openpyxl import Workbook

        outside = Path(tempfile.mkdtemp())
        target = outside / "secret.xlsx"
        wb = Workbook()
        wb.active["A1"] = "leak"
        wb.save(str(target))
        wb.close()
        code = f"import pandas as pd\npd.read_excel(r'{target}')"
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode != 0
        assert "PATH_OUTSIDE_WORKSPACE" in result.stderr

    def test_pathlib_read_outside_workspace_denied(self, workspace: Path) -> None:
        """pathlib.Path.read_text 也走同一守卫（回归：Path.open 读侧曾漏检）。"""
        import tempfile
        outside = Path(tempfile.mkdtemp())
        outside_file = outside / "readable.txt"
        outside_file.write_text("safe_data", encoding="utf-8")
        code = (
            "from pathlib import Path\n"
            f"print(Path(r'{outside_file}').read_text())"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode != 0
        assert "PATH_OUTSIDE_WORKSPACE" in result.stderr

    def test_symlink_escaping_workspace_denied(self, workspace: Path) -> None:
        """工作区内指向外部的符号链接按真实解析路径拒绝。"""
        _require_symlink_privilege(workspace)
        import tempfile
        outside = Path(tempfile.mkdtemp())
        outside_file = outside / "secret.txt"
        outside_file.write_text("leak", encoding="utf-8")
        link = workspace / "link.txt"
        link.symlink_to(outside_file)
        code = f"print(open(r'{link}').read())"
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode != 0
        assert "PATH_OUTSIDE_WORKSPACE" in result.stderr

    def test_excelmanus_internal_read_denied(self, workspace: Path) -> None:
        """.excelmanus 内部文件（非本 run pending）不可读、不可枚举。"""
        internal = workspace / ".excelmanus" / "excelmanus.db"
        internal.parent.mkdir(parents=True, exist_ok=True)
        internal.write_text("internal", encoding="utf-8")
        code = (
            "import os\n"
            f"print('exists', os.path.exists(r'{internal}'))\n"
            f"print('listdir_root', os.listdir(r'{workspace}'))\n"
            f"print(open(r'{internal}').read())\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode != 0
        assert "exists False" in result.stdout
        assert ".excelmanus" not in result.stdout.split("listdir_root", 1)[1].split("\n", 1)[0]
        assert "RESERVED_NAMESPACE" in result.stderr or "SENSITIVE_FILE" in result.stderr

    def test_symlink_into_excelmanus_denied(self, workspace: Path) -> None:
        """符号链接解析进 .excelmanus 同样拒绝。"""
        _require_symlink_privilege(workspace)
        internal = workspace / ".excelmanus" / "sessions"
        internal.mkdir(parents=True, exist_ok=True)
        target_file = internal / "state.json"
        target_file.write_text("{}", encoding="utf-8")
        link = workspace / "peek.json"
        link.symlink_to(target_file)
        code = f"print(open(r'{link}').read())"
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode != 0
        assert "RESERVED_NAMESPACE" in result.stderr or "SENSITIVE_FILE" in result.stderr

    def test_workspace_env_and_reserved_denied(self, workspace: Path) -> None:
        """工作区内 .env 与保留前缀（outputs/backups、.versions）拒绝读取。"""
        env_file = workspace / ".env"
        env_file.write_text("SECRET=1", encoding="utf-8")
        backups = workspace / "outputs" / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        backup_file = backups / "old.xlsx"
        backup_file.write_bytes(b"PK")
        versions = workspace / ".versions"
        versions.mkdir(exist_ok=True)
        version_file = versions / "v1.json"
        version_file.write_text("{}", encoding="utf-8")
        code = (
            "import os\n"
            "errors = []\n"
            f"for p in (r'{env_file}', r'{backup_file}', r'{version_file}'):\n"
            "    try:\n"
            "        open(p).read()\n"
            "        errors.append('READ:' + p)\n"
            "    except PermissionError as e:\n"
            "        errors.append('DENIED:' + str(e).split(':')[0])\n"
            "print(errors)\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0, result.stderr
        assert "READ:" not in result.stdout
        assert result.stdout.count("DENIED:") == 3

    def test_interpreter_files_still_readable(self, workspace: Path) -> None:
        """解释器/库文件仍可读（import 机制与库内部数据文件依赖）。"""
        code = (
            "import sysconfig, os\n"
            "stdlib = sysconfig.get_path('stdlib')\n"
            "target = os.path.join(stdlib, 'json', '__init__.py')\n"
            "with open(target) as f:\n"
            "    head = f.read(16)\n"
            "print('stdlib_read_ok', bool(head))\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0, result.stderr
        assert "stdlib_read_ok True" in result.stdout


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


class TestBenchWriteRefused:
    """受保护 bench 目录拒绝写入，不再 Copy-on-Write。"""

    def test_open_write_on_protected_dir_refused(self, workspace: Path) -> None:
        bench_dir = workspace / "bench" / "external"
        bench_dir.mkdir(parents=True, exist_ok=True)
        target = bench_dir / "protected.txt"
        target.write_text("original_data", encoding="utf-8")

        code = (
            "import os\n"
            f"with open(r'{target}', 'w') as f:\n"
            f"    f.write('new_data')\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr
        assert "bench" in result.stderr
        assert target.read_text(encoding="utf-8") == "original_data"
        assert not (workspace / "outputs" / "backups").exists()

    def test_openpyxl_save_on_protected_dir_refused(self, workspace: Path) -> None:
        import openpyxl

        bench_dir = workspace / "bench" / "external"
        bench_dir.mkdir(parents=True, exist_ok=True)
        target = bench_dir / "protected.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"] = "original"
        wb.save(target)

        code = (
            "import openpyxl\n"
            f"wb = openpyxl.load_workbook(r'{target}')\n"
            "ws = wb.active\n"
            "ws['A1'] = 'new_data'\n"
            f"wb.save(r'{target}')\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr or "工作区表格禁止直接保存" in result.stderr
        wb_orig = openpyxl.load_workbook(target)
        assert wb_orig.active["A1"].value == "original"
        assert not (workspace / "outputs" / "backups").exists()


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
    """Sandbox Workbook.save 只把表格写到本 run 的 pending，CAS 由宿主负责。"""

    def test_wrapper_has_save_versions_without_workbook_commit(self) -> None:
        for tier in ("GREEN", "YELLOW", "RED"):
            src = generate_wrapper_script(tier, "/tmp/ws")
            assert "_SAVE_VERSIONS" in src
            assert "EXCELMANUS_SAVE_VERSION" in src
            assert "reconfigure(encoding=\"utf-8\")" in src
            code_lines = [
                ln.strip() for ln in src.splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            assert not any(
                ln.startswith("import excelmanus.workbook_commit")
                or ln.startswith("from excelmanus.workbook_commit")
                for ln in code_lines
            )
            assert "EXCELMANUS_PENDING_WRITE" in src
            assert "manifest.jsonl" in src
            assert "EXCELMANUS_PENDING_RUN_ID" in src
            assert "_check_expected_version" not in src
            assert "_EXPECTED_VERSIONS" not in src

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
        assert "em.format_spreadsheet" in result.stderr or "em.edit_spreadsheet" in result.stderr
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
        assert result.returncode != 0
        assert "em.format_spreadsheet" in result.stderr or "em.edit_spreadsheet" in result.stderr
        assert not xlsx.exists()
        assert _parse_pending_writes(result.stderr) == []

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
        assert result.returncode != 0
        assert "em.format_spreadsheet" in result.stderr or "em.edit_spreadsheet" in result.stderr
        from openpyxl import load_workbook
        orig = load_workbook(str(target))
        assert orig.active["A1"].value == "initial"
        orig.close()
        assert _parse_pending_writes(result.stderr) == []

    def test_bench_save_refused_no_copy(self, workspace: Path) -> None:
        from openpyxl import Workbook, load_workbook

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
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr or "工作区表格禁止直接保存" in result.stderr
        assert not (workspace / "outputs" / "backups").exists()
        assert _parse_pending_writes(result.stderr) == []
        assert _parse_save_versions(result.stderr) == {}
        assert _sha256_version(target) == orig_ver
        wb2 = load_workbook(str(target))
        assert wb2.active["A1"].value == "original"
        wb2.close()

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
        assert result.returncode != 0
        assert "em.format_spreadsheet" in result.stderr or "em.edit_spreadsheet" in result.stderr
        assert not log.exists()
        assert not xlsx.exists()

    def test_failed_outside_save_emits_no_version(self, workspace: Path) -> None:
        """工作区外的 xlsx save 失败，不得写出 content_version。"""
        target = Path("/tmp/_excelmanus_sandbox_should_not_exist/fail.xlsx")
        code = (
            "from openpyxl import Workbook\n"
            "wb = Workbook()\n"
            f"wb.save(r'{target}')\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode != 0
        assert _parse_save_versions(result.stderr) == {}
        assert _parse_pending_writes(result.stderr) == []
        assert not target.exists()

    def test_bytesio_workbook_save_allowed(self, workspace: Path) -> None:
        code = (
            "from io import BytesIO\n"
            "from openpyxl import Workbook\n"
            "wb = Workbook()\n"
            "wb.active['A1'] = 'ok'\n"
            "buf = BytesIO()\n"
            "wb.save(buf)\n"
            "print(len(buf.getvalue()))\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0, result.stderr
        assert int(result.stdout.strip()) > 0

    def test_to_excel_workspace_xlsx_denied(self, workspace: Path) -> None:
        out = workspace / "outputs"
        out.mkdir()
        target = out / "frame.xlsx"
        code = (
            "import pandas as pd\n"
            f"pd.DataFrame({{'a': [1]}}).to_excel(r'{target}', index=False)\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode != 0
        assert "em.format_spreadsheet" in result.stderr or "em.edit_spreadsheet" in result.stderr
        assert not target.exists()


class TestYellowIoWrappers:
    """os/shutil/pathlib 写入也必须走 pending / 工作区守卫。"""

    def test_os_open_write_xlsx_does_not_touch_original(self, workspace: Path) -> None:
        target = workspace / "book.xlsx"
        target.write_bytes(b"original-xlsx")
        code = (
            "import os\n"
            f"fd = os.open(r'{target}', os.O_WRONLY | os.O_TRUNC)\n"
            "os.write(fd, b'pwned')\n"
            "os.close(fd)\n"
        )
        result = _run_in_sandbox(workspace, code, "YELLOW")
        assert result.returncode != 0
        assert "em.format_spreadsheet" in result.stderr or "em.edit_spreadsheet" in result.stderr
        assert target.read_bytes() == b"original-xlsx"

    def test_os_replace_xlsx_does_not_replace_original(self, workspace: Path) -> None:
        src = workspace / "src.xlsx"
        dest = workspace / "dest.xlsx"
        src.write_bytes(b"src-bytes")
        dest.write_bytes(b"dest-bytes")
        code = (
            "import os\n"
            f"os.replace(r'{src}', r'{dest}')\n"
        )
        result = _run_in_sandbox(workspace, code, "YELLOW")
        assert result.returncode != 0
        assert dest.read_bytes() == b"dest-bytes"
        assert src.read_bytes() == b"src-bytes"

    def test_path_write_bytes_xlsx_does_not_touch_original(self, workspace: Path) -> None:
        target = workspace / "book.xlsx"
        target.write_bytes(b"keep-me")
        code = (
            "from pathlib import Path\n"
            f"Path(r'{target}').write_bytes(b'pwned')\n"
        )
        result = _run_in_sandbox(workspace, code, "YELLOW")
        assert result.returncode != 0
        assert target.read_bytes() == b"keep-me"

    def test_shutil_copy_xlsx_does_not_overwrite_original(self, workspace: Path) -> None:
        src = workspace / "note.txt"
        dest = workspace / "book.xlsx"
        src.write_text("hello", encoding="utf-8")
        dest.write_bytes(b"keep-xlsx")
        code = (
            "import shutil\n"
            f"shutil.copy(r'{src}', r'{dest}')\n"
        )
        result = _run_in_sandbox(workspace, code, "YELLOW")
        assert result.returncode != 0
        assert dest.read_bytes() == b"keep-xlsx"


    def test_path_write_text_csv_does_not_touch_original(self, workspace: Path) -> None:
        target = workspace / "a.csv"
        target.write_text("old,csv\n", encoding="utf-8")
        code = (
            "from pathlib import Path\n"
            f"Path(r'{target}').write_text('new,csv\\n', encoding='utf-8')\n"
        )
        result = _run_in_sandbox(workspace, code, "YELLOW")
        assert result.returncode == 0, result.stderr
        assert target.read_text(encoding="utf-8") == "old,csv\n"

    def test_csv_pending_publishes_via_cas(self, workspace: Path) -> None:
        from excelmanus.workspace.runtime import (
            prepare_pending_run_dir,
            publish_pending_writes,
        )

        target = workspace / "notes.md"
        target.write_text("keep\n", encoding="utf-8")
        seen = _sha256_version(target)
        run_id = "cccccccccccccccc"
        pending_dir = prepare_pending_run_dir(workspace, run_id)
        code = (
            "from pathlib import Path\n"
            f"Path(r'{target}').write_text('published\\n', encoding='utf-8')\n"
        )
        result = _run_in_sandbox(
            workspace,
            code,
            "YELLOW",
            env_override={
                "EXCELMANUS_PENDING_RUN_ID": run_id,
                "EXCELMANUS_PENDING_DIR": str(pending_dir),
            },
        )
        assert result.returncode == 0, result.stderr
        assert target.read_text(encoding="utf-8") == "keep\n"
        published = publish_pending_writes(
            workspace,
            run_id=run_id,
            expected_versions={"notes.md": seen},
        )
        assert published, "md write should land in pending manifest"
        assert published[0]["status"] == "committed"
        assert target.read_text(encoding="utf-8") == "published\n"

    def test_tmp_write_stays_live(self, workspace: Path) -> None:
        tmp = workspace / ".tmp"
        tmp.mkdir()
        dest = tmp / "scratch.txt"
        code = (
            "from pathlib import Path\n"
            f"Path(r'{dest}').write_text('ephemeral', encoding='utf-8')\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0, result.stderr
        assert dest.read_text(encoding="utf-8") == "ephemeral"


class TestPendingIsolation:
    def test_cannot_write_sibling_run_pending(self, workspace: Path) -> None:
        run_a = "aaaaaaaaaaaaaaaa"
        run_b = "bbbbbbbbbbbbbbbb"
        from excelmanus.workspace.runtime import prepare_pending_run_dir, publish_pending_writes

        a_dir = prepare_pending_run_dir(workspace, run_a)
        victim = a_dir / "aabbccddeeff0011_book.xlsx"
        victim.write_bytes(b"from-A")
        (a_dir / "manifest.jsonl").write_text(
            json.dumps({"rel": "book.xlsx", "name": victim.name}) + "\n",
            encoding="utf-8",
        )
        original = victim.read_bytes()

        code = (
            "from pathlib import Path\n"
            f"p = Path(r'{a_dir / 'manifest.jsonl'}')\n"
            "p.write_text('{\"rel\":\"book.xlsx\",\"name\":\"hijack\"}\\n', encoding='utf-8')\n"
            f"Path(r'{victim}').write_bytes(b'from-B')\n"
        )
        result = _run_in_sandbox(
            workspace,
            code,
            "GREEN",
            env_override={
                "EXCELMANUS_PENDING_RUN_ID": run_b,
                "EXCELMANUS_PENDING_DIR": str(
                    prepare_pending_run_dir(workspace, run_b)
                ),
            },
        )
        assert result.returncode != 0
        assert victim.read_bytes() == original
        published = publish_pending_writes(
            workspace, run_id=run_a, expected_versions={},
        )
        dest = workspace / "book.xlsx"
        assert dest.read_bytes() == b"from-A"
        assert published[0]["status"] == "committed"

    def test_os_symlink_blocked(self, workspace: Path) -> None:
        code = (
            "import os\n"
            f"os.symlink(r'{workspace / 'a.txt'}', r'{workspace / 'link.txt'}')\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode != 0
        assert "安全策略禁止" in result.stderr
        assert not (workspace / "link.txt").exists()

    def test_rel_of_does_not_basename_sibling_workspace(self, workspace: Path) -> None:
        evil = workspace.parent / (workspace.name + "-evil")
        evil.mkdir()
        target = evil / "book.xlsx"
        code = (
            "from openpyxl import Workbook\n"
            "wb = Workbook()\n"
            "wb.active['A1'] = 'stolen'\n"
            f"wb.save(r'{target}')\n"
        )
        try:
            result = _run_in_sandbox(workspace, code, "GREEN")
            assert result.returncode != 0
            assert not target.exists()
            assert _parse_pending_writes(result.stderr) == []
        finally:
            if target.exists():
                target.unlink()
            if evil.exists():
                evil.rmdir()



class TestRealpathLstatNoRecursion:
    """回归：os.lstat 被守卫后，os.path.realpath/os.lstat 互调不得无限递归。

    _guarded_os_lstat 内部需要 realpath 解析路径，而 realpath 又会回调
    os.lstat；若守卫内直接调 realpath 将自递归（RecursionError）。
    """

    @pytest.mark.parametrize("tier", ["GREEN", "YELLOW", "RED"])
    def test_realpath_and_lstat_safe_under_guard(
        self, workspace: Path, tier: str
    ) -> None:
        code = (
            "import os\n"
            "p = os.path.realpath('.')\n"
            "st = os.lstat('.')\n"
            "os.stat('.')\n"
            "print('OK', p)\n"
        )
        result = _run_in_sandbox(workspace, code, tier)
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout
        assert "RecursionError" not in result.stderr

    def test_realpath_on_nested_and_missing_paths(self, workspace: Path) -> None:
        (workspace / "sub").mkdir()
        code = (
            "import os\n"
            "print(os.path.realpath('sub/../sub'))\n"
            "print(os.path.realpath('missing/child.txt'))\n"
            "print('DONE')\n"
        )
        result = _run_in_sandbox(workspace, code, "GREEN")
        assert result.returncode == 0, result.stderr
        assert "DONE" in result.stdout
