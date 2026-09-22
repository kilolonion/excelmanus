"""统计 jev decision 日志里 recovery.next_step 的建议送达及后续工具结果。

用法：python bench/jev_recovery_stats.py "logs/*.log" [更多 glob...]

匹配 ``record_jev_decision`` 输出的行：
``jev decision pack=recovery.next_step ... kind=... reason=... extras={...}``
extras 用 ``ast.literal_eval`` 解析；解析失败的行跳过并计数。

输出 Markdown：按来源（deterministic / jev）分列的建议次数、next 分布、
outcome 分布、送达后继续调用比例、后续工具成功比例。不能推断建议采纳或任务成功。
"""

from __future__ import annotations

import ast
import glob
import re
import sys
from dataclasses import dataclass, field

OUTCOMES = ("escaped", "different_failure", "repeated", "not_continued", "not_delivered", "stopped")
SOURCES = ("deterministic", "jev")

_LINE_RE = re.compile(r"jev decision pack=recovery\.next_step\b.*?\bextras=")
_EXTRAS_RE = re.compile(r"\bextras=(\{.*\})\s+provider=")


@dataclass
class RecoveryStats:
    parse_failures: int = 0
    # advice lifecycle rows; an evaluation is not an advice row.
    advice_by_source: dict[str, int] = field(default_factory=lambda: {s: 0 for s in SOURCES})
    advice_by_next: dict[str, int] = field(default_factory=dict)
    delivered_by_source: dict[str, int] = field(default_factory=lambda: {s: 0 for s in SOURCES})
    legacy_rows: int = 0
    # outcome 行
    outcomes: dict[str, dict[str, int]] = field(
        default_factory=lambda: {s: {o: 0 for o in OUTCOMES} for s in SOURCES}
    )
    outcome_total: int = 0

    def record_line(self, line: str) -> bool:
        """是 recovery.next_step 决策行则记录并返回 True。"""
        if not _LINE_RE.search(line):
            return False
        match = _EXTRAS_RE.search(line)
        extras: dict | None = None
        if match is not None:
            try:
                parsed = ast.literal_eval(match.group(1))
                if isinstance(parsed, dict):
                    extras = parsed
            except (SyntaxError, ValueError):
                extras = None
        if extras is None:
            self.parse_failures += 1
            return True
        outcome = str(extras.get("outcome") or "")
        source = str(extras.get("source") or "jev")
        if source not in SOURCES:
            source = "jev"
        stage = str(extras.get("stage") or "")
        if not stage:
            self.legacy_rows += 1
            return True
        if outcome and stage in {"outcome", "advice_outcome"}:
            self.outcome_total += 1
            bucket = self.outcomes[source]
            bucket[outcome] = bucket.get(outcome, 0) + 1
            return True
        if stage == "effect" and extras.get("advice_delivered"):
            self.delivered_by_source[source] += 1
            return True
        if stage != "advice":
            return True
        self.advice_by_source[source] += 1
        nxt = str(extras.get("next") or "unknown")
        self.advice_by_next[nxt] = self.advice_by_next.get(nxt, 0) + 1
        return True

    def _rates(self, source: str) -> tuple[int, int, float | None, float | None]:
        delivered = self.delivered_by_source[source]
        continued = sum(self.outcomes[source].get(name, 0) for name in ("escaped", "different_failure", "repeated"))
        adoption = (continued / delivered) if delivered > 0 and continued <= delivered else None
        escape = (self.outcomes[source]["escaped"] / continued) if continued > 0 else None
        return delivered, continued, adoption, escape

    def to_markdown(self) -> str:
        lines: list[str] = ["# Jev 恢复建议与后续工具结果", ""]
        lines.append("以下比例不代表建议被遵循、任务完成或因果改善；没有生命周期标记的旧日志不计入比率。")
        lines.append("")
        lines.append("## 建议次数（按来源）")
        lines.append("")
        lines.append("| source | advice |")
        lines.append("|---|---|")
        for source in SOURCES:
            lines.append(f"| {source} | {self.advice_by_source[source]} |")
        lines.append("")
        lines.append("## 建议次数（按 next）")
        lines.append("")
        lines.append("| next | count |")
        lines.append("|---|---|")
        for nxt in sorted(self.advice_by_next):
            lines.append(f"| {nxt} | {self.advice_by_next[nxt]} |")
        lines.append("")
        lines.append("## Outcome 分布")
        lines.append("")
        header = "| source | " + " | ".join(OUTCOMES) + " |"
        lines.append(header)
        lines.append("|---|" + "---|" * len(OUTCOMES))
        for source in SOURCES:
            row = self.outcomes[source]
            lines.append(
                f"| {source} | " + " | ".join(str(row.get(o, 0)) for o in OUTCOMES) + " |"
            )
        lines.append("")
        lines.append("## 比率")
        lines.append("")
        lines.append("| source | 已送达 | 后续有工具结果 | 送达后继续调用比例 | 后续工具成功比例 |")
        lines.append("|---|---|---|---|---|")
        for source in SOURCES:
            delivered, continued, adoption, escape = self._rates(source)
            adoption_s = f"{adoption:.2%}" if adoption is not None else "—"
            escape_s = f"{escape:.2%}" if escape is not None else "—"
            lines.append(f"| {source} | {delivered} | {continued} | {adoption_s} | {escape_s} |")
        lines.append("")
        lines.append(f"解析失败行数：{self.parse_failures}")
        lines.append(f"未计入的旧格式行数：{self.legacy_rows}")
        return "\n".join(lines)


def collect(paths: list[str]) -> RecoveryStats:
    stats = RecoveryStats()
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    stats.record_line(line)
        except OSError:
            stats.parse_failures += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    paths: list[str] = []
    for pattern in args:
        matched = sorted(glob.glob(pattern))
        paths.extend(matched if matched else [pattern])
    if not paths:
        print("用法: python bench/jev_recovery_stats.py <log-glob> [...]")
        return 2
    print(collect(paths).to_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
