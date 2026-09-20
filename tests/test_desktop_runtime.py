"""Desktop behavior at the source/frozen boundary."""
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from starlette.requests import Request


def test_frozen_backend_is_never_python_candidate(monkeypatch):
    from excelmanus.tools import code_tools as tools
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('EXCELMANUS_RUN_PYTHON', '/bundled python/python3')
    probe = Mock(return_value=tools._InterpreterProbe(['/bundled python/python3'], 'ok', ''))
    monkeypatch.setattr(tools, '_probe_environment', probe)
    command, _, _ = tools._resolve_python_command_uncached('auto', require_excel_deps=True)
    assert command == ['/bundled python/python3']
    probe.assert_called_once()
    monkeypatch.delenv('EXCELMANUS_RUN_PYTHON')
    probe.reset_mock()
    probe.return_value = tools._InterpreterProbe(['missing'], 'not_found', '')
    with pytest.raises(RuntimeError, match='未找到可用环境'):
        tools._resolve_python_command_uncached('auto', require_excel_deps=True)
    assert [sys.executable] not in [call.args[0] for call in probe.call_args_list]


def test_desktop_restart_signals_supervisor(monkeypatch):
    from excelmanus import restart
    monkeypatch.setenv('EXCELMANUS_DESKTOP', '1')
    exit_mock = Mock()
    popen = Mock()
    monkeypatch.setattr(restart.os, '_exit', exit_mock)
    monkeypatch.setattr(restart.subprocess, 'Popen', popen)
    from types import SimpleNamespace
    server = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(restart, '_desktop_server', server)
    monkeypatch.setattr(restart, '_desktop_restart_requested', False)
    restart._do_restart(54321, 'unused', deploy_mode='standalone')
    assert server.should_exit
    assert restart._desktop_restart_requested
    exit_mock.assert_not_called()
    popen.assert_not_called()


@pytest.mark.asyncio
async def test_desktop_updates_are_installer_only(monkeypatch):
    from excelmanus import api_routes_version as routes
    monkeypatch.setenv('EXCELMANUS_DESKTOP', '1')
    req = Request({'type':'http','method':'GET','path':'/','headers':[], 'query_string':b'', 'client':('127.0.0.1',1234)})
    body = json.loads((await routes.version_check(req)).body)
    assert body['check_method'] == 'desktop_installer'
    assert not body['has_update']
    assert routes._require_control_plane(req).status_code == 409
    assert routes._get_git_commit(Path.cwd()) is None


def test_version_expression_is_not_a_version(tmp_path):
    from excelmanus.updater import _read_version_from_disk
    package = tmp_path / 'excelmanus'
    package.mkdir()
    (package / '__init__.py').write_text('__version__ = _read_version()\n')
    assert _read_version_from_disk(tmp_path) == 'unknown'
    (package / '__init__.py').write_text('__version__ = "1.8.0"\n')
    assert _read_version_from_disk(tmp_path) == '1.8.0'


def test_fresh_profile_loads_packaged_skills_outside_repo(monkeypatch, tmp_path):
    from excelmanus import api
    from excelmanus.config import ConfigError
    monkeypatch.chdir(tmp_path)
    def missing():
        raise ConfigError('missing model')
    monkeypatch.setattr(api, 'load_config', missing)
    config, error = api._build_bootstrap_config()
    assert error is not None
    assert Path(config.skills_system_dir).is_absolute()
    assert list(Path(config.skills_system_dir).glob('*/SKILL.md'))


def test_isolated_python_keeps_bundle_readonly():
    from excelmanus.tools.code_tools import _ensure_isolated_python
    command, isolated = _ensure_isolated_python(['/bundle/python3'])
    assert isolated
    assert '-I' in command and '-B' in command
