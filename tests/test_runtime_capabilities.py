"""Environment disclosure reports observations, never an execution guarantee."""
from pathlib import Path

import pytest

from excelmanus import runtime_capabilities as capabilities


@pytest.fixture(autouse=True)
def isolated_discovery(monkeypatch):
    monkeypatch.setattr(capabilities.shutil, "which", lambda name: None)
    monkeypatch.setattr(capabilities.platform, "system", lambda: "Linux")
    monkeypatch.delenv("EXCELMANUS_FORMULA_RECALC", raising=False)
    monkeypatch.delenv("EXCELMANUS_EXECUTION_ISOLATION", raising=False)


def test_missing_office_does_not_claim_recalculation():
    facts = capabilities.host_capabilities()
    assert facts["scope"] == "host"
    assert facts["soffice"]["status"] == "unavailable"
    assert facts["soffice"]["executable"] is None
    assert facts["formula_recalculation"] == "unavailable"
    assert "宿主环境未变时无需重复试探" in capabilities.environment_text()


def test_detected_engine_is_not_a_success_guarantee(monkeypatch):
    monkeypatch.setattr(capabilities.shutil, "which", lambda name: "/tools/soffice" if name == "soffice" else None)
    facts = capabilities.host_capabilities()
    assert facts["soffice"]["status"] == "installed"
    assert facts["formula_recalculation"] == "engine_detected"
    text = capabilities.environment_text()
    assert "执行结果以工具返回为准" in text
    assert "GREEN/YELLOW 禁止启动子进程" in text


@pytest.mark.parametrize("setting", ["0", "false", "OFF", " never "])
def test_disabled_recalculation_is_reported_with_engine_present(monkeypatch, setting):
    monkeypatch.setenv("EXCELMANUS_FORMULA_RECALC", setting)
    monkeypatch.setattr(capabilities.shutil, "which", lambda name: "/tools/soffice")
    assert capabilities.host_capabilities()["formula_recalculation"] == "disabled"
    assert "已由配置禁用" in capabilities.environment_text()


def test_windows_programw6432_cli_launcher_is_discovered(monkeypatch, tmp_path):
    monkeypatch.setattr(capabilities.platform, "system", lambda: "Windows")
    monkeypatch.setenv("ProgramW6432", str(tmp_path))
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    program = tmp_path / "LibreOffice" / "program"
    program.mkdir(parents=True)
    exe, console = program / "soffice.exe", program / "soffice.com"
    exe.touch()
    console.touch()
    monkeypatch.setattr(capabilities.os, "access", lambda path, mode: True)
    assert capabilities.office_executable() == str(console)
    assert "Windows 不启用 Unix RLIMIT" in capabilities.environment_text(full_access=True)
    assert "当前允许子进程" in capabilities.environment_text(full_access=True)
    monkeypatch.setattr(capabilities.os, "access", lambda path, mode: path != console)
    assert capabilities.office_executable() == str(exe)
    monkeypatch.setattr(capabilities.os, "access", lambda path, mode: False)
    assert capabilities.office_executable() is None


def test_mac_user_application_is_discovered(monkeypatch, tmp_path):
    monkeypatch.setattr(capabilities.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    app = tmp_path / "Applications/LibreOffice.app/Contents/MacOS/soffice"
    app.parent.mkdir(parents=True)
    app.touch()
    monkeypatch.setattr(capabilities.os, "access", lambda path, mode: path == app)
    assert capabilities.office_executable() == str(app)


@pytest.mark.parametrize("mode", ["docker", "container", "containerized"])
def test_container_does_not_inherit_host_availability(monkeypatch, mode):
    monkeypatch.setenv("EXCELMANUS_EXECUTION_ISOLATION", mode)
    text = capabilities.environment_text(full_access=True)
    assert "以上仅为宿主探测" in text
    assert "容器内工具尚未探测" in text
    assert "宿主路径不保证在容器内存在" in text
    assert "使用本机子进程" not in text
