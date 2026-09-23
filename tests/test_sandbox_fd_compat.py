"""Descriptors retain Python I/O semantics without bypassing path checks."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from excelmanus.security.sandbox_hook import generate_wrapper_script


def run_script(workspace: Path, code: str, tier: str):
    script = workspace / "script.py"
    script.write_text(code, encoding="utf-8")
    wrapper = workspace / "wrapper.py"
    wrapper.write_text(generate_wrapper_script(tier, str(workspace)), encoding="utf-8")
    temp = workspace / ".tmp"
    temp.mkdir(exist_ok=True)
    env = dict(os.environ, TMP=str(temp), TEMP=str(temp), TMPDIR=str(temp))
    return subprocess.run(
        [sys.executable, str(wrapper), str(script)], cwd=workspace,
        capture_output=True, text=True, encoding="utf-8", timeout=20, env=env,
    )


@pytest.mark.parametrize("tier", ["GREEN", "YELLOW", "RED"])
def test_file_and_pipe_descriptor_stat_matches_native(tmp_path, tier):
    result = run_script(tmp_path, '''import os, stat, tempfile
with tempfile.TemporaryFile() as stream:
    stream.write(b"abc")
    stream.flush()
    fd = stream.fileno()
    assert os.stat(fd) == os.fstat(fd)
    assert os.path.exists(fd)
    assert os.path.isfile(fd)
    assert not os.path.isdir(fd)
    assert os.path.getsize(fd) == 3
reader, writer = os.pipe()
try:
    assert os.stat(reader) == os.fstat(reader)
    assert os.path.exists(reader)
    assert not os.path.isfile(reader)
finally:
    os.close(reader)
    os.close(writer)
assert not os.path.exists(reader)
try:
    os.stat(reader)
except OSError as exc:
    assert exc.errno == 9, exc
else:
    raise AssertionError("closed fd must retain EBADF")
print("descriptor-stat-ok")
''', tier)
    assert result.returncode == 0, result.stderr
    assert "descriptor-stat-ok" in result.stdout


@pytest.mark.parametrize("tier", ["GREEN", "YELLOW", "RED"])
def test_fd_writes_still_use_pending_copy(tmp_path, tier):
    target = tmp_path / "data.txt"
    target.write_text("old", encoding="utf-8")
    result = run_script(tmp_path, '''import os
fd = os.open("data.txt", os.O_WRONLY | os.O_TRUNC)
with open(fd, "w", encoding="utf-8", closefd=False) as stream:
    stream.write("pending-new")
assert os.stat(fd).st_size == len("pending-new")
os.close(fd)
with open("data.txt", encoding="utf-8") as stream:
    assert stream.read() == "pending-new"
print("pending-fd-ok")
''', tier)
    assert result.returncode == 0, result.stderr
    assert target.read_text(encoding="utf-8") == "old"
    manifest = next((tmp_path / ".excelmanus/pending").glob("*/manifest.jsonl"))
    record = json.loads(manifest.read_text(encoding="utf-8").splitlines()[0])
    assert (manifest.parent / record["name"]).read_text(encoding="utf-8") == "pending-new"


@pytest.mark.parametrize("tier", ["GREEN", "YELLOW", "RED"])
def test_fd_open_does_not_bypass_restricted_path(tmp_path, tier):
    # No inherited user descriptors are passed by run_code (close_fds=True).
    # A script must acquire a descriptor through the same path guard first.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "outside.txt"
    external.write_text("outside", encoding="utf-8")
    result = run_script(workspace, f'''import os
for flags in (os.O_RDONLY, os.O_RDWR):
    try:
        fd = os.open({str(external)!r}, flags)
    except PermissionError:
        pass
    else:
        os.close(fd)
        raise AssertionError("path guard must run before returning a descriptor")
print("path-guard-ok")
''', tier)
    assert result.returncode == 0, result.stderr
    assert "path-guard-ok" in result.stdout
    assert external.read_text(encoding="utf-8") == "outside"
