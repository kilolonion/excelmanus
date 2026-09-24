"""Reproducible local workbook V2 checks; never calls a model provider."""

from __future__ import annotations
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "test_workbook_v2.py",
    "test_workbook_v2_regressions.py",
    "test_workbook_extensions.py",
    "test_workbook_document.py",
    "test_workbook_view.py",
    "test_workbook_commit.py",
    "test_workbook_merge.py",
    "test_workbook_snapshot.py",
    "test_workbook_spec.py",
    "test_write_contract_live.py",
    "test_agent_tool_call_recovery.py",
    "test_native_tool_closure.py",
    "test_history_robustness.py",
    "test_view_style_fidelity.py",
    "test_print_layout.py",
    "test_tool_contract_roundtrip.py",
    "test_spreadsheet_model_projection.py",
    "test_schema_disclosure_types.py",
    "test_effective_catalog.py",
]
WEB_TESTS = [
    "workbook-command-contract.test.ts",
    "workbook-style-hydration.test.ts",
    "workbook-cache.test.ts",
    "workbook-concurrent-edit.test.ts",
    "revision-history-panel.test.tsx",
    "excel-cell-edit.test.ts",
    "write-excel-cells.test.ts",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Run installed Chromium/Univer scenarios; requires the Node playwright package",
    )
    args = parser.parse_args()
    subprocess.run(
        [sys.executable, "-m", "scripts.generate_workbook_contracts", "--check"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *[f"tests/{name}" for name in TESTS]],
        cwd=ROOT,
        check=True,
    )
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit(
            "npm is required; install web dependencies using npm --prefix web ci"
        )
    subprocess.run(
        [npm, "test", "--", *[f"src/__tests__/{name}" for name in WEB_TESTS]],
        cwd=ROOT / "web",
        check=True,
    )
    if args.browser:
        subprocess.run(
            [shutil.which("node") or "node", "web/scripts/check-workbook-loading.mjs"],
            cwd=ROOT,
            check=True,
        )


if __name__ == "__main__":
    main()
