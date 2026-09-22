from unittest.mock import patch
from types import SimpleNamespace

import pytest

from excelmanus.upgrade.preflight import check_upgrade_environment
from excelmanus.updater import _run_cmd


@pytest.mark.parametrize("scenario,expected", [
    ("ok", None), ("missing-git", "Git"), ("detached", "detached HEAD"),
    ("dirty", "未提交"), ("missing-node", "Node.js"), ("old-node", "20.9"),
    ("read-only", "不可写"), ("bad-owner", "所有者"),
])
def test_upgrade_environment_checked_before_shutdown(tmp_path, scenario, expected):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text("{}")
    def which(name):
        if (scenario == "missing-git" and name == "git") or (scenario == "missing-node" and name == "node"):
            return None
        return f"/bin/{name}"
    def command(args, **kwargs):
        if args[0] == "node":
            return 0, "v18.20.0" if scenario == "old-node" else "v22.0.0", ""
        if "symbolic-ref" in args:
            return (1, "", "detached") if scenario == "detached" else (0, "main", "")
        if "status" in args:
            if scenario == "bad-owner":
                return 1, "", "dubious ownership"
            return 0, " M README.md" if scenario == "dirty" else "", ""
        raise AssertionError(args)
    with patch("excelmanus.upgrade.preflight.shutil.which", side_effect=which), \
         patch("excelmanus.upgrade.preflight._run_cmd", side_effect=command), \
         patch("excelmanus.upgrade.preflight.os.access", return_value=scenario != "read-only"):
        result = check_upgrade_environment(tmp_path)
    if expected is None:
        assert result is None
    else:
        assert expected in result


def test_windows_command_resolves_cmd_shim_without_shell():
    with patch("excelmanus.updater.os", SimpleNamespace(name="nt", environ={})), \
         patch("excelmanus.updater.shutil.which", return_value=r"C:\Program Files\nodejs\npm.cmd"), \
         patch("excelmanus.updater.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="ok", stderr="")) as run:
        assert _run_cmd(["npm", "ci"])[0] == 0
    assert run.call_args.args[0] == [r"C:\Program Files\nodejs\npm.cmd", "ci"]
    assert not run.call_args.kwargs.get("shell")
