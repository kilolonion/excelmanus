#!/usr/bin/env python3
"""提示词段预算：--report 打印，--check 按 frontmatter max_tokens 判定。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from excelmanus.prompt.budget import collect_report, format_report, over_budget_names


def main() -> int:
    parser = argparse.ArgumentParser(description="测量提示词段与场景预算")
    parser.add_argument("--report", action="store_true", help="打印四栏预算报告")
    parser.add_argument("--check", action="store_true", help="有超限段时以非零退出")
    parser.add_argument("--json", type=Path, default=None, help="把完整报告写入 JSON")
    args = parser.parse_args()
    if not args.report and not args.check and args.json is None:
        args.report = True

    report = collect_report()
    if args.report:
        sys.stdout.write(format_report(report))
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    over = over_budget_names(report)
    if args.check and over:
        sys.stderr.write("超限段: " + ", ".join(over) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
