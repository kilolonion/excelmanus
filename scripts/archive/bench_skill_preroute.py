#!/usr/bin/env python3
"""小模型预路由 A/B 测试脚本。

用法：
    # Phase 1: 仅测试预路由准确率（快速，不消耗主模型 token）
    python scripts/bench_skill_preroute.py

    # Phase 2: 完整 bench 对比（耗时较长）
    python scripts/bench_skill_preroute.py --full

    # 指定测试集
    python scripts/bench_skill_preroute.py --suite bench/cases/suite_skill_preroute_ab.json
"""

import asyncio
import json
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── 模型配置 ──

DEEPSEEK_CONFIG = {
    "api_key": "sk-da37ceb79edd499cbf72cd538eba87e0",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
}

GEMINI_CONFIG = {
    "api_key": "sk-30f732d2a12943caaf73355f158b698f",
    "base_url": "https://right.codes/gemini/v1beta",
    "model": "gemini-3-flash-preview",
}

# 当前主模型配置（从 .env 读取，用于 baseline 和 meta-only 组的完整 bench）
# Phase 1 不需要主模型


def load_suite(path: str) -> list[dict]:
    """加载测试集。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("cases", [])


async def run_phase1(suite_path: str) -> dict:
    """Phase 1: 仅测试预路由准确率。"""
    from excelmanus.skillpacks.pre_router import pre_route_skill

    cases = load_suite(suite_path)
    print(f"\n{'='*70}")
    print(f"Phase 1: 预路由准确率测试 ({len(cases)} 条用例)")
    print(f"{'='*70}\n")

    results = {"deepseek": [], "gemini": []}

    for case in cases:
        msg = case.get("message", "")
        expected_skill = case.get("expected", {}).get("skill")
        case_id = case.get("id", "unknown")

        # DeepSeek
        ds_result = await pre_route_skill(msg, **DEEPSEEK_CONFIG)
        ds_match = (ds_result.skill_name == expected_skill) or (
            ds_result.skill_name is None and expected_skill is None
        )
        results["deepseek"].append({
            "case_id": case_id,
            "message": msg[:60],
            "expected": expected_skill,
            "predicted": ds_result.skill_name,
            "confidence": ds_result.confidence,
            "latency_ms": round(ds_result.latency_ms, 1),
            "match": ds_match,
            "reason": ds_result.reason,
        })

        # Gemini
        gm_result = await pre_route_skill(msg, **GEMINI_CONFIG)
        gm_match = (gm_result.skill_name == expected_skill) or (
            gm_result.skill_name is None and expected_skill is None
        )
        results["gemini"].append({
            "case_id": case_id,
            "message": msg[:60],
            "expected": expected_skill,
            "predicted": gm_result.skill_name,
            "confidence": gm_result.confidence,
            "latency_ms": round(gm_result.latency_ms, 1),
            "match": gm_match,
            "reason": gm_result.reason,
        })

        # 实时输出
        ds_icon = "✓" if ds_match else "✗"
        gm_icon = "✓" if gm_match else "✗"
        print(
            f"  {case_id:30s}"
            f" | DS: {ds_icon} {str(ds_result.skill_name):20s} {ds_result.latency_ms:6.0f}ms"
            f" | GM: {gm_icon} {str(gm_result.skill_name):20s} {gm_result.latency_ms:6.0f}ms"
        )

    # 汇总
    print(f"\n{'─'*70}")
    for model_name, model_results in results.items():
        total = len(model_results)
        correct = sum(1 for r in model_results if r["match"])
        avg_latency = sum(r["latency_ms"] for r in model_results) / max(total, 1)
        avg_confidence = sum(r["confidence"] for r in model_results) / max(total, 1)
        print(
            f"  {model_name:12s}"
            f" | 准确率: {correct}/{total} ({correct/max(total,1)*100:.1f}%)"
            f" | 平均延迟: {avg_latency:.0f}ms"
            f" | 平均置信度: {avg_confidence:.2f}"
        )

    # 保存结果
    output_dir = PROJECT_ROOT / "outputs" / "skill_preroute"
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    output_path = output_dir / f"phase1_{ts}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": ts,
            "suite_path": suite_path,
            "total_cases": len(cases),
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n  结果已保存: {output_path}")
    return results


def main():
    parser = argparse.ArgumentParser(description="小模型预路由 A/B 测试")
    parser.add_argument(
        "--suite",
        default="bench/cases/suite_skill_preroute_ab.json",
        help="测试集路径",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="运行完整 bench 对比（Phase 2）",
    )
    args = parser.parse_args()

    asyncio.run(run_phase1(args.suite))

    if args.full:
        print(
            "\n⚠️  Phase 2 (完整 bench 对比) 尚未实现，"
            "需要先完成 engine.py 的预路由集成。"
        )


if __name__ == "__main__":
    main()
