"""bench 运行器单元测试。"""

from __future__ import annotations

import asyncio
import json
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus import bench


def _run(coro):
    """同步执行异步协程。"""
    return asyncio.run(coro)


def _make_config() -> SimpleNamespace:
    """创建最小配置对象。"""
    return SimpleNamespace(log_level="INFO")


def _make_result(
    case_id: str,
    *,
    status: str = "ok",
    error: dict[str, str] | None = None,
) -> bench.BenchResult:
    """构造 bench 结果对象。"""
    return bench.BenchResult(
        case_id=case_id,
        case_name=f"case-{case_id}",
        message=f"message-{case_id}",
        timestamp="2026-02-14T00:00:00+00:00",
        duration_seconds=1.23,
        iterations=2,
        route_mode="fallback",
        skills_used=["data_basic"],
        tool_scope=["read_excel"],
        tool_calls=[
            bench.ToolCallLog(
                tool_name="read_excel",
                arguments={"file_path": "a.xlsx"},
                success=True,
                result="ok",
                error=None,
                iteration=1,
                duration_ms=12.3,
            )
        ],
        thinking_log=["思考记录"],
        reply="执行完成",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        subagent_events=[],
        llm_calls=[{"request": {"model": "test"}}],
        conversation_messages=[{"role": "user", "content": "hi"}],
        status=status,
        error=error,
    )


def test_main_without_args_shows_help_and_exit_zero(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["python -m excelmanus.bench"])
    with patch("excelmanus.bench.load_config") as mock_load:
        code = _run(bench._main())

    assert code == 0
    mock_load.assert_not_called()
    captured = capsys.readouterr()
    assert "usage:" in captured.out.lower()


def test_main_suite_flag_dispatches_to_run_suite(tmp_path: Path, monkeypatch) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["python -m excelmanus.bench", "--suite", str(suite_path)],
    )

    with (
        patch("excelmanus.bench.load_config", return_value=_make_config()) as _cfg,
        patch("excelmanus.bench.setup_logging") as _logging,
        patch("excelmanus.bench.run_suite", new=AsyncMock(return_value=[])) as mock_run_suite,
        patch("excelmanus.bench.run_single", new=AsyncMock()) as mock_run_single,
    ):
        code = _run(bench._main())

    assert code == 0
    mock_run_single.assert_not_called()
    mock_run_suite.assert_awaited_once()
    assert mock_run_suite.await_args.args[0] == suite_path
    assert mock_run_suite.await_args.args[2] == Path("outputs/bench")
    assert mock_run_suite.await_args.kwargs["concurrency"] == 1


def test_main_positional_existing_json_uses_suite(tmp_path: Path, monkeypatch) -> None:
    suite_path = tmp_path / "suite_a.json"
    suite_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["python -m excelmanus.bench", str(suite_path)],
    )

    with (
        patch("excelmanus.bench.load_config", return_value=_make_config()),
        patch("excelmanus.bench.setup_logging"),
        patch("excelmanus.bench.run_suite", new=AsyncMock(return_value=[])) as mock_run_suite,
        patch("excelmanus.bench.run_single", new=AsyncMock()) as mock_run_single,
    ):
        code = _run(bench._main())

    assert code == 0
    mock_run_suite.assert_awaited_once()
    mock_run_single.assert_not_called()


def test_main_positional_text_uses_message(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["python -m excelmanus.bench", "读取", "前十行"],
    )

    with (
        patch("excelmanus.bench.load_config", return_value=_make_config()),
        patch("excelmanus.bench.setup_logging"),
        patch("excelmanus.bench.run_suite", new=AsyncMock()) as mock_run_suite,
        patch("excelmanus.bench.run_single", new=AsyncMock(return_value=_make_result("adhoc"))) as mock_run_single,
    ):
        code = _run(bench._main())

    assert code == 0
    mock_run_suite.assert_not_called()
    mock_run_single.assert_awaited_once()
    assert mock_run_single.await_args.args[0] == "读取 前十行"


def test_main_all_mode_missing_cases_dir_returns_one(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["python -m excelmanus.bench", "--all"])

    with (
        patch("excelmanus.bench.load_config", return_value=_make_config()),
        patch("excelmanus.bench.setup_logging"),
        patch("excelmanus.bench.run_suite", new=AsyncMock()) as mock_run_suite,
    ):
        code = _run(bench._main())

    assert code == 1
    mock_run_suite.assert_not_called()


def test_main_invalid_concurrency_exits_two(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["python -m excelmanus.bench", "--concurrency", "0", "--message", "hello"],
    )
    with pytest.raises(SystemExit) as exc_info:
        _run(bench._main())
    assert exc_info.value.code == 2


def test_main_output_dir_is_passed_to_run_single(tmp_path: Path, monkeypatch) -> None:
    output_dir = tmp_path / "custom-output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "python -m excelmanus.bench",
            "--message",
            "hello",
            "--output-dir",
            str(output_dir),
        ],
    )

    with (
        patch("excelmanus.bench.load_config", return_value=_make_config()),
        patch("excelmanus.bench.setup_logging"),
        patch("excelmanus.bench.run_single", new=AsyncMock(return_value=_make_result("adhoc"))) as mock_run_single,
    ):
        code = _run(bench._main())

    assert code == 0
    assert mock_run_single.await_args.args[2] == output_dir


def test_resolve_run_mode_json_targets_to_suite() -> None:
    args = Namespace(
        suite=None,
        all=False,
        message=None,
        targets=["a.json", "b.json"],
    )
    plan = bench._resolve_run_mode(args)
    assert plan.mode == "suite"
    assert plan.suite_paths == [Path("a.json"), Path("b.json")]


def test_resolve_run_mode_mixed_targets_to_message() -> None:
    args = Namespace(
        suite=None,
        all=False,
        message=None,
        targets=["读取", "a.json", "前十行"],
    )
    plan = bench._resolve_run_mode(args)
    assert plan.mode == "message"
    assert plan.message == "读取 a.json 前十行"


def test_run_suite_serial_mode_enables_render(tmp_path: Path) -> None:
    cases = [
        bench.BenchCase(id="c1", name="case-1", message="m1"),
        bench.BenchCase(id="c2", name="case-2", message="m2"),
    ]
    render_flags: list[bool] = []

    async def _fake_run_case(case, config, *, render_enabled, trace_enabled=False, output_dir=None):
        render_flags.append(render_enabled)
        return _make_result(case.id)

    with (
        patch("excelmanus.bench._load_suite", return_value=("demo", cases)),
        patch("excelmanus.bench.run_case", side_effect=_fake_run_case),
        patch(
            "excelmanus.bench._save_result",
            side_effect=lambda result, output_dir: output_dir / f"{result.case_id}.json",
        ),
        patch("excelmanus.bench._save_suite_summary", return_value=tmp_path / "suite.json"),
    ):
        results = _run(bench.run_suite("demo.json", _make_config(), tmp_path, concurrency=1))

    assert [r.case_id for r in results] == ["c1", "c2"]
    assert render_flags == [True, True]


def test_run_suite_concurrent_mode_disables_render_and_keeps_order(tmp_path: Path) -> None:
    cases = [
        bench.BenchCase(id="c1", name="case-1", message="m1"),
        bench.BenchCase(id="c2", name="case-2", message="m2"),
        bench.BenchCase(id="c3", name="case-3", message="m3"),
    ]
    delays = {"c1": 0.05, "c2": 0.01, "c3": 0.02}
    render_flags: list[bool] = []

    async def _fake_run_case(case, config, *, render_enabled, trace_enabled=False, output_dir=None):
        render_flags.append(render_enabled)
        await asyncio.sleep(delays[case.id])
        return _make_result(case.id)

    with (
        patch("excelmanus.bench._load_suite", return_value=("demo", cases)),
        patch("excelmanus.bench.run_case", side_effect=_fake_run_case),
        patch(
            "excelmanus.bench._save_result",
            side_effect=lambda result, output_dir: output_dir / f"{result.case_id}.json",
        ),
        patch("excelmanus.bench._save_suite_summary", return_value=tmp_path / "suite.json"),
    ):
        results = _run(bench.run_suite("demo.json", _make_config(), tmp_path, concurrency=3))

    assert [r.case_id for r in results] == ["c1", "c2", "c3"]
    assert render_flags == [False, False, False]


def test_bench_result_to_dict_schema_v2() -> None:
    payload = _make_result("c1").to_dict()
    assert payload["schema_version"] == 2
    assert payload["kind"] == "case_result"
    assert payload["execution"]["status"] == "ok"
    assert payload["meta"]["case_id"] == "c1"
    assert set(payload.keys()) == {
        "schema_version",
        "kind",
        "timestamp",
        "meta",
        "execution",
        "artifacts",
        "result",
        "stats",
    }


def test_save_suite_summary_schema_v2(tmp_path: Path) -> None:
    results = [
        _make_result("c1"),
        _make_result(
            "c2",
            status="error",
            error={"type": "RuntimeError", "message": "boom"},
        ),
    ]
    case_logs = [tmp_path / "c1.json", tmp_path / "c2.json"]
    summary_path = bench._save_suite_summary(
        "demo-suite",
        "bench/cases/demo.json",
        results,
        tmp_path,
        concurrency=2,
        case_log_files=case_logs,
    )

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["kind"] == "suite_summary"
    assert payload["execution"]["concurrency"] == 2
    assert payload["execution"]["status"] == "completed_with_errors"
    assert payload["meta"]["case_count"] == 2
    assert payload["result"]["failed_case_ids"] == ["c2"]
    assert payload["stats"]["total_tokens"] == 30


def test_run_case_exception_returns_structured_error() -> None:
    async def _dummy_create(**kwargs):
        return None

    class _FailEngine:
        def __init__(self) -> None:
            self._client = SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(create=_dummy_create)
                )
            )
            self._memory = SimpleNamespace(get_messages=lambda: [])
            self.last_route_result = SimpleNamespace(
                route_mode="fallback",
                skills_used=[],
                tool_scope=[],
            )

        async def chat(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    case = bench.BenchCase(id="x1", name="error-case", message="hello")
    with patch("excelmanus.bench._create_engine", return_value=_FailEngine()):
        result = _run(bench.run_case(case, _make_config(), render_enabled=False))

    assert result.status == "error"
    assert result.error is not None
    assert result.error["type"] == "RuntimeError"
    assert "boom" in result.error["message"]
    payload = result.to_dict()
    assert payload["execution"]["status"] == "error"
    assert payload["execution"]["error"]["type"] == "RuntimeError"


def test_run_single_still_saves_log_file(tmp_path: Path) -> None:
    mock_result = _make_result("adhoc")
    with patch("excelmanus.bench.run_case", new=AsyncMock(return_value=mock_result)) as mock_run_case:
        result = _run(bench.run_single("hello", _make_config(), tmp_path))

    assert result is mock_result
    mock_run_case.assert_awaited_once()
    assert mock_run_case.await_args.kwargs["render_enabled"] is True
    saved_files = list(tmp_path.glob("run_*.json"))
    assert len(saved_files) == 1
    payload = json.loads(saved_files[0].read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["kind"] == "case_result"


# ── _run_suites 测试 ──────────────────────────────────────


def test_run_suites_serial_calls_run_suite_in_order(tmp_path: Path) -> None:
    """串行模式下 _run_suites 按顺序调用 run_suite。"""
    suite_a = tmp_path / "suite_a.json"
    suite_b = tmp_path / "suite_b.json"
    suite_a.write_text("{}", encoding="utf-8")
    suite_b.write_text("{}", encoding="utf-8")

    call_order: list[str] = []

    async def _fake_run_suite(path, config, output_dir, *, concurrency=1, trace_enabled=False):
        call_order.append(Path(path).stem)
        return [_make_result(f"{Path(path).stem}_c1")]

    with patch("excelmanus.bench.run_suite", side_effect=_fake_run_suite):
        code = _run(bench._run_suites(
            [suite_a, suite_b],
            _make_config(),
            tmp_path / "out",
            concurrency=1,
            suite_concurrency=1,
        ))

    assert code == 0
    assert call_order == ["suite_a", "suite_b"]
    # 全局汇总文件应存在
    global_files = list((tmp_path / "out").glob("global_*.json"))
    assert len(global_files) == 1
    payload = json.loads(global_files[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "global_summary"
    assert payload["stats"]["total_cases"] == 2
    assert payload["stats"]["passed"] == 2


def test_run_suites_parallel_runs_concurrently(tmp_path: Path) -> None:
    """suite_concurrency > 1 时多个 suite 并发执行。"""
    suites = []
    for i in range(3):
        p = tmp_path / f"suite_{i}.json"
        p.write_text("{}", encoding="utf-8")
        suites.append(p)

    timestamps: list[float] = []

    async def _fake_run_suite(path, config, output_dir, *, concurrency=1, trace_enabled=False):
        import time as _time
        timestamps.append(_time.monotonic())
        await asyncio.sleep(0.05)
        return [_make_result(f"{Path(path).stem}_c1")]

    with patch("excelmanus.bench.run_suite", side_effect=_fake_run_suite):
        code = _run(bench._run_suites(
            suites,
            _make_config(),
            tmp_path / "out",
            concurrency=1,
            suite_concurrency=3,
        ))

    assert code == 0
    # 并发执行时，三个 suite 的启动时间差应很小（< 0.1s）
    assert max(timestamps) - min(timestamps) < 0.1


def test_run_suites_returns_one_on_failure(tmp_path: Path) -> None:
    """存在失败用例时返回退出码 1。"""
    suite_path = tmp_path / "suite_fail.json"
    suite_path.write_text("{}", encoding="utf-8")

    async def _fake_run_suite(path, config, output_dir, *, concurrency=1, trace_enabled=False):
        return [
            _make_result("ok_case"),
            _make_result("fail_case", status="error", error={"type": "E", "message": "x"}),
        ]

    with patch("excelmanus.bench.run_suite", side_effect=_fake_run_suite):
        code = _run(bench._run_suites(
            [suite_path],
            _make_config(),
            tmp_path / "out",
            concurrency=1,
            suite_concurrency=1,
        ))

    assert code == 1
    global_files = list((tmp_path / "out").glob("global_*.json"))
    payload = json.loads(global_files[0].read_text(encoding="utf-8"))
    assert payload["stats"]["failed"] == 1


def test_run_suites_global_summary_has_suite_details(tmp_path: Path) -> None:
    """全局汇总包含每个 suite 的统计明细。"""
    suite_a = tmp_path / "suite_a.json"
    suite_b = tmp_path / "suite_b.json"
    suite_a.write_text("{}", encoding="utf-8")
    suite_b.write_text("{}", encoding="utf-8")

    async def _fake_run_suite(path, config, output_dir, *, concurrency=1, trace_enabled=False):
        stem = Path(path).stem
        if stem == "suite_a":
            return [_make_result("a1"), _make_result("a2")]
        return [_make_result("b1")]

    with patch("excelmanus.bench.run_suite", side_effect=_fake_run_suite):
        _run(bench._run_suites(
            [suite_a, suite_b],
            _make_config(),
            tmp_path / "out",
            concurrency=2,
            suite_concurrency=2,
        ))

    global_files = list((tmp_path / "out").glob("global_*.json"))
    payload = json.loads(global_files[0].read_text(encoding="utf-8"))
    assert payload["execution"]["suite_concurrency"] == 2
    assert payload["execution"]["case_concurrency"] == 2
    assert payload["stats"]["total_cases"] == 3
    assert len(payload["suites"]) == 2


def test_main_suite_concurrency_flag_passed_through(tmp_path: Path, monkeypatch) -> None:
    """--suite-concurrency 参数正确传递到 _run_suites。"""
    suite_path = tmp_path / "suite.json"
    suite_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "python -m excelmanus.bench",
            "--suite", str(suite_path),
            "--suite-concurrency", "4",
            "--concurrency", "2",
        ],
    )

    captured_kwargs: dict = {}

    async def _fake_run_suites(paths, config, output_dir, **kwargs):
        captured_kwargs.update(kwargs)
        return 0

    with (
        patch("excelmanus.bench.load_config", return_value=_make_config()),
        patch("excelmanus.bench.setup_logging"),
        patch("excelmanus.bench._run_suites", side_effect=_fake_run_suites) as mock,
    ):
        code = _run(bench._main())

    assert code == 0
    assert captured_kwargs["suite_concurrency"] == 4
    assert captured_kwargs["concurrency"] == 2
