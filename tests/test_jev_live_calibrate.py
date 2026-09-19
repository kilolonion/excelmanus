"""片 D live 标定器：无密钥失败、报告格式、脱敏、不改签字集合。禁止打网。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from excelmanus.system_one.calibration import SIGNED_ENFORCE_FAMILIES, SIGNED_ENFORCE_PACKS
from excelmanus.system_one.live_calibrate import (
    NO_KEY_EXIT,
    CalibJob,
    _SETTING_KEY_NAMES,
    async_main,
    build_report,
    collect_jobs,
    compare_row,
    detect_transport,
    next_step_banner,
    redact_value,
    render_markdown,
    summarize_rows,
    write_reports,
)
from excelmanus.system_one.policy import T_ALLOW


def test_signed_sets_remain_empty() -> None:
    assert SIGNED_ENFORCE_PACKS == frozenset()
    assert SIGNED_ENFORCE_FAMILIES == frozenset()


def test_detect_transport() -> None:
    assert detect_transport("vck_test") == "gateway"
    assert detect_transport("ts_live_xxx") == "typesafe_direct"


def test_redact_strips_keys_and_prefixes() -> None:
    blob = redact_value(
        {
            "api_key": "vck_live_should_not_leak",
            "nested": {"typesafe_api_key": "sk-abc", "user_text": "删掉报销.xlsx"},
            "note": "vck_live_should_not_leak",
        }
    )
    dumped = json.dumps(blob, ensure_ascii=False)
    assert "vck_live" not in dumped
    assert "sk-abc" not in dumped
    assert blob["nested"]["user_text"] == "删掉报销.xlsx"
    assert blob["api_key"] == "[redacted]"


def test_collect_jobs_prefers_write_approval() -> None:
    jobs = collect_jobs(source="fixture", limit=8)
    assert jobs
    assert jobs[0].source.endswith("suite_write_approval.json")
    assert jobs[0].pack_id == "approval.tool_call"
    assert any(job.id == "A01" for job in jobs)


def test_summarize_keeps_t_allow_conservative() -> None:
    rows = [
        {
            "matched": True,
            "live_kind": "ask",
            "offline_expect": {"kind": "ask"},
            "live_answers": {"action": {"choice": "allow", "confidence": 0.85}},
            "error": "",
        },
        {
            "matched": False,
            "live_kind": "auto",
            "offline_expect": {"kind": "ask"},
            "live_answers": {},
            "error": "",
        },
    ]
    summary = summarize_rows(rows)
    assert summary["keep_t_allow_conservative"] is True
    assert summary["t_allow"] == T_ALLOW == 0.99
    assert "未人工签字" in summary["t_allow_reason"]
    assert summary["next"] == next_step_banner()
    assert summary["uncertain_ratio"] == 0.5
    assert summary["auto_count"] == 1


def test_report_format_and_write_is_local(tmp_path: Path) -> None:
    job = CalibJob(
        id="A01",
        source="bench/cases/suite_write_approval.json",
        pack_id="approval.tool_call",
        state={"user_text": "请用 run_shell 执行 echo bench-approval-ok", "tool": {"name": "run_shell"}},
        expect={"kind": "ask"},
        fixture_kind="ask",
    )
    row = compare_row(
        job,
        live_kind="ask",
        live_reason="uncertain",
        live_extras={"wire_narrow": False},
        live_answers={"action": {"choice": "ask", "confidence": 0.4}},
        latency_ms=120.4,
        model="jev-1.13.0",
    )
    report = build_report(
        [row],
        transport="gateway",
        model="jev-1.13.0",
        key_env="EXCELMANUS_AI_GATEWAY_API_KEY",
    )
    assert report["signed"] is False
    assert report["signoff"] is None
    dumped = json.dumps(report)
    assert "vck_" not in dumped
    json_path, md_path = write_reports(tmp_path / "local", report)
    text = md_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert "人工审阅后才能改 SIGNED_ENFORCE_PACKS / CALIBRATED" in text
    assert payload["signed"] is False
    assert SIGNED_ENFORCE_PACKS == frozenset()
    assert SIGNED_ENFORCE_FAMILIES == frozenset()
    assert render_markdown(payload).startswith("# Jev live")


def test_no_key_exits_without_pretending(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    monkeypatch.delenv("EXCELMANUS_DB_PATH", raising=False)
    for name in (
        *_SETTING_KEY_NAMES,
        "TYPESAFE_API_KEY",
        "AI_GATEWAY_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    code = asyncio.run(async_main(["--limit", "1", "--out-dir", str(tmp_path)], root=tmp_path))
    assert code == NO_KEY_EXIT
    assert list(tmp_path.glob("live_*.json")) == []
    assert SIGNED_ENFORCE_PACKS == frozenset()
