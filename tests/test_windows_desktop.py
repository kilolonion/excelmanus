"""Host-independent regressions; native Windows acceptance runs in desktop CI."""
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def test_windows_shell_paths_preserve_backslashes(monkeypatch):
    from excelmanus.tools import shell_tools as shell
    monkeypatch.setattr(shell, 'os', SimpleNamespace(name='nt'))
    assert shell._split_command(r'cat C:\work\data.txt') == ['cat', r'C:\work\data.txt']
    assert shell._split_command(r'cat "C:\中文 空格\data.txt"') == ['cat', r'C:\中文 空格\data.txt']
    assert shell._split_command(r'echo "a b"') == ['echo', 'a b']
    with pytest.raises(ValueError):
        shell._split_command('cat "unfinished')


def test_windows_python_probe_hides_console(monkeypatch):
    from excelmanus.tools import code_tools as tools
    monkeypatch.setattr(tools, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(tools, '_command_exists', lambda command: True)
    monkeypatch.setattr(tools.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(tools.subprocess, 'run', run)
    assert tools._probe_environment(['python.exe'], require_excel_deps=False).status == 'ok'
    assert run.call_args.kwargs['creationflags'] == 0x08000000
    assert '-B' in run.call_args.args[0]


def sharing_error(code):
    error = PermissionError('locked')
    error.winerror = code
    return error


def test_windows_file_lock_retries_then_preserves_file(tmp_path, monkeypatch):
    from excelmanus.workspace import txlog
    target = tmp_path/'用户.xlsx'
    target.write_bytes(b'original')
    src = tmp_path/'pending'
    src.write_bytes(b'new')
    replace = Mock(side_effect=sharing_error(32))
    monkeypatch.setattr(txlog.os, 'replace', replace)
    with pytest.raises(txlog.FileBusyError, match='关闭 Excel'):
        txlog.replace_with_retry(str(src), str(target), retries=3, delay=0)
    assert replace.call_count == 3
    assert target.read_bytes() == b'original'
    assert src.read_bytes() == b'new'


def test_windows_access_denied_is_not_retried(monkeypatch):
    from excelmanus.workspace import txlog
    replace = Mock(side_effect=sharing_error(5))
    monkeypatch.setattr(txlog.os, 'replace', replace)
    with pytest.raises(PermissionError):
        txlog.replace_with_retry('a','b', retries=5, delay=0)
    assert replace.call_count == 1


def test_revision_blobs_use_retry(tmp_path, monkeypatch):
    from excelmanus.workspace import revisions, txlog
    original = txlog.os.replace
    calls=[]
    def first_busy(src,dst):
        calls.append((src,dst))
        if len(calls)==1: raise sharing_error(33)
        return original(src,dst)
    monkeypatch.setattr(txlog.os,'replace',first_busy)
    store = revisions.RevisionStore(tmp_path)
    digest = store.put_blob('book.xlsx',b'payload')
    assert store.read_blob('book.xlsx',digest)==b'payload'
    assert len(calls)==2


def install_acl_mocks(monkeypatch, *, count=1):
    sid='S-1-5-21-domain-user'
    token=Mock(); acl=Mock(); descriptor=Mock()
    descriptor.GetSecurityDescriptorDacl.return_value=acl
    descriptor.GetSecurityDescriptorControl.return_value=(0x1000,1)
    acl.GetAceCount.return_value=count
    acl.GetAce.return_value=((0,0),0x1f01ff,sid)
    security=SimpleNamespace(OpenProcessToken=Mock(return_value=token),GetTokenInformation=Mock(return_value=(sid,0)),
        TokenUser=1,ACL=Mock(return_value=acl),ACL_REVISION=2,DACL_SECURITY_INFORMATION=4,PROTECTED_DACL_SECURITY_INFORMATION=0x80000000,
        SE_FILE_OBJECT=1,SE_DACL_PROTECTED=0x1000,ACCESS_ALLOWED_ACE_TYPE=0,
        SetNamedSecurityInfo=Mock(),GetNamedSecurityInfo=Mock(return_value=descriptor),ConvertSidToStringSid=str)
    monkeypatch.setitem(sys.modules,'win32security',security)
    monkeypatch.setitem(sys.modules,'win32api',SimpleNamespace(GetCurrentProcess=lambda:42))
    monkeypatch.setitem(sys.modules,'win32con',SimpleNamespace(TOKEN_QUERY=8,FILE_ALL_ACCESS=0x1f01ff))
    return security,token,acl,sid


def test_acl_uses_token_sid_and_verifies(tmp_path,monkeypatch):
    from excelmanus.security.cipher import _restrict_windows_file_permissions
    security,token,acl,sid=install_acl_mocks(monkeypatch)
    monkeypatch.setenv('USERNAME','wrong-name')
    _restrict_windows_file_permissions(tmp_path/'.secret_key')
    acl.AddAccessAllowedAce.assert_called_once_with(2,0x1f01ff,sid)
    token.Close.assert_called_once()
    security.GetNamedSecurityInfo.assert_called_once()


def test_acl_verification_rejects_extra_principals(tmp_path,monkeypatch):
    from excelmanus.security.cipher import _restrict_windows_file_permissions,CipherUnavailableError
    install_acl_mocks(monkeypatch,count=2)
    with pytest.raises(CipherUnavailableError):
        _restrict_windows_file_permissions(tmp_path/'.secret_key')


def test_windows_runtime_sanitizes_once_and_owns_job(tmp_path,monkeypatch):
    module_path=Path(__file__).resolve().parents[1]/'desktop/windows_runtime.py'
    spec=importlib.util.spec_from_file_location('windows_runtime_test',module_path)
    runtime=importlib.util.module_from_spec(spec); spec.loader.exec_module(runtime)
    runtime.sys=SimpleNamespace(platform='win32',frozen=True,_MEIPASS=str(tmp_path/'bundle'),stdout=None,stderr=None)
    root=tmp_path/'bundle';root.mkdir(); extra=root/'dlls';extra.mkdir()
    monkeypatch.setenv('PATH',os.pathsep.join([str(extra),str(tmp_path/'external')]))
    add=Mock(return_value=object());monkeypatch.setattr(os,'add_dll_directory',add,raising=False)
    import ctypes
    set_dll=Mock(return_value=1)
    monkeypatch.setattr(ctypes,'WinDLL',lambda *a,**k:SimpleNamespace(SetDllDirectoryW=set_dll),raising=False)
    handle=Mock();handle.Detach.return_value=99
    jobs=SimpleNamespace(CreateJobObject=Mock(return_value=handle),QueryInformationJobObject=Mock(return_value={'BasicLimitInformation':{'LimitFlags':0}}),
      JobObjectExtendedLimitInformation=9,JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=8192,SetInformationJobObject=Mock(),AssignProcessToJobObject=Mock())
    monkeypatch.setitem(sys.modules,'win32api',SimpleNamespace(GetCurrentProcess=lambda:42))
    monkeypatch.setitem(sys.modules,'win32job',jobs)
    runtime.initialize_windows_runtime()
    set_dll.assert_called_once_with(None)
    assert os.environ['PATH']==str(tmp_path/'external')
    assert len(runtime._dll_handles)==2
    jobs.AssignProcessToJobObject.assert_called_once_with(handle,42)
    handle.Close.assert_not_called()
    assert runtime._job_handle==99


def test_first_run_settings_survive_without_model_credentials():
    from excelmanus.config import load_config, ConfigError
    values={'EXCELMANUS_SESSION_TTL_SECONDS':'1729'}
    with pytest.raises(ConfigError):
        load_config(values)
    incomplete=load_config(values,allow_incomplete=True)
    assert incomplete.session_ttl_seconds==1729
    assert not incomplete.api_key and not incomplete.model


def test_acl_failure_does_not_publish_unprotected_key(tmp_path, monkeypatch):
    from excelmanus.security import cipher
    target=tmp_path/'.secret_key'
    target.write_bytes(b'existing')
    def refuse(path):
        assert path.read_bytes()==b''
        raise cipher.CipherUnavailableError('ACL denied')
    monkeypatch.setattr(cipher,'_restrict_file_permissions',refuse)
    with pytest.raises(cipher.CipherUnavailableError):
        cipher._write_protected_key(target,b'new secret')
    assert target.read_bytes()==b'existing'
    assert list(tmp_path.glob('.key-*'))==[]


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows ACL acceptance')
def test_native_windows_acl(tmp_path):
    from excelmanus.security.cipher import _write_protected_key, _restrict_windows_file_permissions
    key=tmp_path/'中文 空格密钥'
    _write_protected_key(key,b'acceptance-only')
    _restrict_windows_file_permissions(key)
    assert key.read_bytes()==b'acceptance-only'
