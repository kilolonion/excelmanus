"""Port cleanup must not kill an unrelated service that reused the port."""

from __future__ import annotations

from excelmanus.upgrade import helper

def test_kill_port_skips_unrelated_listener(monkeypatch, tmp_path) -> None:
    killed: list[tuple[int, bool]] = []
    monkeypatch.setattr(helper, "_listener_pids", lambda port: [4242])
    monkeypatch.setattr(helper, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(helper, "_pid_command_line", lambda pid: "/usr/bin/notepad")
    monkeypatch.setattr(helper, "_kill_pid", lambda pid, force=False: killed.append((pid, force)))

    helper._kill_port(8000, project_root=tmp_path / "excelmanus")
    assert killed == []


def test_kill_port_kills_managed_listener(monkeypatch, tmp_path) -> None:
    killed: list[tuple[int, bool]] = []
    monkeypatch.setattr(helper, "_listener_pids", lambda port: [4242])
    monkeypatch.setattr(helper, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        helper,
        "_pid_command_line",
        lambda pid: r"python -m uvicorn excelmanus.api:app",
    )
    monkeypatch.setattr(helper, "_kill_pid", lambda pid, force=False: killed.append((pid, force)))

    helper._kill_port(8000, project_root=tmp_path / "excelmanus")
    assert killed == [(4242, True)]


def test_kill_port_allows_runtime_pid_even_without_command(monkeypatch) -> None:
    killed: list[tuple[int, bool]] = []
    monkeypatch.setattr(helper, "_listener_pids", lambda port: [4242])
    monkeypatch.setattr(helper, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(helper, "_pid_command_line", lambda pid: "")
    monkeypatch.setattr(helper, "_kill_pid", lambda pid, force=False: killed.append((pid, force)))

    helper._kill_port(8000, allowed_pids={4242})
    assert killed == [(4242, True)]


def test_pid_is_managed_matches_project_root(monkeypatch, tmp_path) -> None:
    project_root = tmp_path / "excelmanus"
    project_root.mkdir()
    monkeypatch.setattr(
        helper,
        "_pid_command_line",
        lambda pid: f"{project_root / '.venv' / 'bin' / 'python'} -m excelmanus",
    )
    assert helper._pid_is_managed(4242, project_root=project_root) is True
