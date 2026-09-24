"""Native folder selection for the local browser UI."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

PICKER_TIMEOUT_SECONDS = 300
_picker_lock = threading.Lock()

# No user input is interpolated into the script. Cancellation is a normal result.
_MAC_FOLDER_SCRIPT = '''
activate
try
    return POSIX path of (choose folder with prompt "选择 ExcelManus 源文件夹" with invisibles)
on error number -128
    return ""
end try
'''


class FolderPickerError(RuntimeError):
    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


def select_local_folder() -> str | None:
    """Run outside the event loop; only an explicitly local API may call this."""
    if sys.platform != "darwin":
        raise FolderPickerError("此系统的网页版暂未接入文件夹选择器，请输入运行 ExcelManus 的电脑上的文件夹绝对路径", 501)
    if not _picker_lock.acquire(blocking=False):
        raise FolderPickerError("文件夹选择窗口已打开，请先完成或取消当前选择", 409)
    try:
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-e", _MAC_FOLDER_SCRIPT],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=PICKER_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FolderPickerError("文件夹选择已超时，请重新选择", 408) from exc
        except OSError as exc:
            raise FolderPickerError("无法打开 Mac 文件夹选择窗口，请重试或输入文件夹绝对路径") from exc
        if result.returncode != 0:
            raise FolderPickerError("无法打开 Mac 文件夹选择窗口，请确认本机桌面可用，或输入文件夹绝对路径")
        # osascript appends one newline. Do not strip meaningful filename spaces.
        selected = result.stdout.removesuffix("\n")
        if not selected:
            return None
        path = Path(selected)
        if not path.is_absolute() or not path.is_dir():
            raise FolderPickerError("所选文件夹已不可用，请重新选择", 400)
        return str(path)
    finally:
        _picker_lock.release()
