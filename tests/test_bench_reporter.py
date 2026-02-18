"""bench_reporter Markdown 报告生成器的单元测试。"""

from __future__ import annotations

import pytest
from pathlib import Path

from excelmanus.bench_reporter import generate_suite_report, save_suite_report
from excelmanus.bench_validator import SuiteValidationSummary


# ── 测试数据工厂 ─────────────────────────────────────────


def _make_suite_summary(
    *,
    suite_name: str = "测试套件",
    case_count: int = 2,
    status: str = "ok",
    total_tokens: int = 24000,
    total_duration: float = 10.5,
    tool_call_count: int = 6,
    tool_failures: int = 0,
    cases: list[dict] | None = None,
) -> dict:
    """构造一个最小化的 suite summary dict。"""
    if cases is None:
        cases = [
            {
                "meta": {
                    "case_id": "case_read",
                    "case_name": "读取前10行",
                    "active_model": "gpt-test",
                    "config_snapshot": {"model": "gpt-test"},
                },
                "execution": {
                    "status": "ok",
                    "iterations": 2,
                    "duration_seconds": 5.2,
                    "skills_used": ["data_basic"],
                },
                "stats": {
                    "tool_call_count": 3,
                    "tool_failures": 0,
                    "total_tokens": 12000,
                    "llm_call_count": 2,
                },
                "artifacts": {
                    "llm_calls": [
                        {"response": {"content": "", "tool_calls": [{"function": {"name": "read_excel"}}]}},
                        {"response": {"content": "完成", "tool_calls": None}},
                    ],
                },
                "result": {"reply": "读取完成"},
            },
            {
                "meta": {
                    "case_id": "case_greeting",
                    "case_name": "问候",
                    "active_model": "gpt-test",
                    "config_snapshot": {"model": "gpt-test"},
                },
                "execution": {
                    "status": "ok",
                    "iterations": 1,
                    "duration_seconds": 5.3,
                    "skills_used": [],
                },
                "stats": {
                    "tool_call_count": 0,
                    "tool_failures": 0,
                    "total_tokens": 12000,
                    "llm_call_count": 1,
                },
                "artifacts": {
                    "llm_calls": [
                        {"response": {"content": "你好！", "tool_calls": None}},
                    ],
                },
                "result": {"reply": "你好！"},
            },
        ]
    return {
        "schema_version": 3,
        "kind": "suite_summary",
        "timestamp": "2026-02-18T03:00:00+00:00",
        "meta": {
            "suite_name": suite_name,
            "suite_path": "bench/cases/test.json",
            "case_count": case_count,
        },
        "execution": {
            "concurrency": 1,
            "status": status,
        },
        "artifacts": {
            "case_log_files": [],
            "cases": cases,
        },
        "result": {
            "failed_case_ids": [],
        },
        "stats": {
            "total_tokens": total_tokens,
            "total_prompt_tokens": total_tokens // 2,
            "total_completion_tokens": total_tokens // 2,
            "total_duration_seconds": total_duration,
            "average_iterations": 1.5,
            "tool_call_count": tool_call_count,
            "tool_failures": tool_failures,
        },
    }


# ── generate_suite_report ────────────────────────────────


class TestGenerateSuiteReport:
    def test_basic_report_structure(self):
        summary = _make_suite_summary()
        report = generate_suite_report(summary)

        assert "# Bench 报告: 测试套件" in report
        assert "## 总览" in report
        assert "## 用例明细" in report
        assert "## 质量检查" in report
        assert "case_read" in report
        assert "case_greeting" in report

    def test_report_includes_model(self):
        summary = _make_suite_summary()
        report = generate_suite_report(summary)
        assert "gpt-test" in report

    def test_report_with_validation(self):
        summary = _make_suite_summary()
        sv = SuiteValidationSummary(
            total_assertions=4,
            passed=3,
            failed=1,
            pass_rate=75.0,
            failed_cases=["case_read"],
        )
        report = generate_suite_report(summary, suite_validation=sv)
        assert "断言通过率" in report
        assert "75.0%" in report

    def test_report_with_case_validation_violations(self):
        summary = _make_suite_summary()
        # 给 case_read 添加 validation 失败
        summary["artifacts"]["cases"][0]["validation"] = {
            "total": 3,
            "passed": 2,
            "failed": 1,
            "results": [
                {"rule": "status", "passed": True},
                {"rule": "max_iterations", "passed": True},
                {
                    "rule": "expected_skill",
                    "passed": False,
                    "expected": "data_basic",
                    "actual": "data_basic",
                    "message": "路由未命中期望技能",
                },
            ],
        }
        report = generate_suite_report(summary)
        assert "## 断言违规" in report
        assert "expected_skill" in report
        assert "路由未命中" in report

    def test_report_empty_cases(self):
        summary = _make_suite_summary(cases=[], case_count=0)
        report = generate_suite_report(summary)
        assert "# Bench 报告" in report
        # 不应有用例明细表
        assert "## 用例明细" not in report

    def test_report_quality_checks(self):
        summary = _make_suite_summary()
        report = generate_suite_report(summary)
        assert "空承诺检测" in report
        assert "工具调用失败" in report
        assert "用例执行失败" in report

    def test_report_empty_promise_detection(self):
        """case_greeting 首轮有文字但无 tool_calls → 检测为空承诺"""
        summary = _make_suite_summary()
        report = generate_suite_report(summary)
        # case_greeting 的首轮响应是纯文字回复
        assert "**空承诺检测**: 1 例" in report

    def test_token_formatting(self):
        summary = _make_suite_summary(total_tokens=150000)
        report = generate_suite_report(summary)
        assert "150K" in report


# ── save_suite_report ────────────────────────────────────


class TestSaveSuiteReport:
    def test_saves_markdown_file(self, tmp_path: Path):
        summary = _make_suite_summary()
        report_path = save_suite_report(summary, tmp_path)
        assert report_path.exists()
        assert report_path.suffix == ".md"
        assert report_path.name.startswith("report_")

        content = report_path.read_text(encoding="utf-8")
        assert "# Bench 报告" in content

    def test_saves_with_validation(self, tmp_path: Path):
        summary = _make_suite_summary()
        sv = SuiteValidationSummary(
            total_assertions=2,
            passed=2,
            failed=0,
            pass_rate=100.0,
            failed_cases=[],
        )
        report_path = save_suite_report(summary, tmp_path, suite_validation=sv)
        content = report_path.read_text(encoding="utf-8")
        assert "断言通过率" in content
        assert "100.0%" in content
