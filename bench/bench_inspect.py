#!/usr/bin/env python3
"""Bench 运行日志分析工具。

子命令：
    overview  — 每轮 LLM 调用摘要
    system    — 查看某轮的 system message
    tools     — 查看某轮的 tool_calls 详情
    trace     — 查看 engine_trace
    call      — 查看某轮完整 LLM 请求/响应

用法示例：
    python scripts/bench_inspect.py overview run_xxx.json
    python scripts/bench_inspect.py system run_xxx.json --call 3 --grep "窗口"
    python scripts/bench_inspect.py tools run_xxx.json --call 3
    python scripts/bench_inspect.py trace run_xxx.json
    python scripts/bench_inspect.py call run_xxx.json --call 3 --max-chars 5000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ── 数据加载 ──────────────────────────────────────────────


def _load(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"错误：文件不存在: {path}", file=sys.stderr)
        sys.exit(1)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _get_llm_calls(data: dict) -> list[dict]:
    return data.get("artifacts", {}).get("llm_calls", [])


def _get_tool_calls(data: dict) -> list[dict]:
    return data.get("artifacts", {}).get("tool_calls", [])


def _get_engine_trace(data: dict) -> list[dict]:
    return data.get("engine_trace", [])


def _truncate(text: str, max_chars: int) -> str:
    if max_chars and len(text) > max_chars:
        return text[:max_chars] + f"\n... [截断，共 {len(text)} 字符]"
    return text


# ── overview ──────────────────────────────────────────────


def cmd_overview(args: argparse.Namespace) -> int:
    data = _load(args.file)
    llm_calls = _get_llm_calls(data)

    # 基本信息
    meta = data.get("meta", {})
    execution = data.get("execution", {})
    stats = data.get("stats", {})
    print(f"案例: {meta.get('case_id', '?')} — {meta.get('case_name', '?')}")
    print(f"模型: {meta.get('active_model', '?')}")
    print(f"状态: {execution.get('status', '?')}  迭代: {execution.get('iterations', '?')}")
    print(f"耗时: {execution.get('duration_seconds', '?')}s")
    print(f"Token: {stats.get('prompt_tokens', 0)}p + {stats.get('completion_tokens', 0)}c = {stats.get('total_tokens', 0)}")
    print(f"工具调用: {stats.get('tool_call_count', 0)} (成功 {stats.get('tool_successes', 0)}, 失败 {stats.get('tool_failures', 0)})")
    print(f"LLM 调用: {len(llm_calls)} 轮")
    print()

    if not llm_calls:
        print("无 LLM 调用记录。")
        return 0

    # 每轮摘要
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

        # 响应摘要
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

    # 汇总
    total_prompt = sum(c.get("response", {}).get("usage", {}).get("prompt_tokens", 0) for c in llm_calls)
    total_compl = sum(c.get("response", {}).get("usage", {}).get("completion_tokens", 0) for c in llm_calls)
    total_dur = sum(c.get("duration_ms", 0) for c in llm_calls)
    print("─" * 100)
    print(f"{'合计':>10}  {'':>5}  {'':>12}  {'':>2}  {total_prompt:>7}  {total_compl:>7}  {total_prompt + total_compl:>7}  {total_dur:>7.0f}")
    return 0


# ── system ────────────────────────────────────────────────


def cmd_system(args: argparse.Namespace) -> int:
    data = _load(args.file)
    llm_calls = _get_llm_calls(data)

    if not llm_calls:
        print("无 LLM 调用记录。")
        return 0

    # 确定要查看的 call
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

            # grep 过滤
            if args.grep and args.grep.lower() not in content.lower():
                continue

            print(f"=== call[{idx}] system msg[{msg_idx}] ({len(content)} chars) ===")
            print(_truncate(content, args.max_chars))
            print()

    return 0


# ── tools ─────────────────────────────────────────────────


def cmd_tools(args: argparse.Namespace) -> int:
    data = _load(args.file)
    llm_calls = _get_llm_calls(data)

    if args.call is not None:
        # 从特定 LLM call 的响应中提取 tool_calls
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
                # 尝试格式化参数
                try:
                    parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    formatted = json.dumps(parsed, ensure_ascii=False, indent=4)
                    print(f"      args: {_truncate(formatted, args.max_chars)}")
                except (json.JSONDecodeError, TypeError):
                    print(f"      args: {_truncate(str(raw_args), args.max_chars)}")

            # 同时显示下一轮的 tool result（如果有）
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
        # 显示 artifacts 中的全局 tool_calls 列表
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
            result = tc.get("result", "")[:80].replace("\n", " ")
            error = tc.get("error", "")

            line = f"{name} {args_str} {result} {error}"
            if grep and grep not in line.lower():
                continue

            summary = args_str[:50] if len(args_str) <= 50 else args_str[:50] + "..."
            if not tc.get("success") and error:
                summary += f" ERR: {error[:40]}"
            else:
                summary += f" → {result[:40]}" if result else ""

            print(f"{i:>3}  {iteration:>4}  {name:<25}  {ok:>3}  {dur:>7.0f}  {summary}")

    return 0


# ── trace ─────────────────────────────────────────────────


def cmd_trace(args: argparse.Namespace) -> int:
    data = _load(args.file)
    trace = _get_engine_trace(data)

    if not trace:
        print("无 engine_trace 记录（需要 --trace 模式运行 bench）。")
        return 0

    grep = args.grep.lower() if args.grep else None

    for i, entry in enumerate(trace):
        event = entry.get("event", "?")
        iteration = entry.get("iteration", "?")
        ts = entry.get("timestamp", "")
        entry_data = entry.get("data", {})

        # grep 过滤
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
    data = _load(args.file)
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

        # 请求消息
        msgs = req.get("messages", [])
        print(f"── 请求 ({len(msgs)} messages) ──")
        for i, m in enumerate(msgs):
            role = m.get("role", "?")
            content = str(m.get("content", "") or "")

            # grep 过滤
            if args.grep and args.grep.lower() not in content.lower() and args.grep.lower() not in role:
                # 只显示摘要
                print(f"  [{i}] {role} ({len(content)} chars) [不匹配 grep]")
                continue

            print(f"  [{i}] {role} ({len(content)} chars):")
            if role == "tool":
                tid = m.get("tool_call_id", "")
                print(f"      tool_call_id: {tid}")
            if content:
                print(_truncate(content, args.max_chars))
            # tool_calls in assistant message
            tc_list = m.get("tool_calls")
            if tc_list:
                for tc in tc_list:
                    func = tc.get("function", {})
                    print(f"      → {func.get('name', '?')}({func.get('arguments', '')[:100]})")
            print()

        # 响应
        print(f"── 响应 ──")
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
        description="Bench 运行日志分析工具",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # 通用参数
    def _add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("file", help="bench 运行日志 JSON 文件路径")
        p.add_argument("--call", "-c", type=int, default=None, help="指定第 N 轮 LLM 调用（0-indexed）")
        p.add_argument("--grep", "-g", type=str, default=None, help="过滤包含关键词的内容")
        p.add_argument("--max-chars", "-m", type=int, default=3000, help="截断长内容（默认 3000，0=不截断）")

    # overview
    p_overview = sub.add_parser("overview", aliases=["ov"], help="每轮 LLM 调用摘要")
    _add_common(p_overview)

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
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
