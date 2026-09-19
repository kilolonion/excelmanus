"""按 wave / case id 裁一套 JSON，给 run.ps1 用。不改原套件文件。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def filter_suite(
    data: dict,
    *,
    wave: str = "",
    case_ids: list[str] | None = None,
) -> dict:
    wanted_ids = {item.strip() for item in (case_ids or []) if item.strip()}
    wave_tag = f"wave-{wave.strip()}" if wave.strip() else ""
    cases = []
    for case in data.get("cases", []):
        tags = [str(tag) for tag in case.get("tags", [])]
        if wanted_ids and case.get("id") not in wanted_ids:
            continue
        if wave_tag and wave_tag not in tags:
            continue
        cases.append(case)
    if not cases:
        raise ValueError("过滤后没有用例")
    filtered = dict(data)
    filtered["cases"] = cases
    return filtered


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--wave", default="")
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    source = Path(args.suite)
    data = json.loads(source.read_text(encoding="utf-8"))
    filtered = filter_suite(data, wave=args.wave, case_ids=args.case)
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
