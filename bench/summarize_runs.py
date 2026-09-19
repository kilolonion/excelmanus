"""汇总一批 run_*.json 的效率指标（Markdown 表），用于跑次间对比。

用法：
    python bench/summarize_runs.py "outputs/realistic/run_*.json"
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path


def summarize(pattern: str) -> str:
    rows = [
        "| case | 状态 | 断言 | 耗时 s | LLM | 工具 | 失败 | 首次 prompt tok | 累计 prompt tok | 末次 msgs |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    tot = {"dur": 0.0, "llm": 0, "tools": 0, "fails": 0, "prompt": 0, "n": 0, "ok": 0, "v_pass": 0, "v_total": 0}
    for path in sorted(glob.glob(pattern)):
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        calls = d["artifacts"].get("llm_calls") or [c for t in d.get("turns", []) for c in t.get("llm_calls", [])]
        first = ((calls[0].get("response") or {}).get("usage") or {}).get("prompt_tokens", 0) if calls else 0
        last_msgs = len((calls[-1].get("request") or {}).get("messages", [])) if calls else 0
        s, e, m = d["stats"], d["execution"], d["meta"]
        v = d.get("validation") or {}
        badge = f"{v.get('passed', 0)}/{v.get('total', 0)}" if v.get("total") else "-"
        if v.get("errors") or v.get("warnings"):
            badge += f" (E{v.get('errors', 0)} W{v.get('warnings', 0)})"
        rows.append(
            f"| {m['case_id']} | {e['status']} | {badge} | {e['duration_seconds']:.0f} | {s['llm_call_count']} | "
            f"{s['tool_call_count']} | {s['tool_failures']} | {first:,} | {s['prompt_tokens']:,} | {last_msgs} |"
        )
        tot["dur"] += e["duration_seconds"]
        tot["llm"] += s["llm_call_count"]
        tot["tools"] += s["tool_call_count"]
        tot["fails"] += s["tool_failures"]
        tot["prompt"] += s["prompt_tokens"]
        tot["n"] += 1
        tot["ok"] += e["status"] == "ok"
        tot["v_pass"] += v.get("passed", 0)
        tot["v_total"] += v.get("total", 0)
    if tot["n"]:
        rows.append(
            f"| **合计 {tot['n']} 例** | ok {tot['ok']} | {tot['v_pass']}/{tot['v_total']} | {tot['dur']:.0f} | {tot['llm']} | "
            f"{tot['tools']} | {tot['fails']} | | {tot['prompt']:,} | |"
        )
    return "\n".join(rows)


if __name__ == "__main__":
    print(summarize(sys.argv[1] if len(sys.argv) > 1 else "outputs/experiential/wave-*/run_*.json"))
