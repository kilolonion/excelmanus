#!/usr/bin/env python3
"""Phase 2 全量 A/B 测试脚本：对比 4 种预路由策略的 agent 表现。

通过调用 excelmanus.bench.run_suite 运行完整 agent 循环，
分别在 A_baseline / B_meta_only / C_deepseek / D_gemini 四组配置下
执行指定 suite，收集 tokens、耗时、成功率、工具调用数等指标，
最终输出汇总对比表和 comparison.json。

用法示例：
    # 全部组 × 全部 suite × 3 次
    python scripts/bench_phase2_ab.py

    # 只跑 A 和 C 组
    python scripts/bench_phase2_ab.py --groups A_baseline C_deepseek

    # 自定义 suite 和重复次数
    python scripts/bench_phase2_ab.py --suites bench/cases/suite_phase2_data.json --runs 5

    # 并发执行 case
    python scripts/bench_phase2_ab.py --concurrency 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from excelmanus.bench import BenchResult, run_suite  # noqa: E402
from excelmanus.config import ExcelManusConfig, load_config  # noqa: E402

# ── 实验组定义 ────────────────────────────────────────────

GROUPS: dict[str, dict[str, Any]] = {
    "A_baseline": {
        "skill_preroute_mode": "off",
        "auto_activate_default_skill": True,
    },
    "B_meta_only": {
        "skill_preroute_mode": "meta_only",
        "auto_activate_default_skill": False,
    },
    "C_deepseek": {
        "skill_preroute_mode": "deepseek",
        "auto_activate_default_skill": False,
        "skill_preroute_api_key": "sk-da37ceb79edd499cbf72cd538eba87e0",
        "skill_preroute_base_url": "https://api.deepseek.com/v1",
        "skill_preroute_model": "deepseek-chat",
    },
    "D_gemini": {
        "skill_preroute_mode": "gemini",
        "auto_activate_default_skill": False,
        "skill_preroute_api_key": "sk-30f732d2a12943caaf73355f158b698f",
        "skill_preroute_base_url": "https://right.codes/gemini/v1beta",
        "skill_preroute_model": "gemini-3-flash-preview",
    },
}

DEFAULT_SUITES = [
    "bench/cases/suite_phase2_data.json",
    "bench/cases/suite_phase2_visual.json",
    "bench/cases/suite_phase2_file_code.json",
]

# ── 工具函数 ──────────────────────────────────────────────


def _suite_short_name(suite_path: str) -> str:
    """从 suite 路径提取短名称，如 suite_phase2_data。"""
    return Path(suite_path).stem


def _make_group_config(base: ExcelManusConfig, overrides: dict[str, Any]) -> ExcelManusConfig:
    """用 dataclasses.replace 创建实验组的修改版 config。"""
    return replace(base, **overrides)


def _extract_metrics(results: list[BenchResult]) -> dict[str, Any]:
    """从一次 run_suite 的结果中提取关键指标。"""
    n = len(results)
    if n == 0:
        return {
            "case_count": 0,
            "total_tokens": 0,
            "avg_tokens": 0,
            "total_duration": 0.0,
            "avg_duration": 0.0,
            "success_count": 0,
            "success_rate": 0.0,
            "total_tool_calls": 0,
            "avg_tool_calls": 0.0,
        }
    total_tokens = sum(r.total_tokens for r in results)
    total_duration = sum(r.duration_seconds for r in results)
    success_count = sum(1 for r in results if r.status == "ok")
    total_tool_calls = sum(len(r.tool_calls) for r in results)
    return {
        "case_count": n,
        "total_tokens": total_tokens,
        "avg_tokens": total_tokens / n,
        "total_duration": round(total_duration, 2),
        "avg_duration": round(total_duration / n, 2),
        "success_count": success_count,
        "success_rate": round(success_count / n * 100, 1),
        "total_tool_calls": total_tool_calls,
        "avg_tool_calls": round(total_tool_calls / n, 2),
    }


def _aggregate_runs(run_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合多次 run 的指标，计算平均值。"""
    n = len(run_metrics)
    if n == 0:
        return {"runs": 0}
    return {
        "runs": n,
        "case_count": run_metrics[0]["case_count"],
        "avg_tokens": round(sum(m["avg_tokens"] for m in run_metrics) / n, 1),
        "avg_duration": round(sum(m["avg_duration"] for m in run_metrics) / n, 2),
        "avg_success_rate": round(sum(m["success_rate"] for m in run_metrics) / n, 1),
        "avg_tool_calls": round(sum(m["avg_tool_calls"] for m in run_metrics) / n, 2),
    }


# ── 汇总表打印 ───────────────────────────────────────────


def _print_summary(
    all_data: dict[str, dict[str, dict[str, Any]]],
    groups: list[str],
    suites: list[str],
) -> None:
    """用 rich.table.Table 打印汇总对比表。"""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    # 总览表
    console.print("\n[bold cyan]═══ Phase 2 A/B 总览 ═══[/bold cyan]\n")
    overview = Table(title="各组汇总（所有 suite 平均）", show_lines=True)
    overview.add_column("组", style="bold")
    overview.add_column("平均 Tokens", justify="right")
    overview.add_column("平均耗时(s)", justify="right")
    overview.add_column("成功率(%)", justify="right")
    overview.add_column("平均工具调用", justify="right")

    for group in groups:
        group_suite_aggs = []
        for suite in suites:
            key = _suite_short_name(suite)
            if group in all_data and key in all_data[group]:
                group_suite_aggs.append(all_data[group][key])
        if not group_suite_aggs:
            overview.add_row(group, "-", "-", "-", "-")
            continue
        n = len(group_suite_aggs)
        avg_tok = round(sum(a["avg_tokens"] for a in group_suite_aggs) / n, 1)
        avg_dur = round(sum(a["avg_duration"] for a in group_suite_aggs) / n, 2)
        avg_sr = round(sum(a["avg_success_rate"] for a in group_suite_aggs) / n, 1)
        avg_tc = round(sum(a["avg_tool_calls"] for a in group_suite_aggs) / n, 2)
        overview.add_row(group, str(avg_tok), str(avg_dur), str(avg_sr), str(avg_tc))

    console.print(overview)

    # 按 suite 分组的细分表
    for suite in suites:
        key = _suite_short_name(suite)
        console.print(f"\n[bold yellow]── {key} ──[/bold yellow]")
        tbl = Table(title=key, show_lines=True)
        tbl.add_column("组", style="bold")
        tbl.add_column("Runs", justify="right")
        tbl.add_column("平均 Tokens", justify="right")
        tbl.add_column("平均耗时(s)", justify="right")
        tbl.add_column("成功率(%)", justify="right")
        tbl.add_column("平均工具调用", justify="right")

        for group in groups:
            agg = all_data.get(group, {}).get(key)
            if agg is None:
                tbl.add_row(group, "-", "-", "-", "-", "-")
                continue
            tbl.add_row(
                group,
                str(agg["runs"]),
                str(agg["avg_tokens"]),
                str(agg["avg_duration"]),
                str(agg["avg_success_rate"]),
                str(agg["avg_tool_calls"]),
            )
        console.print(tbl)


# ── 主流程 ────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 2 A/B 测试：对比 4 种预路由策略",
    )
    parser.add_argument(
        "--groups",
        nargs="+",
        default=list(GROUPS.keys()),
        choices=list(GROUPS.keys()),
        help="要运行的实验组（默认全部）",
    )
    parser.add_argument(
        "--suites",
        nargs="+",
        default=DEFAULT_SUITES,
        help="suite JSON 路径列表（默认 3 个 phase2 suite）",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="每组每 suite 重复次数（默认 3）",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="case 并发数（默认 1）",
    )
    args = parser.parse_args()

    # 加载主模型配置
    base_config = load_config()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base_output = PROJECT_ROOT / "outputs" / "phase2_ab" / timestamp

    from rich.console import Console
    console = Console()
    console.print(f"\n[bold green]Phase 2 A/B 测试启动[/bold green]")
    console.print(f"  组: {args.groups}")
    console.print(f"  Suites: {[_suite_short_name(s) for s in args.suites]}")
    console.print(f"  每组重复: {args.runs} 次")
    console.print(f"  并发: {args.concurrency}")
    console.print(f"  输出: {base_output}\n")

    # all_data[group][suite_short_name] = aggregated metrics
    all_data: dict[str, dict[str, dict[str, Any]]] = {}
    # comparison_detail 保存完整的每次 run 指标
    comparison_detail: dict[str, dict[str, list[dict[str, Any]]]] = {}

    total_start = time.monotonic()

    for group_name in args.groups:
        overrides = GROUPS[group_name]
        group_config = _make_group_config(base_config, overrides)
        all_data[group_name] = {}
        comparison_detail[group_name] = {}

        console.print(f"[bold magenta]▶ 组: {group_name}[/bold magenta]")
        console.print(f"  preroute_mode={group_config.skill_preroute_mode}, "
                       f"auto_activate={group_config.auto_activate_default_skill}")

        for suite_path in args.suites:
            suite_key = _suite_short_name(suite_path)
            run_metrics: list[dict[str, Any]] = []

            for run_idx in range(1, args.runs + 1):
                output_dir = base_output / group_name / suite_key / f"run_{run_idx}"
                output_dir.mkdir(parents=True, exist_ok=True)

                console.print(
                    f"  [cyan]{suite_key}[/cyan] run {run_idx}/{args.runs} ...",
                    end=" ",
                )
                run_start = time.monotonic()

                try:
                    results = await run_suite(
                        suite_path,
                        group_config,
                        output_dir,
                        concurrency=args.concurrency,
                        trace_enabled=False,
                    )
                    metrics = _extract_metrics(results)
                    console.print(
                        f"[green]✓[/green] {metrics['avg_tokens']:.0f} tok, "
                        f"{metrics['avg_duration']:.1f}s, "
                        f"{metrics['success_rate']}% ok, "
                        f"{metrics['avg_tool_calls']:.1f} calls "
                        f"({time.monotonic() - run_start:.0f}s)"
                    )
                except Exception as exc:
                    console.print(f"[red]✗ {exc}[/red]")
                    metrics = _extract_metrics([])
                    metrics["error"] = str(exc)

                run_metrics.append(metrics)

            aggregated = _aggregate_runs(run_metrics)
            all_data[group_name][suite_key] = aggregated
            comparison_detail[group_name][suite_key] = run_metrics

    total_elapsed = time.monotonic() - total_start

    # 打印汇总表
    _print_summary(all_data, args.groups, args.suites)

    console.print(f"\n[dim]总耗时: {total_elapsed:.0f}s[/dim]")

    # 保存 comparison.json
    comparison = {
        "schema_version": 1,
        "kind": "phase2_ab_comparison",
        "timestamp": timestamp,
        "groups": args.groups,
        "suites": [_suite_short_name(s) for s in args.suites],
        "runs_per_group": args.runs,
        "concurrency": args.concurrency,
        "total_elapsed_seconds": round(total_elapsed, 2),
        "summary": all_data,
        "detail": comparison_detail,
    }
    comparison_path = base_output / "comparison.json"
    comparison_path.parent.mkdir(parents=True, exist_ok=True)
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    console.print(f"\n[bold green]汇总已保存: {comparison_path}[/bold green]\n")


if __name__ == "__main__":
    asyncio.run(main())
