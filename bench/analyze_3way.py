#!/usr/bin/env python3
"""三模式 AB 对比分析脚本。

用法：
    python bench/analyze_3way.py outputs/bench_3way_XXXXXXXX

读取 `off + enriched + anchored`（或兼容 `off + rules + hybrid`）子目录的 suite summary，
按用例 ID 对齐生成对比表格和 CSV。
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ── 数据模型 ──────────────────────────────────────────────

@dataclass
class CaseMetrics:
    """单个用例的关键指标。"""

    case_id: str
    case_name: str
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    iterations: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    llm_calls: int = 0
    duration_seconds: float = 0.0
    status: str = "ok"
    turn_count: int = 1
    invalid_for_perf: bool = False


@dataclass
class SuiteMetrics:
    """单个套件的汇总指标。"""

    suite_name: str
    cases: dict[str, CaseMetrics] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return sum(c.total_tokens for c in self.cases.values())

    @property
    def total_iterations(self) -> int:
        return sum(c.iterations for c in self.cases.values())

    @property
    def total_tool_calls(self) -> int:
        return sum(c.tool_calls for c in self.cases.values())

    @property
    def total_tool_failures(self) -> int:
        return sum(c.tool_failures for c in self.cases.values())

    @property
    def total_duration(self) -> float:
        return sum(c.duration_seconds for c in self.cases.values())

    @property
    def invalid_for_perf_count(self) -> int:
        return sum(1 for c in self.cases.values() if c.invalid_for_perf)


# ── 解析 ──────────────────────────────────────────────────

def _load_mode_results(mode_dir: Path) -> dict[str, SuiteMetrics]:
    """从一个模式目录加载所有 suite summary。"""
    suites: dict[str, SuiteMetrics] = {}

    for json_file in sorted(mode_dir.glob("suite_*.json")):
        data = json.loads(json_file.read_text(encoding="utf-8"))
        if data.get("kind") != "suite_summary":
            continue

        suite_name = data.get("meta", {}).get("suite_name", json_file.stem)
        suite = SuiteMetrics(suite_name=suite_name)

        for case_data in data.get("artifacts", {}).get("cases", []):
            meta = case_data.get("meta", {})
            stats = case_data.get("stats", {})
            execution = case_data.get("execution", {})
            turn_count = int(meta.get("turn_count", 1) or 1)
            iterations = int(execution.get("iterations", 0) or 0)

            case_id = meta.get("case_id", "unknown")
            suite.cases[case_id] = CaseMetrics(
                case_id=case_id,
                case_name=meta.get("case_name", ""),
                total_tokens=stats.get("total_tokens", 0),
                prompt_tokens=stats.get("total_prompt_tokens", stats.get("prompt_tokens", 0)),
                completion_tokens=stats.get("total_completion_tokens", stats.get("completion_tokens", 0)),
                iterations=iterations,
                tool_calls=stats.get("tool_call_count", 0),
                tool_failures=stats.get("tool_failures", 0),
                llm_calls=stats.get("llm_call_count", 0),
                duration_seconds=execution.get("duration_seconds", 0.0),
                status=execution.get("status", "ok"),
                turn_count=turn_count,
                invalid_for_perf=iterations < turn_count,
            )

        suites[suite_name] = suite

    return suites


# ── 对比计算 ──────────────────────────────────────────────

def _pct_change(base: float, current: float) -> str:
    """计算百分比变化，返回格式化字符串。"""
    if base == 0:
        return "N/A"
    change = (current - base) / base * 100
    sign = "+" if change > 0 else ""
    return f"{sign}{change:.1f}%"


def _format_number(n: int | float) -> str:
    """格式化数字，千分位分隔。"""
    if isinstance(n, float):
        return f"{n:,.1f}"
    return f"{n:,}"


# ── 输出 ──────────────────────────────────────────────────

def _print_separator(char: str = "─", width: int = 120) -> None:
    print(char * width)


def _print_case_comparison(
    case_id: str,
    case_name: str,
    off: CaseMetrics | None,
    mode_a: CaseMetrics | None,
    mode_b: CaseMetrics | None,
    mode_a_label: str,
    mode_b_label: str,
) -> None:
    """打印单个用例的三模式对比。"""
    metrics = [
        ("total_tokens", "总 Tokens"),
        ("iterations", "迭代次数"),
        ("tool_calls", "工具调用"),
        ("tool_failures", "工具失败"),
        ("llm_calls", "LLM 调用"),
        ("duration_seconds", "耗时(s)"),
    ]

    print(f"\n  📋 {case_id}: {case_name}")
    status_parts = []
    for label, m in [("OFF", off), (mode_a_label, mode_a), (mode_b_label, mode_b)]:
        if m and m.status != "ok":
            status_parts.append(f"{label}={m.status}")
    if status_parts:
        print(f"     ⚠️  状态异常: {', '.join(status_parts)}")
    invalid_labels = []
    for label, m in [("OFF", off), (mode_a_label, mode_a), (mode_b_label, mode_b)]:
        if m and m.invalid_for_perf:
            invalid_labels.append(f"{label}(iterations={m.iterations}<turns={m.turn_count})")
    if invalid_labels:
        print(f"     🚫 invalid_for_perf: {', '.join(invalid_labels)}")

    # 表头
    print(
        f"     {'指标':<12} {'OFF':>10} {mode_a_label:>10} {mode_b_label:>10} "
        f"{'A vs OFF':>10} {'B vs OFF':>10} {'B vs A':>10}"
    )
    print(f"     {'─'*12} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10}")

    for attr, label in metrics:
        off_val = getattr(off, attr, 0) if off else 0
        mode_a_val = getattr(mode_a, attr, 0) if mode_a else 0
        mode_b_val = getattr(mode_b, attr, 0) if mode_b else 0

        a_vs_off = _pct_change(off_val, mode_a_val)
        b_vs_off = _pct_change(off_val, mode_b_val)
        b_vs_a = _pct_change(mode_a_val, mode_b_val)

        print(
            f"     {label:<12} "
            f"{_format_number(off_val):>10} "
            f"{_format_number(mode_a_val):>10} "
            f"{_format_number(mode_b_val):>10} "
            f"{a_vs_off:>10} "
            f"{b_vs_off:>10} "
            f"{b_vs_a:>10}"
        )


def _print_suite_summary(
    suite_name: str,
    off: SuiteMetrics | None,
    mode_a: SuiteMetrics | None,
    mode_b: SuiteMetrics | None,
    mode_a_label: str,
    mode_b_label: str,
) -> None:
    """打印套件级汇总。"""
    print(f"\n  📊 套件汇总: {suite_name}")
    metrics = [
        ("total_tokens", "总 Tokens"),
        ("total_iterations", "总迭代"),
        ("total_tool_calls", "总工具调用"),
        ("total_tool_failures", "总工具失败"),
        ("total_duration", "总耗时(s)"),
    ]

    print(
        f"     {'指标':<12} {'OFF':>10} {mode_a_label:>10} {mode_b_label:>10} "
        f"{'A vs OFF':>10} {'B vs OFF':>10} {'B vs A':>10}"
    )
    print(f"     {'─'*12} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10}")

    for attr, label in metrics:
        off_val = getattr(off, attr, 0) if off else 0
        mode_a_val = getattr(mode_a, attr, 0) if mode_a else 0
        mode_b_val = getattr(mode_b, attr, 0) if mode_b else 0

        a_vs_off = _pct_change(off_val, mode_a_val)
        b_vs_off = _pct_change(off_val, mode_b_val)
        b_vs_a = _pct_change(mode_a_val, mode_b_val)

        print(
            f"     {label:<12} "
            f"{_format_number(off_val):>10} "
            f"{_format_number(mode_a_val):>10} "
            f"{_format_number(mode_b_val):>10} "
            f"{a_vs_off:>10} "
            f"{b_vs_off:>10} "
            f"{b_vs_a:>10}"
        )

    off_invalid = off.invalid_for_perf_count if off else 0
    mode_a_invalid = mode_a.invalid_for_perf_count if mode_a else 0
    mode_b_invalid = mode_b.invalid_for_perf_count if mode_b else 0
    print(
        f"     invalid_for_perf  OFF={off_invalid}  {mode_a_label}={mode_a_invalid}  {mode_b_label}={mode_b_invalid}"
    )


def _export_csv(
    output_path: Path,
    all_suites: set[str],
    off_data: dict[str, SuiteMetrics],
    mode_a_data: dict[str, SuiteMetrics],
    mode_b_data: dict[str, SuiteMetrics],
    mode_a_label: str,
    mode_b_label: str,
) -> None:
    """导出 CSV 对比表。"""
    rows: list[dict[str, str]] = []

    for suite_name in sorted(all_suites):
        off_suite = off_data.get(suite_name)
        mode_a_suite = mode_a_data.get(suite_name)
        mode_b_suite = mode_b_data.get(suite_name)

        # 收集所有用例 ID
        all_case_ids: list[str] = []
        for s in [off_suite, mode_a_suite, mode_b_suite]:
            if s:
                for cid in s.cases:
                    if cid not in all_case_ids:
                        all_case_ids.append(cid)

        for case_id in all_case_ids:
            off_c = off_suite.cases.get(case_id) if off_suite else None
            mode_a_c = mode_a_suite.cases.get(case_id) if mode_a_suite else None
            mode_b_c = mode_b_suite.cases.get(case_id) if mode_b_suite else None

            row = {
                "suite": suite_name,
                "case_id": case_id,
                "case_name": (off_c or mode_a_c or mode_b_c).case_name,
                "off_tokens": str(off_c.total_tokens if off_c else ""),
                f"{mode_a_label.lower()}_tokens": str(mode_a_c.total_tokens if mode_a_c else ""),
                f"{mode_b_label.lower()}_tokens": str(mode_b_c.total_tokens if mode_b_c else ""),
                "off_iterations": str(off_c.iterations if off_c else ""),
                "off_turn_count": str(off_c.turn_count if off_c else ""),
                f"{mode_a_label.lower()}_iterations": str(mode_a_c.iterations if mode_a_c else ""),
                f"{mode_a_label.lower()}_turn_count": str(mode_a_c.turn_count if mode_a_c else ""),
                f"{mode_b_label.lower()}_iterations": str(mode_b_c.iterations if mode_b_c else ""),
                f"{mode_b_label.lower()}_turn_count": str(mode_b_c.turn_count if mode_b_c else ""),
                "off_tool_calls": str(off_c.tool_calls if off_c else ""),
                f"{mode_a_label.lower()}_tool_calls": str(mode_a_c.tool_calls if mode_a_c else ""),
                f"{mode_b_label.lower()}_tool_calls": str(mode_b_c.tool_calls if mode_b_c else ""),
                "off_tool_failures": str(off_c.tool_failures if off_c else ""),
                f"{mode_a_label.lower()}_tool_failures": str(mode_a_c.tool_failures if mode_a_c else ""),
                f"{mode_b_label.lower()}_tool_failures": str(mode_b_c.tool_failures if mode_b_c else ""),
                "off_duration": f"{off_c.duration_seconds:.1f}" if off_c else "",
                f"{mode_a_label.lower()}_duration": f"{mode_a_c.duration_seconds:.1f}" if mode_a_c else "",
                f"{mode_b_label.lower()}_duration": f"{mode_b_c.duration_seconds:.1f}" if mode_b_c else "",
                "off_status": off_c.status if off_c else "",
                "off_invalid_for_perf": str(bool(off_c.invalid_for_perf) if off_c else ""),
                f"{mode_a_label.lower()}_status": mode_a_c.status if mode_a_c else "",
                f"{mode_a_label.lower()}_invalid_for_perf": str(bool(mode_a_c.invalid_for_perf) if mode_a_c else ""),
                f"{mode_b_label.lower()}_status": mode_b_c.status if mode_b_c else "",
                f"{mode_b_label.lower()}_invalid_for_perf": str(bool(mode_b_c.invalid_for_perf) if mode_b_c else ""),
                f"{mode_a_label.lower()}_vs_off_tokens": _pct_change(
                    off_c.total_tokens if off_c else 0,
                    mode_a_c.total_tokens if mode_a_c else 0,
                ),
                f"{mode_b_label.lower()}_vs_off_tokens": _pct_change(
                    off_c.total_tokens if off_c else 0,
                    mode_b_c.total_tokens if mode_b_c else 0,
                ),
                f"{mode_b_label.lower()}_vs_{mode_a_label.lower()}_tokens": _pct_change(
                    mode_a_c.total_tokens if mode_a_c else 0,
                    mode_b_c.total_tokens if mode_b_c else 0,
                ),
            }
            rows.append(row)

    if not rows:
        print("  ⚠️  无数据可导出")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  📄 CSV 已导出: {output_path}")


# ── 全局汇总 ──────────────────────────────────────────────

def _print_global_summary(
    off_data: dict[str, SuiteMetrics],
    mode_a_data: dict[str, SuiteMetrics],
    mode_b_data: dict[str, SuiteMetrics],
    mode_a_label: str,
    mode_b_label: str,
) -> None:
    """打印全局汇总（所有套件合计）。"""
    def _sum_attr(data: dict[str, SuiteMetrics], attr: str) -> int | float:
        total = 0
        for s in data.values():
            total += getattr(s, attr, 0)
        return total

    print("\n" + "═" * 80)
    print("  🏆 全局汇总（所有套件合计）")
    print("═" * 80)

    metrics = [
        ("total_tokens", "总 Tokens"),
        ("total_iterations", "总迭代"),
        ("total_tool_calls", "总工具调用"),
        ("total_tool_failures", "总工具失败"),
        ("total_duration", "总耗时(s)"),
    ]

    print(
        f"     {'指标':<12} {'OFF':>12} {mode_a_label:>12} {mode_b_label:>12} "
        f"{'A vs OFF':>10} {'B vs OFF':>10} {'B vs A':>10}"
    )
    print(f"     {'─'*12} {'─'*12} {'─'*12} {'─'*12} {'─'*10} {'─'*10} {'─'*10}")

    for attr, label in metrics:
        off_val = _sum_attr(off_data, attr)
        mode_a_val = _sum_attr(mode_a_data, attr)
        mode_b_val = _sum_attr(mode_b_data, attr)

        a_vs_off = _pct_change(off_val, mode_a_val)
        b_vs_off = _pct_change(off_val, mode_b_val)
        b_vs_a = _pct_change(mode_a_val, mode_b_val)

        print(
            f"     {label:<12} "
            f"{_format_number(off_val):>12} "
            f"{_format_number(mode_a_val):>12} "
            f"{_format_number(mode_b_val):>12} "
            f"{a_vs_off:>10} "
            f"{b_vs_off:>10} "
            f"{b_vs_a:>10}"
        )

    # 用例数统计
    off_cases = sum(len(s.cases) for s in off_data.values())
    mode_a_cases = sum(len(s.cases) for s in mode_a_data.values())
    mode_b_cases = sum(len(s.cases) for s in mode_b_data.values())
    off_errors = sum(
        sum(1 for c in s.cases.values() if c.status != "ok")
        for s in off_data.values()
    )
    mode_a_errors = sum(
        sum(1 for c in s.cases.values() if c.status != "ok")
        for s in mode_a_data.values()
    )
    mode_b_errors = sum(
        sum(1 for c in s.cases.values() if c.status != "ok")
        for s in mode_b_data.values()
    )
    off_invalid = sum(s.invalid_for_perf_count for s in off_data.values())
    mode_a_invalid = sum(s.invalid_for_perf_count for s in mode_a_data.values())
    mode_b_invalid = sum(s.invalid_for_perf_count for s in mode_b_data.values())

    print(
        f"\n     用例总数:  OFF={off_cases}  {mode_a_label}={mode_a_cases}  {mode_b_label}={mode_b_cases}"
    )
    print(
        f"     异常用例:  OFF={off_errors}  {mode_a_label}={mode_a_errors}  {mode_b_label}={mode_b_errors}"
    )
    print(
        f"     invalid_for_perf:  OFF={off_invalid}  {mode_a_label}={mode_a_invalid}  {mode_b_label}={mode_b_invalid}"
    )


# ── 主入口 ────────────────────────────────────────────────

def main(base_dir: str) -> None:
    """主分析流程。"""
    base = Path(base_dir)

    off_dir = base / "off"
    mode_a_label = "ENRICHED"
    mode_b_label = "ANCHORED"
    mode_a_dir = base / "enriched"
    mode_b_dir = base / "anchored"
    if not (mode_a_dir.exists() and mode_b_dir.exists()):
        # 兼容旧目录结构
        mode_a_label = "RULES"
        mode_b_label = "HYBRID"
        mode_a_dir = base / "rules"
        mode_b_dir = base / "hybrid"

    missing = []
    for label, d in [("off", off_dir), (mode_a_label.lower(), mode_a_dir), (mode_b_label.lower(), mode_b_dir)]:
        if not d.exists():
            missing.append(label)
    if missing:
        print(f"❌ 缺少目录: {', '.join(missing)}")
        print(f"   期望结构: {base}/{{off,enriched,anchored}} 或 {base}/{{off,rules,hybrid}}")
        sys.exit(1)

    # 加载数据
    off_data = _load_mode_results(off_dir)
    mode_a_data = _load_mode_results(mode_a_dir)
    mode_b_data = _load_mode_results(mode_b_dir)

    if not off_data and not mode_a_data and not mode_b_data:
        print("❌ 未找到任何 suite_summary JSON 文件")
        sys.exit(1)

    # 收集所有套件名
    all_suites = set(off_data.keys()) | set(mode_a_data.keys()) | set(mode_b_data.keys())

    print("\n" + "═" * 80)
    print("  🔬 三模式 AB 对比分析报告")
    print("═" * 80)
    print(f"  数据目录: {base}")
    print(f"  套件数量: {len(all_suites)}")
    print(f"  模式: OFF / {mode_a_label} / {mode_b_label}")

    # 逐套件逐用例对比
    for suite_name in sorted(all_suites):
        off_suite = off_data.get(suite_name)
        mode_a_suite = mode_a_data.get(suite_name)
        mode_b_suite = mode_b_data.get(suite_name)

        print(f"\n{'─' * 80}")
        print(f"  📦 套件: {suite_name}")
        print(f"{'─' * 80}")

        # 收集所有用例 ID（保持顺序）
        all_case_ids: list[str] = []
        for s in [off_suite, mode_a_suite, mode_b_suite]:
            if s:
                for cid in s.cases:
                    if cid not in all_case_ids:
                        all_case_ids.append(cid)

        for case_id in all_case_ids:
            off_c = off_suite.cases.get(case_id) if off_suite else None
            mode_a_c = mode_a_suite.cases.get(case_id) if mode_a_suite else None
            mode_b_c = mode_b_suite.cases.get(case_id) if mode_b_suite else None
            name = (off_c or mode_a_c or mode_b_c).case_name

            _print_case_comparison(
                case_id,
                name,
                off_c,
                mode_a_c,
                mode_b_c,
                mode_a_label=mode_a_label,
                mode_b_label=mode_b_label,
            )

        # 套件汇总
        _print_suite_summary(
            suite_name,
            off_suite,
            mode_a_suite,
            mode_b_suite,
            mode_a_label=mode_a_label,
            mode_b_label=mode_b_label,
        )

    # 全局汇总
    _print_global_summary(
        off_data,
        mode_a_data,
        mode_b_data,
        mode_a_label=mode_a_label,
        mode_b_label=mode_b_label,
    )

    # 导出 CSV
    csv_path = base / "comparison_report.csv"
    _export_csv(
        csv_path,
        all_suites,
        off_data,
        mode_a_data,
        mode_b_data,
        mode_a_label=mode_a_label,
        mode_b_label=mode_b_label,
    )

    print("\n" + "═" * 80)
    print("  ✅ 分析完成")
    print("═" * 80)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python bench/analyze_3way.py <输出目录>")
        print("示例: python bench/analyze_3way.py outputs/bench_3way_20260215T120000")
        sys.exit(1)
    main(sys.argv[1])
