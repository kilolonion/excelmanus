"""Recalculation uses bounded process cleanup and reports real output failures."""
import io
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from openpyxl import Workbook

from excelmanus import runtime_capabilities, workbook_commit
from excelmanus.tools import runtime


@pytest.fixture
def conversion(monkeypatch):
    monkeypatch.delenv("EXCELMANUS_FORMULA_RECALC", raising=False)
    monkeypatch.setenv("EXCELMANUS_FORMULA_RECALC_TIMEOUT", "5")
    monkeypatch.setattr(runtime_capabilities, "office_executable", lambda: "/tools/soffice")
    registered, unregistered, terminated = Mock(), Mock(), Mock()
    monkeypatch.setattr(runtime, "register_killable_process", registered)
    monkeypatch.setattr(runtime, "unregister_killable_process", unregistered)
    monkeypatch.setattr(runtime, "_terminate_process_tree", terminated)
    monkeypatch.setattr(runtime, "current_execution", lambda: SimpleNamespace(execution_id="conversion-1"))
    process = Mock(pid=1234, returncode=0)
    process.communicate.return_value = ("converted", "")
    launch = Mock(return_value=process)
    monkeypatch.setattr(workbook_commit.subprocess, "Popen", launch)
    return SimpleNamespace(
        registered=registered, unregistered=unregistered, terminated=terminated,
        process=process, launch=launch,
    )


def converted_output(conversion, data):
    def communicate(**kwargs):
        args = conversion.launch.call_args.args[0]
        output = Path(args[args.index("--outdir") + 1])
        (output / "input.xlsx").write_bytes(data)
        return "converted", ""
    conversion.process.communicate.side_effect = communicate


def workbook_bytes():
    wb = Workbook()
    wb.active["A1"] = "# customer reference"
    wb.active["A2"] = "=1+1"
    stream = io.BytesIO()
    wb.save(stream)
    wb.close()
    return stream.getvalue()


def test_conversion_uses_isolated_registered_process(conversion):
    output = workbook_bytes()
    converted_output(conversion, output)
    data, result = workbook_commit.recalculate_workbook_bytes(b"original")
    assert data == output
    assert result["status"] == "recalculated"
    assert result["formula_count"] == 1
    assert result["error_count"] == 0  # '# customer reference' is normal text.
    kwargs = conversion.launch.call_args.kwargs
    assert kwargs["start_new_session"] is (os.name != "nt")
    assert kwargs["close_fds"] is True
    if os.name == "nt":
        assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
        assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
    conversion.registered.assert_called_once_with("conversion-1", conversion.process)
    conversion.unregistered.assert_called_once_with("conversion-1", conversion.process)
    conversion.terminated.assert_not_called()


@pytest.mark.parametrize("drain_failure", [False, True])
def test_timeout_terminates_tree_and_cleanup_wait_is_bounded(conversion, drain_failure):
    cleanup = subprocess.TimeoutExpired("soffice", 3) if drain_failure else ("", "")
    conversion.process.communicate.side_effect = [subprocess.TimeoutExpired("soffice", 5), cleanup]
    data, result = workbook_commit.recalculate_workbook_bytes(b"original")
    assert data == b"original"
    assert result["status"] == "failed"
    assert "超时" in result["errors"][0]
    assert [call.kwargs["timeout"] for call in conversion.process.communicate.call_args_list] == [5, 3]
    conversion.terminated.assert_called_once_with(conversion.process)
    conversion.unregistered.assert_called_once_with("conversion-1", conversion.process)


def test_communication_failure_also_cleans_up(conversion):
    conversion.process.communicate.side_effect = OSError("pipe read failed")
    data, result = workbook_commit.recalculate_workbook_bytes(b"original")
    assert data == b"original"
    assert result["status"] == "failed"
    conversion.terminated.assert_called_once_with(conversion.process)
    conversion.unregistered.assert_called_once_with("conversion-1", conversion.process)


def test_failed_launch_is_not_reported_as_missing_installation(conversion):
    conversion.launch.side_effect = PermissionError("execution denied")
    data, result = workbook_commit.recalculate_workbook_bytes(b"original")
    assert data == b"original"
    assert result["status"] == "failed"
    assert result["engine"] == "/tools/soffice"
    assert "execution denied" in result["errors"][0]
    conversion.terminated.assert_not_called()
    conversion.registered.assert_not_called()


def test_corrupt_output_is_not_returned_as_recalculated(conversion):
    converted_output(conversion, b"not an xlsx")
    data, result = workbook_commit.recalculate_workbook_bytes(b"original")
    assert data == b"original"
    assert result["status"] == "failed"
    assert "invalid_converted_workbook" in result["errors"][0]


def test_formula_error_cells_are_reported(conversion):
    wb = Workbook()
    wb.active["A1"] = "#DIV/0!"
    stream = io.BytesIO()
    wb.save(stream)
    wb.close()
    converted_output(conversion, stream.getvalue())
    _, result = workbook_commit.recalculate_workbook_bytes(b"original")
    assert result["status"] == "recalculated"
    assert result["errors"] == ["Sheet!A1:#DIV/0!"]


def test_unavailable_or_disabled_engine_never_launches(conversion, monkeypatch):
    monkeypatch.setattr(runtime_capabilities, "office_executable", lambda: None)
    data, result = workbook_commit.recalculate_workbook_bytes(b"original")
    assert data == b"original"
    assert result["status"] == "unavailable"
    monkeypatch.setenv("EXCELMANUS_FORMULA_RECALC", "off")
    _, result = workbook_commit.recalculate_workbook_bytes(b"original")
    assert result["status"] == "disabled"
    conversion.launch.assert_not_called()


def test_timeout_stops_real_child_and_grandchild(monkeypatch, tmp_path):
    """Exercise the platform tree terminator without requiring LibreOffice."""
    monkeypatch.setenv("EXCELMANUS_FORMULA_RECALC", "auto")
    monkeypatch.setenv("EXCELMANUS_FORMULA_RECALC_TIMEOUT", "5")
    monkeypatch.setenv("EXCELMANUS_WINDOWS_JOB_OBJECT", "0")
    monkeypatch.setattr(runtime_capabilities, "office_executable", lambda: sys.executable)
    heartbeat = tmp_path / "heartbeat.txt"
    child_pid_file = tmp_path / "child.pid"
    child = (
        "import os, pathlib, time\n"
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(os.getpid()))\n"
        f"heartbeat = pathlib.Path({str(heartbeat)!r})\n"
        "for counter in range(200):\n"
        "    heartbeat.write_text(str(counter))\n"
        "    time.sleep(0.1)\n"
    )
    parent = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "time.sleep(20)\n"
    )
    real_popen = subprocess.Popen
    launched = []

    def launch(command, **kwargs):
        if len(command) > 1 and command[1] == "--headless":
            process = real_popen([sys.executable, "-c", parent], **kwargs)
            launched.append(process)
            return process
        return real_popen(command, **kwargs)  # Windows taskkill stays real.

    monkeypatch.setattr(workbook_commit.subprocess, "Popen", launch)
    started = time.monotonic()
    try:
        data, result = workbook_commit.recalculate_workbook_bytes(b"original")
        assert time.monotonic() - started < 15
        assert data == b"original" and result["status"] == "failed"
        assert "超时" in result["errors"][0]
        assert len(launched) == 1 and launched[0].poll() is not None
        assert child_pid_file.is_file(), "grandchild must have actually started"
        before = heartbeat.read_bytes()
        time.sleep(0.4)
        assert heartbeat.read_bytes() == before, "grandchild continued writing after timeout"
    finally:
        for process in launched:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
        if child_pid_file.is_file():
            import signal
            try:
                os.kill(int(child_pid_file.read_text()), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
