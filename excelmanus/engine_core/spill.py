"""版本绑定工具结果的有界投影与 content-addressed 存储。

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
from collections.abc import Mapping
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
from excelmanus.engine_core.tool_result import ToolResult, error_result
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
_LOCATOR_RE = re.compile(r"^(?:spill|result_spill|selection_spill):(?P<digest>[0-9a-f]{64})$")
_REFERENCE_PREFIXES = ("spill:", "result_spill:", "selection_spill:")
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


def is_spill_reference(text: str | None) -> bool:
    """Recognize reserved handles, including malformed ones, before path resolution."""
    return str(text or "").strip().startswith(_REFERENCE_PREFIXES)


def parse_locator(text: str) -> SpillLocator:
    raw = str(text or "").strip()
    match = _LOCATOR_RE.fullmatch(raw)
    if match is None:
        raise ValueError(f"非法 spill 句柄: {text!r}")
    # Output keys are often copied as prefixes. Accept those exact aliases but
    # keep one storage/return format; never interpret the digest as a file path.
    return SpillLocator(LOCATOR_PREFIX + match.group("digest"))


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
    for key in ("file_path", "path", "locator", "spill", "result_spill", "selection_spill"):
        value = arguments.get(key)
        if isinstance(value, str) and is_spill_reference(value):
            return value.strip()
        if key in {"file_path", "path"} and value:
            return None
    # A search query/cell value that looks like a handle is still user data.
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
                "按 next_call 取回原文；将 spill 字段的完整值原样传入 file_path，"
                "不要把字段名拼到哈希前。句柄不是磁盘路径。"
            ),
            "next_call": {"tool": "read_text_file", "arguments": {"file_path": str(self.locator)}},
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
    except ValueError:
        return replace(error_result(
            f"结果句柄格式无效：{locator}", code="INVALID_ARGS",
            fields={
                "locator": str(locator),
                "remediation": "原样复制返回的 spill/result_spill/selection_spill 字段值（spill: 加 64 位小写十六进制摘要），用 read_text_file(file_path=该值)；不要拼接字段名或目录。",
            },
        ), coverage={"spill_retrieve": True, "kind": "invalid"})
    except SpillNotFound:
        return replace(error_result(
            f"spill 句柄不存在或已失效：{locator}",
            code=NOT_FOUND,
            fields={
                "locator": str(parse_locator(locator)),
                "remediation": "该结果句柄在当前工作区不存在或内容已损坏。重新执行产生它的只读查询以取得新句柄；不要列目录寻找句柄，也不要重放写入操作。",
            },
        ), coverage={"spill_retrieve": True, "kind": "missing"})
    parsed: Any = text
    stripped = text.strip()
    if (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]")):
        try:
            loaded = json.loads(stripped)
        except (json.JSONDecodeError, TypeError, ValueError):
            loaded = None
        if isinstance(loaded, (dict, list)):
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


def _same_json_array(left: Any, right: Any) -> bool:
    """Compare aliases without treating JSON booleans as numeric values."""
    if not isinstance(left, list) or not isinstance(right, list):
        return False
    if left is right:
        return True
    return json.dumps(left, ensure_ascii=False, default=str, separators=(",", ":")) == json.dumps(
        right, ensure_ascii=False, default=str, separators=(",", ":"),
    )


def _project_spreadsheet_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove proven aliases only from a spreadsheet result's wrapper.

    Do not walk cell values, records, metadata or other arbitrary dictionaries:
    ``data`` and ``values`` can be real user column names there. Alias metadata
    describes the model projection; the structured value retains every field.
    """
    projected = dict(payload)
    if "model_field_aliases" in payload:
        return projected
    aliases: dict[str, str] = {}
    for alias, canonical in (("data", "values"), ("formula_grid", "formulas")):
        if alias in payload and canonical in payload and _same_json_array(payload[alias], payload[canonical]):
            projected.pop(alias)
            aliases[alias] = canonical

    areas = payload.get("areas")
    if (
        isinstance(areas, list)
        and areas
        and isinstance(payload.get("range"), str)
        and all(isinstance(area, dict) and isinstance(area.get("range"), str) for area in areas)
    ):
        projected["areas"] = [_project_spreadsheet_fields(area) for area in areas]
        # A union read repeats all grids both at the top and in its areas.
        # Keep each area's coordinates with its grid, and expose the aggregate
        # names as aliases only when every area's original data agrees.
        for canonical in ("values", "formulas"):
            if all(canonical in area for area in areas) and _same_json_array(
                payload.get(canonical), [area[canonical] for area in areas],
            ):
                target = f"areas[*].{canonical}"
                projected.pop(canonical, None)
                aliases[canonical] = target
                for alias, source in list(aliases.items()):
                    if source == canonical:
                        aliases[alias] = target
    if aliases:
        projected["model_field_aliases"] = aliases
    return projected


def expose_spreadsheet_value(result: ToolResult, *, store: SpillStore, project_large: bool = True) -> ToolResult:
    """Keep native tool results as usable as their SDK value, with bounded text.

    Small results carry the V2 facts once. Decide whether to spill using this
    bounded model projection,
    so duplicate grids do not force another tool call. Large results carry an
    opaque handle to the *original* payload. SDK values and UI stay untouched.
    The existing read_text_file path retrieves the complete original JSON.
    """
    if not isinstance(result.value, dict) or (result.coverage or {}).get("spill_retrieve"):
        return result
    payload = result.value
    projected = _project_spreadsheet_fields(payload)
    model_text = json.dumps(projected, ensure_ascii=False, default=str, separators=(",", ":"))
    if not project_large or not should_spill(model_text):
        return result.with_model_text(model_text)
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    locator = store.put(raw)
    envelope = {
        "result_spill": str(locator),
        "read_result": "按 next_call 取回完整 JSON；file_path 原样使用 result_spill 字段的值（spill:…），不要把 result_spill 当作前缀或目录。",
        "next_call": {"tool": "read_text_file", "arguments": {"file_path": str(locator)}},
    }
    for key in (
        "schema_version", "observation_id", "snapshot_id", "attachment_id", "render_id", "surface", "limitations",
        "status", "file_path", "file_a", "file_b", "content_version",
        "content_version_a", "content_version_b", "resolved_sheet", "scope",
        "coverage", "selection_spill", "warnings", "error_code", "message", "remediation",
        "committed", "partial", "tx_id", "operation_id", "recovery_required",
        "operation_index", "operation_kind",
    ):
        if key in payload:
            envelope[key] = payload[key]
    # A short summary is navigation only; the complete structured payload is
    # always recoverable, including artifact lists, selections and warnings.
    envelope["preview"] = model_text[:DEFAULT_PREVIEW_CHARS]
    envelope["result_projection"] = "partial; full payload in result_spill"
    return result.with_model_text(json.dumps(envelope, ensure_ascii=False, default=str))
