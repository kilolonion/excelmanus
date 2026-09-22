from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))

from jev_recovery_stats import collect  # noqa: E402


def _write_log(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "run.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _line(extras: dict, *, kind: str = "noop", reason: str = "next:inspect_more") -> str:
    return (
        f"2026-09-21 INFO jev decision pack=recovery.next_step gate=enforce "
        f"kind={kind} reason={reason} applied=True extras={extras!r} "
        f"provider=gw protocol=gw model=m latency_ms=10 answers={{}} usage={{}}"
    )


def test_recovery_stats_counts(tmp_path: Path) -> None:
    log = _write_log(
        tmp_path,
        [
            _line({"stage": "advice", "next": "inspect_more", "source": "deterministic"}),
            _line({"stage": "advice", "next": "inspect_more", "source": "jev"}),
            _line({"stage": "effect", "advice_delivered": True, "next": "inspect_more", "source": "jev"}),
            _line({"stage": "outcome", "next": "inspect_more", "source": "jev", "outcome": "escaped"}, kind="outcome", reason="recovery_outcome:escaped"),
            _line({"stage": "outcome", "next": "stop", "source": "deterministic", "outcome": "stopped"}, kind="outcome", reason="recovery_outcome:stopped"),
            _line({"stage": "evaluation", "next": "inspect_more"}),
            _line({"next": "inspect_more"}),
            "unrelated log line",
            "jev decision pack=recovery.next_step extras={bad python} provider=x",
        ],
    )
    stats = collect([str(log)])
    assert stats.advice_by_source == {"deterministic": 1, "jev": 1}
    assert stats.advice_by_next == {"inspect_more": 2}
    assert stats.outcomes["jev"]["escaped"] == 1
    assert stats.outcomes["deterministic"]["stopped"] == 1
    assert stats.parse_failures == 1
    delivered, continued, adoption, escape = stats._rates("jev")
    assert delivered == 1
    assert continued == 1
    assert adoption == 1.0
    assert escape == 1.0
    markdown = stats.to_markdown()
    assert "deterministic" in markdown
    assert "送达后继续调用比例" in markdown
    assert stats.legacy_rows == 1
