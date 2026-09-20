"""模型可见的工具错误载荷：唯一构造入口与错误码词表。

Canonical 形状::

    {"status": "error", "error_code": ..., "message": ...,
     "failure_class": ..., "remediation": ..., ...可选}

不要再写顶层 ``error`` / ``code``；机器可读码同时落在 ``error_code`` 与
``ToolError.code``。``failure_class`` 是给模型的稳定分类，``remediation``
是下一步该怎么做（不是给人类排障）。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

ERROR_STATUS = "error"

REQUIRED_ERROR_KEYS: frozenset[str] = frozenset({
    "status",
    "error_code",
    "message",
    "failure_class",
    "remediation",
})
_ALIAS_KEYS: frozenset[str] = frozenset({"error", "code"})
_DATA_KEYS: frozenset[str] = frozenset({"shape", "columns", "data", "sheets", "file"})
_KEEP_STATUS: frozenset[str] = frozenset({
    "blocked",
    "failed",
    "fail",
    "pending_confirmation",
    "success",
    "ok",
    "confirmation_required",
})

# ── failure_class 词表（C2 只 import，不自行发明）──

FAILURE_INVALID_ARGS = "invalid_args"
FAILURE_PERMISSION_DENIED = "permission_denied"
FAILURE_APPROVAL_DENIED = "approval_denied"
FAILURE_APPROVAL_TIMEOUT = "approval_timeout"
FAILURE_NOT_FOUND = "not_found"
FAILURE_CONFLICT = "conflict"
FAILURE_UNSUPPORTED = "unsupported"
FAILURE_BLOCKED = "blocked"
FAILURE_INTERNAL = "internal"

FAILURE_CLASSES: frozenset[str] = frozenset({
    FAILURE_INVALID_ARGS,
    FAILURE_PERMISSION_DENIED,
    FAILURE_APPROVAL_DENIED,
    FAILURE_APPROVAL_TIMEOUT,
    FAILURE_NOT_FOUND,
    FAILURE_CONFLICT,
    FAILURE_UNSUPPORTED,
    FAILURE_BLOCKED,
    FAILURE_INTERNAL,
})

# ── 词表（工具错误码；CommitError / 审批等共用同一组名字）──

INVALID_ARGS = "INVALID_ARGS"
NOT_FOUND = "NOT_FOUND"
SHEET_NOT_FOUND = "SHEET_NOT_FOUND"
RANGE_INVALID = "RANGE_INVALID"
PATH_INVALID = "PATH_INVALID"
PATH_REQUIRED = "PATH_REQUIRED"
PERMISSION_DENIED = "PERMISSION_DENIED"
VERSION_CONFLICT = "VERSION_CONFLICT"
SAVE_FAILED = "SAVE_FAILED"
FILE_LOCKED = "FILE_LOCKED"
DECODE_ERROR = "DECODE_ERROR"
LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
EXECUTION_FAILED = "EXECUTION_FAILED"
TOOL_ERROR = "TOOL_ERROR"
TOOL_ARGUMENT_VALIDATION_ERROR = "TOOL_ARGUMENT_VALIDATION_ERROR"
TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"
TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
UNKNOWN_TOOL = "UNKNOWN_TOOL"
PROBE_FILE_FORBIDDEN = "PROBE_FILE_FORBIDDEN"
PRODUCT_SOURCE_FORBIDDEN = "PRODUCT_SOURCE_FORBIDDEN"
SPEC_VALIDATION_FAILED = "SPEC_VALIDATION_FAILED"
SPEC_NOT_PATCH = "SPEC_NOT_PATCH"
COMPILE_FAILED = "COMPILE_FAILED"
PLAN_INACTIVE = "PLAN_INACTIVE"
STALE_READ = "STALE_READ"
STALE_SNAPSHOT = "STALE_SNAPSHOT"
SELECTION_STALE = "SELECTION_STALE"
SHEET_REQUIRED = "SHEET_REQUIRED"
REF_UNSUPPORTED = "REF_UNSUPPORTED"
COORD_CONTRACT = "COORD_CONTRACT"
NOOP = "NOOP"
FILE_EXISTS = "FILE_EXISTS"
AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
RUN_CODE_FAILED = "RUN_CODE_FAILED"
RUN_CODE_TIMEOUT = "RUN_CODE_TIMEOUT"
RUN_CODE_PUBLISH_FAILED = "RUN_CODE_PUBLISH_FAILED"
CANCELLED = "CANCELLED"
PENDING_APPROVAL = "PENDING_APPROVAL"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
CONVERSION_UNAVAILABLE = "CONVERSION_UNAVAILABLE"
RESULT_UNCERTAIN = "RESULT_UNCERTAIN"
# 只读/策略守卫发出的码；先前漏出 ERROR_CODES。与 PERMISSION_DENIED 不同源：
# PERMISSION_DENIED = 计划/只读会话拒写；PRE_EXECUTE_DENIED = pre_execute/guard。
PRE_EXECUTE_DENIED = "PRE_EXECUTE_DENIED"
TOOL_CONTEXT_MISSING = "TOOL_CONTEXT_MISSING"
APPROVAL_DENIED = "APPROVAL_DENIED"
APPROVAL_TIMEOUT = "APPROVAL_TIMEOUT"
CODE_MODE_UNAVAILABLE = "CODE_MODE_UNAVAILABLE"
SDK_CONTRACT_VIOLATION = "SDK_CONTRACT_VIOLATION"
INVALID_DEPENDENCY = "INVALID_DEPENDENCY"
DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
DEPENDENCY_FAILED = "DEPENDENCY_FAILED"
OPERATION_ID_REUSED = "OPERATION_ID_REUSED"
EXTERNAL_COMMIT_UNKNOWN = "EXTERNAL_COMMIT_UNKNOWN"
TURN_TIMEOUT = "TURN_TIMEOUT"
# C2 Excel 语义码（本单元只登记词表，不改工具名/参数）
FORMULA_ERROR = "FORMULA_ERROR"
WORKBOOK_PROTECTED = "WORKBOOK_PROTECTED"
OUT_OF_RANGE = "OUT_OF_RANGE"
NAMED_RANGE_NOT_FOUND = "NAMED_RANGE_NOT_FOUND"
TABLE_NOT_FOUND = "TABLE_NOT_FOUND"

ERROR_CODES: frozenset[str] = frozenset({
    INVALID_ARGS,
    NOT_FOUND,
    SHEET_NOT_FOUND,
    RANGE_INVALID,
    PATH_INVALID,
    PATH_REQUIRED,
    PERMISSION_DENIED,
    VERSION_CONFLICT,
    SAVE_FAILED,
    FILE_LOCKED,
    DECODE_ERROR,
    LIMIT_EXCEEDED,
    EXECUTION_FAILED,
    TOOL_ERROR,
    TOOL_ARGUMENT_VALIDATION_ERROR,
    TOOL_EXECUTION_ERROR,
    TOOL_NOT_ALLOWED,
    UNKNOWN_TOOL,
    PROBE_FILE_FORBIDDEN,
    PRODUCT_SOURCE_FORBIDDEN,
    SPEC_VALIDATION_FAILED,
    SPEC_NOT_PATCH,
    COMPILE_FAILED,
    PLAN_INACTIVE,
    STALE_READ,
    STALE_SNAPSHOT,
    SELECTION_STALE,
    SHEET_REQUIRED,
    REF_UNSUPPORTED,
    COORD_CONTRACT,
    NOOP,
    FILE_EXISTS,
    AMBIGUOUS_MATCH,
    RUN_CODE_FAILED,
    RUN_CODE_TIMEOUT,
    RUN_CODE_PUBLISH_FAILED,
    CANCELLED,
    PENDING_APPROVAL,
    BUDGET_EXCEEDED,
    CONVERSION_UNAVAILABLE,
    RESULT_UNCERTAIN,
    PRE_EXECUTE_DENIED,
    TOOL_CONTEXT_MISSING,
    APPROVAL_DENIED,
    APPROVAL_TIMEOUT,
    CODE_MODE_UNAVAILABLE,
    SDK_CONTRACT_VIOLATION,
    INVALID_DEPENDENCY,
    DEPENDENCY_CYCLE,
    DEPENDENCY_FAILED,
    OPERATION_ID_REUSED,
    EXTERNAL_COMMIT_UNKNOWN,
    TURN_TIMEOUT,
    FORMULA_ERROR,
    WORKBOOK_PROTECTED,
    OUT_OF_RANGE,
    NAMED_RANGE_NOT_FOUND,
    TABLE_NOT_FOUND,
})

# error_code → failure_class。未列出的码（含未知）回落 internal。
ERROR_CODE_TO_FAILURE_CLASS: dict[str, str] = {
    INVALID_ARGS: FAILURE_INVALID_ARGS,
    TOOL_ARGUMENT_VALIDATION_ERROR: FAILURE_INVALID_ARGS,
    RANGE_INVALID: FAILURE_INVALID_ARGS,
    PATH_INVALID: FAILURE_INVALID_ARGS,
    PATH_REQUIRED: FAILURE_INVALID_ARGS,
    DECODE_ERROR: FAILURE_INVALID_ARGS,
    SPEC_VALIDATION_FAILED: FAILURE_INVALID_ARGS,
    SPEC_NOT_PATCH: FAILURE_INVALID_ARGS,
    COMPILE_FAILED: FAILURE_INVALID_ARGS,
    FORMULA_ERROR: FAILURE_INVALID_ARGS,
    OUT_OF_RANGE: FAILURE_INVALID_ARGS,
    PERMISSION_DENIED: FAILURE_PERMISSION_DENIED,
    TOOL_NOT_ALLOWED: FAILURE_PERMISSION_DENIED,
    PRE_EXECUTE_DENIED: FAILURE_PERMISSION_DENIED,
    TOOL_CONTEXT_MISSING: FAILURE_INTERNAL,
    APPROVAL_DENIED: FAILURE_APPROVAL_DENIED,
    APPROVAL_TIMEOUT: FAILURE_APPROVAL_TIMEOUT,
    CODE_MODE_UNAVAILABLE: FAILURE_BLOCKED,
    SDK_CONTRACT_VIOLATION: FAILURE_INTERNAL,
    INVALID_DEPENDENCY: FAILURE_INVALID_ARGS,
    DEPENDENCY_CYCLE: FAILURE_INVALID_ARGS,
    DEPENDENCY_FAILED: FAILURE_BLOCKED,
    OPERATION_ID_REUSED: FAILURE_CONFLICT,
    EXTERNAL_COMMIT_UNKNOWN: FAILURE_BLOCKED,
    TURN_TIMEOUT: FAILURE_BLOCKED,
    NOT_FOUND: FAILURE_NOT_FOUND,
    SHEET_NOT_FOUND: FAILURE_NOT_FOUND,
    UNKNOWN_TOOL: FAILURE_NOT_FOUND,
    NAMED_RANGE_NOT_FOUND: FAILURE_NOT_FOUND,
    TABLE_NOT_FOUND: FAILURE_NOT_FOUND,
    VERSION_CONFLICT: FAILURE_CONFLICT,
    STALE_READ: FAILURE_CONFLICT,
    STALE_SNAPSHOT: FAILURE_CONFLICT,
    SELECTION_STALE: FAILURE_CONFLICT,
    SHEET_REQUIRED: FAILURE_INVALID_ARGS,
    REF_UNSUPPORTED: FAILURE_UNSUPPORTED,
    COORD_CONTRACT: FAILURE_INVALID_ARGS,
    FILE_EXISTS: FAILURE_CONFLICT,
    AMBIGUOUS_MATCH: FAILURE_CONFLICT,
    PLAN_INACTIVE: FAILURE_UNSUPPORTED,
    CONVERSION_UNAVAILABLE: FAILURE_UNSUPPORTED,
    NOOP: FAILURE_UNSUPPORTED,
    PROBE_FILE_FORBIDDEN: FAILURE_BLOCKED,
    PRODUCT_SOURCE_FORBIDDEN: FAILURE_BLOCKED,
    CANCELLED: FAILURE_BLOCKED,
    PENDING_APPROVAL: FAILURE_BLOCKED,
    BUDGET_EXCEEDED: FAILURE_BLOCKED,
    LIMIT_EXCEEDED: FAILURE_BLOCKED,
    WORKBOOK_PROTECTED: FAILURE_BLOCKED,
    SAVE_FAILED: FAILURE_INTERNAL,
    FILE_LOCKED: FAILURE_BLOCKED,
    EXECUTION_FAILED: FAILURE_INTERNAL,
    TOOL_ERROR: FAILURE_INTERNAL,
    TOOL_EXECUTION_ERROR: FAILURE_INTERNAL,
    RUN_CODE_FAILED: FAILURE_INTERNAL,
    RUN_CODE_TIMEOUT: FAILURE_INTERNAL,
    RUN_CODE_PUBLISH_FAILED: FAILURE_INTERNAL,
    RESULT_UNCERTAIN: FAILURE_INTERNAL,
}

# 给模型的一句话。优先按 error_code，再按 failure_class。
_REMEDIATION_BY_CODE: dict[str, str] = {
    INVALID_ARGS: "按工具 schema 修正参数后重试，不要用同一组错误参数再调；字段合同可用 introspect_capability(query_type=\"tool_detail\") 查询。",
    TOOL_ARGUMENT_VALIDATION_ERROR: "根据 violations 列出的字段修正参数后重试；例如缺 file_path 时传相对路径 'workbook.xlsx'。",
    RANGE_INVALID: "按 message 中的错误点与示例改正引用，例如 range='Sheet1!A1:C10'。不要整段重抄 A1 教材。",
    PATH_INVALID: (
        "若是拼写错误，按候选改正工作区相对路径后重试；"
        "任务允许查找时用 list_directory 或 analyze_spreadsheet(mode=\"files\")；"
        "候选不唯一时用 ask_user 问一个问题；确实未上传时请用户提供。"
        "不要擅自换成另一份表，也不要改用工作区外路径。"
    ),
    PATH_REQUIRED: "补上 file_path（工作区相对路径）后重试一次；仍不存在就按 PATH_INVALID 查找或请用户提供。",
    DECODE_ERROR: "文本文件先换 encoding 参数重试（常见 gbk/gb18030/utf-16）；xlsx 等二进制文件换用匹配工具（inspect_spreadsheet），不要对二进制再调本工具。",
    SPEC_VALIDATION_FAILED: "按 errors 中的字段路径、原因与合法形状修正 workbook_spec 后重试，不要读产品源码。",
    SPEC_NOT_PATCH: "workbook_spec 只能用于创建新文件：换成尚不存在的输出路径，或对已有文件改用 operations 更新。",
    COMPILE_FAILED: "修正规格里的非法引用或操作后再编译，不要原样重试。",
    FORMULA_ERROR: "修正公式语法或引用（避免 #REF!）后重写该单元格，不要原样重试。",
    OUT_OF_RANGE: "把行列缩小到工作表实际范围后重试；先 inspect_spreadsheet 看 max_row/max_col。",
    PERMISSION_DENIED: "不要重试这次写入；改用 inspect_spreadsheet 等只读工具，或请用户切到编辑模式（也可切出只读/计划模式）。",
    TOOL_NOT_ALLOWED: "不要再调这个工具名；改用当前授权目录里已有的工具。",
    PRE_EXECUTE_DENIED: "不要重试这次调用；当前守卫/只读子代理拒绝写入，改用只读工具或把限制写回主代理。",
    TOOL_CONTEXT_MISSING: "这次调用没有工作区上下文；不要猜测当前目录，请从会话入口重新发起。",
    APPROVAL_DENIED: "用户已拒绝该操作，不要用同一工具和参数重试；询问用户下一步或换方案。",
    APPROVAL_TIMEOUT: "审批超时，不能当作已批准；先向用户确认，再决定是否重新提交。",
    CODE_MODE_UNAVAILABLE: "Code Mode 桥不可用；不要假设脚本已执行。改用 Native 意图工具，或等桥恢复后再 run_code。",
    SDK_CONTRACT_VIOLATION: "工具返回值不符合声明合同；按 introspect_capability(query_type=\"tool_detail\", query=\"工具名.output\") 核对顶层键与类型后再用，不要把违约值当成功结果继续写。",
    INVALID_DEPENDENCY: "依赖必须引用本批唯一的 tool call ID；修正依赖后重发未执行的调用。",
    DEPENDENCY_CYCLE: "移除自依赖或循环，遵守写入顺序；不要重放已经提交的写入。",
    DEPENDENCY_FAILED: "先处理前置调用的失败或未完成状态，再发起尚未执行的后续步骤。",
    OPERATION_ID_REUSED: "该操作 ID 已用于不同意图；先核对原操作状态，不要用它提交另一项写入，也不要重放已提交的操作。",
    EXTERNAL_COMMIT_UNKNOWN: "外部写入是否提交尚不确定；先查询原操作或外部系统的实际状态，不要自动重放。",
    TURN_TIMEOUT: "本回合已超时；先核对已完成调用和写入状态，再继续尚未执行的步骤，不要重放已提交或结果不确定的写入。",
    NOT_FOUND: "message 指出缺失的是列名/键名时，按返回的可用列列表修正后重试；是文件路径时按候选核对拼写，任务允许查找时再列目录，不要擅自换文件。",
    SHEET_NOT_FOUND: "若返回了 available_sheets，用其中准确表名重试；没有候选时先 overview 再按实际表名重试。不要编造默认表名。",
    UNKNOWN_TOOL: "先用 introspect_capability 查询当前可用能力及工具详情，再使用已确认的工具名。",
    NAMED_RANGE_NOT_FOUND: "先列出工作簿命名区域，再用存在的名称重试。",
    TABLE_NOT_FOUND: "先列出工作表 Table 名称，再用存在的表名重试。",
    VERSION_CONFLICT: "这次写入没有落盘。重读受影响范围，按新数据重新计算或确认后再写；不要只替换 expected_version 重放基于旧数据的操作。更早已经成功的写入不要当成失败。",
    STALE_READ: "重新读取文件后再继续，不要用过期快照写入。",
    STALE_SNAPSHOT: "重新打开快照后再继续；不要用期望版本与当前字节不一致的文件。",
    SELECTION_STALE: "选择集已过期；重新筛选或读取后再写，不要套用旧行号。",
    SHEET_REQUIRED: "工作簿有多张可见表或表结构存在歧义，补上 sheet 后再试。若收到自动绑定 warning，请核对 resolved_sheet 是否符合预期，不符则显式指定。",
    REF_UNSUPPORTED: "该入口不执行这种引用；按返回的能力说明改用支持的语法。",
    COORD_CONTRACT: "行列号使用 Excel 1-based；不要把内部 0-based 索引传给公共参数。",
    FILE_EXISTS: "换一个不冲突的目标路径，或先确认是否允许覆盖。",
    AMBIGUOUS_MATCH: "把匹配条件收窄到唯一结果后再重试。",
    PLAN_INACTIVE: "先 write_plan / 进入计划模式，或改用当前模式允许的工具。",
    CONVERSION_UNAVAILABLE: "该转换能力不可用；改用 xlsx 或请用户在 Excel 中另存。",
    NOOP: "这是空操作，不要原样重试；先 inspect 当前内容再决定是否修改。",
    PROBE_FILE_FORBIDDEN: "不要探测该路径；改用用户工作区内的相对路径。",
    PRODUCT_SOURCE_FORBIDDEN: "不要读取产品源码。改用 introspect_capability 查询工具字段，或 inspect_spreadsheet 读取工作区表格。不要改用 shell 或绝对路径绕过。",
    CANCELLED: "任务已取消，不要重试同一调用。",
    PENDING_APPROVAL: "等待用户审批，不要用同一参数再提交一次。",
    BUDGET_EXCEEDED: "本轮工具调用预算已用尽，不要继续堆调用；汇总后结束。",
    LIMIT_EXCEEDED: "缩小范围或条数后重试，不要用同样的超限参数。",
    WORKBOOK_PROTECTED: "工作簿受保护，不要重试写入；请用户解除保护或改用只读。",
    FILE_LOCKED: "请用户关闭 Excel 或文件预览，释放占用后再重试；不要循环重试同一写入。",
    SAVE_FAILED: "不要盲目重试同一写入；检查路径/磁盘后换参数或换工具。",
    EXECUTION_FAILED: "根据 message 修正输入后重试，不要用完全相同的参数连打。",
    TOOL_ERROR: "把 error_code 与 message 视为失败分类，修正后再试，不要同参连打。",
    TOOL_EXECUTION_ERROR: "这是执行期失败；根据 exception/message 改参数，不要同参连打。",
    RUN_CODE_FAILED: "修正 run_code 程序后重试；不要原样再跑同一段失败代码。",
    RUN_CODE_TIMEOUT: "缩短计算、提高 timeout_seconds，或拆成更小的步骤后重试；不要假设超时前的写入已全部提交。",
    RUN_CODE_PUBLISH_FAILED: "查看 published 里未 committed 的项；已提交的不要重放，冲突项先读最新 expected_version 再写。",
    RESULT_UNCERTAIN: "结果不确定，先 inspect 核对再决定是否重写。",
}

_REMEDIATION_BY_CLASS: dict[str, str] = {
    FAILURE_INVALID_ARGS: "按工具 schema 修正参数后重试，不要用同一组错误参数；字段合同可用 introspect_capability(query_type=\"tool_detail\") 查询。",
    FAILURE_PERMISSION_DENIED: "不要重试该调用；改用授权范围内的替代工具或请用户调整模式。",
    FAILURE_APPROVAL_DENIED: "用户已拒绝，不要重试同一操作；询问用户或换方案。",
    FAILURE_APPROVAL_TIMEOUT: "审批超时，先向用户确认再决定是否重新提交。",
    FAILURE_NOT_FOUND: "先列出可用名称/路径，再用存在的标识重试。",
    FAILURE_CONFLICT: "读取最新状态（含 content_version）后再重试，不要用过期前提。",
    FAILURE_UNSUPPORTED: "该能力当前不可用；换替代工具或把限制告诉用户。",
    FAILURE_BLOCKED: "该操作被策略拦住，不要用同一调用重试。",
    FAILURE_INTERNAL: "运行失败；根据 message 改参数或换路径，不要盲目同参重试。",
}

# SubagentError.code → (error_code, failure_class)
# 配置/能力 → unsupported 或 invalid_args；运行失败 → internal。
SUBAGENT_CODE_TO_TAXONOMY: dict[str, tuple[str, str]] = {
    "DISABLED": (TOOL_NOT_ALLOWED, FAILURE_UNSUPPORTED),
    "EMPTY_TASK": (INVALID_ARGS, FAILURE_INVALID_ARGS),
    "NOT_FOUND": (NOT_FOUND, FAILURE_NOT_FOUND),
    "PARALLEL_LIMIT": (LIMIT_EXCEEDED, FAILURE_INVALID_ARGS),
    "PARALLEL_CONFLICT": (TOOL_ERROR, FAILURE_CONFLICT),
    "UNSUPPORTED_CAPABILITY": (TOOL_ERROR, FAILURE_UNSUPPORTED),
    "HOOK_DENIED": (PERMISSION_DENIED, FAILURE_PERMISSION_DENIED),
    "PERMISSION_DENIED": (PERMISSION_DENIED, FAILURE_PERMISSION_DENIED),
    "DEPTH_EXCEEDED": (LIMIT_EXCEEDED, FAILURE_UNSUPPORTED),
    "LIFECYCLE_MISMATCH": (TOOL_ERROR, FAILURE_INTERNAL),
}


def failure_class_for_error_code(error_code: str) -> str:
    mapped = ERROR_CODE_TO_FAILURE_CLASS.get(str(error_code or TOOL_ERROR))
    if mapped in FAILURE_CLASSES:
        return mapped
    return FAILURE_INTERNAL


def remediation_for(
    error_code: str,
    *,
    failure_class: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> str:
    code = str(error_code or TOOL_ERROR)
    text = _REMEDIATION_BY_CODE.get(code)
    if text:
        extra = extra or {}
        violations = extra.get("violations")
        if isinstance(violations, list) and violations:
            preview = "; ".join(str(item) for item in violations[:3] if str(item).strip())
            if preview:
                return f"{text} 违规：{preview}"
        sheets = extra.get("available_sheets")
        if isinstance(sheets, list) and sheets:
            names = ", ".join(str(item) for item in sheets[:5] if str(item).strip())
            if names:
                return f"{text} 可用工作表：{names}"
        candidates = extra.get("candidates") or extra.get("available_excel_files")
        if isinstance(candidates, list) and candidates:
            names = ", ".join(str(item) for item in candidates[:8] if str(item).strip())
            if names:
                return f"{text} 候选：{names}"
        errors = extra.get("errors")
        if isinstance(errors, list) and errors:
            preview_errs = []
            for item in errors[:3]:
                if isinstance(item, dict):
                    path = item.get("path") or ""
                    message = item.get("message") or ""
                    preview_errs.append(f"{path}: {message}".strip(": "))
                elif str(item).strip():
                    preview_errs.append(str(item))
            if preview_errs:
                return f"{text} 详情：{'; '.join(preview_errs)}"
        return text
    klass = failure_class if failure_class in FAILURE_CLASSES else failure_class_for_error_code(code)
    return _REMEDIATION_BY_CLASS.get(klass, _REMEDIATION_BY_CLASS[FAILURE_INTERNAL])


def make_error_payload(
    message: str,
    *,
    error_code: str = TOOL_ERROR,
    failure_class: str | None = None,
    remediation: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """构造 canonical 错误 dict。忽略会与规范键冲突的别名。"""
    code = str(error_code or TOOL_ERROR)
    klass = str(failure_class or "") or failure_class_for_error_code(code)
    if klass not in FAILURE_CLASSES:
        klass = failure_class_for_error_code(code)
    extra = {
        key: value
        for key, value in fields.items()
        if key not in REQUIRED_ERROR_KEYS and key not in _ALIAS_KEYS
    }
    hint = str(remediation or "").strip() or remediation_for(
        code, failure_class=klass, extra=extra,
    )
    payload: dict[str, Any] = {
        "status": ERROR_STATUS,
        "error_code": code,
        "message": str(message),
        "failure_class": klass,
        "remediation": hint,
    }
    payload.update(extra)
    return payload


def dumps_error_payload(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, default=str)


def payload_from_subagent_error(exc: Any, **fields: Any) -> dict[str, Any]:
    """把 SubagentError 收成 canonical 失败载荷，保留原 code/message。"""
    raw_code = str(getattr(exc, "code", "") or "") or TOOL_ERROR
    message = str(getattr(exc, "message", None) or exc)
    mapped_code, mapped_class = SUBAGENT_CODE_TO_TAXONOMY.get(
        raw_code,
        (TOOL_ERROR, FAILURE_INTERNAL),
    )
    extra = dict(fields)
    extra.setdefault("subagent_code", raw_code)
    return make_error_payload(
        message,
        error_code=mapped_code,
        failure_class=mapped_class,
        **extra,
    )


def error_code_from_mapping(payload: Mapping[str, Any], *, default: str = TOOL_ERROR) -> str:
    raw = payload.get("error_code") or payload.get("code") or default
    return str(raw or default)


def error_message_from_mapping(payload: Mapping[str, Any], *, default: str = "error") -> str:
    raw = payload.get("message") or payload.get("error") or payload.get("reason") or default
    return str(raw or default)


def is_canonical_error_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if REQUIRED_ERROR_KEYS - payload.keys():
        return False
    if payload.get("status") != ERROR_STATUS:
        return False
    if not isinstance(payload.get("error_code"), str) or not payload["error_code"]:
        return False
    if not isinstance(payload.get("message"), str):
        return False
    if payload.get("failure_class") not in FAILURE_CLASSES:
        return False
    if not isinstance(payload.get("remediation"), str) or not payload["remediation"].strip():
        return False
    return _ALIAS_KEYS.isdisjoint(payload.keys())


def should_canonicalize_error_payload(payload: Mapping[str, Any]) -> bool:
    """纯工具错误才改写成 canonical；blocked / run_code failed / 混有数据键的保持原样。"""
    status = str(payload.get("status") or "").lower()
    if status in _KEEP_STATUS:
        return False
    if any(key in payload for key in _DATA_KEYS) and status != ERROR_STATUS:
        return False
    if status == ERROR_STATUS:
        return True
    if payload.get("error") and not any(key in payload for key in _DATA_KEYS):
        return True
    if payload.get("error_code") or payload.get("code"):
        return True
    return False


def canonicalize_error_payload(payload: Mapping[str, Any], *, default_code: str = TOOL_ERROR) -> dict[str, Any]:
    message = error_message_from_mapping(payload)
    code = error_code_from_mapping(payload, default=default_code)
    raw_class = payload.get("failure_class")
    raw_hint = payload.get("remediation")
    extra = {
        key: value
        for key, value in payload.items()
        if key not in REQUIRED_ERROR_KEYS
        and key not in _ALIAS_KEYS
        and key != "reason"
    }
    return make_error_payload(
        message,
        error_code=code,
        failure_class=str(raw_class) if raw_class else None,
        remediation=str(raw_hint) if raw_hint else None,
        **extra,
    )
