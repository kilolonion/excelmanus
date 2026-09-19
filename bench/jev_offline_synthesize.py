#!/usr/bin/env python3
"""离线合成片 D 夹具。固定 JSON 答案，禁止打网。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from excelmanus.system_one.calibration import (  # noqa: E402
    load_suite_states,
    run_offline_fixture,
)


def main() -> int:
    rows = run_offline_fixture()
    failed = [row for row in rows if not row["ok"]]
    print(json.dumps({"samples": rows, "failed": len(failed)}, ensure_ascii=False, indent=2))
    suites = load_suite_states()
    print(f"# suite-mapped states: {len(suites)}", file=sys.stderr)
    print(f"# fixture samples: {len(rows)} failed={len(failed)}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
