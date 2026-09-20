"""One-time Windows desktop process setup, before any worker threads start."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_dll_handles: list[object] = []
_job_handle: int | None = None  # raw handle: closed by the OS at process exit


def initialize_windows_runtime() -> None:
    global _job_handle
    if sys.platform != 'win32':
        return
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    if getattr(sys, 'frozen', False):
        import ctypes
        root = Path(sys._MEIPASS).resolve()
        # AddDllDirectory cookies apply to this process, not its children. Keep
        # them alive for delayed .pyd imports after removing SetDllDirectory.
        _dll_handles.append(os.add_dll_directory(str(root)))
        external_path = []
        for part in os.environ.get('PATH', '').split(os.pathsep):
            if not part:
                continue
            try:
                candidate = Path(part).resolve()
                bundled = candidate == root or root in candidate.parents
            except OSError:
                bundled = False
            if bundled:
                if Path(part).is_dir():
                    _dll_handles.append(os.add_dll_directory(part))
            else:
                external_path.append(part)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel32.SetDllDirectoryW.restype = ctypes.c_int
        if not kernel32.SetDllDirectoryW(None):
            raise ctypes.WinError(ctypes.get_last_error())
        os.environ['PATH'] = os.pathsep.join(external_path)
    # Own the entire backend tree, including MCP/runtime grandchildren. An OS
    # job covers abrupt exit without a ps scan or a recycled PID lookup. Nested
    # jobs are supported by the Windows versions supported by Electron.
    import win32api
    import win32job
    job = win32job.CreateJobObject(None, '')
    try:
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        win32job.AssignProcessToJobObject(job, win32api.GetCurrentProcess())
        # A PyHANDLE destructor during interpreter finalization could terminate
        # this process before exit 75 is delivered. Let the OS close the handle.
        _job_handle = job.Detach()
    except BaseException:
        job.Close()
        raise
