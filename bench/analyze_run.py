"""把 bench 的 run_*.json 展开成便于人工/子代理复盘的 Markdown。

用法：
    python bench/analyze_run.py "outputs/experiential/wave-*/run_*.json" [--out bench/reports/raw]

与 excelmanus.bench 内置的 conversations/{case}.digest.md 相比，这里额外展开：
- 首次请求的完整 system prompt（看注入了什么、多大）
- 每次 LLM 调用的 prompt/completion token、返回的工具名、assistant 文本
- 每次工具调用的参数与返回（更长截断）
- 最后一次请求的 messages 逐条长度（看上下文膀胀来源）
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
from typing import Any


def _compact(value: Any, limit: int = 600) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _content_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def _sdk_calls_marker(result: Any) -> str:
    """run_code 结果里 sdk_calls 子调用的标注（嵌套工具使用单独可见）。"""
    try:
        data = json.loads(result) if isinstance(result, str) else result
        sdk = (data or {}).get("sdk_calls")
    except (TypeError, ValueError):
        return ""
    if not isinstance(sdk, dict) or not sdk.get("count"):
        return ""
    writes = ", ".join(f"{w.get('tool')}@{w.get('content_version')}" for w in sdk.get("writes") or [])
    return (
        f"sdk_calls: {sdk.get('count')} 次子调用（成功 {sdk.get('succeeded')}，失败 {sdk.get('failed')}）"
        + (f"，写入: {writes}" if writes else "")
    )


def _dump_turn(
    lines: list[str],
    label: str,
    tool_calls: list[dict[str, Any]],
    llm_calls: list[dict[str, Any]],
    reply: str | None,
    thinking: list[str],
    approval_events: list[dict[str, Any]] | None = None,
    question_events: list[dict[str, Any]] | None = None,
) -> None:
    lines += ["", f"## {label}", "", "### LLM 调用"]
    for i, call in enumerate(llm_calls, 1):
        response = call.get("response") or {}
        usage = response.get("usage") or {}
        request = call.get("request") or {}
        names = [(tc.get("function") or {}).get("name") for tc in (response.get("tool_calls") or [])]
        lines.append(
            f"- #{i} {call.get('duration_ms', 0):.0f}ms prompt={usage.get('prompt_tokens', 0):,} "
            f"completion={usage.get('completion_tokens', 0):,} msgs={len(request.get('messages') or [])} "
            f"finish={response.get('finish_reason')} → {names}"
        )
        if response.get("content"):
            lines.append(f"  - 文本: {_compact(response['content'], 300)}")
        if call.get("error"):
            lines.append(f"  - ERROR: {_compact(call['error'], 300)}")
    lines += ["", "### 工具调用"]
    for i, tc in enumerate(tool_calls, 1):
        status = "ok" if tc.get("success") else f"**FAIL** {_compact(tc.get('error'), 300)}"
        lines.append(
            f"{i}. iter{tc.get('iteration')} `{tc['tool_name']}` {_compact(tc.get('arguments'), 700)} "
            f"→ {status} ({tc.get('duration_ms', 0):.0f}ms)"
        )
        if tc.get("result"):
            lines.append(f"   - 返回: {_compact(tc['result'], 1200)}")
            sdk = _sdk_calls_marker(tc["result"])
            if sdk:
                lines.append(f"   - {sdk}")
    if approval_events:
        lines += ["", "### 审批"]
        lines += [
            f"- {ev.get('event_type')} {ev.get('approval_tool_name')} id={ev.get('approval_id')}"
            for ev in approval_events
        ]
    if question_events:
        lines += ["", "### 提问"]
        lines += [
            f"- {ev.get('question_header')}: {_compact(ev.get('question_text'), 300)}"
            for ev in question_events
        ]
    if thinking:
        lines += ["", "### 思考"]
        lines += [f"- {_compact(t, 600)}" for t in thinking]
    lines += ["", "### 回复", "", reply or "∅"]


def dump(run_path: Path, out_dir: Path) -> Path:
    data = json.loads(run_path.read_text(encoding="utf-8"))
    meta, exe, art, stats = data["meta"], data["execution"], data["artifacts"], data["stats"]
    snap = meta.get("config_snapshot") or {}
    lines = [f"# {meta['case_id']} {meta['case_name']}", ""]
    lines.append(
        f"- model={meta.get('active_model')} chat_mode={snap.get('chat_mode')} status={exe['status']} "
        f"dur={exe['duration_seconds']}s iters={exe['iterations']}"
    )
    lines.append(
        f"- tokens prompt={stats['prompt_tokens']:,} completion={stats['completion_tokens']:,} "
        f"llm_calls={stats['llm_call_count']} tools={stats['tool_call_count']} fails={stats['tool_failures']}"
    )
    _approvals = sum(len(t.get("approval_events") or []) for t in (data.get("turns") or []))
    _approvals += len(art.get("approval_events") or [])
    _questions = sum(len(t.get("question_events") or []) for t in (data.get("turns") or []))
    _questions += len(art.get("question_events") or [])
    if _approvals or _questions:
        lines.append(f"- approvals={_approvals} questions={_questions}")
    if exe.get("error"):
        lines.append(f"- ERROR: {_compact(exe['error'], 500)}")

    llm_calls_all: list[dict[str, Any]] = art.get("llm_calls") or []
    turns = data.get("turns") or []
    if turns and not llm_calls_all:
        llm_calls_all = [c for t in turns for c in (t.get("llm_calls") or [])]

    if llm_calls_all:
        req0 = llm_calls_all[0].get("request") or {}
        msgs = req0.get("messages") or []
        tool_names = req0.get("tool_names") or [
            ((t.get("function") or t).get("name")) for t in (req0.get("tools") or []) if isinstance(t, dict)
        ]
        lines += ["", f"## 首次请求 messages={len(msgs)} tools={len(tool_names)}", "", f"tool_names: {tool_names}"]
        for i, m in enumerate(msgs):
            text = _content_text(m.get("content"))
            role = m.get("role")
            lines += ["", f"### {role}[{i}] ({len(text)} chars)", "```"]
            lines.append(text if role == "system" else text[:6000])
            lines.append("```")

    if turns:
        for t in turns:
            tokens = (t.get("tokens") or {}).get("total_tokens", 0)
            _dump_turn(
                lines,
                f"轮次 {t.get('turn_index', 0) + 1} 用户: {_compact(t.get('message'), 200)} "
                f"({t.get('duration_seconds')}s, {t.get('iterations')} iters, {tokens:,} tok)",
                t.get("tool_calls") or [],
                t.get("llm_calls") or [],
                t.get("reply"),
                t.get("thinking_log") or [],
                t.get("approval_events") or [],
                t.get("question_events") or [],
            )
    else:
        _dump_turn(
            lines,
            f"单轮 用户: {_compact(meta.get('message'), 200)}",
            art.get("tool_calls") or [],
            llm_calls_all,
            (data.get("result") or {}).get("reply"),
            art.get("thinking_log") or [],
            art.get("approval_events") or [],
            art.get("question_events") or [],
        )

    if llm_calls_all:
        last = (llm_calls_all[-1].get("request") or {}).get("messages") or []
        lines += ["", f"## 最后一次请求 messages={len(last)}，逐条长度"]
        for m in last:
            text = _content_text(m.get("content"))
            extra = ""
            if m.get("tool_calls"):
                extra = " tool_calls=" + ",".join((tc.get("function") or {}).get("name", "") for tc in m["tool_calls"])
            lines.append(f"- {m.get('role')} {len(text)} chars{extra}: {_compact(text, 160)}")

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{run_path.stem}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pattern", nargs="?", default="outputs/experiential/wave-*/run_*.json")
    parser.add_argument("--out", default="bench/reports/raw")
    args = parser.parse_args()
    out_dir = Path(args.out)
    for path in sorted(glob.glob(args.pattern)):
        run = Path(path)
        data = json.loads(run.read_text(encoding="utf-8"))
        out = dump(run, out_dir)
        stats = data["stats"]
        print(f"{out}  prompt={stats['prompt_tokens']:,} llm={stats['llm_call_count']} tools={stats['tool_call_count']} {data['execution']['duration_seconds']}s")


if __name__ == "__main__":
    main()
