"""大对象 spill + 写后语义校验。

Wire / durable 不再内联超大工具结果：正文进 workspace 内 content-addressed
仓库，模型只看到摘要 + opaque locator。取回是内部函数；dispatcher 把既有
读工具的 ``file_path`` 识别为句柄后走同一通路，不新增工具名。

写后校验产出有界的 ``meta.write_verification``（值/公式分列），替代
max_row 维度假象。``write_operations_log`` 仍是会话级索引，不平行再造一份
单元格日志。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from excelmanus.engine_core.error_payload import (
    FAILURE_INTERNAL,
    FAILURE_NOT_FOUND,
    NOT_FOUND,
    RESULT_UNCERTAIN,
    SHEET_NOT_FOUND,
    TOOL_ERROR,
    make_error_payload,
)
from excelmanus.engine_core.tool_result import ToolResult
from excelmanus.logger import get_logger

logger = get_logger("spill")

# ── 阈值 ──────────────────────────────────────────────────
# 默认硬截断是 12000 字符（config.tool_result_hard_cap_chars）。spill 必须
# 独立、且默认低于该值，这样超大结果先外置再截断，句柄不会被砍掉。
# 8000 字符 ≈ 一次 inspect 预览的数倍、远小于整表 dump；2000 token 按
# utf-8 字节/4 估算，与 ASCII 8000 字符同量级，CJK 会更早外置（更安全）。
DEFAULT_SPILL_CHAR_THRESHOLD = 8000
DEFAULT_SPILL_BYTE_THRESHOLD = 24_000
DEFAULT_SPILL_TOKEN_THRESHOLD = 2000
DEFAULT_PREVIEW_CHARS = 400
DEFAULT_PREVIEW_LINES = 12

# 写后校验：最多展示 20 条单元格，采样时最多走 64 个意图坐标。
# 不扫整表 used range；只读 operations 声明过的格子。
MAX_WRITE_VERIFY_ENTRIES = 20
MAX_WRITE_VERIFY_SAMPLE = 64

SPILL_DIR_REL = ".excelmanus/spill"
LOCATOR_PREFIX = "spill:"
_LOCATOR_RE = re.compile(r"^spill:[0-9a-f]{64}$")
_HOST_PATH_RE = re.compile(r"(?:^[A-Za-z]:\\)|(?:/Users/)|(?:/home/)")
_CELL_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})$")

ChangeKind = Literal["value", "formula"]


class SpillNotFound(KeyError):
    """句柄不存在或已损坏。"""


class SpillLocator(str):
    """Opaque content-addressed 句柄，形如 ``spill:<sha256>``。

    故意不携带任何文件系统路径。创建后不可再拼接目录。
    """

    __slots__ = ()

    def hex_digest(self) -> str:
        return str(self)[len(LOCATOR_PREFIX) :]


def is_spill_locator(text: str | None) -> bool:
    raw = str(text or "").strip()
    if not _LOCATOR_RE.fullmatch(raw):
        return False
    return _HOST_PATH_RE.search(raw) is None


def parse_locator(text: str) -> SpillLocator:
    raw = str(text or "").strip()
    if not is_spill_locator(raw):
        raise ValueError(f"非法 spill 句柄: {text!r}")
    return SpillLocator(raw)


def estimate_tokens(text: str) -> int:
    """无 tokenizer 时的保守估算：utf-8 字节 / 4（CJK 偏高 → 更早 spill）。"""
    raw = text if isinstance(text, str) else str(text or "")
    return max(1, (len(raw.encode("utf-8")) + 3) // 4) if raw else 0


def should_spill(
    text: str,
    *,
    char_threshold: int = DEFAULT_SPILL_CHAR_THRESHOLD,
    byte_threshold: int = DEFAULT_SPILL_BYTE_THRESHOLD,
    token_threshold: int = DEFAULT_SPILL_TOKEN_THRESHOLD,
) -> bool:
    raw = text if isinstance(text, str) else str(text or "")
    if not raw:
        return False
    if char_threshold > 0 and len(raw) > char_threshold:
        return True
    data = raw.encode("utf-8")
    if byte_threshold > 0 and len(data) > byte_threshold:
        return True
    if token_threshold > 0 and estimate_tokens(raw) > token_threshold:
        return True
    return False


def extract_spill_locator(arguments: dict[str, Any] | None) -> str | None:
    """从工具参数里取出 spill 句柄（优先 file_path / path）。"""
    if not isinstance(arguments, dict):
        return None
    for key in ("file_path", "path", "locator", "spill"):
        value = arguments.get(key)
        if isinstance(value, str) and is_spill_locator(value):
            return value.strip()
    for value in arguments.values():
        if isinstance(value, str) and is_spill_locator(value):
            return value.strip()
    return None


@dataclass(frozen=True)
class SpillProjection:
    model_text: str
    locator: SpillLocator | None
    spilled: bool
    chars: int
    bytes: int
    estimated_tokens: int
    kind: str = "text"
    preview: str = ""

    def summary_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "spilled": self.spilled,
            "spill": self.locator,
            "kind": self.kind,
            "chars": self.chars,
            "bytes": self.bytes,
            "tokens": self.estimated_tokens,
            "preview": self.preview,
            "retrieve": (
                "把 spill 句柄当作 file_path 传给 read_text_file 或 "
                "inspect_spreadsheet 取回原文；句柄不含宿主路径。"
            ),
        }
        return payload


class SpillStore:
    """workspace 内 content-addressed 外置仓库。

    目录：``{workspace}/.excelmanus/spill/{sha256}``。
    句柄只有 ``spill:{sha256}``，文件名也只有 hex，从不写入绝对路径。
    """

    def __init__(self, workspace_root: str | Path) -> None:
        root = Path(workspace_root).expanduser()
        self._workspace_root = root
        self._dir = root / ".excelmanus" / "spill"

    @property
    def directory(self) -> Path:
        return self._dir

    def put(self, content: str) -> SpillLocator:
        data = (content if isinstance(content, str) else str(content or "")).encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        locator = SpillLocator(f"{LOCATOR_PREFIX}{digest}")
        self._dir.mkdir(parents=True, exist_ok=True)
        dest = self._dir / digest
        if dest.is_file() and dest.stat().st_size == len(data):
            return locator
        fd, tmp_name = tempfile.mkstemp(prefix=".spill_", dir=str(self._dir))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp_name, str(dest))
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return locator

    def get(self, locator: str) -> str:
        parsed = parse_locator(locator)
        path = self._dir / parsed.hex_digest()
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise SpillNotFound(str(parsed)) from exc
        except OSError as exc:
            raise SpillNotFound(str(parsed)) from exc
        actual = hashlib.sha256(data).hexdigest()
        if actual != parsed.hex_digest():
            raise SpillNotFound(str(parsed))
        return data.decode("utf-8")

    def exists(self, locator: str) -> bool:
        try:
            parsed = parse_locator(locator)
        except ValueError:
            return False
        return (self._dir / parsed.hex_digest()).is_file()


def retrieve_spill(locator: str, *, workspace_root: str | Path) -> str:
    """按句柄取回原文。内部通路；不要新增面向用户的工具名。"""
    return SpillStore(workspace_root).get(locator)


def retrieve_spill_result(locator: str, *, workspace_root: str | Path) -> ToolResult:
    """给 dispatcher 复用读工具时的 ToolResult 投影。"""
    try:
        text = retrieve_spill(locator, workspace_root=workspace_root)
    except (SpillNotFound, ValueError):
        payload = make_error_payload(
            f"spill 句柄不存在或已失效：{locator}",
            error_code=NOT_FOUND,
            failure_class=FAILURE_NOT_FOUND,
            locator=str(locator),
        )
        return ToolResult(
            success=False,
            model_text=json.dumps(payload, ensure_ascii=False),
            value=payload,
            coverage={"spill_retrieve": True, "kind": "missing"},
        )
    parsed: Any = text
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            loaded = json.loads(stripped)
        except (json.JSONDecodeError, TypeError, ValueError):
            loaded = None
        if isinstance(loaded, dict):
            parsed = loaded
    return ToolResult(
        success=True,
        model_text=text,
        value=parsed,
        coverage={"spill_retrieve": True, "kind": "complete"},
    )


def _preview_text(text: str, *, max_chars: int, max_lines: int) -> tuple[str, int, str]:
    lines = text.splitlines()
    kind = "text"
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            json.loads(stripped)
            kind = "json"
        except (json.JSONDecodeError, TypeError, ValueError):
            kind = "text"
    if len(lines) > max_lines:
        chunk = "\n".join(lines[:max_lines])
        if len(chunk) > max_chars:
            chunk = chunk[:max_chars]
        preview = f"{chunk}\n… ({len(lines) - max_lines} more lines)"
    elif len(text) > max_chars:
        preview = text[:max_chars] + "…"
    else:
        preview = text
    return preview, len(lines), kind


def _copy_small_fields(original: str, envelope: dict[str, Any]) -> None:
    stripped = original.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, TypeError, ValueError):
        return
    if not isinstance(parsed, dict):
        return
    for key in ("status", "file_path", "path", "content_version", "sheet", "range"):
        if key in parsed and parsed[key] is not None:
            envelope[key] = parsed[key]
    meta = parsed.get("meta")
    if isinstance(meta, dict):
        slim = {
            key: value
            for key, value in meta.items()
            if key != "spill" and not isinstance(value, (list, dict))
        }
        if slim:
            envelope.setdefault("meta", {}).update(slim)


def project_for_wire(
    text: str,
    *,
    store: SpillStore,
    char_threshold: int = DEFAULT_SPILL_CHAR_THRESHOLD,
    byte_threshold: int = DEFAULT_SPILL_BYTE_THRESHOLD,
    token_threshold: int = DEFAULT_SPILL_TOKEN_THRESHOLD,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
    preview_lines: int = DEFAULT_PREVIEW_LINES,
) -> SpillProjection:
    raw = text if isinstance(text, str) else str(text or "")
    chars = len(raw)
    data_len = len(raw.encode("utf-8"))
    tokens = estimate_tokens(raw)
    if not should_spill(
        raw,
        char_threshold=char_threshold,
        byte_threshold=byte_threshold,
        token_threshold=token_threshold,
    ):
        return SpillProjection(
            model_text=raw,
            locator=None,
            spilled=False,
            chars=chars,
            bytes=data_len,
            estimated_tokens=tokens,
        )
    locator = store.put(raw)
    preview, _line_count, kind = _preview_text(
        raw, max_chars=preview_chars, max_lines=preview_lines,
    )
    projection = SpillProjection(
        model_text="",
        locator=locator,
        spilled=True,
        chars=chars,
        bytes=data_len,
        estimated_tokens=tokens,
        kind=kind,
        preview=preview,
    )
    envelope = projection.summary_payload()
    _copy_small_fields(raw, envelope)
    # 句柄放在最前，避免后续硬截断切掉 locator。
    ordered = {"spill": locator, **{k: v for k, v in envelope.items() if k != "spill"}}
    model_text = json.dumps(ordered, ensure_ascii=False, default=str)
    return replace(projection, model_text=model_text)


def apply_spill(
    result: ToolResult,
    *,
    store: SpillStore,
    char_threshold: int = DEFAULT_SPILL_CHAR_THRESHOLD,
    byte_threshold: int = DEFAULT_SPILL_BYTE_THRESHOLD,
    token_threshold: int = DEFAULT_SPILL_TOKEN_THRESHOLD,
) -> ToolResult:
    """只改 model_text（呈现）；value 原字段保留，外置信息进 meta.spill。"""
    if result.coverage and result.coverage.get("spill_retrieve"):
        return result
    projection = project_for_wire(
        result.model_text,
        store=store,
        char_threshold=char_threshold,
        byte_threshold=byte_threshold,
        token_threshold=token_threshold,
    )
    if not projection.spilled:
        return result
    value = result.value
    model_text = projection.model_text
    if isinstance(value, dict):
        value = dict(value)
        meta = dict(value.get("meta") or {})
        meta["spill"] = {
            "locator": projection.locator,
            "kind": projection.kind,
            "chars": projection.chars,
            "bytes": projection.bytes,
        }
        value["meta"] = meta
        write_ver = meta.get("write_verification")
        if write_ver:
            try:
                envelope = json.loads(model_text)
            except (json.JSONDecodeError, TypeError, ValueError):
                envelope = None
            if isinstance(envelope, dict):
                envelope["write_verification"] = write_ver
                model_text = json.dumps(envelope, ensure_ascii=False, default=str)
    coverage = dict(result.coverage or {})
    coverage["spilled"] = True
    coverage["declared"] = True
    return replace(
        result,
        model_text=model_text,
        value=value,
        truncated=True,
        coverage=coverage,
    )


# ── 写后语义校验 ─────────────────────────────────────────


def _cell_addr(row: int, col: int) -> str:
    from openpyxl.utils import get_column_letter

    return f"{get_column_letter(col)}{row}"


def _parse_cell(token: str) -> tuple[int, int] | None:
    from openpyxl.utils import column_index_from_string

    text = str(token or "").strip().split("!")[-1].replace("$", "")
    match = _CELL_RE.match(text)
    if not match:
        return None
    try:
        return int(match.group(2)), column_index_from_string(match.group(1))
    except (ValueError, TypeError):
        return None


def _is_formula(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("=")


def _date_semantic_equal(a: Any, b: Any) -> bool:
    """date/datetime 语义等价：混比时要求 datetime 侧为午夜，避免吞掉时间差异。"""
    if isinstance(a, datetime.datetime) and isinstance(b, datetime.datetime):
        return a == b
    a_date = a.date() if isinstance(a, datetime.datetime) else a
    b_date = b.date() if isinstance(b, datetime.datetime) else b
    if a_date != b_date:
        return False
    for value in (a, b):
        if isinstance(value, datetime.datetime) and value.time() != datetime.time(0, 0):
            return False
    return True


def _str_date_equal(text: str, dt_value: Any) -> bool:
    """ISO 日期/时间字符串 vs 读回的日期值（序列化边界会把 date 降级为字符串）。"""
    s = text.strip()
    try:
        parsed: Any = (
            datetime.datetime.fromisoformat(s)
            if "T" in s or " " in s
            else datetime.date.fromisoformat(s)
        )
    except ValueError:
        return False
    return _date_semantic_equal(parsed, dt_value)


def _values_equal(expected: Any, actual: Any) -> bool:
    if expected is actual:
        return True
    if expected is None or actual is None:
        return expected is None and actual is None
    if _is_formula(expected) or _is_formula(actual):
        return str(expected).strip() == str(actual).strip()
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return float(expected) == float(actual)
    date_types = (datetime.date, datetime.datetime)
    if isinstance(expected, date_types) and isinstance(actual, date_types):
        return _date_semantic_equal(expected, actual)
    if isinstance(expected, str) and isinstance(actual, date_types):
        return _str_date_equal(expected, actual)
    if isinstance(actual, str) and isinstance(expected, date_types):
        return _str_date_equal(actual, expected)
    return expected == actual


def _sample_grid_coords(n_rows: int, n_cols: int, limit: int) -> list[tuple[int, int]]:
    if n_rows <= 0 or n_cols <= 0:
        return []
    total = n_rows * n_cols
    if total <= limit:
        return [(r, c) for r in range(n_rows) for c in range(n_cols)]
    picked: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()

    def add(r: int, c: int) -> None:
        key = (max(0, min(n_rows - 1, r)), max(0, min(n_cols - 1, c)))
        if key not in seen:
            seen.add(key)
            picked.append(key)

    add(0, 0)
    add(0, n_cols - 1)
    add(n_rows - 1, 0)
    add(n_rows - 1, n_cols - 1)
    remaining = limit - len(picked)
    if remaining > 0:
        stride = max(1, total // (remaining + 1))
        for index in range(stride, total, stride):
            add(index // n_cols, index % n_cols)
            if len(picked) >= limit:
                break
    return picked[:limit]


def _op_get(op: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in op and op[key] is not None:
            return op[key]
    return None


def _intended_from_write_op(op: dict[str, Any], default_sheet: str) -> tuple[list[dict[str, Any]], int]:
    start_raw = str(_op_get(op, "start_cell", "startCell", "cell", "start") or "")
    sheet = str(_op_get(op, "sheet", "sheet_name") or default_sheet or "")
    if "!" in start_raw:
        maybe_sheet, start_raw = start_raw.split("!", 1)
        sheet = sheet or maybe_sheet.strip("'")
    parsed = _parse_cell(start_raw)
    values = _op_get(op, "values")
    if parsed is None or not isinstance(values, list) or not values:
        return [], 0
    row0, col0 = parsed
    n_rows = len(values)
    first = values[0] if values else []
    n_cols = len(first) if isinstance(first, list) else 1
    total = 0
    for row in values:
        cells = row if isinstance(row, list) else [row]
        total += len(cells)
    coords = _sample_grid_coords(n_rows, n_cols, MAX_WRITE_VERIFY_SAMPLE)
    intended: list[dict[str, Any]] = []
    for r_off, c_off in coords:
        row = values[r_off]
        cells = row if isinstance(row, list) else [row]
        if c_off >= len(cells):
            continue
        raw = cells[c_off]
        intended.append({
            "sheet": sheet,
            "row": row0 + r_off,
            "col": col0 + c_off,
            "expected": raw,
            "kind": "formula" if _is_formula(raw) else "value",
        })
    return intended, total


def collect_intended_cells(
    arguments: dict[str, Any],
    *,
    limit: int = MAX_WRITE_VERIFY_SAMPLE,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """从写入参数抽出有界意图格子 + 结构改动。不打开工作簿。"""
    default_sheet = str(arguments.get("sheet") or arguments.get("sheet_name") or "")
    ops = arguments.get("operations")
    intended: list[dict[str, Any]] = []
    total = 0
    structure: list[dict[str, Any]] = []
    if not isinstance(ops, list):
        return intended, total, structure
    for raw in ops:
        if not isinstance(raw, dict):
            continue
        kind = str(_op_get(raw, "kind") or "")
        if kind == "write" or (kind == "" and _op_get(raw, "values") is not None):
            cells, count = _intended_from_write_op(raw, default_sheet)
            intended.extend(cells)
            total += count
        elif kind in {"insert", "delete_rows", "delete_columns"}:
            structure.append({
                "kind": kind,
                "sheet": str(_op_get(raw, "sheet", "sheet_name") or default_sheet),
                "at": _op_get(raw, "at", "row", "column"),
                "count": _op_get(raw, "count") or 1,
            })
        elif kind == "copy":
            start = str(_op_get(raw, "target_start", "targetStart") or "A1")
            parsed = _parse_cell(start)
            if parsed:
                intended.append({
                    "sheet": str(
                        _op_get(raw, "target_sheet", "targetSheet") or default_sheet
                    ),
                    "row": parsed[0],
                    "col": parsed[1],
                    "expected": None,
                    "kind": "value",
                    "note": "copy-dest",
                })
                total += 1
        if len(intended) >= limit:
            intended = intended[:limit]
            break
    return intended[:limit], total, structure[:MAX_WRITE_VERIFY_ENTRIES]


def _resolve_workbook_path(file_path: str, workspace_root: str) -> Path | None:
    if not file_path:
        return None
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = Path(workspace_root) / file_path
    try:
        return candidate.resolve()
    except OSError:
        return None


def _error_verification(
    message: str,
    *,
    error_code: str,
    failure_class: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    payload = make_error_payload(
        message,
        error_code=error_code,
        failure_class=failure_class,
        **fields,
    )
    payload.setdefault("value_changes", [])
    payload.setdefault("formula_changes", [])
    payload.setdefault("total_changes", 0)
    payload.setdefault("shown", 0)
    payload.setdefault("truncated", False)
    return payload


def verify_write(
    tool_name: str,
    arguments: dict[str, Any] | None,
    *,
    workspace_root: str,
    entry_limit: int = MAX_WRITE_VERIFY_ENTRIES,
) -> dict[str, Any]:
    """抽样回读：只读 operations 声明的格子，不上整表扫描。

    抽样：矩形 write 取四角 + 均匀步进，上限 ``MAX_WRITE_VERIFY_SAMPLE``；
    展示再截到 ``entry_limit``（默认 20）并给出总数。
    """
    arguments = arguments or {}
    excel_tools = {
        "edit_spreadsheet",
        "format_spreadsheet",
        "manage_spreadsheet_objects",
        "manage_spreadsheet_versions",
    }
    if tool_name == "write_word":
        return _verify_write_word(arguments, workspace_root)
    if tool_name not in excel_tools:
        return {"skipped": True, "status": "success"}

    file_path = str(arguments.get("file_path") or arguments.get("path") or "").strip()
    if not file_path:
        return {"skipped": True, "status": "success"}

    abs_path = _resolve_workbook_path(file_path, workspace_root)
    if abs_path is None or not abs_path.is_file():
        return _error_verification(
            f"写后回读找不到文件：{file_path}",
            error_code=NOT_FOUND,
            failure_class=FAILURE_NOT_FOUND,
            file_path=file_path,
        )

    intended, total_intended, structure = collect_intended_cells(arguments)
    try:
        from excelmanus.workbook.snapshot import SnapshotError, open_snapshot_at, require_default_sheet
        from excelmanus.workspace.refs import WorkspaceRef

        rel = str(file_path).replace("\\", "/")
        try:
            rel = str(abs_path.resolve().relative_to(Path(workspace_root).resolve())).replace("\\", "/")
        except ValueError:
            pass
        # content_version 是写前乐观锁版本，写后必然过期——只认提交管线回传的 after_version
        expected = str(arguments.get("after_version") or "").strip() or None
        snap = open_snapshot_at(
            abs_path,
            relative=rel,
            workspace=WorkspaceRef.from_root(workspace_root),
            expected_version=expected,
        )
        wb = snap.open_workbook(data_only=False, read_only=False)
    except SnapshotError as exc:
        return _error_verification(
            f"写后回读打开失败：{exc}",
            error_code=getattr(exc, "code", TOOL_ERROR),
            failure_class=FAILURE_INTERNAL,
            file_path=file_path,
        )
    except Exception as exc:
        return _error_verification(
            f"写后回读打开失败：{exc}",
            error_code=TOOL_ERROR,
            failure_class=FAILURE_INTERNAL,
            file_path=file_path,
        )

    value_changes: list[dict[str, Any]] = []
    formula_changes: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    try:
        sheets_available = list(wb.sheetnames)
        for item in intended:
            sheet_name = item.get("sheet") or arguments.get("sheet") or arguments.get("sheet_name")
            try:
                title = require_default_sheet(sheets_available, sheet_name)
            except SnapshotError as exc:
                return _error_verification(
                    str(exc),
                    error_code=getattr(exc, "code", SHEET_NOT_FOUND),
                    failure_class=FAILURE_NOT_FOUND,
                    sheet=sheet_name,
                    available_sheets=sheets_available,
                )
            if title not in wb.sheetnames:
                return _error_verification(
                    f"写后回读工作表不存在：{title}",
                    error_code=SHEET_NOT_FOUND,
                    failure_class=FAILURE_NOT_FOUND,
                    sheet=title,
                    available_sheets=sheets_available,
                )
            ws = wb[title]
            row = int(item["row"])
            col = int(item["col"])
            cell = ws.cell(row=row, column=col)
            actual = cell.value
            addr = _cell_addr(row, col)
            kind: ChangeKind = "formula" if _is_formula(item.get("expected")) or _is_formula(actual) else "value"
            if item.get("note") == "copy-dest":
                entry = {
                    "sheet": ws.title,
                    "cell": addr,
                    "value": actual if kind == "value" else None,
                    "formula": actual if kind == "formula" else None,
                }
                (formula_changes if kind == "formula" else value_changes).append(entry)
                continue
            expected = item.get("expected")
            if not _values_equal(expected, actual):
                mismatches.append({
                    "sheet": ws.title,
                    "cell": addr,
                    "kind": kind,
                    "expected": expected,
                    "actual": actual,
                })
                continue
            if kind == "formula":
                formula_changes.append({
                    "sheet": ws.title,
                    "cell": addr,
                    "formula": actual,
                })
            else:
                value_changes.append({
                    "sheet": ws.title,
                    "cell": addr,
                    "value": actual,
                })
    finally:
        try:
            wb.close()
        except Exception:
            pass

    shown_values = value_changes[:entry_limit]
    remaining = max(0, entry_limit - len(shown_values))
    shown_formulas = formula_changes[:remaining]
    shown_mismatches = mismatches[: max(0, entry_limit - len(shown_values) - len(shown_formulas))]
    listed = len(shown_values) + len(shown_formulas) + len(shown_mismatches)
    total_changes = total_intended or (len(value_changes) + len(formula_changes) + len(mismatches))
    truncated = total_changes > listed or len(value_changes) > len(shown_values) or len(formula_changes) > len(shown_formulas)

    sheet_hint = str(
        arguments.get("sheet")
        or arguments.get("sheet_name")
        or ""
    )
    if not sheet_hint:
        ops = arguments.get("operations") or []
        if isinstance(ops, list):
            for raw in ops:
                if isinstance(raw, dict):
                    sheet_hint = str(_op_get(raw, "sheet", "sheet_name") or "")
                    if sheet_hint:
                        break
    if not sheet_hint:
        for bucket in (value_changes, formula_changes, mismatches):
            if bucket:
                sheet_hint = str(bucket[0].get("sheet") or "")
                break
    base: dict[str, Any] = {
        "sheet": sheet_hint,
        "value_changes": shown_values,
        "formula_changes": shown_formulas,
        "structure_changes": structure[:entry_limit],
        "total_changes": total_changes,
        "shown": listed,
        "truncated": truncated,
        "sampled": True,
        "coverage": {
            "kind": "sampled",
            "sample_size": len(intended),
            "snapshot_id": snap.id.key(),
        },
        "snapshot_id": snap.id.key(),
        "content_version": snap.content_version,
    }
    if mismatches:
        extra = dict(base)
        extra["mismatches"] = shown_mismatches
        extra["mismatch_count"] = len(mismatches)
        return _error_verification(
            f"写后抽样发现 {len(mismatches)} 格与意图不符（抽样 {len(intended)} / 意图 {total_changes}）",
            error_code=RESULT_UNCERTAIN,
            **extra,
        )
    base["status"] = "success"
    return base


def _verify_write_word(arguments: dict[str, Any], workspace_root: str) -> dict[str, Any]:
    file_path = str(arguments.get("file_path") or "").strip()
    if not file_path:
        return {"skipped": True, "status": "success"}
    abs_path = _resolve_workbook_path(file_path, workspace_root)
    if abs_path is None or not abs_path.is_file():
        return _error_verification(
            f"写后回读找不到 Word 文件：{file_path}",
            error_code=NOT_FOUND,
            failure_class=FAILURE_NOT_FOUND,
            file_path=file_path,
        )
    try:
        from docx import Document as _Document

        doc = _Document(str(abs_path))
        return {
            "status": "success",
            "kind": "word",
            "paragraphs": len(doc.paragraphs),
            "tables": len(doc.tables),
            "value_changes": [],
            "formula_changes": [],
            "total_changes": 0,
            "shown": 0,
            "truncated": False,
        }
    except Exception as exc:
        return _error_verification(
            f"写后回读 Word 失败：{exc}",
            error_code=TOOL_ERROR,
            failure_class=FAILURE_INTERNAL,
            file_path=file_path,
        )


def format_write_verification_line(payload: dict[str, Any]) -> str:
    if not payload or payload.get("skipped"):
        return ""
    if payload.get("status") == "error" or payload.get("error_code"):
        code = payload.get("error_code") or "TOOL_ERROR"
        message = payload.get("message") or "写后校验失败"
        n_mis = payload.get("mismatch_count") or len(payload.get("mismatches") or [])
        return f"\n回读确认: {code} {message}" + (f" mismatches={n_mis}" if n_mis else "")
    if payload.get("kind") == "word":
        return (
            f"\n回读确认: Word 文档可读，"
            f"当前段落数={payload.get('paragraphs', 0)}，"
            f"表格数={payload.get('tables', 0)}"
        )
    n_val = len(payload.get("value_changes") or [])
    n_fml = len(payload.get("formula_changes") or [])
    total = payload.get("total_changes") or (n_val + n_fml)
    sheet = str(payload.get("sheet") or "")
    if not sheet:
        for bucket in (payload.get("value_changes"), payload.get("formula_changes")):
            if bucket:
                sheet = str(bucket[0].get("sheet") or "")
                break
    sheet_bit = f"{sheet} " if sheet else ""
    trunc = " 已截断" if payload.get("truncated") else ""
    return (
        f"\n回读确认: {sheet_bit}值核验 {n_val} 公式核验 {n_fml}（仅写后值，无编辑前值）"
        f"（共 {total}，显示 {payload.get('shown', n_val + n_fml)}）{trunc}"
    ).replace("  ", " ")


def compact_write_verification(payload: dict[str, Any] | None) -> str:
    if not payload or payload.get("skipped"):
        return ""
    if payload.get("status") == "error" or payload.get("error_code"):
        return f"verify {payload.get('error_code')}"
    n_val = len(payload.get("value_changes") or [])
    n_fml = len(payload.get("formula_changes") or [])
    return f"verify value={n_val} formula={n_fml} total={payload.get('total_changes', n_val + n_fml)}"


def attach_write_verification(
    result: ToolResult | None,
    verification: dict[str, Any],
    result_str: str,
) -> tuple[ToolResult | None, str]:
    """把校验写入 value.meta.write_verification，并追加一行给模型。"""
    if not verification or verification.get("skipped"):
        return result, result_str
    line = format_write_verification_line(verification)
    entries = [*(verification.get("value_changes") or []), *(verification.get("formula_changes") or [])]
    if entries:
        line += "\n回读样本: " + json.dumps(entries[:8], ensure_ascii=False, default=str)
    if line and line not in result_str:
        result_str = result_str + line
    if result is None:
        return result, result_str
    value = result.value
    if isinstance(value, dict):
        value = dict(value)
        meta = dict(value.get("meta") or {})
        meta["write_verification"] = verification
        value["meta"] = meta
        result = replace(result, value=value, model_text=result_str)
    else:
        result = result.with_model_text(result_str)
    return result, result_str


def spill_result_text(
    result_str: str,
    structured: ToolResult | None,
    *,
    store: SpillStore | None,
    char_threshold: int = DEFAULT_SPILL_CHAR_THRESHOLD,
    byte_threshold: int = DEFAULT_SPILL_BYTE_THRESHOLD,
    token_threshold: int = DEFAULT_SPILL_TOKEN_THRESHOLD,
) -> tuple[str, ToolResult | None]:
    if store is None:
        return result_str, structured
    if structured is not None and structured.coverage and structured.coverage.get("spill_retrieve"):
        return result_str, structured
    target = structured.with_model_text(result_str) if structured is not None else ToolResult.from_text(result_str)
    try:
        spilled = apply_spill(
            target,
            store=store,
            char_threshold=char_threshold,
            byte_threshold=byte_threshold,
            token_threshold=token_threshold,
        )
    except OSError:
        logger.debug("spill 写入失败，回退原文", exc_info=True)
        return result_str, structured
    if structured is None:
        return spilled.model_text, None
    return spilled.model_text, spilled
