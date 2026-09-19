"""Bench 自动报告生成器：将 suite 运行结果 + 断言校验输出为 Markdown。

在 ``run_suite`` 完成后自动调用，生成 ``report_YYYYMMDD_hash.md``，
与 suite JSON 输出在同一目录。通过 ``--no-report`` 可关闭。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from excelmanus.bench_validator import SuiteValidationSummary


# ── 单 case 报告行 ───────────────────────────────────────


def _case_status_icon(status: str) -> str:
    if status == "ok":
        return "✅"
    if status == "error":
        return "❌"
    return "⚠️"


def _validation_badge(v: dict[str, Any] | None) -> str:
    """生成断言结果的简短 badge，如 '3/3 ✅'、'5/6 ⚠W1'（仅效率告警）或 '5/6 ❌E1'。

    severity 缺失按 error 计（兼容旧 run_*.json）。
    """
    if not v or v.get("total", 0) == 0:
        return "-"
    total = v["total"]
    passed = v["passed"]
    if passed == total:
        return f"{passed}/{total} ✅"
    errs = sum(
        1 for r in v.get("results", [])
        if not r.get("passed", True) and r.get("severity", "error") == "error"
    )
    warns = sum(
        1 for r in v.get("results", [])
        if not r.get("passed", True) and r.get("severity", "error") != "error"
    )
    if errs == 0:
        return f"{passed}/{total} ⚠W{warns}"
    suffix = f"+W{warns}" if warns else ""
    return f"{passed}/{total} ❌E{errs}{suffix}"


def _format_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.0f}K"
    return str(n)


# ── case 明细表 ──────────────────────────────────────────


def _render_case_table(cases: list[dict[str, Any]]) -> str:
    """渲染用例明细表格。"""
    lines = [
        "| ID | 状态 | 断言 | 耗时 | 迭代 | 工具 | 失败/root | Token | LLM | 技能 |",
        "|:---|:-----|:-----|-----:|-----:|-----:|-----:|------:|----:|:-----|",
    ]
    for c in cases:
        meta = c.get("meta", {})
        exe = c.get("execution", {})
        stats = c.get("stats", {})
        validation = c.get("validation")

        cid = meta.get("case_id", "?")
        status = _case_status_icon(exe.get("status", "?"))
        badge = _validation_badge(validation)
        dur = f"{exe.get('duration_seconds', 0):.1f}s"
        iters = str(exe.get("iterations", 0))
        tools = str(stats.get("tool_call_count", 0))
        fails = str(stats.get("tool_failures", 0))
        distinct = stats.get("distinct_tool_errors")
        if distinct is not None:
            fails = f"{fails}/{distinct}"
        tokens = _format_tokens(stats.get("total_tokens", 0))
        llm = str(stats.get("llm_call_count", 0))
        skills = ", ".join(exe.get("skills_used", [])) or "-"

        lines.append(
            f"| {cid} | {status} | {badge} | {dur} | {iters} | {tools} | {fails} | {tokens} | {llm} | {skills} |"
        )
    return "\n".join(lines)


# ── 断言违规明细 ─────────────────────────────────────────


def _render_violations(cases: list[dict[str, Any]]) -> str:
    """渲染断言违规明细（仅有失败的 case），error 与 warn-only 效率告警分开展示。"""
    err_sections: list[str] = []
    warn_sections: list[str] = []
    for c in cases:
        validation = c.get("validation")
        if not validation or validation.get("failed", 0) == 0:
            continue
        meta = c.get("meta", {})
        cid = meta.get("case_id", "?")
        cname = meta.get("case_name", "")

        errs = [r for r in validation.get("results", []) if not r.get("passed", True) and r.get("severity", "error") == "error"]
        warns = [r for r in validation.get("results", []) if not r.get("passed", True) and r.get("severity", "error") != "error"]

        def _block(items: list[dict[str, Any]]) -> list[str]:
            lines: list[str] = []
            for r in items:
                rule = r.get("rule", "?")
                msg = r.get("message", "")
                expected = r.get("expected", "")
                actual = r.get("actual", "")
                icon = "⚠" if r.get("severity", "error") != "error" else "❌"
                lines.append(f"- {icon} **`{rule}`**: {msg}")
                if expected:
                    lines.append(f"  - 期望: `{expected}`")
                if actual:
                    actual_str = str(actual)
                    if len(actual_str) > 120:
                        actual_str = actual_str[:120] + "..."
                    lines.append(f"  - 实际: `{actual_str}`")
            return lines

        title = f"### {cid}" + (f" — {cname}" if cname else "")
        if errs:
            err_sections.append("\n".join([title] + _block(errs)))
        if warns:
            warn_sections.append("\n".join([title] + _block(warns)))

    parts: list[str] = []
    if err_sections:
        parts.append("## 断言违规\n\n" + "\n\n".join(err_sections))
    if warn_sections:
        parts.append("## 效率告警（warn-only，不影响通过）\n\n" + "\n\n".join(warn_sections))
    return "\n\n".join(parts)


# ── 质量检查 ─────────────────────────────────────────────


def _render_quality_checks(cases: list[dict[str, Any]]) -> str:
    """渲染质量检查段落。"""
    lines = ["## 质量检查"]

    # 空承诺检测
    empty_promise_count = 0
    for c in cases:
        llm_calls = c.get("artifacts", {}).get("llm_calls", [])
        if llm_calls:
            first_resp = llm_calls[0].get("response", {})
            content = first_resp.get("content") or ""
            tool_calls = first_resp.get("tool_calls") or []
            if content.strip() and not tool_calls:
                empty_promise_count += 1
    lines.append(f"- **空承诺检测**: {empty_promise_count} 例")

    # 工具失败统计
    total_tool_failures = sum(
        c.get("stats", {}).get("tool_failures", 0) for c in cases
    )
    total_model_failures = sum(
        c.get("stats", {}).get("model_tool_failures", 0) for c in cases
    )
    total_distinct = sum(
        c.get("stats", {}).get("distinct_tool_errors", 0) for c in cases
    )
    lines.append(
        f"- **工具调用失败**: {total_tool_failures} 例"
        f"（模型可见 {total_model_failures}，root {total_distinct}）"
    )

    # 用例失败统计
    case_errors = sum(
        1 for c in cases if c.get("execution", {}).get("status") != "ok"
    )
    lines.append(f"- **用例执行失败**: {case_errors} 例")

    # 推理质量统计
    total_silent = 0
    total_reasoned = 0
    total_reasoning_chars = 0
    total_mismatch = 0
    total_nudge = 0
    for c in cases:
        rm = c.get("stats", {}).get("reasoning_metrics", {})
        total_silent += rm.get("silent_call_count", 0)
        total_reasoned += rm.get("reasoned_call_count", 0)
        total_reasoning_chars += rm.get("reasoning_chars_total", 0)
        total_mismatch += rm.get("level_mismatch_count", 0)
        total_nudge += rm.get("upgrade_nudge_count", 0)
    total_calls = total_silent + total_reasoned
    if total_calls > 0:
        silent_rate = total_silent / total_calls * 100
        avg_chars = total_reasoning_chars / max(1, total_reasoned)
        lines.append(f"- **沉默调用率**: {silent_rate:.1f}% ({total_silent}/{total_calls})")
        lines.append(f"- **平均推理字符/调用**: {avg_chars:.0f} chars")
        if total_mismatch > 0 or total_nudge > 0:
            lines.append(f"- **推理级别不匹配**: {total_mismatch} 次")
            lines.append(f"- **推理升级提醒**: {total_nudge} 次")

    return "\n".join(lines)


# ── 主生成函数 ────────────────────────────────────────────


def generate_suite_report(
    suite_summary: dict[str, Any],
    *,
    suite_validation: SuiteValidationSummary | None = None,
) -> str:
    """从 suite 汇总 dict 生成完整 Markdown 报告文本。

    Args:
        suite_summary: ``_save_suite_summary`` 产出的 dict 结构。
        suite_validation: 可选的 suite 级断言校验汇总。

    Returns:
        完整的 Markdown 报告字符串。
    """
    meta = suite_summary.get("meta", {})
    stats = suite_summary.get("stats", {})
    execution = suite_summary.get("execution", {})
    cases = suite_summary.get("artifacts", {}).get("cases", [])
    timestamp = suite_summary.get("timestamp", datetime.now(timezone.utc).isoformat())

    suite_name = meta.get("suite_name", "Unknown Suite")
    case_count = meta.get("case_count", len(cases))

    # 从首个 case 提取模型信息
    model = ""
    if cases:
        model = cases[0].get("meta", {}).get("active_model", "")
        if not model:
            model = cases[0].get("meta", {}).get("config_snapshot", {}).get("model", "")

    # ── 头部
    parts: list[str] = [
        f"# Bench 报告: {suite_name}",
        f"> {timestamp} | 模型: {model or '?'} | {case_count} 用例",
        "",
    ]

    # ── 总览表
    total_tokens = stats.get("total_tokens", 0)
    total_duration = stats.get("total_duration_seconds", 0)
    total_tool_calls = stats.get("tool_call_count", 0)
    total_tool_failures = stats.get("tool_failures", 0)
    status = execution.get("status", "?")

    ok_count = sum(
        1 for c in cases if c.get("execution", {}).get("status") == "ok"
    )
    fail_count = case_count - ok_count

    parts.append("## 总览")
    parts.append("")
    parts.append("| 指标 | 值 |")
    parts.append("|:-----|:---|")
    parts.append(f"| 通过/总数 | {ok_count}/{case_count} ({ok_count / case_count * 100:.1f}%) |" if case_count else "| 通过/总数 | 0/0 |")

    if suite_validation and suite_validation.total_assertions > 0:
        parts.append(
            f"| 断言通过率 | {suite_validation.passed}/{suite_validation.total_assertions} ({suite_validation.pass_rate}%) |"
        )
        if suite_validation.errors or suite_validation.warnings:
            parts.append(
                f"| 其中 | error {suite_validation.errors} / warning {suite_validation.warnings} |"
            )

    parts.append(f"| 总 Token | {_format_tokens(total_tokens)} |")
    parts.append(f"| 总耗时 | {total_duration:.1f}s |")
    model_failures = stats.get("model_tool_failures", total_tool_failures)
    internal_failures = stats.get(
        "internal_tool_failures", total_tool_failures - model_failures
    )
    parts.append(
        f"| 工具调用 | {total_tool_calls} (失败 {total_tool_failures}，"
        f"模型可见 {model_failures}，root {stats.get('distinct_tool_errors', 0)}，"
        f"内层 {internal_failures}) |"
    )
    parts.append(f"| 执行状态 | {status} |")

    if case_count > 0:
        parts.append(f"| 平均每题 Token | {_format_tokens(total_tokens // case_count)} |")
        parts.append(f"| 平均每题耗时 | {total_duration / case_count:.1f}s |")
    parts.append("")

    # ── 用例明细
    if cases:
        parts.append("## 用例明细")
        parts.append("")
        parts.append(_render_case_table(cases))
        parts.append("")

    # ── 断言违规
    violations = _render_violations(cases)
    if violations:
        parts.append(violations)
        parts.append("")

    # ── 质量检查
    if cases:
        parts.append(_render_quality_checks(cases))
        parts.append("")

    return "\n".join(parts)


def save_suite_report(
    suite_summary: dict[str, Any],
    output_dir: Path,
    *,
    suite_validation: SuiteValidationSummary | None = None,
) -> Path:
    """生成并保存 Markdown 报告到文件。

    Returns:
        报告文件路径。
    """
    report_text = generate_suite_report(
        suite_summary, suite_validation=suite_validation,
    )
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    filename = f"report_{ts}_{short_id}.md"
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / filename
    filepath.write_text(report_text, encoding="utf-8")
    return filepath
