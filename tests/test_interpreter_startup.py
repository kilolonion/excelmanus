"""解释器冷启动：后台预热、并发缓存与超时诊断回归。"""

from concurrent.futures import ThreadPoolExecutor
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from excelmanus.tools import code_tools as tools


@pytest.fixture(autouse=True)
def isolated_resolver(monkeypatch):
    monkeypatch.setattr(tools, "_RESOLVE_CACHE", {})
    monkeypatch.setattr(tools, "_RESOLVE_LOCKS", {})


@pytest.mark.parametrize("tier", ["YELLOW", "GREEN", "RED"])
@pytest.mark.parametrize("warmup_first", [True, False])
def test_warmup_and_foreground_share_success(monkeypatch, tier, warmup_first):
    entered = threading.Event()
    release = threading.Event()
    contender_started = threading.Event()
    duplicate = threading.Event()
    calls = []

    def resolve(command, *, require_excel_deps, sandbox_tier):
        calls.append(sandbox_tier)
        if sandbox_tier == tier:
            if calls.count(tier) > 1:
                duplicate.set()
                raise RuntimeError("competing probe failed")
            entered.set()
            assert release.wait(5)
        return ["python"], [], "auto"

    def foreground():
        contender_started.set()
        return tools._resolve_python_command(
            "auto", require_excel_deps=True, sandbox_tier=tier,
        )

    def background():
        contender_started.set()
        return tools.warmup_interpreter()

    monkeypatch.setattr(tools, "_resolve_python_command_uncached", resolve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(background if warmup_first else foreground)
        try:
            assert entered.wait(5)
            contender_started.clear()
            second = pool.submit(foreground if warmup_first else background)
            assert contender_started.wait(5)
            assert not duplicate.wait(0.2), "预热期间前台不应重复探测"
        finally:
            release.set()
        warmup, request = (first, second) if warmup_first else (second, first)
        warmup.result(timeout=5)
        assert request.result(timeout=5)[0] == ["python"]
    assert calls.count(tier) == 1
    assert tools._resolve_python_command(
        "auto", require_excel_deps=True, sandbox_tier=tier,
    )[0] == ["python"]


def test_waiting_foreground_retries_failed_warmup_immediately(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    request_started = threading.Event()
    calls = []

    def resolve(command, *, require_excel_deps, sandbox_tier):
        calls.append(sandbox_tier)
        if sandbox_tier == "YELLOW" and calls.count("YELLOW") == 1:
            entered.set()
            assert release.wait(5)
            raise RuntimeError("cold start failed")
        return ["python"], [], "auto"

    def foreground():
        request_started.set()
        return tools._resolve_python_command(
            "auto", require_excel_deps=True, sandbox_tier="YELLOW",
        )

    monkeypatch.setattr(tools, "_resolve_python_command_uncached", resolve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        warmup = pool.submit(tools.warmup_interpreter)
        try:
            assert entered.wait(5)
            request = pool.submit(foreground)
            assert request_started.wait(5)
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="YELLOW: cold start failed"):
            warmup.result(timeout=5)
        assert request.result(timeout=5)[0] == ["python"]
    assert calls.count("YELLOW") == 2
    tools._resolve_python_command("auto", require_excel_deps=True, sandbox_tier="YELLOW")
    assert calls.count("YELLOW") == 2, "真实调用成功不能被失败的预热覆盖"


def test_concurrent_foreground_failures_are_cached_once(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    started = threading.Event()
    duplicate = threading.Event()
    calls = []

    def resolve(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            duplicate.set()
        entered.set()
        assert release.wait(5)
        raise RuntimeError("probe failed")

    def request():
        started.set()
        with pytest.raises(RuntimeError, match="probe failed"):
            tools._resolve_python_command("auto", require_excel_deps=True)

    monkeypatch.setattr(tools, "_resolve_python_command_uncached", resolve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(request)
        try:
            assert entered.wait(5)
            started.clear()
            second = pool.submit(request)
            assert started.wait(5)
            assert not duplicate.wait(0.2)
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert len(calls) == 1
    with pytest.raises(RuntimeError, match="60s"):
        tools._resolve_python_command("auto", require_excel_deps=True)


def test_slow_interpreter_does_not_block_another_configuration(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def resolve(command, **kwargs):
        if command == "slow-python":
            entered.set()
            assert release.wait(5)
        return [command], [], "explicit"

    monkeypatch.setattr(tools, "_resolve_python_command_uncached", resolve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(tools._resolve_python_command, "slow-python", require_excel_deps=True)
        try:
            assert entered.wait(5)
            fast = pool.submit(tools._resolve_python_command, "fast-python", require_excel_deps=True)
            assert fast.result(timeout=2)[0] == ["fast-python"]
        finally:
            release.set()
        slow.result(timeout=5)


def test_warmup_checks_and_caches_each_sandbox_tier(monkeypatch):
    from excelmanus.security.code_policy import CodePolicyEngine

    tiers = []

    def probe(command, *, require_excel_deps, sandbox_tier):
        assert require_excel_deps
        tiers.append(sandbox_tier)
        return tools._InterpreterProbe(command, "ok", "")

    monkeypatch.setattr(tools, "_probe_environment", probe)
    tools.warmup_interpreter()
    assert tiers == ["YELLOW", "GREEN", "RED"]
    sample_tier = CodePolicyEngine().analyze(
        'import em, os\nos.makedirs("outputs/my_first_skill", exist_ok=True)\n'
        'with open("outputs/my_first_skill/SKILL.md", "w") as f:\n    f.write("template")'
    ).tier.value
    assert sample_tier == "YELLOW"
    for tier in (sample_tier, "GREEN", "RED"):
        tools._resolve_python_command("auto", require_excel_deps=True, sandbox_tier=tier)
    assert len(tiers) == 3, "各等级预热后，真实请求应直接命中缓存"


def test_successful_red_probe_cannot_bypass_yellow_guard(monkeypatch):
    def probe(command, *, require_excel_deps, sandbox_tier):
        if sandbox_tier == "YELLOW":
            return tools._InterpreterProbe(command, "missing_deps", "sandbox blocks dependency")
        return tools._InterpreterProbe(command, "ok", "")

    monkeypatch.setattr(tools, "_probe_environment", probe)
    tools._resolve_python_command("auto", require_excel_deps=True, sandbox_tier="RED")
    with pytest.raises(RuntimeError, match="YELLOW") as error:
        tools.warmup_interpreter()
    assert "sandbox blocks dependency" in str(error.value)
    with pytest.raises(RuntimeError, match="sandbox blocks dependency"):
        tools._resolve_python_command("auto", require_excel_deps=True, sandbox_tier="YELLOW")


def test_failed_warmup_preserves_other_cache_and_allows_retry(monkeypatch):
    unrelated = ("other-python", True, "RED", "")
    existing_failure = (None, "existing request failed", time.monotonic() + 60)
    tools._RESOLVE_CACHE[unrelated] = existing_failure
    failed_tier = "YELLOW"
    calls = []

    def resolve(command, *, require_excel_deps, sandbox_tier):
        calls.append(sandbox_tier)
        if sandbox_tier == failed_tier:
            raise RuntimeError("cold start failed")
        return ["python"], [], "auto"

    monkeypatch.setattr(tools, "_resolve_python_command_uncached", resolve)
    with pytest.raises(RuntimeError, match="YELLOW"):
        tools.warmup_interpreter()
    assert sorted(calls) == ["GREEN", "RED", "YELLOW"]
    assert tools._RESOLVE_CACHE[unrelated] == existing_failure
    assert all(value[0] is not None for key, value in tools._RESOLVE_CACHE.items() if key != unrelated)
    failed_tier = None
    tools._resolve_python_command("auto", require_excel_deps=True, sandbox_tier="YELLOW")
    assert calls.count("YELLOW") == 2
    for tier in ("RED", "GREEN"):
        tools._resolve_python_command("auto", require_excel_deps=True, sandbox_tier=tier)
        assert calls.count(tier) == 1


@pytest.mark.parametrize("tier", ["RED", "YELLOW", "GREEN"])
def test_timeout_hint_survives_long_command(monkeypatch, tier):
    command = "python-" + "x" * 300
    timeouts = []

    def run(args, **kwargs):
        timeouts.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setenv("EXCELMANUS_RUN_PYTHON", command)
    monkeypatch.setattr(tools, "_command_exists", lambda args: args == [command])
    monkeypatch.setattr(tools, "_resolve_candidate_executable", lambda args: None)
    monkeypatch.setattr(tools.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="提示：探测超时") as error:
        tools._resolve_python_command("auto", require_excel_deps=True, sandbox_tier=tier)
    assert timeouts == [tools._PROBE_TIMEOUT_SECONDS, tools._PROBE_TIMEOUT_RETRY_SECONDS]
    assert "timed out" not in str(error.value), "超时提示不能依赖异常末尾被截掉的英文"
    assert "90 秒" in str(error.value)


@pytest.mark.parametrize("failure", [OSError("cannot start"), SimpleNamespace(returncode=1, stderr="missing dependency", stdout="")])
def test_non_timeout_failure_is_not_retried(monkeypatch, failure):
    calls = []

    def run(*args, **kwargs):
        calls.append(1)
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(tools, "_command_exists", lambda command: True)
    monkeypatch.setattr(tools.subprocess, "run", run)
    result = tools._probe_environment(["python"], require_excel_deps=True)
    assert result.status in ("error", "missing_deps")
    assert len(calls) == 1
