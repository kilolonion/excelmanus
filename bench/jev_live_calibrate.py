#!/usr/bin/env python3
"""Jev live 中文对照。有密钥才打网；永不自动签字。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from excelmanus.system_one.live_calibrate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
