#!/usr/bin/env python3
"""Bench 运行日志分析工具。

子命令：
    overview   — 每轮 LLM 调用摘要（自动识别 suite / case / 目录）
    system     — 查看某轮的 system message
    tools      — 查看某轮的 tool_calls 详情
    trace      — 查看 engine_trace
    call       — 查看某轮完整 LLM 请求/响应
    failures   — 汇总所有失败案例的错误信息
    compare    — 对比两次 suite 运行结果

用法示例：
    # 传入目录 — 自动发现 suite 文件
    python scripts/bench_inspect.py overview outputs/bench_stress_xxx/

    # 传入 suite 文件
    python scripts/bench_inspect.py overview suite_xxx.json

    # 传入 case 文件
    python scripts/bench_inspect.py overview run_xxx.json

    # suite overview 排序/过滤
    python scripts/bench_inspect.py overview outputs/bench_stress_xxx/ --sort tokens --failed-only

    # suite 级联穿透到 case
    python scripts/bench_inspect.py tools suite_xxx.json --case sb_24_23

    # 失败分析
    python scripts/bench_inspect.py failures outputs/bench_stress_xxx/

    # 对比两次运行
    python scripts/bench_inspect.py compare outputs/run_a/ outputs/run_b/

    # 其他
    python scripts/bench_inspect.py system run_xxx.json --call 3 --grep "窗口"
    python scripts/bench_inspect.py call run_xxx.json --call 3 --max-chars 5000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ── 数据加载与路径解析 ────────────────────────────────────


def _load(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"错误：文件不存在: {path}", file=sys.stderr)
        sys.exit(1)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _resolve_input(path_str: str) -> tuple[Path, dict]:
    """解析输入路径，返回 (resolved_path, data)。

    支持：
    - JSON 文件路径（直接加载）
    - 目录路径（自动发现最新的 suite 文件）
    """
    p = Path(path_str)
    if not p.exists():
        print(f"错误：路径不存在: {path_str}", file=sys.stderr)
        sys.exit(1)

    if p.is_file():
        return p, _load(str(p))

    # 目录：查找 suite 文件
    suite_files = sorted(p.glob("suite_*.json"))
    if suite_files:
        # 取最新的（按文件名排序，时间戳在名称中）
        chosen = suite_files[-1]
        print(f"[自动发现] {chosen.name}", file=sys.stderr)
        return chosen, _load(str(chosen))

    # 没有 suite，查找 global 汇总文件
    global_files = sorted(p.glob("global_*.json"))
    if global_files:
        chosen = global_files[-1]
        print(f"[自动发现] {chosen.name}", file=sys.stderr)
        return chosen, _load(str(chosen))

    # 没有 global，查找 run 文件
    run_files = sorted(p.glob("run_*.json"))
    if run_files:
        if len(run_files) == 1:
            chosen = run_files[0]
            print(f"[自动发现] {chosen.name}", file=sys.stderr)
            return chosen, _load(str(chosen))
        # 多个 run 文件，提示用户
        print(f"目录中有 {len(run_files)} 个 case 文件，未找到 suite 汇总文件。")
        print("请指定具体文件，或使用 --case 参数。")
        print(f"可用文件: {', '.join(f.name for f in run_files[:5])}...")
        sys.exit(1)

    print(f"错误：目录中未找到 JSON 日志文件: {path_str}", file=sys.stderr)
    sys.exit(1)


def _find_case_log(suite_path: Path, data: dict, case_id: str) -> tuple[Path, dict] | None:
    """从 suite 数据中查找指定 case 的日志文件。"""
    log_files = data.get("artifacts", {}).get("case_log_files", [])
    # 先从 case_log_files 列表中匹配
    for log_path in log_files:
        if case_id in log_path:
            p = Path(log_path)
            if not p.is_absolute():
                # 相对于工作区根目录
                pass
            if p.exists():
                return p, _load(str(p))

    # 回退：在 suite 同目录下查找
    parent = suite_path.parent
    candidates = list(parent.glob(f"run_*_{case_id}_*.json"))
    if candidates:
        chosen = candidates[0]
        return chosen, _load(str(chosen))

    return None


def _get_llm_calls(data: dict) -> list[dict]:
    return data.get("artifacts", {}).get("llm_calls", [])


def _get_tool_calls(data: dict) -> list[dict]:
    return data.get("artifacts", {}).get("tool_calls", [])


def _get_engine_trace(data: dict) -> list[dict]:
    return data.get("engine_trace", [])


def _is_suite(data: dict) -> bool:
    return data.get("kind") in ("suite_summary", "global_summary")


def _is_global(data: dict) -> bool:
    return data.get("kind") == "global_summary"


def _truncate(text: str, max_chars: int) -> str:
    if max_chars and len(text) > max_chars:
        return text[:max_chars] + f"\n... [截断，共 {len(text)} 字符]"
    return text


# ── overview ──────────────────────────────────────────────


def cmd_overview(args: argparse.Namespace) -> int:
    path, data = _resolve_input(args.file)

    if _is_global(data):
        return _overview_global(data)

    if _is_suite(data):
        return _overview_suite(data, args)

    return _overview_case(data)


def _overview_global(data: dict) -> int:
    """Global 汇总文件的 overview 输出。"""
    execution = data.get("execution", {})
    stats = data.get("stats", {})
    suites = data.get("suites", [])

    print(f"全局汇总: {data.get('timestamp', '?')}")
    print(f"Suite 数: {execution.get('suite_count', len(suites))}  "
          f"Suite 并发: {execution.get('suite_concurrency', '?')}  "
          f"Case 并发: {execution.get('case_concurrency', '?')}")
    print(f"总案例: {stats.get('total_cases', 0)}  "
          f"通过: {stats.get('passed', 0)}  "
          f"失败: {stats.get('failed', 0)}")
    print(f"总 Token: {stats.get('total_tokens', 0)}  "
          f"总耗时: {stats.get('total_duration_seconds', 0):.1f}s  "
          f"工具失败: {stats.get('tool_failures', 0)}")
    print()

    if suites:
        print(f"{'Suite':<30} {'案例':>4} {'通过':>4} {'失败':>4}")
        print("─" * 50)
        for s in suites:
            print(
                f"{s.get('name', '?'):<30} "
                f"{s.get('case_count', 0):>4} "
                f"{s.get('passed', 0):>4} "
                f"{s.get('failed', 0):>4}"
            )
    return 0



def _overview_suite(data: dict, args: argparse.Namespace) -> int:
    """Suite 汇总文件的 overview 输出。"""
    meta = data.get("meta", {})
    execution = data.get("execution", {})
    result = data.get("result", {})
    cases = data.get("artifacts", {}).get("cases", [])

    # 头部信息
    print(f"套件: {meta.get('suite_name', '?')}")
    print(f"路径: {meta.get('suite_path', '?')}")
    print(f"状态: {execution.get('status', '?')}  并发: {execution.get('concurrency', '?')}")
    print(f"案例数: {meta.get('case_count', len(cases))}")
    # schema v3: 从首个 case 提取模型信息
    if cases:
        first_model = cases[0].get("meta", {}).get("active_model", "")
        first_config = cases[0].get("meta", {}).get("config_snapshot", {})
        if first_model:
            print(f"模型: {first_model}")
        elif first_config.get("model"):
            print(f"模型: {first_config['model']}")
    failed = result.get("failed_case_ids", [])
    if failed:
        print(f"失败案例: {', '.join(failed)}")
    print()

    if not cases:
        suite_stats = data.get("stats", {})
        print("无案例数据（artifacts.cases 为空）。")
        if suite_stats:
            print(f"Token 合计: {suite_stats.get('total_tokens', 0)}")
            print(f"耗时合计: {suite_stats.get('total_duration_seconds', 0):.1f}s")
            print(f"工具调用: {suite_stats.get('tool_call_count', 0)} (失败 {suite_stats.get('tool_failures', 0)})")
        return 0

    # 过滤：只看失败
    failed_only = getattr(args, "failed_only", False)
    if failed_only:
        cases = [c for c in cases if c.get("execution", {}).get("status") != "ok"]
        if not cases:
            print("无失败案例。")
            return 0

    # 排序
    sort_key = getattr(args, "sort", None)
    if sort_key:
        key_map = {
            "time": lambda c: c.get("execution", {}).get("duration_seconds", 0),
            "tokens": lambda c: c.get("stats", {}).get("total_tokens", 0),
            "tools": lambda c: c.get("stats", {}).get("tool_call_count", 0),
            "iters": lambda c: c.get("execution", {}).get("iterations", 0),
            "llm": lambda c: c.get("stats", {}).get("llm_call_count", 0),
        }
        if sort_key in key_map:
            cases = sorted(cases, key=key_map[sort_key], reverse=True)

    # 案例明细表
    print(
        f"{'ID':<16} {'状态':<6} {'耗时':>6} {'迭代':>4} "
        f"{'工具':>4} {'失败':>4} {'Token':>8} {'LLM':>4}  {'写意图':<10} 技能"
    )
    print("─" * 95)

    for c in cases:
        cm = c.get("meta", {})
        ce = c.get("execution", {})
        cs = c.get("stats", {})
        skills = ", ".join(ce.get("skills_used", [])) or "-"
        status = ce.get("status", "?")
        dur = ce.get("duration_seconds", 0)
        iters = ce.get("iterations", 0)
        tools = cs.get("tool_call_count", 0)
        fails = cs.get("tool_failures", 0)
        tokens = cs.get("total_tokens", 0)
        llm = cs.get("llm_call_count", 0)
        write_hint = ce.get("write_hint", "-") or "-"
        print(
            f"{cm.get('case_id', '?'):<16} {status:<6} {dur:>5.1f}s {iters:>4} "
            f"{tools:>4} {fails:>4} {tokens:>8} {llm:>4}  {write_hint:<10} {skills}"
        )

    # 汇总行
    print("─" * 95)
    n = len(cases)
    total_time = sum(c.get("execution", {}).get("duration_seconds", 0) for c in cases)
    total_tokens = sum(c.get("stats", {}).get("total_tokens", 0) for c in cases)
    total_tools = sum(c.get("stats", {}).get("tool_call_count", 0) for c in cases)
    total_failures = sum(c.get("stats", {}).get("tool_failures", 0) for c in cases)
    total_llm = sum(c.get("stats", {}).get("llm_call_count", 0) for c in cases)
    print(
        f"{'合计':<16} {'':6} {total_time:>5.1f}s {'':>4} "
        f"{total_tools:>4} {total_failures:>4} {total_tokens:>8} {total_llm:>4}"
    )
    print()
    print(
        f"平均每题: {total_time / n:.1f}s | {total_tokens / n:.0f} tokens | "
        f"{total_tools / n:.1f} 工具调用 | {total_llm / n:.1f} LLM 调用"
    )
    return 0


def _overview_case(data: dict) -> int:
    """单个 case 日志文件的 overview 输出。"""
    llm_calls = _get_llm_calls(data)

    meta = data.get("meta", {})
    execution = data.get("execution", {})
    stats = data.get("stats", {})
    print(f"案例: {meta.get('case_id', '?')} — {meta.get('case_name', '?')}")
    print(f"模型: {meta.get('active_model', '?')}")
    # schema v3: config_snapshot
    config_snap = meta.get("config_snapshot", {})
    if config_snap:
        snap_model = config_snap.get("model", "")
        snap_router = config_snap.get("router_model", "")
        if snap_model:
            print(f"配置模型: {snap_model}" + (f"  路由模型: {snap_router}" if snap_router else ""))
    print(f"状态: {execution.get('status', '?')}  迭代: {execution.get('iterations', '?')}")
    # schema v3: write_hint
    write_hint = execution.get("write_hint", "")
    if write_hint and write_hint != "unknown":
        print(f"写意图: {write_hint}")
    print(f"耗时: {execution.get('duration_seconds', '?')}s")
    print(f"Token: {stats.get('prompt_tokens', 0)}p + {stats.get('completion_tokens', 0)}c = {stats.get('total_tokens', 0)}")
    print(f"工具调用: {stats.get('tool_call_count', 0)} (成功 {stats.get('tool_successes', 0)}, 失败 {stats.get('tool_failures', 0)})")
    print(f"LLM 调用: {len(llm_calls)} 轮")
    # schema v3: 任务/问答/审批事件摘要
    artifacts = data.get("artifacts", {})
    task_events = artifacts.get("task_events", [])
    question_events = artifacts.get("question_events", [])
    approval_events = artifacts.get("approval_events", [])
    if task_events or question_events or approval_events:
        parts = []
        if task_events:
            parts.append(f"任务事件 {len(task_events)}")
        if question_events:
            parts.append(f"问答 {len(question_events)}")
        if approval_events:
            parts.append(f"审批 {len(approval_events)}")
        print(f"交互事件: {' | '.join(parts)}")
    print()

    if not llm_calls:
        print("无 LLM 调用记录。")
        return 0

    print(f"{'#':>3}  {'msgs':>4}  {'tools':>5}  {'finish':<12}  {'tc':>2}  {'prompt':>7}  {'compl':>7}  {'total':>7}  {'ms':>7}  响应摘要")
    print("─" * 100)
    for i, c in enumerate(llm_calls):
        req = c.get("request", {})
        resp = c.get("response", {})
        msgs = req.get("messages", [])
        tool_names = req.get("tool_names", [])
        usage = resp.get("usage", {})
        tc = resp.get("tool_calls", [])
        fr = resp.get("finish_reason", "")
        duration = c.get("duration_ms", 0)

        summary = ""
        if tc:
            names = [t.get("function", {}).get("name", "?") for t in tc]
            summary = f"→ {', '.join(names)}"
        elif resp.get("content"):
            content = str(resp["content"])
            summary = content[:60].replace("\n", " ")
        elif resp.get("thinking"):
            summary = "[thinking]"

        print(
            f"{i:>3}  {len(msgs):>4}  {len(tool_names):>5}  {fr:<12}  {len(tc or []):>2}  "
            f"{usage.get('prompt_tokens', 0):>7}  {usage.get('completion_tokens', 0):>7}  "
            f"{usage.get('total_tokens', 0):>7}  {duration:>7.0f}  {summary}"
        )

    total_prompt = sum(c.get("response", {}).get("usage", {}).get("prompt_tokens", 0) for c in llm_calls)
    total_compl = sum(c.get("response", {}).get("usage", {}).get("completion_tokens", 0) for c in llm_calls)
    total_dur = sum(c.get("duration_ms", 0) for c in llm_calls)
    print("─" * 100)
    print(f"{'合计':>10}  {'':>5}  {'':>12}  {'':>2}  {total_prompt:>7}  {total_compl:>7}  {total_prompt + total_compl:>7}  {total_dur:>7.0f}")
    return 0


# ── suite 级联穿透辅助 ───────────────────────────────────


def _resolve_case_data(args: argparse.Namespace) -> tuple[Path, dict]:
    """解析输入，如果是 suite 且指定了 --case，穿透到具体 case 日志。"""
    path, data = _resolve_input(args.file)
    case_id = getattr(args, "case", None)

    if _is_suite(data):
        if not case_id:
            print("错误：对 suite 文件使用此命令需要 --case <case_id> 指定案例。", file=sys.stderr)
            print("可用案例:", file=sys.stderr)
            for c in data.get("artifacts", {}).get("cases", []):
                cid = c.get("meta", {}).get("case_id", "?")
                status = c.get("execution", {}).get("status", "?")
                print(f"  {cid} ({status})", file=sys.stderr)
            sys.exit(1)

        result = _find_case_log(path, data, case_id)
        if not result:
            print(f"错误：未找到案例 {case_id} 的日志文件。", file=sys.stderr)
            sys.exit(1)
        case_path, case_data = result
        print(f"[穿透] {case_path.name}", file=sys.stderr)
        return case_path, case_data

    return path, data


# ── system ────────────────────────────────────────────────


def cmd_system(args: argparse.Namespace) -> int:
    _, data = _resolve_case_data(args)
    llm_calls = _get_llm_calls(data)

    if not llm_calls:
        print("无 LLM 调用记录。")
        return 0

    indices = _resolve_call_indices(args.call, len(llm_calls))

    for idx in indices:
        c = llm_calls[idx]
        msgs = c.get("request", {}).get("messages", [])
        system_msgs = [
            (i, m) for i, m in enumerate(msgs) if m.get("role") == "system"
        ]

        if not system_msgs:
            print(f"call[{idx}]: 无 system message")
            continue

        for msg_idx, m in system_msgs:
            content = str(m.get("content", ""))

            if args.grep and args.grep.lower() not in content.lower():
                continue

            print(f"=== call[{idx}] system msg[{msg_idx}] ({len(content)} chars) ===")
            print(_truncate(content, args.max_chars))
            print()

    return 0


# ── tools ─────────────────────────────────────────────────


def cmd_tools(args: argparse.Namespace) -> int:
    _, data = _resolve_case_data(args)
    llm_calls = _get_llm_calls(data)

    if args.call is not None:
        indices = _resolve_call_indices(args.call, len(llm_calls))
        for idx in indices:
            c = llm_calls[idx]
            resp = c.get("response", {})
            tc_list = resp.get("tool_calls", [])
            if not tc_list:
                print(f"call[{idx}]: 响应中无 tool_calls")
                continue
            print(f"=== call[{idx}] tool_calls ({len(tc_list)}) ===")
            for j, tc in enumerate(tc_list):
                func = tc.get("function", {})
                name = func.get("name", "?")
                raw_args = func.get("arguments", "{}")
                print(f"  [{j}] {name}")
                try:
                    parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    formatted = json.dumps(parsed, ensure_ascii=False, indent=4)
                    print(f"      args: {_truncate(formatted, args.max_chars)}")
                except (json.JSONDecodeError, TypeError):
                    print(f"      args: {_truncate(str(raw_args), args.max_chars)}")

            next_idx = idx + 1
            if next_idx < len(llm_calls):
                next_msgs = llm_calls[next_idx].get("request", {}).get("messages", [])
                tool_results = [m for m in next_msgs if m.get("role") == "tool"]
                if tool_results:
                    print(f"\n  ── call[{next_idx}] 中的 tool results ──")
                    for tr in tool_results:
                        tid = tr.get("tool_call_id", "?")
                        content = str(tr.get("content", ""))
                        print(f"  tool_call_id: {tid}")
                        print(f"  result: {_truncate(content, args.max_chars)}")
                        print()
    else:
        tool_calls = _get_tool_calls(data)
        if not tool_calls:
            print("无工具调用记录。")
            return 0

        grep = args.grep.lower() if args.grep else None
        print(f"共 {len(tool_calls)} 次工具调用：\n")
        print(f"{'#':>3}  {'iter':>4}  {'tool_name':<25}  {'ok':>3}  {'ms':>7}  参数/结果摘要")
        print("─" * 90)
        for i, tc in enumerate(tool_calls):
            name = tc.get("tool_name", "?")
            ok = "✓" if tc.get("success") else "✗"
            iteration = tc.get("iteration", "?")
            dur = tc.get("duration_ms", 0)
            args_str = json.dumps(tc.get("arguments", {}), ensure_ascii=False)
            result_str = str(tc.get("result", ""))[:80].replace("\n", " ")
            error = str(tc.get("error", ""))

            line = f"{name} {args_str} {result_str} {error}"
            if grep and grep not in line.lower():
                continue

            summary = args_str[:50] if len(args_str) <= 50 else args_str[:50] + "..."
            if not tc.get("success") and error:
                summary += f" ERR: {error[:40]}"
            else:
                summary += f" → {result_str[:40]}" if result_str else ""

            print(f"{i:>3}  {iteration:>4}  {name:<25}  {ok:>3}  {dur:>7.0f}  {summary}")

    return 0


# ── trace ─────────────────────────────────────────────────


def cmd_trace(args: argparse.Namespace) -> int:
    _, data = _resolve_case_data(args)
    trace = _get_engine_trace(data)

    if not trace:
        print("无 engine_trace 记录（需要 --trace 模式运行 bench）。")
        return 0

    grep = args.grep.lower() if args.grep else None

    for i, entry in enumerate(trace):
        event = entry.get("event", "?")
        iteration = entry.get("iteration", "?")
        entry_data = entry.get("data", {})

        if grep:
            entry_str = json.dumps(entry, ensure_ascii=False).lower()
            if grep not in entry_str:
                continue

        print(f"=== trace[{i}] {event} (iter={iteration}) ===")

        if event == "system_prompts_injected":
            print(f"  prompt_count: {entry_data.get('prompt_count')}")
            print(f"  total_chars: {entry_data.get('total_chars')}")
            print(f"  skill_context_count: {entry_data.get('skill_context_count')}")
            if entry_data.get("context_error"):
                print(f"  context_error: {entry_data['context_error']}")
            for comp in entry_data.get("components", []):
                label = comp.get("label", "?")
                chars = comp.get("char_count", 0)
                truncated = " [截断]" if comp.get("truncated") else ""
                print(f"  ── {label} ({chars} chars){truncated}")
                if args.verbose:
                    content = comp.get("content", "")
                    print(_truncate(content, args.max_chars))
                    print()

        elif event == "window_perception_enrichment":
            print(f"  tool_name: {entry_data.get('tool_name')}")
            print(f"  original: {entry_data.get('original_chars')} chars")
            print(f"  enriched: {entry_data.get('enriched_chars')} chars (+{entry_data.get('added_chars')})")
            suffix = entry_data.get("enriched_suffix", "")
            if suffix:
                print(f"  增强内容: {_truncate(suffix, args.max_chars)}")

        elif event == "tool_scope_resolved":
            tools = entry_data.get("tools", [])
            print(f"  tool_count: {entry_data.get('tool_count')}")
            print(f"  tools: {', '.join(tools)}")

        else:
            print(f"  {json.dumps(entry_data, ensure_ascii=False, indent=2)[:500]}")

        print()

    return 0


# ── call ──────────────────────────────────────────────────


def cmd_call(args: argparse.Namespace) -> int:
    _, data = _resolve_case_data(args)
    llm_calls = _get_llm_calls(data)

    if not llm_calls:
        print("无 LLM 调用记录。")
        return 0

    indices = _resolve_call_indices(args.call, len(llm_calls))

    for idx in indices:
        c = llm_calls[idx]
        req = c.get("request", {})
        resp = c.get("response", {})
        duration = c.get("duration_ms", 0)

        print(f"=== call[{idx}] ({duration:.0f}ms) ===")
        print(f"模型: {req.get('model', '?')}")
        print(f"工具: {', '.join(req.get('tool_names', []))}")
        print()

        msgs = req.get("messages", [])
        print(f"── 请求 ({len(msgs)} messages) ──")
        for i, m in enumerate(msgs):
            role = m.get("role", "?")
            content = str(m.get("content", "") or "")

            if args.grep and args.grep.lower() not in content.lower() and args.grep.lower() not in role:
                print(f"  [{i}] {role} ({len(content)} chars) [不匹配 grep]")
                continue

            print(f"  [{i}] {role} ({len(content)} chars):")
            if role == "tool":
                tid = m.get("tool_call_id", "")
                print(f"      tool_call_id: {tid}")
            if content:
                print(_truncate(content, args.max_chars))
            tc_list = m.get("tool_calls")
            if tc_list:
                for tc in tc_list:
                    func = tc.get("function", {})
                    print(f"      → {func.get('name', '?')}({func.get('arguments', '')[:100]})")
            print()

        print("── 响应 ──")
        print(f"  finish_reason: {resp.get('finish_reason', '?')}")
        usage = resp.get("usage", {})
        print(f"  tokens: {usage.get('prompt_tokens', 0)}p + {usage.get('completion_tokens', 0)}c = {usage.get('total_tokens', 0)}")

        if resp.get("thinking"):
            print(f"  thinking: {_truncate(resp['thinking'], args.max_chars)}")
        if resp.get("content"):
            print(f"  content: {_truncate(str(resp['content']), args.max_chars)}")
        if resp.get("tool_calls"):
            for tc in resp["tool_calls"]:
                func = tc.get("function", {})
                print(f"  → {func.get('name', '?')}({_truncate(func.get('arguments', ''), min(args.max_chars, 500))})")
        print()

    return 0


# ── failures ──────────────────────────────────────────────


def cmd_failures(args: argparse.Namespace) -> int:
    """汇总所有失败案例的错误信息。"""
    path, data = _resolve_input(args.file)

    if _is_suite(data):
        return _failures_suite(path, data, args)

    # 单个 case 文件
    execution = data.get("execution", {})
    if execution.get("status") == "ok":
        print("该案例状态为 ok，无失败。")
        return 0

    meta = data.get("meta", {})
    print(f"案例: {meta.get('case_id', '?')} — {meta.get('case_name', '?')}")
    print(f"状态: {execution.get('status', '?')}")
    _print_error(execution.get("error"))
    _print_case_failure_context(data, args.max_chars)
    return 0


def _failures_suite(suite_path: Path, data: dict, args: argparse.Namespace) -> int:
    """Suite 级失败分析。"""
    cases = data.get("artifacts", {}).get("cases", [])
    failed_cases = [c for c in cases if c.get("execution", {}).get("status") != "ok"]

    if not failed_cases:
        total = len(cases)
        print(f"全部 {total} 个案例均通过，无失败。")
        return 0

    print(f"失败案例: {len(failed_cases)}/{len(cases)}\n")

    for c in failed_cases:
        cm = c.get("meta", {})
        ce = c.get("execution", {})
        cs = c.get("stats", {})
        case_id = cm.get("case_id", "?")

        print(f"━━━ {case_id} — {cm.get('case_name', '?')} ━━━")
        print(f"  状态: {ce.get('status', '?')}  迭代: {ce.get('iterations', 0)}  耗时: {ce.get('duration_seconds', 0):.1f}s")
        print(f"  Token: {cs.get('total_tokens', 0)}  工具: {cs.get('tool_call_count', 0)} (失败 {cs.get('tool_failures', 0)})")
        model = cm.get("active_model", "")
        write_hint = ce.get("write_hint", "")
        if model or write_hint:
            parts = []
            if model:
                parts.append(f"模型: {model}")
            if write_hint and write_hint != "unknown":
                parts.append(f"写意图: {write_hint}")
            print(f"  {'  '.join(parts)}")
        _print_error(ce.get("error"), indent="  ")

        # 尝试加载详细日志获取更多上下文
        result = _find_case_log(suite_path, data, case_id)
        if result:
            _, case_data = result
            _print_case_failure_context(case_data, args.max_chars, indent="  ")
        print()

    return 0


def _print_error(error: object, indent: str = "") -> None:
    """打印错误信息，支持 str 和 dict 格式。"""
    if not error:
        return
    if isinstance(error, dict):
        etype = error.get("type", "")
        emsg = error.get("message", "")
        print(f"{indent}错误: [{etype}] {emsg}")
    else:
        print(f"{indent}错误: {error}")


def _print_case_failure_context(data: dict, max_chars: int, indent: str = "") -> None:
    """从 case 日志中提取失败上下文：最后一轮 LLM 响应 + 失败的工具调用。"""
    # 失败的工具调用
    tool_calls = _get_tool_calls(data)
    failed_tools = [tc for tc in tool_calls if not tc.get("success")]
    if failed_tools:
        print(f"{indent}失败工具调用:")
        for tc in failed_tools[-3:]:  # 最多显示最后 3 个
            name = tc.get("tool_name", "?")
            error = str(tc.get("error", ""))
            print(f"{indent}  ✗ {name}: {_truncate(error, min(max_chars, 200))}")

    # 最后一轮 LLM 响应
    llm_calls = _get_llm_calls(data)
    if llm_calls:
        last = llm_calls[-1]
        resp = last.get("response", {})
        content = str(resp.get("content", "") or "")
        if content:
            print(f"{indent}最后 LLM 响应: {_truncate(content, min(max_chars, 300))}")


# ── compare ───────────────────────────────────────────────


def cmd_compare(args: argparse.Namespace) -> int:
    """对比两次 suite 运行结果。"""
    _, data_a = _resolve_input(args.file_a)
    _, data_b = _resolve_input(args.file_b)

    if not _is_suite(data_a) or not _is_suite(data_b):
        print("错误：compare 命令需要两个 suite 汇总文件（或包含 suite 的目录）。", file=sys.stderr)
        return 1

    meta_a = data_a.get("meta", {})
    meta_b = data_b.get("meta", {})
    print(f"对比: A={meta_a.get('suite_name', '?')}  vs  B={meta_b.get('suite_name', '?')}")
    print()

    cases_a = {c.get("meta", {}).get("case_id"): c for c in data_a.get("artifacts", {}).get("cases", [])}
    cases_b = {c.get("meta", {}).get("case_id"): c for c in data_b.get("artifacts", {}).get("cases", [])}

    all_ids = sorted(set(cases_a.keys()) | set(cases_b.keys()))

    if not all_ids:
        print("无案例数据可对比。")
        return 0

    # 表头
    print(
        f"{'ID':<16} {'状态A':<6} {'状态B':<6} "
        f"{'耗时A':>6} {'耗时B':>6} {'Δ耗时':>7} "
        f"{'TokenA':>8} {'TokenB':>8} {'ΔToken':>8} "
        f"{'工具A':>4} {'工具B':>4} {'写A':<10} {'写B':<10}"
    )
    print("─" * 115)

    total_da = total_db = 0.0
    total_ta = total_tb = 0
    total_tla = total_tlb = 0

    for cid in all_ids:
        ca = cases_a.get(cid)
        cb = cases_b.get(cid)

        sa = ca.get("execution", {}).get("status", "-") if ca else "-"
        sb = cb.get("execution", {}).get("status", "-") if cb else "-"

        da = ca.get("execution", {}).get("duration_seconds", 0) if ca else 0
        db = cb.get("execution", {}).get("duration_seconds", 0) if cb else 0
        dd = db - da

        ta = ca.get("stats", {}).get("total_tokens", 0) if ca else 0
        tb = cb.get("stats", {}).get("total_tokens", 0) if cb else 0
        dt = tb - ta

        tla = ca.get("stats", {}).get("tool_call_count", 0) if ca else 0
        tlb = cb.get("stats", {}).get("tool_call_count", 0) if cb else 0

        wa = ca.get("execution", {}).get("write_hint", "-") if ca else "-"
        wb = cb.get("execution", {}).get("write_hint", "-") if cb else "-"

        total_da += da
        total_db += db
        total_ta += ta
        total_tb += tb
        total_tla += tla
        total_tlb += tlb

        # 变化标记
        dd_str = f"{dd:>+6.1f}s"
        dt_str = f"{dt:>+8}"

        print(
            f"{cid:<16} {sa:<6} {sb:<6} "
            f"{da:>5.1f}s {db:>5.1f}s {dd_str} "
            f"{ta:>8} {tb:>8} {dt_str} "
            f"{tla:>4} {tlb:>4} {wa or '-':<10} {wb or '-':<10}"
        )

    print("─" * 115)
    dd_total = total_db - total_da
    dt_total = total_tb - total_ta
    print(
        f"{'合计':<16} {'':6} {'':6} "
        f"{total_da:>5.1f}s {total_db:>5.1f}s {dd_total:>+6.1f}s "
        f"{total_ta:>8} {total_tb:>8} {dt_total:>+8} "
        f"{total_tla:>4} {total_tlb:>4}"
    )

    # 百分比变化
    print()
    if total_da > 0:
        pct_time = (total_db - total_da) / total_da * 100
        print(f"耗时变化: {pct_time:>+.1f}%")
    if total_ta > 0:
        pct_token = (total_tb - total_ta) / total_ta * 100
        print(f"Token 变化: {pct_token:>+.1f}%")

    return 0


# ── 工具函数 ──────────────────────────────────────────────


def _resolve_call_indices(call_arg: int | None, total: int) -> list[int]:
    """解析 --call 参数，返回要查看的 call 索引列表。"""
    if call_arg is not None:
        if call_arg < 0 or call_arg >= total:
            print(f"错误：--call {call_arg} 超出范围（共 {total} 轮，0-{total - 1}）", file=sys.stderr)
            sys.exit(1)
        return [call_arg]
    return list(range(total))


# ── CLI ───────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench_inspect",
        description="Bench 运行日志分析工具（支持目录/suite/case 自动识别）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # 通用参数（case 级命令）
    def _add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("file", help="JSON 文件路径或 output 目录路径")
        p.add_argument("--call", "-c", type=int, default=None, help="指定第 N 轮 LLM 调用（0-indexed）")
        p.add_argument("--case", type=str, default=None, help="指定 case_id（suite 穿透到具体案例）")
        p.add_argument("--grep", "-g", type=str, default=None, help="过滤包含关键词的内容")
        p.add_argument("--max-chars", "-m", type=int, default=3000, help="截断长内容（默认 3000，0=不截断）")

    # overview
    p_overview = sub.add_parser("overview", aliases=["ov"], help="运行摘要（自动识别 suite/case）")
    p_overview.add_argument("file", help="JSON 文件路径或 output 目录路径")
    p_overview.add_argument("--sort", "-s", choices=["time", "tokens", "tools", "iters", "llm"], default=None, help="suite 案例排序字段")
    p_overview.add_argument("--failed-only", "-f", action="store_true", help="仅显示失败案例")
    p_overview.add_argument("--grep", "-g", type=str, default=None, help="过滤包含关键词的内容")
    p_overview.add_argument("--max-chars", "-m", type=int, default=3000, help="截断长内容")

    # system
    p_system = sub.add_parser("system", aliases=["sys"], help="查看 system message")
    _add_common(p_system)

    # tools
    p_tools = sub.add_parser("tools", aliases=["tc"], help="查看 tool_calls 详情")
    _add_common(p_tools)

    # trace
    p_trace = sub.add_parser("trace", aliases=["tr"], help="查看 engine_trace")
    _add_common(p_trace)
    p_trace.add_argument("--verbose", "-v", action="store_true", help="显示 trace 组件的完整内容")

    # call
    p_call = sub.add_parser("call", help="查看完整 LLM 请求/响应")
    _add_common(p_call)

    # failures
    p_failures = sub.add_parser("failures", aliases=["fail"], help="汇总失败案例的错误信息")
    p_failures.add_argument("file", help="JSON 文件路径或 output 目录路径")
    p_failures.add_argument("--max-chars", "-m", type=int, default=3000, help="截断长内容")

    # compare
    p_compare = sub.add_parser("compare", aliases=["cmp", "diff"], help="对比两次 suite 运行结果")
    p_compare.add_argument("file_a", help="第一次运行的 suite 文件或目录（A 基准）")
    p_compare.add_argument("file_b", help="第二次运行的 suite 文件或目录（B 对比）")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    cmd = args.command
    if cmd in ("overview", "ov"):
        return cmd_overview(args)
    elif cmd in ("system", "sys"):
        return cmd_system(args)
    elif cmd in ("tools", "tc"):
        return cmd_tools(args)
    elif cmd in ("trace", "tr"):
        return cmd_trace(args)
    elif cmd == "call":
        return cmd_call(args)
    elif cmd in ("failures", "fail"):
        return cmd_failures(args)
    elif cmd in ("compare", "cmp", "diff"):
        return cmd_compare(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
