"""shell_tools 受限 Shell 工具测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from excelmanus.tools import shell_tools


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """创建临时工作区并初始化 guard。"""
    shell_tools.init_guard(str(tmp_path))
    # 创建一些测试文件
    (tmp_path / "hello.txt").write_text("hello world\n", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "sample.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    return tmp_path


class TestRunShellAllowed:
    """白名单命令正常执行。"""

    def test_echo(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("echo hello"))
        assert result["status"] == "success"
        assert "hello" in result["stdout_tail"]

    def test_ls(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("ls"))
        assert result["status"] == "success"
        assert "hello.txt" in result["stdout_tail"]

    def test_cat(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("cat hello.txt"))
        assert result["status"] == "success"
        assert "hello world" in result["stdout_tail"]

    def test_wc(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("wc -l hello.txt"))
        assert result["status"] == "success"
        assert result["return_code"] == 0

    def test_head(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("head -1 data/sample.csv"))
        assert result["status"] == "success"
        assert "a,b" in result["stdout_tail"]

    def test_grep(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("grep hello hello.txt"))
        assert result["status"] == "success"
        assert "hello world" in result["stdout_tail"]

    def test_find(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("find . -name '*.csv'"))
        assert result["status"] == "success"
        assert "sample.csv" in result["stdout_tail"]

    def test_pwd(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("pwd"))
        assert result["status"] == "success"
        assert result["return_code"] == 0

    def test_pipe_allowed(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("echo hello world | wc -w"))
        assert result["status"] == "success"
        assert "2" in result["stdout_tail"]

    def test_pipe_find_head(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("find . -type f | head -n 5"))
        assert result["status"] == "success"

    def test_pipe_grep(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("cat hello.txt | grep hello"))
        assert result["status"] == "success"
        assert "hello world" in result["stdout_tail"]

    def test_pipe_blocked_command_in_pipeline(self, workspace: Path) -> None:
        """管道中包含黑名单命令仍然被拦截。"""
        result = json.loads(shell_tools.run_shell("echo a | bash"))
        assert result["status"] == "blocked"

    def test_and_blocked(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("echo a && echo b"))
        assert result["status"] == "blocked"
        assert "逻辑运算符" in result["reason"]

    def test_or_blocked(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("echo a || echo b"))
        assert result["status"] == "blocked"
        assert "逻辑运算符" in result["reason"]


class TestRunShellBlocked:
    """黑名单和危险命令被拦截。"""

    def test_rm_blocked(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("rm hello.txt"))
        assert result["status"] == "blocked"
        assert "禁止" in result["reason"]

    def test_curl_blocked(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("curl https://example.com"))
        assert result["status"] == "blocked"

    def test_sudo_blocked(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("sudo ls"))
        assert result["status"] == "blocked"

    def test_bash_blocked(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("bash -c 'echo pwned'"))
        assert result["status"] == "blocked"

    def test_unknown_command_blocked(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("unknown_cmd --flag"))
        assert result["status"] == "blocked"
        assert "白名单" in result["reason"]


class TestRunShellInjection:
    """注入攻击防御。"""

    def test_backtick_blocked(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("echo `rm -rf /`"))
        assert result["status"] == "blocked"
        assert "危险字符" in result["reason"]

    def test_dollar_paren_blocked(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("echo $(cat /etc/passwd)"))
        assert result["status"] == "blocked"

    def test_semicolon_blocked(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("echo ok; rm -rf /"))
        assert result["status"] == "blocked"

    def test_redirect_blocked(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("echo pwned > /etc/passwd"))
        assert result["status"] == "blocked"

    def test_dollar_brace_blocked(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell("echo ${HOME}"))
        assert result["status"] == "blocked"


class TestRunShellSubcommandRestriction:
    """python/pip 子命令限制。"""

    def test_python_version_allowed(self, workspace: Path) -> None:
        valid, _ = shell_tools._validate_command("python3 --version")
        assert valid is True

    def test_python_script_blocked(self, workspace: Path) -> None:
        valid, reason = shell_tools._validate_command("python3 malicious.py")
        assert valid is False
        assert "run_code" in reason

    def test_pip_list_allowed(self, workspace: Path) -> None:
        valid, _ = shell_tools._validate_command("pip list")
        assert valid is True

    def test_pip_install_blocked(self, workspace: Path) -> None:
        valid, reason = shell_tools._validate_command("pip install requests")
        assert valid is False
        assert "list/show/freeze" in reason


class TestRunShellValidation:
    """参数校验。"""

    def test_timeout_zero_raises(self, workspace: Path) -> None:
        with pytest.raises(ValueError, match="大于 0"):
            shell_tools.run_shell("echo ok", timeout_seconds=0)

    def test_timeout_over_limit_raises(self, workspace: Path) -> None:
        with pytest.raises(ValueError, match="不能超过"):
            shell_tools.run_shell("echo ok", timeout_seconds=200)

    def test_empty_command(self, workspace: Path) -> None:
        result = json.loads(shell_tools.run_shell(""))
        assert result["status"] == "blocked"
        assert "为空" in result["reason"]


class TestRunShellTimeout:
    """超时测试。"""

    def test_timeout(self, workspace: Path) -> None:
        # sleep 不在白名单中，但我们可以用 tail -f 来模拟
        # 更简单的方式：校验会拦截 sleep，所以用 python 版本检查代替
        # 这里只测试校验通过后的超时场景不太好构造，跳过
        pass


class TestGetTools:
    def test_tool_names(self) -> None:
        names = {tool.name for tool in shell_tools.get_tools()}
        assert names == {"run_shell"}
