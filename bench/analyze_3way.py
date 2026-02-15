#!/usr/bin/env python3
"""三模式 AB 对比分析脚本。

用法：
    python bench/analyze_3way.py outputs/bench_3way_XXXXXXXX

读取 off/ rules/ hybrid/ 三个子目录的 suite summary，
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

            case_id = meta.get("case_id", "unknown")
            suite.cases[case_id] = CaseMetrics(
                case_id=case_id,
                case_name=meta.get("case_name", ""),
                total_tokens=stats.get("total_tokens", 0),
                prompt_tokens=stats.get("total_prompt_tokens", stats.get("prompt_tokens", 0)),
                completion_tokens=stats.get("total_completion_tokens", stats.get("completion_tokens", 0)),
                iterations=execution.get("iterations", 0),
                tool_calls=stats.get("tool_call_count", 0),
                tool_failures=stats.get("tool_failures", 0),
                llm_calls=stats.get("llm_call_count", 0),
                duration_seconds=execution.get("duration_seconds", 0.0),
                status=execution.get("status", "ok"),
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
    rules: CaseMetrics | None,
    hybrid: CaseMetrics | None,
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
    for label, m in [("OFF", off), ("RULES", rules), ("HYBRID", hybrid)]:
        if m and m.status != "ok":
            status_parts.append(f"{label}={m.status}")
    if status_parts:
        print(f"     ⚠️  状态异常: {', '.join(status_parts)}")

    # 表头
    print(f"     {'指标':<12} {'OFF':>10} {'RULES':>10} {'HYBRID':>10} {'R vs OFF':>10} {'H vs OFF':>10} {'H vs R':>10}")
    print(f"     {'─'*12} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10}")

    for attr, label in metrics:
        off_val = getattr(off, attr, 0) if off else 0
        rules_val = getattr(rules, attr, 0) if rules else 0
        hybrid_val = getattr(hybrid, attr, 0) if hybrid else 0

        r_vs_off = _pct_change(off_val, rules_val)
        h_vs_off = _pct_change(off_val, hybrid_val)
        h_vs_r = _pct_change(rules_val, hybrid_val)

        print(
            f"     {label:<12} "
            f"{_format_number(off_val):>10} "
            f"{_format_number(rules_val):>10} "
            f"{_format_number(hybrid_val):>10} "
            f"{r_vs_off:>10} "
            f"{h_vs_off:>10} "
            f"{h_vs_r:>10}"
        )


def _print_suite_summary(
    suite_name: str,
    off: SuiteMetrics | None,
    rules: SuiteMetrics | None,
    hybrid: SuiteMetrics | None,
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

    print(f"     {'指标':<12} {'OFF':>10} {'RULES':>10} {'HYBRID':>10} {'R vs OFF':>10} {'H vs OFF':>10} {'H vs R':>10}")
    print(f"     {'─'*12} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10}")

    for attr, label in metrics:
        off_val = getattr(off, attr, 0) if off else 0
        rules_val = getattr(rules, attr, 0) if rules else 0
        hybrid_val = getattr(hybrid, attr, 0) if hybrid else 0

        r_vs_off = _pct_change(off_val, rules_val)
        h_vs_off = _pct_change(off_val, hybrid_val)
        h_vs_r = _pct_change(rules_val, hybrid_val)

        print(
            f"     {label:<12} "
            f"{_format_number(off_val):>10} "
            f"{_format_number(rules_val):>10} "
            f"{_format_number(hybrid_val):>10} "
            f"{r_vs_off:>10} "
            f"{h_vs_off:>10} "
            f"{h_vs_r:>10}"
        )


def _export_csv(
    output_path: Path,
    all_suites: set[str],
    off_data: dict[str, SuiteMetrics],
    rules_data: dict[str, SuiteMetrics],
    hybrid_data: dict[str, SuiteMetrics],
) -> None:
    """导出 CSV 对比表。"""
    rows: list[dict[str, str]] = []

    for suite_name in sorted(all_suites):
        off_suite = off_data.get(suite_name)
        rules_suite = rules_data.get(suite_name)
        hybrid_suite = hybrid_data.get(suite_name)

        # 收集所有用例 ID
        all_case_ids: list[str] = []
        for s in [off_suite, rules_suite, hybrid_suite]:
            if s:
                for cid in s.cases:
                    if cid not in all_case_ids:
                        all_case_ids.append(cid)

        for case_id in all_case_ids:
            off_c = off_suite.cases.get(case_id) if off_suite else None
            rules_c = rules_suite.cases.get(case_id) if rules_suite else None
            hybrid_c = hybrid_suite.cases.get(case_id) if hybrid_suite else None

            row = {
                "suite": suite_name,
                "case_id": case_id,
                "case_name": (off_c or rules_c or hybrid_c).case_name,
                "off_tokens": str(off_c.total_tokens if off_c else ""),
                "rules_tokens": str(rules_c.total_tokens if rules_c else ""),
                "hybrid_tokens": str(hybrid_c.total_tokens if hybrid_c else ""),
                "off_iterations": str(off_c.iterations if off_c else ""),
                "rules_iterations": str(rules_c.iterations if rules_c else ""),
                "hybrid_iterations": str(hybrid_c.iterations if hybrid_c else ""),
                "off_tool_calls": str(off_c.tool_calls if off_c else ""),
                "rules_tool_calls": str(rules_c.tool_calls if rules_c else ""),
                "hybrid_tool_calls": str(hybrid_c.tool_calls if hybrid_c else ""),
                "off_tool_failures": str(off_c.tool_failures if off_c else ""),
                "rules_tool_failures": str(rules_c.tool_failures if rules_c else ""),
                "hybrid_tool_failures": str(hybrid_c.tool_failures if hybrid_c else ""),
                "off_duration": f"{off_c.duration_seconds:.1f}" if off_c else "",
                "rules_duration": f"{rules_c.duration_seconds:.1f}" if rules_c else "",
                "hybrid_duration": f"{hybrid_c.duration_seconds:.1f}" if hybrid_c else "",
                "off_status": off_c.status if off_c else "",
                "rules_status": rules_c.status if rules_c else "",
                "hybrid_status": hybrid_c.status if hybrid_c else "",
                "rules_vs_off_tokens": _pct_change(
                    off_c.total_tokens if off_c else 0,
                    rules_c.total_tokens if rules_c else 0,
                ),
                "hybrid_vs_off_tokens": _pct_change(
                    off_c.total_tokens if off_c else 0,
                    hybrid_c.total_tokens if hybrid_c else 0,
                ),
                "hybrid_vs_rules_tokens": _pct_change(
                    rules_c.total_tokens if rules_c else 0,
                    hybrid_c.total_tokens if hybrid_c else 0,
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
    rules_data: dict[str, SuiteMetrics],
    hybrid_data: dict[str, SuiteMetrics],
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

    print(f"     {'指标':<12} {'OFF':>12} {'RULES':>12} {'HYBRID':>12} {'R vs OFF':>10} {'H vs OFF':>10} {'H vs R':>10}")
    print(f"     {'─'*12} {'─'*12} {'─'*12} {'─'*12} {'─'*10} {'─'*10} {'─'*10}")

    for attr, label in metrics:
        off_val = _sum_attr(off_data, attr)
        rules_val = _sum_attr(rules_data, attr)
        hybrid_val = _sum_attr(hybrid_data, attr)

        r_vs_off = _pct_change(off_val, rules_val)
        h_vs_off = _pct_change(off_val, hybrid_val)
        h_vs_r = _pct_change(rules_val, hybrid_val)

        print(
            f"     {label:<12} "
            f"{_format_number(off_val):>12} "
            f"{_format_number(rules_val):>12} "
            f"{_format_number(hybrid_val):>12} "
            f"{r_vs_off:>10} "
            f"{h_vs_off:>10} "
            f"{h_vs_r:>10}"
        )

    # 用例数统计
    off_cases = sum(len(s.cases) for s in off_data.values())
    rules_cases = sum(len(s.cases) for s in rules_data.values())
    hybrid_cases = sum(len(s.cases) for s in hybrid_data.values())
    off_errors = sum(
        sum(1 for c in s.cases.values() if c.status != "ok")
        for s in off_data.values()
    )
    rules_errors = sum(
        sum(1 for c in s.cases.values() if c.status != "ok")
        for s in rules_data.values()
    )
    hybrid_errors = sum(
        sum(1 for c in s.cases.values() if c.status != "ok")
        for s in hybrid_data.values()
    )

    print(f"\n     用例总数:  OFF={off_cases}  RULES={rules_cases}  HYBRID={hybrid_cases}")
    print(f"     异常用例:  OFF={off_errors}  RULES={rules_errors}  HYBRID={hybrid_errors}")


# ── 主入口 ────────────────────────────────────────────────

def main(base_dir: str) -> None:
    """主分析流程。"""
    base = Path(base_dir)

    off_dir = base / "off"
    rules_dir = base / "rules"
    hybrid_dir = base / "hybrid"

    # 检查目录存在
    missing = []
    for label, d in [("off", off_dir), ("rules", rules_dir), ("hybrid", hybrid_dir)]:
        if not d.exists():
            missing.append(label)
    if missing:
        print(f"❌ 缺少目录: {', '.join(missing)}")
        print(f"   期望结构: {base}/{{off,rules,hybrid}}/")
        sys.exit(1)

    # 加载数据
    off_data = _load_mode_results(off_dir)
    rules_data = _load_mode_results(rules_dir)
    hybrid_data = _load_mode_results(hybrid_dir)

    if not off_data and not rules_data and not hybrid_data:
        print("❌ 未找到任何 suite_summary JSON 文件")
        sys.exit(1)

    # 收集所有套件名
    all_suites = set(off_data.keys()) | set(rules_data.keys()) | set(hybrid_data.keys())

    print("\n" + "═" * 80)
    print("  🔬 三模式 AB 对比分析报告")
    print("═" * 80)
    print(f"  数据目录: {base}")
    print(f"  套件数量: {len(all_suites)}")
    print(f"  模式: OFF / RULES（仅规则） / HYBRID（规则+小模型）")

    # 逐套件逐用例对比
    for suite_name in sorted(all_suites):
        off_suite = off_data.get(suite_name)
        rules_suite = rules_data.get(suite_name)
        hybrid_suite = hybrid_data.get(suite_name)

        print(f"\n{'─' * 80}")
        print(f"  📦 套件: {suite_name}")
        print(f"{'─' * 80}")

        # 收集所有用例 ID（保持顺序）
        all_case_ids: list[str] = []
        for s in [off_suite, rules_suite, hybrid_suite]:
            if s:
                for cid in s.cases:
                    if cid not in all_case_ids:
                        all_case_ids.append(cid)

        for case_id in all_case_ids:
            off_c = off_suite.cases.get(case_id) if off_suite else None
            rules_c = rules_suite.cases.get(case_id) if rules_suite else None
            hybrid_c = hybrid_suite.cases.get(case_id) if hybrid_suite else None
            name = (off_c or rules_c or hybrid_c).case_name

            _print_case_comparison(case_id, name, off_c, rules_c, hybrid_c)

        # 套件汇总
        _print_suite_summary(suite_name, off_suite, rules_suite, hybrid_suite)

    # 全局汇总
    _print_global_summary(off_data, rules_data, hybrid_data)

    # 导出 CSV
    csv_path = base / "comparison_report.csv"
    _export_csv(csv_path, all_suites, off_data, rules_data, hybrid_data)

    print("\n" + "═" * 80)
    print("  ✅ 分析完成")
    print("═" * 80)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python bench/analyze_3way.py <输出目录>")
        print("示例: python bench/analyze_3way.py outputs/bench_3way_20260215T120000")
        sys.exit(1)
    main(sys.argv[1])
