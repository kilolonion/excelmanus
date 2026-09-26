"""Version-bound delivery evidence, independent of model prose and reviewers."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from excelmanus.engine_core.execution_facts import records, tool_publications


def _path(raw, root=None):
    if not isinstance(raw, str):
        return None
    if root:
        from excelmanus.workspace.identity import resolve_canonical
        try:
            return resolve_canonical(root, raw).relative
        except Exception:
            pass  # retain the rejected spelling as failure evidence only
    value = raw.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


# ── 校验回执的语义分类 ──────────────────────────────────────────────
# 只有"整列求和"这一类规则会被口径误用（agent 想断言某个单元格，却写了 total）。
# 判定只依据工具回执里的事实，不做措辞判断：
#   * 工具显式标记（rule_suspect / expected_matches_cell 等）；
#   * 被求和的列里含非数值文本（新回执 excluded_non_numeric>0 / reason=non_numeric_text；
#     旧回执用字符串 expected 标记"非数值文本不计入合计"）。
# 纯数值列的求和与 expected 不符（真正的合计错误）仍是产物缺陷。
_SUSPECT_MARKERS = ("rule_suspect", "semantics_suspect", "expected_matches_cell",
                    "expected_is_cell_value", "likely_cell_assertion")
_EXCLUDED_TEXT_REASONS = frozenset({"non_numeric_text", "excluded_non_numeric"})


def _marker(issue):
    for key in _SUSPECT_MARKERS:
        value = issue.get(key)
        if value is True or (isinstance(value, (list, tuple, dict, str)) and value):
            return key
    return None


def _issue_rule(issue, rules):
    """失败项对应的 (规则下标 或 None, 规则 kind 或 None)。"""
    number = issue.get("rule")
    if not isinstance(number, int) or isinstance(number, bool) or not 0 <= number < len(rules):
        number = None
    rule = rules[number] if number is not None and isinstance(rules[number], dict) else {}
    kind = issue.get("kind") or rule.get("kind")
    if number is None and str(kind or "") == "total":
        # 单条 total 规则时，失败项省略 rule 下标也能确定归属。
        totals = [i for i, item in enumerate(rules) if isinstance(item, dict) and item.get("kind") == "total"]
        number = totals[0] if len(totals) == 1 else None
    return number, (str(kind) if kind else None)


def _total_misuse_reason(rule_number, issues, results):
    """该 total 规则是否在求和一个"不能当合计看"的列；返回原因码或 None。"""
    related = [issue for issue in issues if isinstance(issue, dict) and issue.get("rule") == rule_number]
    if any(_marker(issue) for issue in related):
        return "tool_marked_semantic_misuse"
    entry = next((item for item in results
                  if isinstance(item, dict) and item.get("rule") == rule_number), None)
    if isinstance(entry, dict):
        excluded = entry.get("excluded_non_numeric")
        if isinstance(excluded, int) and not isinstance(excluded, bool) and excluded > 0:
            return "column_contains_non_numeric_text"
    for issue in related:
        if str(issue.get("reason") or "") in _EXCLUDED_TEXT_REASONS:
            return "column_contains_non_numeric_text"
        expected = issue.get("expected")
        if isinstance(expected, str) and expected.strip():
            return "column_contains_non_numeric_text"
    return None


def classify_validation_issues(rules, issues, results):
    """把一条校验回执拆成 (产物缺陷, 规则口径可疑)。

    非 dict 的失败项（版本失效等合成标记）永远算产物缺陷；公式错误、唯一性、
    必填、外键、行表达式始终算产物缺陷，只有 total 会进入口径可疑判定。
    """
    rules = rules if isinstance(rules, list) else []
    results = results if isinstance(results, list) else []
    product, suspect = [], []
    for issue in issues or []:
        if not isinstance(issue, dict):
            product.append(issue)
            continue
        number, kind = _issue_rule(issue, rules)
        reason = _marker(issue) if kind == "total" else None
        if reason is None and kind == "total" and number is not None:
            reason = _total_misuse_reason(number, issues, results)
        if reason:
            suspect.append({**issue, "rule_suspect": reason})
        else:
            product.append(issue)
    return product, suspect


def _entry_issues(entry):
    """(产物缺陷, 口径可疑, 有产物缺陷的校验键) —— 从持久化的校验证据重算。

    `entry["validations"]` 只保存**当前版本上尚未失效**的校验回执，因此这里
    遍历到的一切都是"产物缺陷 / 口径可疑"二选一。证据过期不在这里表达：
    见 `_stale_keys` 与 `pending()` 中的 "validation_stale"。
    """
    product, suspect, product_rules = [], [], []
    for key, issues in (entry.get("validations") or {}).items():
        if not issues:
            continue
        try:
            parsed = json.loads(key)
        except (TypeError, ValueError):
            parsed = []
        rules = parsed[1] if isinstance(parsed, list) and len(parsed) > 1 else []
        kept, flagged = classify_validation_issues(rules, issues, (entry.get("validation_results") or {}).get(key))
        if kept:
            product_rules.append(parsed if isinstance(parsed, list) else rules)
        product.extend(kept)
        suspect.extend(flagged)
    return product, suspect, product_rules


def _stale_keys(entry):
    """写入使哪些校验规则的证据过期（= 需要重新校验，而不是产物缺陷）。

    "证据过期"与"产物缺陷"是两件事：旧版本上的校验结论不能证明新版本，
    所以写入后必须重来一次；但文件本身并没有任何被证实的缺陷。把两者混在
    `validations` 里的旧实现会让过期标记永久留在 issues 中：合成标记不是
    dict，分类器只能当产物缺陷，而只有**同一 rules 键**再次成功校验才会
    把它清掉——规则集一变（真实会话里 3 轮里换了 3 套规则）就永远清不掉。

    现在过期只记录在 `stale_validations`（规则键列表），失败回执仍留在
    `validations` 里（那是产物缺陷的证据）。空回执（已通过的规则）记在
    `validated_keys`，它同样是旧版本上的结论，写入后一并转入待办。
    """
    stale = [key for key in (entry.get("stale_validations") or []) if isinstance(key, str)]
    return _dedupe([*stale, *(entry.get("validated_keys") or []), *_failed_keys(entry)])


def _dedupe(keys):
    seen, ordered = set(), []
    for key in keys:
        if isinstance(key, str) and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def _failed_keys(entry):
    """当前版本上校验失败（有失败项）的规则键：这份证据也随写入过期。"""
    return [key for key, issues in (entry.get("validations") or {}).items() if issues]


def _mark_stale(entry, stale_keys):
    """把 stale_keys 记为"需重新校验"（去重、累积），并从当前校验证据中移除。

    过期条目只进 `stale_validations`，永远不进 `validations`/`issues`：这样
    `_entry_issues` 只看到"当前版本的结论"，过期不会再被当成产物缺陷。
    """
    stale = set(_dedupe(stale_keys))
    ordered = [key for key in _dedupe([*(entry.get("stale_validations") or []), *stale_keys])]
    if ordered:
        entry["stale_validations"] = ordered
    else:
        entry.pop("stale_validations", None)
    entry["validated_keys"] = [key for key in (entry.get("validated_keys") or []) if key not in stale]
    if not entry["validated_keys"]:
        entry.pop("validated_keys", None)
    return entry


def _drop_results(entry, stale_keys):
    """丢弃过期规则键的校验结果（失败项与逐规则回执）。"""
    stale = set(stale_keys)
    entry["validations"] = {key: value for key, value in (entry.get("validations") or {}).items() if key not in stale}
    entry["validation_results"] = {key: value for key, value in (entry.get("validation_results") or {}).items() if key not in stale}
    return entry


def _clear_stale(entry, keep_keys=()):
    """当前版本上的一次 complete + valid=true 校验重建了证据，过期义务整体消除。

    义务一旦被满足就必须整体清除（含规则集已经换掉的旧过期条目），否则过期
    条目会无限累积——旧 rules 键再也不会被访问到。只有本次校验的规则键保留
    在 scope 里（它还欠着公式/图表等其它证据）。
    """
    keep = set(keep_keys)
    stale = [key for key in (entry.get("stale_validations") or []) if isinstance(key, str) and key not in keep]
    entry.pop("stale_validations", None)
    return stale


def _resolve_stale(entry, key):
    """某规则键已在当前版本上重新校验（回执可能是失败），不再需要"重新校验"。

    失败回执本身就是当前版本的结论：它是产物缺陷（走 issues /
    validation_failed_or_partial），而不是"证据过期"。其它规则键的过期义务
    不受影响，仍留在 `stale_validations` 里等待各自的重跑。
    """
    remaining = [item for item in (entry.get("stale_validations") or []) if isinstance(item, str) and item != key]
    if remaining:
        entry["stale_validations"] = remaining
    else:
        entry.pop("stale_validations", None)
    return entry


def _refresh_issues(entry):
    product, suspect, _ = _entry_issues(entry)
    entry["issues"] = product
    entry["rule_suspect"] = suspect
    return entry


def _intent(tool, arguments, path, root):
    # Preserve target identity while permitting values, versions and formatting
    # parameters to be corrected. A successful chart elsewhere is not recovery.
    operations = []
    for op in records(arguments.get("operations")):
        target = op.get("target_sheet") or op.get("sheet")
        operations.append({"kind": op.get("kind"), "sheet": target,
                           **{key: op[key] for key in ("range", "start_cell", "target_cell", "index", "action") if key in op}})
    goal = "create" if "workbook_spec" in arguments or arguments.get("create") else operations
    return json.dumps([tool, path, goal, _path(arguments.get("output_path"), root)], sort_keys=True, ensure_ascii=False)


class DeliveryLedger:
    def __init__(self):
        self.artifacts = {}
        self.active = []
        self.commits = []
        self.feedback = []
        self.task_id = ""
        self.failures = {}

    def begin_turn(self):
        # A continuation must not erase unfinished verification merely because
        # a new chat turn starts. Completed artifacts drop out of the scope.
        self.active = [row["file_path"] for row in self.pending()]
        self.feedback = []

    def start_task(self, task_id):
        if isinstance(task_id, str) and task_id and task_id != self.task_id:
            self.task_id = task_id
            self.active = []
            self.feedback = []
            self.failures = {}

    def to_dict(self):
        return deepcopy({"artifacts": self.artifacts, "active": self.active,
                         "commits": self.commits[-1000:], "feedback": self.feedback,
                         "task_id": self.task_id, "failures": self.failures})

    @classmethod
    def from_dict(cls, data):
        ledger = cls()
        if isinstance(data, dict):
            for key in ("artifacts", "active", "commits", "feedback", "task_id", "failures"):
                if isinstance(data.get(key), type(getattr(ledger, key))):
                    setattr(ledger, key, deepcopy(data[key]))
        return ledger

    def record(self, tool, arguments, result, *, vision=True, mutating=False, workspace_root=None):
        new_publications = []
        value = result.value if isinstance(result.value, dict) else {}
        path = _path(arguments.get("file_path") or arguments.get("output_path") or arguments.get("destination"), workspace_root)
        intent = _intent(tool, arguments, path, workspace_root)
        cancelled = result.error is not None and result.error.code == "CANCELLED"
        if isinstance(path, str) and path and mutating and not result.success and not cancelled and not arguments.get("dry_run"):
            self.failures[intent] = {"file_path": path, "content_version": None,
                                     "missing": ["mutation_failed"],
                                     "tool": tool, "error_code": result.error.code if result.error else None,
                                     "issues": [result.error.message if result.error else "operation_failed"]}
        file_results = {_path(item["file_path"], workspace_root): item for item in records(value.get("files")) if isinstance(item.get("file_path"), str)}
        for effect in tool_publications(tool, value, success=result.success):
            path, version = _path(effect["file_path"], workspace_root), effect.get("content_version")
            stamp = json.dumps([path, version, effect.get("operation_id"), effect.get("operation")])
            if stamp in self.commits:
                continue
            self.commits.append(stamp)
            new_publications.append(effect)
            if path not in self.active:
                self.active.append(path)
            if effect.get("operation") == "delete":
                self.artifacts.pop(path, None)
                continue
            previous = self.artifacts.get(path, {})
            observation = file_results.get(path, value).get("observation")
            observation = observation if isinstance(observation, dict) else {}
            # 计算输入未变的写入（纯格式/尺寸/合并等）只作废视觉证据；数据级证据
            # 在公式缓存原样保留时延续到新版本，不再强制等价的重算/校验往返。
            # calculate_spreadsheet 更是"同一份数据换一份缓存"：它发布新版本，
            # 但既不改数据也不改规则，已有的校验结论仍然是对这份数据的结论。
            data_unchanged = bool(previous) and (
                observation.get("calculation_inputs") == "unchanged" or tool == "calculate_spreadsheet")
            caches_kept = observation.get("formula_cache") == "preserved_unchanged_calculation_inputs"
            checks: dict = {}
            for key in ("calculation", "formula_errors"):
                if caches_kept and previous.get("checks", {}).get(key) is True:
                    checks[key] = True
            if data_unchanged and previous.get("checks", {}).get("business") is True:
                checks["business"] = True
            # 数据级写入使旧版本的校验结论失效：留下"需重新校验"的义务
            # （stale_validations），但不把过期当缺陷。data-level 写入后即使
            # 公式缓存原样保留，业务断言是对旧单元格值的断言，也必须重来。
            validations = deepcopy(previous.get("validations", {})) if data_unchanged else {}
            entry = {"content_version": version, "requirements": deepcopy(previous.get("requirements", {})),
                     "checks": checks, "visual_objects": [], "visual_sheets": [], "visual_pages": {},
                     "issues": [], "rule_suspect": [], "validation_results": {} if not data_unchanged else deepcopy(previous.get("validation_results", {})),
                     "validations": validations}
            stale = _stale_keys(previous)
            if data_unchanged:
                # 纯格式/尺寸/重算类操作不动数据：当前版本上已通过的证据继续有效，
                # 只有此前就欠着的重新校验义务顺延到新版本。
                if stale:
                    entry["stale_validations"] = stale
                if previous.get("validated_keys"):
                    entry["validated_keys"] = deepcopy(previous["validated_keys"])
            elif stale:
                # 数据级写入：旧版本的一切校验结论作废 → 全部转入"需重新校验"。
                _mark_stale(entry, stale)
                _drop_results(entry, stale)
            _refresh_issues(entry)
            requirements = observation.get("verification_requirements")
            if isinstance(requirements, dict):
                entry["requirements"] = deepcopy(requirements)
            if observation.get("cell_checks") and all(check.get("verified") is True for check in observation["cell_checks"]):
                entry["checks"]["readback"] = "sampled_after_serialization"
            self.artifacts[path] = entry

        if result.success and tool_publications(tool, value, success=True) and not arguments.get("dry_run"):
            self.failures.pop(intent, None)

        path = _path(value.get("file_path"), workspace_root)
        entry = self.artifacts.get(path) if isinstance(path, str) else None
        if not entry or not result.success:
            return new_publications
        if (tool == "observe_spreadsheet" and value.get("content_version")
                and value["content_version"] != entry["content_version"]):
            # A user/editor may change the artifact between calls. Observing
            # that version invalidates earlier evidence but is not our write.
            # 外部写入同样只是"证据过期"：留重新校验义务，不算产物缺陷。
            entry.update(content_version=value["content_version"], checks={}, visual_objects=[], visual_sheets=[], visual_pages={})
            stale = _stale_keys(entry)
            _mark_stale(entry, stale)
            _drop_results(entry, stale)
            _refresh_issues(entry)
            if path not in self.active:
                self.active.append(path)
        if not value.get("content_version") or value["content_version"] != entry["content_version"]:
            return new_publications  # stale evidence never proves the current version
        if tool == "calculate_spreadsheet":
            info = value.get("formula_recalculation") or {}
            recalculated = info.get("status") == "recalculated" and not info.get("errors")
            entry["checks"]["calculation"] = recalculated
            if recalculated:
                # calculate_spreadsheet 发现公式错误时拒绝发布；一次成功的全簿重算
                # （0 错误）就是完整公式错误检查，不再要求等价的 validate 往返。
                entry["checks"]["formula_errors"] = True
        elif tool == "observe_spreadsheet":
            entry["checks"]["readback"] = value.get("coverage") or "requested_region"
        elif tool == "validate_spreadsheet":
            validation_key = json.dumps([arguments.get("sheet"), arguments.get("rules")], sort_keys=True, ensure_ascii=False)
            validations = entry.setdefault("validations", {})
            if value.get("validation_status") == "complete" and value.get("valid") is True:
                # Only a complete workbook formula check satisfies that
                # obligation; a successful unrelated unique check does not.
                formula_rules = [r for r in arguments.get("rules", []) if r.get("kind") == "formula_errors"]
                if any(not (r.get("sheet") or arguments.get("sheet")) for r in formula_rules):
                    entry["checks"]["formula_errors"] = True
                entry["checks"]["business"] = True
                validations[validation_key] = []
                entry.setdefault("validation_results", {}).pop(validation_key, None)
                # 这次 complete + valid 的校验在最新版本上重建了证据，此前所有
                # "需重新校验"的义务（含规则集已经换掉的旧过期条目）随之消失，
                # 否则过期条目会无限累积。本规则键重新记入当前版本的已校验集合。
                _clear_stale(entry, keep_keys=(validation_key,))
                entry["validated_keys"] = _dedupe([*(entry.get("validated_keys") or []), validation_key])
            else:
                entry["checks"]["business"] = False
                validations[validation_key] = value.get("failures") or value.get("uncalculated_cells") or ["validation_incomplete"]
                # 逐规则回执（checked/skipped/excluded_non_numeric）是区分"规则口径
                # 可疑"与"产物缺陷"的唯一证据，必须和失败项一起留存。
                entry.setdefault("validation_results", {})[validation_key] = deepcopy(value.get("rules") or [])
                # 这次回执就是**最新版本**上的校验结论：本规则键的过期义务
                # 已重建为真实缺陷（issues），不必再列一次"需重新校验"。
                _resolve_stale(entry, validation_key)
                if validation_key in (entry.get("validated_keys") or []):
                    entry["validated_keys"].remove(validation_key)
                    if not entry["validated_keys"]:
                        entry.pop("validated_keys", None)
            _refresh_issues(entry)
        elif tool == "preview_spreadsheet" and result.ui_meta.image and vision:
            visual = value.get("visual_coverage") or {}
            whole_region = visual.get("whole_region") is True
            measured = value.get("measured") or {}
            count, page = measured.get("page_count"), measured.get("page")
            if value.get("surface") == "print" and isinstance(count, int) and isinstance(page, int) and 1 <= page <= count:
                key = json.dumps([value.get("sheet"), value.get("range"), value.get("renderer_digest"), count])
                pages = entry.setdefault("visual_pages", {}).setdefault(key, [])
                if page not in pages:
                    pages.append(page)
                whole_region = len(pages) == count
            if whole_region:
                entry["visual_sheets"] = list(dict.fromkeys([*entry["visual_sheets"], value.get("sheet")]))
                entry["visual_objects"] = list(dict.fromkeys([*entry["visual_objects"], *visual.get("complete_objects", [])]))
        return new_publications

    def pending(self):
        rows = []
        for path in self.active:
            entry = self.artifacts.get(path)
            if not entry:
                continue
            req, checks = entry["requirements"], entry["checks"]
            missing = []
            if req.get("formula_count"):
                if checks.get("calculation") is not True:
                    missing.append("calculation")
                if checks.get("formula_errors") is not True:
                    missing.append("formula_errors")
            drawings = [d for d in req.get("drawings", []) if d["id"] not in entry["visual_objects"]]
            sheets = [s for s in req.get("visual_sheets", []) if s not in entry["visual_sheets"]]
            if drawings or sheets:
                missing.append("visual_preview")
            issues, suspect, product_rules = _entry_issues(entry)
            if issues:
                # 只有产物缺陷才算"校验未通过或不完整"；口径误用（rule_suspect）
                # 是 agent 自己规则写错，不该转嫁成一轮返工或用户侧失败文案。
                # 证据过期（stale）同样不是缺陷：它在下面单独列为"需重新校验"。
                missing.append("validation_failed_or_partial")
            stale = [str(key) for key in (entry.get("stale_validations") or []) if isinstance(key, str)]
            if stale:
                # "需重算/需重校验"是待办的核验义务，不是产物缺陷。旧实现把过期
                # 标记塞进 issues，于是早先规则的过期条目会让交付门永久误报
                # validation_failed_or_partial，白跑返工轮。
                missing.append("validation_stale")
            if missing:
                row = {"file_path": path, "content_version": entry["content_version"],
                       "missing": missing, "drawings": drawings, "sheets": sheets,
                       "validation_rules": product_rules[:5],
                       "issues": issues[:5]}
                if suspect:
                    row["rule_suspect"] = suspect[:5]
                if stale:
                    row["stale_validation_rules"] = stale[:5]
                rows.append(row)
        return rows + list(self.failures.values())

    def next_feedback(self):
        pending = self.pending()
        if not pending:
            return None
        text = json.dumps(pending, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(text.encode()).hexdigest()
        # Bounded correction: changed evidence permits a second pass, unchanged
        # evidence never creates a loop. No prose classification or extra LLM.
        if digest in self.feedback or len(self.feedback) >= 2:
            return None
        self.feedback.append(digest)
        note = ""
        if any(row.get("rule_suspect") for row in pending):
            note = ("标注 rule_suspect 的条目是校验规则口径问题（例如把整列求和 total 用在单个单元格的期望值上），"
                    "不是文件缺陷：请改用正确规则（单点断言用 row_expression 或 observe_spreadsheet 回读），"
                    "不要因此改写文件或重写已给结论；只需补齐其余缺项。")
        if any("validation_stale" in row.get("missing", ()) for row in pending):
            note += ("missing 里的 validation_stale 表示该规则是在更早版本上校验过的，需要按当前版本重新校验；"
                     "它不是产物缺陷（缺陷会以 issues + validation_failed_or_partial 出现），不要因此改写文件。")
        return ("交付证据尚不完整（来自实际工具回执，不是对回复措辞的判断）。"
                "请按最新版本完成下面缺项；重算、校验、预览应依次使用返回的新版本。"
                "本轮只补缺失证据并简短收尾，不要重复已给结论；若无法完成，请清楚说明限制，不重复已提交写入。"
                "图表用 preview_spreadsheet(surface='auto')，"
                "范围须覆盖完整图表，导出文件不能替代图像观察。" + note + "\n" + text)
