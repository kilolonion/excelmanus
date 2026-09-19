"""片 D live 中文对照标定器。

有密钥才打 TypeSafe / Gateway；无密钥明确失败。本模块**永不**改
``SIGNED_ENFORCE_PACKS`` / ``SIGNED_ENFORCE_FAMILIES`` / ``CALIBRATED``。
报告默认写到 gitignore 本地目录。测试禁止打网。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from excelmanus.system_one.adapter import bound_state
from excelmanus.system_one.calibration import (
    SIGNED_ENFORCE_FAMILIES,
    SIGNED_ENFORCE_PACKS,
    decision_matches_expect,
    load_samples,
    load_suite_states,
    synthesize_sample,
)
from excelmanus.system_one.client import (
    GATEWAY_MODEL,
    detect_transport,
    resolve_jev_api_key,
    sdk_available,
    system_one,
)
from excelmanus.system_one.packs import get_pack
from excelmanus.system_one.policy import T_ALLOW, settings_from, synthesize
from excelmanus.system_one.types import ChoiceAnswer, Evaluation, NoulAnswer, ScoreAnswer

NO_KEY_EXIT = 2
DEFAULT_OUT_DIR = Path("bench/fixtures/jev_calibration/local")
_SIGNED_PACKS_AT_IMPORT = frozenset(SIGNED_ENFORCE_PACKS)
_SIGNED_FAMILIES_AT_IMPORT = frozenset(SIGNED_ENFORCE_FAMILIES)
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|password|secret|authorization|credential)",
    re.IGNORECASE,
)
_SECRET_PREFIXES = ("vck_", "sk-", "tsk_")
_SETTING_KEY_NAMES = (
    "EXCELMANUS_TYPESAFE_API_KEY",
    "EXCELMANUS_AI_GATEWAY_API_KEY",
)


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


@dataclass(frozen=True)
class CalibJob:
    id: str
    source: str
    pack_id: str
    state: dict[str, Any]
    expect: dict[str, Any] = field(default_factory=dict)
    fixture_answers: dict[str, Any] = field(default_factory=dict)
    fixture_kind: str = ""


def resolve_api_key() -> tuple[str | None, str, str]:
    """返回 (key, setting_name, transport)。key 不要写入报告。"""
    settings = settings_from(None)
    raw = settings.api_key
    _resolved_key, name = resolve_jev_api_key()
    if not raw:
        return None, "", ""
    return raw, name, detect_transport(raw, settings.protocol)


def assert_unsigned_unchanged() -> None:
    if SIGNED_ENFORCE_PACKS != _SIGNED_PACKS_AT_IMPORT:
        raise RuntimeError("live calibrator must not mutate SIGNED_ENFORCE_PACKS")
    if SIGNED_ENFORCE_FAMILIES != _SIGNED_FAMILIES_AT_IMPORT:
        raise RuntimeError("live calibrator must not mutate SIGNED_ENFORCE_FAMILIES")


def redact_value(value: Any) -> Any:
    """报告脱敏：去掉密钥字段与密钥形字符串。"""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for raw_key, raw_val in value.items():
            key = str(raw_key)
            if _SECRET_KEY_RE.search(key):
                out[key] = "[redacted]"
            else:
                out[key] = redact_value(raw_val)
        return out
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str) and _looks_secret(value):
        return "[redacted]"
    return value


def _looks_secret(text: str) -> bool:
    stripped = text.strip()
    if stripped.startswith(_SECRET_PREFIXES):
        return True
    if "Bearer " in stripped and any(stripped.startswith(p) or p in stripped for p in _SECRET_PREFIXES):
        return True
    return False


def state_summary(state: Mapping[str, Any] | None) -> dict[str, Any]:
    src = dict(state or {})
    text = str(src.get("user_text") or "")
    tool = _mapping(src.get("tool"))
    return {
        "user_text": text[:120],
        "chat_mode": str(src.get("chat_mode") or ""),
        "tool": str(tool.get("name") or "") or None,
    }


def answers_json(answers: Mapping[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for qid, ans in (answers or {}).items():
        if isinstance(ans, NoulAnswer):
            out[qid] = {"noul": ans.noul}
        elif isinstance(ans, ChoiceAnswer):
            out[qid] = {
                "choice": ans.choice,
                "confidence": ans.confidence,
                "probabilities": dict(ans.probabilities),
            }
        elif isinstance(ans, ScoreAnswer):
            out[qid] = {
                "score": ans.score,
                "confidence": ans.confidence,
            }
        elif isinstance(ans, Mapping):
            out[qid] = dict(ans)
        else:
            out[qid] = str(ans)
    return out


def collect_jobs(
    *,
    root: Path | None = None,
    source: str = "all",
    limit: int = 8,
    packs: Sequence[str] | None = None,
    ids: Sequence[str] | None = None,
) -> list[CalibJob]:
    """fixture 优先（有 expect），再补 suite 映射。write_approval 排前。"""
    wanted = {str(p) for p in packs} if packs else None
    wanted_ids = {str(i) for i in ids} if ids else None
    jobs: list[CalibJob] = []
    seen: set[str] = set()
    if source in {"all", "fixture"}:
        for sample in load_samples():
            job = _job_from_sample(sample)
            if _keep_job(job, wanted, wanted_ids) and job.id not in seen:
                jobs.append(job)
                seen.add(job.id)
    if source in {"all", "suite"}:
        for item in load_suite_states(root):
            job = _job_from_suite(item)
            if job is None:
                continue
            if _keep_job(job, wanted, wanted_ids) and job.id not in seen:
                jobs.append(job)
                seen.add(job.id)
    jobs.sort(key=_prefer_write_approval)
    if limit > 0:
        jobs = jobs[:limit]
    return jobs


def _keep_job(job: CalibJob, packs: set[str] | None, ids: set[str] | None) -> bool:
    if packs is not None and job.pack_id not in packs:
        return False
    if ids is not None and job.id not in ids:
        return False
    return True


def _prefer_write_approval(job: CalibJob) -> tuple[int, str, str]:
    source = job.source.lower()
    rank = 0 if "write_approval" in source else 1 if job.pack_id == "approval.tool_call" else 2
    return (rank, job.pack_id, job.id)


def _job_from_sample(sample: Mapping[str, Any]) -> CalibJob:
    pack_id = str(sample.get("pack_id") or "")
    state = bound_state(pack_id, sample.get("state") if isinstance(sample.get("state"), Mapping) else {})
    expect = dict(sample.get("expect") or {}) if isinstance(sample.get("expect"), Mapping) else {}
    answers = dict(sample.get("answers") or {}) if isinstance(sample.get("answers"), Mapping) else {}
    kind = ""
    if answers:
        row = synthesize_sample(sample)
        kind = str(row.get("kind") or "")
        if not expect:
            expect = dict(row.get("expect") or {})
    return CalibJob(
        id=str(sample.get("id") or ""),
        source=str(sample.get("source") or "fixture"),
        pack_id=pack_id,
        state=state,
        expect=expect,
        fixture_answers=answers,
        fixture_kind=kind,
    )


def _job_from_suite(item: Mapping[str, Any]) -> CalibJob | None:
    pack_id = str(item.get("pack_id") or "")
    state = item.get("state") if isinstance(item.get("state"), Mapping) else None
    if not pack_id or not isinstance(state, Mapping):
        return None
    return CalibJob(
        id=str(item.get("id") or ""),
        source=str(item.get("source") or "suite"),
        pack_id=pack_id,
        state=dict(state),
    )


def compare_row(
    job: CalibJob,
    *,
    live_kind: str = "",
    live_reason: str = "",
    live_extras: Mapping[str, Any] | None = None,
    live_answers: Mapping[str, Any] | None = None,
    latency_ms: float = 0.0,
    model: str = "",
    error: str = "",
) -> dict[str, Any]:
    expect = dict(job.expect or {})
    matched: bool | None
    if error:
        matched = False
    elif expect:
        matched = bool(
            (not expect.get("kind") or live_kind == expect.get("kind"))
            and (
                expect.get("profile") is None
                or str((live_extras or {}).get("profile") or "") == str(expect.get("profile"))
            )
        )
    else:
        matched = None
    return redact_value(
        {
            "id": job.id,
            "source": job.source,
            "pack_id": job.pack_id,
            "state_summary": state_summary(job.state),
            "offline_expect": expect,
            "offline_kind": job.fixture_kind or expect.get("kind"),
            "live_kind": live_kind,
            "live_reason": live_reason,
            "live_extras": dict(live_extras or {}),
            "live_answers": answers_json(live_answers) if live_answers else {},
            "latency_ms": round(float(latency_ms or 0.0), 1),
            "model": model,
            "matched": matched,
            "error": error,
        }
    )


def summarize_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if row.get("matched") is not None]
    matched = [row for row in scored if row.get("matched") is True]
    errors = [row for row in rows if row.get("error")]
    asks = [row for row in rows if row.get("live_kind") == "ask"]
    autos = [row for row in rows if row.get("live_kind") == "auto"]
    unexpected_auto = [
        row for row in autos if row.get("offline_expect", {}).get("kind") not in {None, "", "auto"}
    ]
    allow_confs: list[float] = []
    for row in rows:
        action = (row.get("live_answers") or {}).get("action") or {}
        if isinstance(action, Mapping) and action.get("choice") == "allow":
            try:
                allow_confs.append(float(action.get("confidence") or 0.0))
            except (TypeError, ValueError):
                continue
    keep = True
    reasons = ["未人工签字", f"T_ALLOW 保持 {T_ALLOW}"]
    if unexpected_auto:
        reasons.append("live 出现非期望 auto")
    if allow_confs and max(allow_confs) < T_ALLOW:
        reasons.append(f"live allow confidence 最高 {max(allow_confs):.2f} < {T_ALLOW}")
    if autos:
        reasons.append("存在 live auto，未对照前不得放宽")
    n = len(rows)
    return {
        "n": n,
        "scored": len(scored),
        "matched": len(matched),
        "mismatched": max(0, len(scored) - len(matched)),
        "errors": len(errors),
        "accuracy": (len(matched) / len(scored)) if scored else None,
        "uncertain_ratio": (len(asks) / n) if n else None,
        "auto_count": len(autos),
        "t_allow": T_ALLOW,
        "keep_t_allow_conservative": keep,
        "t_allow_reason": "；".join(reasons),
        "next": next_step_banner(),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = _mapping(report.get("summary"))
    raw_rows = report.get("rows")
    rows: list[Any] = list(raw_rows) if isinstance(raw_rows, list) else []
    lines = [
        "# Jev live 中文对照（未签字）",
        "",
        f"- 运输层: `{report.get('transport') or ''}`",
        f"- 模型: `{report.get('model') or ''}`"
        + (f" / Gateway `{report.get('gateway_model')}`" if report.get("gateway_model") else ""),
        f"- 样本: {summary.get('n')}；对照 {summary.get('scored')}；一致 {summary.get('matched')}；不一致 {summary.get('mismatched')}；错误 {summary.get('errors')}",
        f"- 对错率: {summary.get('accuracy')}",
        f"- uncertain(ask) 比例: {summary.get('uncertain_ratio')}",
        f"- T_ALLOW: {summary.get('t_allow')} — {summary.get('t_allow_reason')}",
        "",
        "**下一步：人工审阅后才能改 SIGNED_ENFORCE_PACKS / CALIBRATED。本运行未修改签字集合。**",
        "",
        "| id | pack | live | expect | match | ms |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        expect = _mapping(row.get("offline_expect"))
        lines.append(
            "| {id} | {pack} | {live} | {expect} | {match} | {ms} |".format(
                id=row.get("id"),
                pack=row.get("pack_id"),
                live=row.get("live_kind") or row.get("error") or "",
                expect=expect.get("kind") or "",
                match=row.get("matched"),
                ms=row.get("latency_ms"),
            )
        )
    lines.extend(["", "## 样本", ""])
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        summary_state = _mapping(row.get("state_summary"))
        lines.append(f"### {row.get('id')} (`{row.get('pack_id')}`)")
        lines.append(f"- source: `{row.get('source')}`")
        lines.append(f"- user_text: {summary_state.get('user_text')}")
        lines.append(f"- live: `{row.get('live_kind')}` ({row.get('live_reason')})")
        lines.append(f"- expect: `{row.get('offline_expect')}`")
        lines.append(f"- matched: {row.get('matched')}  latency_ms: {row.get('latency_ms')}")
        if row.get("error"):
            lines.append(f"- error: {row.get('error')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_reports(out_dir: Path, report: Mapping[str, Any]) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = str(report.get("generated_at") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    safe = re.sub(r"[^0-9A-Za-z._-]", "", stamp) or "run"
    json_path = out_dir / f"live_{safe}.json"
    md_path = out_dir / f"live_{safe}.md"
    payload = redact_value(dict(report))
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    assert_unsigned_unchanged()
    return json_path, md_path


def next_step_banner() -> str:
    return "下一步：人工审阅后才能改 SIGNED_ENFORCE_PACKS / CALIBRATED"


async def live_evaluate(
    pack_id: str,
    state: Mapping[str, Any],
    *,
    api_key: str,
    transport: str,
    model: str,
    timeout_seconds: float,
    protocol: str | None = None,
    base_url: str | None = None,
) -> Evaluation:
    spec = get_pack(pack_id)
    bounded = bound_state(pack_id, state)
    return await system_one(
        spec,
        bounded,
        model=model,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        protocol=protocol,
        base_url=base_url,
    )


async def run_jobs(
    jobs: Sequence[CalibJob],
    *,
    api_key: str,
    transport: str,
    model: str,
    timeout_seconds: float,
    protocol: str | None = None,
    base_url: str | None = None,
    pause_seconds: float = 0.25,
) -> list[dict[str, Any]]:
    import asyncio

    rows: list[dict[str, Any]] = []
    cache: dict[tuple[str, str], Evaluation] = {}
    for index, job in enumerate(jobs):
        cache_key = (job.pack_id, json.dumps(job.state, ensure_ascii=False, sort_keys=True))
        error = ""
        evaluation: Evaluation | None = None
        try:
            if cache_key in cache:
                evaluation = cache[cache_key]
            else:
                evaluation = await live_evaluate(
                    job.pack_id,
                    job.state,
                    api_key=api_key,
                    transport=transport,
                    model=model,
                    timeout_seconds=timeout_seconds,
                    protocol=protocol,
                    base_url=base_url,
                )
                cache[cache_key] = evaluation
            decision = synthesize(job.pack_id, evaluation, job.state)
            row = compare_row(
                job,
                live_kind=decision.kind,
                live_reason=decision.reason,
                live_extras=decision.extras,
                live_answers=evaluation.answers,
                latency_ms=evaluation.latency_ms,
                model=evaluation.model or model,
            )
            if job.expect:
                row["matched"] = decision_matches_expect(decision, job.expect)
        except Exception as exc:
            error = f"{type(exc).__name__}"
            row = compare_row(job, error=error)
        rows.append(row)
        if pause_seconds and index + 1 < len(jobs):
            await asyncio.sleep(pause_seconds)
    return rows


def build_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    transport: str,
    model: str,
    key_env: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return redact_value(
        {
            "signed": False,
            "signoff": None,
            "generated_at": now,
            "transport": transport,
            "model": model,
            "gateway_model": GATEWAY_MODEL if transport == "gateway" else None,
            "key_env": key_env,
            "t_allow": T_ALLOW,
            "note": "本报告不是签字。禁止把密钥写入仓库。人工审阅前不要改 SIGNED_ENFORCE_PACKS / CALIBRATED。",
            "next": next_step_banner(),
            "summary": summarize_rows(rows),
            "rows": list(rows),
        }
    )


def _bind_product_settings() -> None:
    try:
        from excelmanus.database import Database
        from excelmanus.data_home import resolve_db_path
        from excelmanus.settings_persist import bind_settings_store

        bind_settings_store(Database(resolve_db_path()))
    except Exception:
        return


async def async_main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Jev live 中文对照标定器。永不自动签字。")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--source", choices=("all", "fixture", "suite"), default="all")
    parser.add_argument("--pack", action="append", dest="packs")
    parser.add_argument("--ids", default="")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = parser.parse_args(list(argv) if argv is not None else None)

    assert_unsigned_unchanged()
    _bind_product_settings()
    settings = settings_from(None)
    api_key, key_env, transport = resolve_api_key()
    if not api_key:
        print(
            "无 TypeSafe/Gateway 密钥。请在 Web 设置页写入 "
            "EXCELMANUS_TYPESAFE_API_KEY 或 EXCELMANUS_AI_GATEWAY_API_KEY。不假装成功。"
        )
        print(next_step_banner())
        return NO_KEY_EXIT
    if transport == "typesafe_direct" and not sdk_available():
        print("直连 TypeSafe 需要 typesafe-sdk（pip install 'excelmanus[system-one]'），或改用 vck_ Gateway 密钥。不假装成功。")
        print(next_step_banner())
        return NO_KEY_EXIT

    model = settings.model or "jev-1.13.0"
    ids = [part.strip() for part in str(args.ids).split(",") if part.strip()]
    jobs = collect_jobs(
        root=root,
        source=args.source,
        limit=max(0, int(args.limit)),
        packs=args.packs,
        ids=ids or None,
    )
    if not jobs:
        print("没有可对照样本。")
        print(next_step_banner())
        return 1
    rows = await run_jobs(
        jobs,
        api_key=api_key,
        transport=transport,
        model=model,
        timeout_seconds=float(args.timeout),
        protocol=settings.protocol,
        base_url=settings.base_url,
    )
    report = build_report(rows, transport=transport, model=model, key_env=key_env)
    json_path, md_path = write_reports(Path(args.out_dir), report)
    summary = report["summary"]
    print(f"transport={transport} model={model} key_env={key_env}")
    print(
        "n={n} matched={matched}/{scored} uncertain={uncertain} auto={auto}".format(
            n=summary.get("n"),
            matched=summary.get("matched"),
            scored=summary.get("scored"),
            uncertain=summary.get("uncertain_ratio"),
            auto=summary.get("auto_count"),
        )
    )
    print(f"report_json={json_path}")
    print(f"report_md={md_path}")
    print(summary.get("t_allow_reason"))
    print(next_step_banner())
    print("本运行未修改 SIGNED_ENFORCE_PACKS / SIGNED_ENFORCE_FAMILIES / CALIBRATED。")
    assert_unsigned_unchanged()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    import asyncio

    return asyncio.run(async_main(argv))
