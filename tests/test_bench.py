"""bench 运行器单元测试。"""

from __future__ import annotations

import asyncio
import json
import sys
from argparse import Namespace
from datetime import datetime
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
    return SimpleNamespace(
        log_level="INFO",
        model="test-model",
        base_url="http://localhost",
    )


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


def test_list_default_suite_paths_skips_opt_out(tmp_path: Path) -> None:
    (tmp_path / "keep.json").write_text(
        json.dumps({"suite_name": "keep", "cases": []}),
        encoding="utf-8",
    )
    (tmp_path / "skip.json").write_text(
        json.dumps({"suite_name": "skip", "include_in_all": False, "cases": []}),
        encoding="utf-8",
    )
    paths = bench.list_default_suite_paths(tmp_path)
    assert [path.name for path in paths] == ["keep.json"]


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

    async def _fake_run_case(case, config, *, render_enabled, trace_enabled=False, output_dir=None, suite_name="", **_kwargs):
        render_flags.append(render_enabled)
        return _make_result(case.id)

    with (
        patch("excelmanus.bench._load_suite", return_value=("demo", cases, False)),
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

    async def _fake_run_case(case, config, *, render_enabled, trace_enabled=False, output_dir=None, suite_name="", **_kwargs):
        render_flags.append(render_enabled)
        await asyncio.sleep(delays[case.id])
        return _make_result(case.id)

    with (
        patch("excelmanus.bench._load_suite", return_value=("demo", cases, False)),
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


def test_bench_result_to_dict_schema_v3() -> None:
    payload = _make_result("c1").to_dict()
    assert payload["schema_version"] == 3
    assert payload["kind"] == "case_result"
    assert payload["execution"]["status"] == "ok"
    assert payload["meta"]["case_id"] == "c1"
    assert payload["meta"]["active_model"] == ""
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


def test_stats_split_model_visible_and_internal_failures() -> None:
    """外层失败计入 model_tool_failures；run_code 内层 SDK 失败计入 internal，不重复。"""
    result = _make_result("split")
    result.tool_calls = [
        bench.ToolCallLog(
            tool_name="observe_spreadsheet", arguments={}, success=True,
            result="ok", error=None, iteration=1,
        ),
        bench.ToolCallLog(
            tool_name="run_code", arguments={}, success=False,
            result="", error="Traceback", iteration=1,
        ),
        bench.ToolCallLog(
            tool_name="analyze_spreadsheet", arguments={}, success=False,
            result="", error="INVALID_ARGS", iteration=1,
            parent_call_id="call_run_code_1",
        ),
    ]

    stats = result.to_dict()["stats"]
    assert stats["tool_call_count"] == 3
    assert stats["tool_failures"] == 2
    assert stats["model_tool_failures"] == 1
    assert stats["internal_tool_failures"] == 1
    assert stats["inner_tool_calls"] == 1
    assert stats["distinct_tool_errors"] == 1


def test_stats_distinct_tool_errors_collapses_inner_and_outer() -> None:
    """内层 HostToolError + 外层 traceback 以及同 message 重试计 1。"""
    result = _make_result("root")
    tb = (
        "Traceback (most recent call last):\n"
        '  File "em.py", line 1, in <module>\n'
        "HostToolError: 缺 sheet"
    )
    result.tool_calls = [
        bench.ToolCallLog(
            tool_name="run_code", arguments={}, success=False,
            result=tb, error=tb, iteration=1,
        ),
        bench.ToolCallLog(
            tool_name="apply_spreadsheet_changes", arguments={}, success=False,
            result="", error="缺 sheet", iteration=1,
            parent_call_id="run1",
        ),
        bench.ToolCallLog(
            tool_name="run_code", arguments={}, success=False,
            result=tb, error=tb, iteration=2,
        ),
        bench.ToolCallLog(
            tool_name="apply_spreadsheet_changes", arguments={}, success=False,
            result="", error="缺 sheet", iteration=2,
            parent_call_id="run2",
        ),
    ]
    stats = result.to_dict()["stats"]
    assert stats["tool_failures"] == 4
    assert stats["model_tool_failures"] == 2
    assert stats["distinct_tool_errors"] == 1


def test_save_result_serializes_datetime_arguments(tmp_path: Path) -> None:
    result = _make_result("dates")
    result.tool_calls[0].arguments["values"] = [[datetime(2024, 1, 2, 3, 4, 5)]]

    path = bench._save_result(result, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["artifacts"]["tool_calls"][0]["arguments"]["values"] == [
        ["2024-01-02 03:04:05"]
    ]
    assert not list(tmp_path.glob(".*.tmp"))


def test_write_json_keeps_existing_file_on_serialization_failure(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text('{"status":"old"}', encoding="utf-8")
    circular: dict[str, object] = {}
    circular["self"] = circular

    with pytest.raises(ValueError, match="Circular reference"):
        bench._write_json(path, circular)

    assert path.read_text(encoding="utf-8") == '{"status":"old"}'
    assert not list(tmp_path.glob(".*.tmp"))


def test_save_suite_summary_schema_v3(tmp_path: Path) -> None:
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
    assert payload["schema_version"] == 3
    assert payload["kind"] == "suite_summary"
    assert payload["execution"]["concurrency"] == 2
    assert payload["execution"]["status"] == "completed_with_errors"
    assert payload["meta"]["case_count"] == 2
    assert payload["result"]["failed_case_ids"] == ["c2"]
    assert payload["stats"]["total_tokens"] == 30
    assert payload["meta"]["pipeline"] == "fake_frontend"


def test_run_case_exception_returns_structured_error() -> None:
    case = bench.BenchCase(id="x1", name="error-case", message="hello")
    session = bench._CaseSession(
        session_id="sess-1",
        manager=SimpleNamespace(),
        workdir=None,
        owns_runtime=False,
        runtime=None,
    )
    with (
        patch("excelmanus.bench._open_case_session", new=AsyncMock(return_value=session)),
        patch(
            "excelmanus.bench.FakeFrontend.send",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch(
            "excelmanus.bench.FakeFrontend.export_conversation",
            return_value={"transcript": [], "session_id": "sess-1"},
        ),
        patch("excelmanus.bench._close_case_session", new=AsyncMock()),
    ):
        result = _run(bench.run_case(case, _make_config(), render_enabled=False))

    assert result.status == "error"
    assert result.error is not None
    assert result.error["type"] == "RuntimeError"
    assert "boom" in result.error["message"]
    assert result.pipeline == "fake_frontend"
    payload = result.to_dict()
    assert payload["execution"]["status"] == "error"
    assert payload["execution"]["error"]["type"] == "RuntimeError"
    assert payload["meta"]["pipeline"] == "fake_frontend"


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
    assert payload["schema_version"] == 3
    assert payload["kind"] == "case_result"


# ── _run_suites 测试 ──────────────────────────────────────


def test_run_suites_serial_calls_run_suite_in_order(tmp_path: Path) -> None:
    """串行模式下 _run_suites 按顺序调用 run_suite。"""
    suite_a = tmp_path / "suite_a.json"
    suite_b = tmp_path / "suite_b.json"
    suite_a.write_text("{}", encoding="utf-8")
    suite_b.write_text("{}", encoding="utf-8")

    call_order: list[str] = []

    async def _fake_run_suite(path, config, output_dir, *, concurrency=1, trace_enabled=False, **_kwargs):
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

    async def _fake_run_suite(path, config, output_dir, *, concurrency=1, trace_enabled=False, **_kwargs):
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

    async def _fake_run_suite(path, config, output_dir, *, concurrency=1, trace_enabled=False, **_kwargs):
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


def test_run_suites_warning_only_passes_by_default_but_fails_strict(tmp_path: Path) -> None:
    """仅效率 warn：默认退出码 0，--strict-efficiency 下退出码 1。"""
    from excelmanus.bench_validator import AssertionResult, ValidationSummary

    def _warn_result(cid: str) -> bench.BenchResult:
        r = _make_result(cid)
        r.validation = ValidationSummary(
            total=2, passed=1, failed=1, errors=0, warnings=1,
            results=[
                AssertionResult(rule="status", passed=True),
                AssertionResult(
                    rule="max_llm_calls", passed=False, expected="<= 6", actual=10,
                    message="max_llm_calls: 10 超过上限 6", severity="warning",
                ),
            ],
        )
        return r

    async def _fake_run_suite(path, config, output_dir, *, concurrency=1, trace_enabled=False, **_kwargs):
        return [_warn_result("w1")]

    suite_path = tmp_path / "suite_warn.json"
    suite_path.write_text("{}", encoding="utf-8")
    with patch("excelmanus.bench.run_suite", side_effect=_fake_run_suite):
        code_default = _run(bench._run_suites(
            [suite_path], _make_config(), tmp_path / "out1",
            concurrency=1, suite_concurrency=1,
        ))
    assert code_default == 0

    with patch("excelmanus.bench.run_suite", side_effect=_fake_run_suite):
        code_strict = _run(bench._run_suites(
            [suite_path], _make_config(), tmp_path / "out2",
            concurrency=1, suite_concurrency=1, strict_efficiency=True,
        ))
    assert code_strict == 1


def test_strict_efficiency_flag_parsed() -> None:
    """效率门禁默认开启，可显式降级为 warn-only。"""
    args = bench._build_parser().parse_args(["--suite", "x.json", "--strict-efficiency"])
    plan = bench._resolve_run_mode(args)
    assert plan.strict_efficiency is True
    args2 = bench._build_parser().parse_args(["--suite", "x.json"])
    assert bench._resolve_run_mode(args2).strict_efficiency is True
    args3 = bench._build_parser().parse_args(["--suite", "x.json", "--no-strict-efficiency"])
    assert bench._resolve_run_mode(args3).strict_efficiency is False


def test_run_suites_global_summary_has_suite_details(tmp_path: Path) -> None:
    """全局汇总包含每个 suite 的统计明细。"""
    suite_a = tmp_path / "suite_a.json"
    suite_b = tmp_path / "suite_b.json"
    suite_a.write_text("{}", encoding="utf-8")
    suite_b.write_text("{}", encoding="utf-8")

    async def _fake_run_suite(path, config, output_dir, *, concurrency=1, trace_enabled=False, **_kwargs):
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


def test_default_auto_approve_is_fullaccess() -> None:
    case = bench.BenchCase(id="c1", name="n", message="hi")
    assert case.auto_approve == "fullaccess"


def test_case_turns_uses_source_files_as_first_attachments() -> None:
    case = bench.BenchCase(
        id="c1",
        name="n",
        message="请检查这个表",
        source_files=["sales.xlsx"],
        images=["shot.png"],
    )
    turns = bench._case_turns(case)
    assert len(turns) == 1
    assert turns[0].text == "请检查这个表"
    assert turns[0].attachments == ["sales.xlsx"]
    assert turns[0].images == ["shot.png"]


def test_load_suite_merges_suite_and_case_assertions(tmp_path: Path) -> None:
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps({
            "suite_name": "demo",
            "assertions": {"status": "ok"},
            "cases": [
                {
                    "id": "C01",
                    "name": "带附件",
                    "message": "请检查这个表",
                    "attachments": ["sales.xlsx"],
                    "images": ["shot.png"],
                    "chat_mode": "read",
                    "auto_approve": "reject",
                    "auto_replies": ["1"],
                    "assertions": {"max_tokens": 1},
                    "messages": [
                        "下一轮纯文本",
                        {
                            "text": "带附件的一轮",
                            "attachments": ["a.xlsx"],
                            "images": ["b.png"],
                        },
                    ],
                }
            ],
        }),
        encoding="utf-8",
    )

    name, cases, _trace = bench._load_suite(suite)
    assert name == "demo"
    assert len(cases) == 1
    case = cases[0]
    assert case.chat_mode == "read"
    assert case.auto_approve == "reject"
    assert case.auto_replies == ["1"]
    assert case.attachments == ["sales.xlsx"]
    assert [turn.text for turn in case.turns] == ["下一轮纯文本", "带附件的一轮"]
    assert case.turns[1].attachments == ["a.xlsx"]
    # suite 级默认 + case 级覆盖：断言在加载时合并，run_case 结束时校验
    assert case.assertions == {"status": "ok", "max_tokens": 1}


def test_write_conversation_export(tmp_path: Path) -> None:
    path = bench._write_conversation_export(
        {"session_id": "s1", "transcript": [{"role": "user", "content": "hi"}]},
        tmp_path,
        "C01",
    )
    assert path == tmp_path / "conversations" / "C01.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["session_id"] == "s1"
    assert payload["transcript"][0]["content"] == "hi"


def test_bench_result_records_conversation_export() -> None:
    result = _make_result("c1")
    result.session_id = "sess-9"
    result.conversation_export = {
        "session_id": "sess-9",
        "pipeline": "fake_frontend",
        "transcript": [{"role": "user", "content": "hi"}],
    }
    payload = result.to_dict()
    assert payload["meta"]["session_id"] == "sess-9"
    assert payload["artifacts"]["conversation_export"]["pipeline"] == "fake_frontend"


# ── --case / --wave 过滤与 digest ──────────────────────────


def _parse_args(argv: list[str]) -> Namespace:
    parser = bench._build_parser()
    return parser.parse_args(argv[1:])


def test_resolve_run_mode_propagates_timeout_and_filters(tmp_path: Path) -> None:
    """--turn-timeout / --case / --wave 在 suite 模式下不再被丢弃。"""
    args = _parse_args([
        "bench",
        "--suite", "bench/cases/suite_smoke.json",
        "--turn-timeout", "120",
        "--case", "E15",
        "--case", "E20",
        "--wave", "2",
    ])
    plan = bench._resolve_run_mode(args)
    assert plan.mode == "suite"
    assert plan.turn_timeout == 120.0
    assert plan.case_ids == ["E15", "E20"]
    assert plan.wave == "2"


def test_filter_cases_by_wave_and_ids() -> None:
    cases = [
        bench.BenchCase(id="E01", name="a", message="m", tags=["wave-1"]),
        bench.BenchCase(id="E15", name="b", message="m", tags=["wave-2", "format"]),
        bench.BenchCase(id="E20", name="c", message="m", tags=["wave-2"]),
    ]
    assert [c.id for c in bench._filter_cases(cases, wave="2")] == ["E15", "E20"]
    assert [c.id for c in bench._filter_cases(cases, case_ids=["E01"])] == ["E01"]
    assert bench._filter_cases(cases, wave="9") == []
    assert [c.id for c in bench._filter_cases(cases)] == ["E01", "E15", "E20"]


def test_run_suite_case_filter_selects_subset(tmp_path: Path) -> None:
    cases = [
        bench.BenchCase(id="c1", name="case-1", message="m1", tags=["wave-1"]),
        bench.BenchCase(id="c2", name="case-2", message="m2", tags=["wave-2"]),
    ]
    ran: list[str] = []

    async def _fake_run_case(case, config, *, render_enabled, **_kwargs):
        ran.append(case.id)
        return _make_result(case.id)

    with (
        patch("excelmanus.bench._load_suite", return_value=("demo", cases, False)),
        patch("excelmanus.bench.run_case", side_effect=_fake_run_case),
        patch(
            "excelmanus.bench._save_result",
            side_effect=lambda result, output_dir: output_dir / f"{result.case_id}.json",
        ),
        patch("excelmanus.bench._save_suite_summary", return_value=tmp_path / "suite.json"),
        patch("excelmanus.bench._write_case_digest", return_value=None),
    ):
        results = _run(bench.run_suite(
            "demo.json", _make_config(), tmp_path, case_ids=["c2"],
        ))

    assert ran == ["c2"]
    assert [r.case_id for r in results] == ["c2"]


def test_run_suite_filter_no_match_raises(tmp_path: Path) -> None:
    cases = [bench.BenchCase(id="c1", name="case-1", message="m1")]
    with patch("excelmanus.bench._load_suite", return_value=("demo", cases, False)):
        with pytest.raises(ValueError, match="过滤后没有用例"):
            _run(bench.run_suite(
                "demo.json", _make_config(), tmp_path, case_ids=["nope"],
            ))


def test_main_case_and_wave_flags_passed_through(tmp_path: Path, monkeypatch) -> None:
    """--case/--wave 透传到 _run_suites。"""
    suite_path = tmp_path / "suite.json"
    suite_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "python -m excelmanus.bench",
            "--suite", str(suite_path),
            "--case", "E15",
            "--wave", "2",
            "--turn-timeout", "60",
        ],
    )

    captured_kwargs: dict = {}

    async def _fake_run_suites(paths, config, output_dir, **kwargs):
        captured_kwargs.update(kwargs)
        return 0

    with (
        patch("excelmanus.bench.load_config", return_value=_make_config()),
        patch("excelmanus.bench.setup_logging"),
        patch("excelmanus.bench._run_suites", side_effect=_fake_run_suites),
    ):
        code = _run(bench._main())

    assert code == 0
    assert captured_kwargs["case_ids"] == ["E15"]
    assert captured_kwargs["wave"] == "2"
    assert captured_kwargs["turn_timeout"] == 60.0


def test_write_case_digest(tmp_path: Path) -> None:
    case = bench.BenchCase(
        id="E99",
        name="摘要用例",
        message="帮我看看",
        tags=["wave-9", "demo"],
        expected={"lens": "测试", "review_focus": ["点一", "点二"]},
    )
    result = _make_result("E99")
    result.conversation_export = {
        "attachments": [
            {"filename": "a.xlsx", "path": "./uploads/a.xlsx", "size": 10,
             "kind": "file", "source": "fixtures/a.xlsx"}
        ],
    }
    run_file = tmp_path / "run_test_E99_abc123.json"
    path = bench._write_case_digest(
        result,
        case,
        suite_name="demo-suite",
        output_dir=tmp_path,
        run_file=run_file,
    )

    assert path == tmp_path / "conversations" / "E99.digest.md"
    text = path.read_text(encoding="utf-8")
    assert "# E99" in text
    assert "wave-9" in text
    assert "点一" in text and "点二" in text
    assert "run_test_E99_abc123.json" in text
    assert "read_excel" in text
    assert "执行完成" in text
    assert "./uploads/a.xlsx" in text
    assert "轮次 1" in text
