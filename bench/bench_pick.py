#!/usr/bin/env python3
"""从 SpreadsheetBench dataset 中筛选候选题目，可选生成 suite JSON。

用法示例：
    # 列出 Cell-Level 候选（默认排除已有 suite 中的题）
    python scripts/bench_pick.py

    # 按 type、长度、数量筛选
    python scripts/bench_pick.py --type "Sheet-Level" --min-len 100 --max-len 400 -n 15

    # 关键词过滤（instruction 包含某词，多个为 OR）
    python scripts/bench_pick.py --keyword VLOOKUP --keyword SUMIFS

    # 额外排除特定 ID
    python scripts/bench_pick.py --exclude 52233 43436

    # 生成 suite JSON
    python scripts/bench_pick.py -n 10 --seed 42 -o suite_new.json

    # 指定 dataset 源
    python scripts/bench_pick.py --dataset bench/external/spreadsheetbench/all_data_912_v0.1/dataset.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# 默认路径
_DEFAULT_DATASET = Path(
    "bench/external/spreadsheetbench/spreadsheetbench_verified_400/dataset.json"
)
_CASES_DIR = Path("bench/cases")
_SPREADSHEET_BASE = "bench/external/spreadsheetbench/spreadsheetbench_verified_400"


# ── 自动扫描已用 ID ──────────────────────────────────────


def _scan_used_ids(cases_dir: Path) -> set[str]:
    """扫描 bench/cases/suite_spreadsheetbench_*.json 中所有已用的 source_id。"""
    used: set[str] = set()
    for path in sorted(cases_dir.glob("suite_spreadsheetbench_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for case in data.get("cases", []):
            sid = case.get("expected", {}).get("source_id")
            if sid is not None:
                used.add(str(sid))
    return used


# ── 加载与筛选 ────────────────────────────────────────────


def _load_dataset(path: Path) -> list[dict]:
    """加载 dataset.json。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _filter_candidates(
    data: list[dict],
    *,
    used_ids: set[str],
    instruction_type: str | None = None,
    min_len: int = 0,
    max_len: int = 0,
    keywords: list[str] | None = None,
) -> list[dict]:
    """按条件筛选候选题目。"""
    candidates = []
    for item in data:
        sid = str(item["id"])
        if sid in used_ids:
            continue
        inst = item["instruction"]
        if instruction_type and item["instruction_type"] != instruction_type:
            continue
        if min_len and len(inst) < min_len:
            continue
        if max_len and len(inst) > max_len:
            continue
        if keywords:
            inst_lower = inst.lower()
            if not any(kw.lower() in inst_lower for kw in keywords):
                continue
        candidates.append(item)
    return candidates


# ── 输出 ──────────────────────────────────────────────────


def _print_candidates(candidates: list[dict], n: int) -> None:
    """打印候选列表到终端。"""
    shown = candidates[:n] if n else candidates
    print(f"\n共 {len(candidates)} 个候选，显示前 {len(shown)} 个：\n")
    for i, c in enumerate(shown, 1):
        itype = "Cell" if "Cell" in c["instruction_type"] else "Sheet"
        print(
            f"  {i:>3}. ID: {c['id']:<12} "
            f"Type: {itype:<6} "
            f"Len: {len(c['instruction']):>4} "
            f"Pos: {c['answer_position']}"
        )
        preview = c["instruction"][:120].replace("\n", " ")
        print(f"       {preview}...")
    print()


def _make_case_entry(item: dict, dataset_base: str) -> dict:
    """将 dataset 条目转换为 suite case 格式。"""
    sid = str(item["id"])
    spreadsheet_dir = f"{dataset_base}/spreadsheet/{sid}"
    init_file = f"{spreadsheet_dir}/1_{sid}_init.xlsx"
    golden_file = f"{spreadsheet_dir}/1_{sid}_golden.xlsx"

    itype = item["instruction_type"]
    tag = "cell_level" if "Cell" in itype else "sheet_level"

    message = (
        f"Open the file {init_file}. "
        f"{item['instruction']}"
    )

    expected: dict = {
        "answer_position": item["answer_position"],
        "golden_file": golden_file,
        "source_id": sid,
    }
    if item.get("answer_sheet"):
        expected["answer_sheet"] = item["answer_sheet"]

    return {
        "id": f"sb_{sid.replace('-', '_')}",
        "name": f"SpreadsheetBench #{sid}",
        "message": message,
        "tags": ["spreadsheetbench", "verified_400", tag],
        "expected": expected,
    }


def _generate_suite(
    candidates: list[dict],
    n: int,
    seed: int,
    dataset_base: str,
    output_name: str,
) -> dict:
    """生成 suite JSON 数据。"""
    cell_count = sum(1 for c in candidates if "Cell" in c["instruction_type"])
    sheet_count = len(candidates) - cell_count
    selected = candidates[:n] if n else candidates

    suite = {
        "suite_name": f"SpreadsheetBench 自动抽样套件（{len(selected)}题）",
        "description": (
            f"由 scripts/bench_pick.py 自动生成，"
            f"从 verified-400 筛选 {len(selected)} 题，"
            f"候选池 {len(candidates)} 题"
        ),
        "source": f"SpreadsheetBench (NeurIPS 2024) — {dataset_base}",
        "sampling": {
            "method": "filtered_random",
            "seed": seed,
            "total": len(selected),
            "pool_size": len(candidates),
            "cell_level": sum(1 for c in selected if "Cell" in c["instruction_type"]),
            "sheet_level": sum(1 for c in selected if "Sheet" in c["instruction_type"]),
        },
        "cases": [_make_case_entry(item, dataset_base) for item in selected],
    }
    return suite


# ── CLI ───────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench_pick",
        description="从 SpreadsheetBench dataset 中筛选候选题目",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=_DEFAULT_DATASET,
        help=f"dataset.json 路径（默认 {_DEFAULT_DATASET}）",
    )
    parser.add_argument(
        "--type", "-t",
        dest="instruction_type",
        choices=["Cell-Level Manipulation", "Sheet-Level Manipulation", "Cell", "Sheet"],
        help="按 instruction_type 筛选（Cell / Sheet 为简写）",
    )
    parser.add_argument(
        "--min-len",
        type=int,
        default=0,
        help="instruction 最小长度",
    )
    parser.add_argument(
        "--max-len",
        type=int,
        default=0,
        help="instruction 最大长度（0=不限）",
    )
    parser.add_argument(
        "--keyword", "-k",
        action="append",
        default=[],
        help="instruction 关键词过滤（多个为 OR），不区分大小写",
    )
    parser.add_argument(
        "--exclude", "-x",
        nargs="+",
        default=[],
        help="额外排除的 source_id 列表",
    )
    parser.add_argument(
        "--no-auto-exclude",
        action="store_true",
        help="禁用自动扫描已有 suite 的排除",
    )
    parser.add_argument(
        "-n",
        type=int,
        default=10,
        help="输出数量（默认 10）",
    )
    parser.add_argument(
        "--seed", "-s",
        type=int,
        default=None,
        help="随机种子（默认基于当前日期 YYYYMMDD）",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="生成 suite JSON 文件名（保存到 bench/cases/ 下）",
    )
    return parser


def _normalize_type(raw: str | None) -> str | None:
    """将简写 type 转为完整名。"""
    if raw is None:
        return None
    mapping = {
        "Cell": "Cell-Level Manipulation",
        "Sheet": "Sheet-Level Manipulation",
    }
    return mapping.get(raw, raw)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # 加载 dataset
    if not args.dataset.exists():
        print(f"错误：dataset 文件不存在: {args.dataset}", file=sys.stderr)
        return 1
    data = _load_dataset(args.dataset)
    print(f"已加载 {len(data)} 条数据")

    # 收集排除 ID
    used_ids: set[str] = set()
    if not args.no_auto_exclude:
        used_ids = _scan_used_ids(_CASES_DIR)
        print(f"自动排除已有 suite 中的 {len(used_ids)} 个 ID")
    if args.exclude:
        extra = set(str(x) for x in args.exclude)
        used_ids |= extra
        print(f"额外排除 {len(extra)} 个 ID")

    # 筛选
    instruction_type = _normalize_type(args.instruction_type)
    candidates = _filter_candidates(
        data,
        used_ids=used_ids,
        instruction_type=instruction_type,
        min_len=args.min_len,
        max_len=args.max_len,
        keywords=args.keyword or None,
    )

    if not candidates:
        print("没有符合条件的候选题目。")
        return 0

    # 随机打乱
    seed = args.seed if args.seed is not None else int(datetime.now().strftime("%Y%m%d"))
    random.seed(seed)
    random.shuffle(candidates)
    print(f"随机种子: {seed}")

    # 打印候选
    _print_candidates(candidates, args.n)

    # 生成 suite JSON
    if args.output:
        dataset_base = str(args.dataset.parent)
        suite = _generate_suite(candidates, args.n, seed, dataset_base, args.output)
        output_path = _CASES_DIR / args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(suite, f, ensure_ascii=False, indent=2)
        print(f"✅ Suite 已保存: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
