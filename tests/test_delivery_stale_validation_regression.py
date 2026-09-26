"""回归：证据过期（需重新校验）不得被当成产物缺陷。

真实会话复刻（Downloads/…回归分析… (3).json 的 _seq222→_seq267）：
    create → validate(旧规则集, complete+valid) → write(数据级) → calculate
    → validate(新规则集, complete+valid) → pending()
最后一步曾经报
    issues: ["validation_stale_after_write"]
    missing: ["visual_preview", "validation_failed_or_partial"]
于是白跑一轮返工：gate 把"早先规则的过期标记"当成产物缺陷，而合成标记
不是 dict，只能落进 issues，且只有**同一 rules 键**再次成功校验才会清除，
规则集一变就永久污染产物。

现在的数据模型把两者分开：
    * issues / validation_failed_or_partial —— 当前版本上被证实的产物缺陷；
    * stale_validations / validation_stale —— 旧版本结论需按当前版本重跑。
"""
from __future__ import annotations

import json

from excelmanus.engine_core.delivery import DeliveryLedger
from excelmanus.engine_core.tool_result import from_payload

ARTIFACT = "outputs/广告与销售_回归分析报告.xlsx"
V1 = "sha256:03a65550f33321df9c4f93234fa1d71a6b4d39e84de36c5e38dbd71a5e15a8a1"
V2 = "sha256:d26812790c1efcce3c4ed3122f4ee92dbeddc93ccdea5f70e3d866b136e7c5a1"
V3 = "sha256:9999999999999999999999999999999999999999999999999999999999999999"

# 日志里的两套规则：旧（B7/B8/... 单点断言 + formula_errors）与新（formula_errors + total + unique）。
OLD_RULES = [{"kind": "formula_errors"},
             {"kind": "cell", "sheet": "回归分析", "cell": "B7", "expected": 6.70706766917293, "tolerance": 1e-09}]
NEW_RULES = [{"kind": "formula_errors"},
             {"kind": "cell", "sheet": "回归分析", "cell": "B30", "expected": 68.5044356917293, "tolerance": 1e-06},
             {"kind": "total", "sheet": "数据", "column": "B", "expected": 1001.2},
             {"kind": "unique", "sheet": "数据", "column": "A"}]


def _published(path=ARTIFACT, version=V1, *, operation_id="op1", calculation_inputs=None, requirements=None):
    observation = {"verification_requirements": requirements or {}}
    if calculation_inputs is not None:
        observation["calculation_inputs"] = calculation_inputs
    return from_payload({
        "status": "success", "file_path": path, "content_version": version,
        "receipt": {"operation_id": operation_id, "state": "committed", "targets": [
            {"path": path, "after_version": version, "op": "update", "publish_status": "published"}]},
        "observation": observation,
    })


def _validated(path=ARTIFACT, version=V1, *, rules=NEW_RULES, valid=True, failures=None, results=None):
    payload = {"status": "success", "file_path": path, "content_version": version,
               "validation_status": "complete", "valid": valid,
               "failures": failures if failures is not None else []}
    if results is not None:
        payload["rules"] = results
    return from_payload(payload)


def _recalculated(version, operation_id, errors=()):
    return from_payload({
        "status": "success", "file_path": ARTIFACT, "content_version": version,
        "receipt": {"operation_id": operation_id, "state": "committed", "targets": [
            {"path": ARTIFACT, "after_version": version, "op": "update", "publish_status": "published"}]},
        "formula_recalculation": {"status": "recalculated", "errors": list(errors)},
    })


def _row(ledger):
    rows = ledger.pending()
    assert rows, "expected the artifact to still be in delivery scope"
    return rows[0]


# ── 日志(3) 的时序：过期标记不得污染交付门 ─────────────────────────────

def test_log3_timeline_after_full_revalidation_has_no_product_issue():
    """create→calculate→validate(旧)→write→calculate→validate(新, complete+valid) 后 pending 干净。"""
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT}, _published(requirements={"formula_count": 51}))
    ledger.record("calculate_spreadsheet", {"file_path": ARTIFACT}, _recalculated(V1, "op-calc-1"))
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": OLD_RULES},
                  _validated(rules=OLD_RULES, results=[{"rule": 0, "kind": "formula_errors", "checked": 138, "failed": 0}]))
    assert ledger.pending() == []

    # _seq222 数据级写入：旧校验结论过期（需重新校验），但文件没有新缺陷。
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT},
                  _published(version=V2, operation_id="op2", calculation_inputs="changed",
                             requirements={"formula_count": 51}))
    row = _row(ledger)
    assert "validation_stale" in row["missing"]
    assert "validation_failed_or_partial" not in row["missing"]
    assert row["issues"] == []
    assert ledger.artifacts[ARTIFACT]["issues"] == []
    assert ledger.artifacts[ARTIFACT]["stale_validations"] == [json.dumps([None, OLD_RULES], sort_keys=True, ensure_ascii=False)]

    # _seq226 重算 + _seq232/235 新规则集全簿校验（complete + valid）。
    ledger.record("calculate_spreadsheet", {"file_path": ARTIFACT}, _recalculated(V2, "op3"))
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": NEW_RULES},
                  _validated(version=V2, rules=NEW_RULES,
                             results=[{"rule": 0, "kind": "formula_errors", "sheet": "数据", "checked": 138, "failed": 0},
                                      {"rule": 0, "kind": "formula_errors", "sheet": "回归分析", "checked": 123, "failed": 0},
                                      {"rule": 3, "kind": "unique", "sheet": "数据", "checked": 20, "failed": 0}]))
    assert ledger.pending() == []
    assert "stale_validations" not in ledger.artifacts[ARTIFACT]


def test_stale_obligation_does_not_survive_a_complete_check_on_a_new_rule_set():
    """规则集换掉后旧过期条目也必须随一次 complete+valid 一起消失（不无限累积）。"""
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT}, _published())
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": OLD_RULES}, _validated(rules=OLD_RULES))
    for index in range(3):
        version = f"sha256:write{index}"
        ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT},
                      _published(version=version, operation_id=f"w{index}", calculation_inputs="changed"))
        ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": [{"kind": "unique", "sheet": "数据", "column": "A"}]},
                      _validated(version=version, rules=[{"kind": "unique", "sheet": "数据", "column": "A"}]))
    key = json.dumps([None, [{"kind": "unique", "sheet": "数据", "column": "A"}]], sort_keys=True, ensure_ascii=False)
    entry = ledger.artifacts[ARTIFACT]
    # 每次 complete+valid 都把此前所有过期条目清掉（含 OLD_RULES 的那条），
    # 因此不会随写入次数无限累积；本次校验的键记为当前版本已校验。
    assert "stale_validations" not in entry
    assert entry["validated_keys"] == [key]
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT},
                  _published(version="sha256:final", operation_id="final", calculation_inputs="changed"))
    assert "validation_stale" in _row(ledger)["missing"]
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": NEW_RULES},
                  _validated(version="sha256:final", rules=NEW_RULES))
    assert ledger.pending() == []


def test_external_write_is_stale_not_a_defect():
    """observe 到更新版本（外部/编辑器改写）同样只留重新校验义务，不算缺陷。"""
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT}, _published())
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": NEW_RULES}, _validated(rules=NEW_RULES))
    assert ledger.pending() == []
    ledger.record("observe_spreadsheet", {"file_path": ARTIFACT},
                  from_payload({"status": "success", "file_path": ARTIFACT, "content_version": "external-v2"}))
    row = _row(ledger)
    assert "validation_stale" in row["missing"]
    assert row["issues"] == []
    assert "validation_stale_after_external_write" not in json.dumps(ledger.artifacts[ARTIFACT])
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": NEW_RULES},
                  _validated(version="external-v2", rules=NEW_RULES))
    assert ledger.pending() == []


def test_stale_marker_survives_snapshot_round_trip_without_becoming_an_issue():
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT}, _published())
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": NEW_RULES}, _validated(rules=NEW_RULES))
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT},
                  _published(version=V2, operation_id="op2", calculation_inputs="changed"))
    restored = DeliveryLedger.from_dict(ledger.to_dict())
    assert restored.pending() == ledger.pending()
    row = restored.pending()[0]
    assert "validation_stale" in row["missing"]
    assert "validation_failed_or_partial" not in row["missing"]


# ── 真缺陷仍须报 validation_failed_or_partial ─────────────────────────

def test_genuine_formula_error_still_reports_partial_validation():
    """真公式错误永远是产物缺陷；失败回执不是"过期"，本规则键不再挂 stale。"""
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT}, _published(requirements={"formula_count": 51}))
    failure = {"rule": 0, "kind": "formula_errors", "sheet": "数据", "cell": "B3", "actual": "#REF!",
               "expected": "no formula error"}
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": NEW_RULES},
                  _validated(rules=NEW_RULES, valid=False, failures=[failure],
                             results=[{"rule": 0, "kind": "formula_errors", "sheet": "数据", "checked": 138, "failed": 1}]))
    row = _row(ledger)
    assert "validation_failed_or_partial" in row["missing"]
    assert row["issues"] == [failure]
    assert "validation_stale" not in row["missing"]


def test_failed_revalidation_of_a_new_rule_set_keeps_the_other_stale_obligation():
    """新规则集校验失败不能冒充"全簿通过"：旧规则集的重校验义务仍在。"""
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT}, _published())
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": OLD_RULES}, _validated(rules=OLD_RULES))
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT},
                  _published(version=V2, operation_id="op2", calculation_inputs="changed"))
    failure = {"rule": 0, "kind": "formula_errors", "sheet": "数据", "cell": "B3", "actual": "#REF!",
               "expected": "no formula error"}
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": NEW_RULES},
                  _validated(version=V2, rules=NEW_RULES, valid=False, failures=[failure],
                             results=[{"rule": 0, "kind": "formula_errors", "sheet": "数据", "checked": 138, "failed": 1}]))
    row = _row(ledger)
    assert "validation_failed_or_partial" in row["missing"]
    assert "validation_stale" in row["missing"]


def test_genuine_total_mismatch_still_reports_partial_validation():
    """纯数值列的合计错误是产物缺陷，不能被"过期≠缺陷"这条规则吞掉。"""
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT}, _published())
    rules = [{"kind": "total", "sheet": "数据", "column": "C", "expected": 1001.2, "tolerance": 0.001}]
    results = [{"rule": 0, "kind": "total", "sheet": "数据", "checked": 20, "failed": 1}]
    failures = [{"rule": 0, "kind": "total", "sheet": "数据", "cell": "C2", "actual": 950.0,
                 "expected": 1001.2, "reason": "total_mismatch"}]
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": rules},
                  _validated(rules=rules, valid=False, failures=failures, results=results))
    row = _row(ledger)
    assert "validation_failed_or_partial" in row["missing"]
    assert row["issues"] == failures
    assert row["validation_rules"] == [[None, rules]]


def test_failure_then_write_then_unrelated_failure_keeps_both_channels():
    """过期与缺陷可以同时存在，各走各的通道：stale 不会被缺陷掩盖，缺陷也不被吞掉。"""
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT}, _published())
    unique = [{"kind": "unique", "sheet": "数据", "column": "A"}]
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": unique}, _validated(rules=unique))
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT},
                  _published(version=V2, operation_id="op2", calculation_inputs="changed"))
    failure = {"rule": 0, "kind": "formula_errors", "sheet": "数据", "cell": "B3", "actual": "#REF!",
               "expected": "no formula error"}
    formula = [{"kind": "formula_errors"}]
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": formula},
                  _validated(version=V2, rules=formula, valid=False, failures=[failure],
                             results=[{"rule": 0, "kind": "formula_errors", "sheet": "数据", "checked": 138, "failed": 1}]))
    row = _row(ledger)
    assert "validation_failed_or_partial" in row["missing"]   # 真公式错误
    assert "validation_stale" in row["missing"]               # unique 规则仍待按当前版本重校验
    assert row["issues"] == [failure]
    assert any("unique" in key for key in ledger.artifacts[ARTIFACT]["stale_validations"])


def test_same_rules_revalidation_replaces_the_stale_obligation():
    """同一 rules 键重新校验（即使失败）后就不再是"过期"，而是当前版本的缺陷。"""
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT}, _published())
    unique = [{"kind": "unique", "sheet": "数据", "column": "A"}]
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": unique}, _validated(rules=unique))
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT},
                  _published(version=V2, operation_id="op2", calculation_inputs="changed"))
    failure = {"rule": 0, "kind": "unique", "sheet": "数据", "cell": "A5", "actual": ["甲"], "expected": "unique"}
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": unique},
                  _validated(version=V2, rules=unique, valid=False, failures=[failure],
                             results=[{"rule": 0, "kind": "unique", "sheet": "数据", "checked": 20, "failed": 1}]))
    row = _row(ledger)
    assert "validation_failed_or_partial" in row["missing"]
    assert row["issues"] == [failure]
    assert "validation_stale" not in row["missing"]
    assert "stale_validations" not in ledger.artifacts[ARTIFACT]


def test_rule_suspect_still_does_not_trigger_rework_after_the_split():
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT}, _published())
    rules = [{"kind": "total", "sheet": "回归分析", "column": "B", "expected": 6.707067669172933}]
    results = [{"rule": 0, "kind": "total", "sheet": "回归分析", "checked": 23, "failed": 1,
                "excluded_non_numeric": 2}]
    failures = [{"rule": 0, "kind": "total", "sheet": "回归分析", "cell": "B2", "actual": 26660.0,
                 "expected": 6.707067669172933, "reason": "total_mismatch"}]
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": rules},
                  _validated(rules=rules, valid=False, failures=failures, results=results))
    assert ledger.pending() == []
    assert ledger.next_feedback() is None


def test_begin_turn_keeps_unfinished_revalidation_in_scope():
    """延续未完成核验的行为不变：需重校验的条目仍留在 active 里。"""
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT}, _published())
    ledger.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": NEW_RULES}, _validated(rules=NEW_RULES))
    ledger.record("apply_spreadsheet_changes", {"file_path": ARTIFACT},
                  _published(version=V2, operation_id="op2", calculation_inputs="changed"))
    pending = ledger.pending()
    assert [row["missing"] for row in pending] == [["validation_stale"]]
    ledger.begin_turn()
    assert ledger.pending() == pending
    feedback = ledger.next_feedback()
    assert feedback and "validation_stale" in feedback
    assert ledger.next_feedback() is None  # 证据未变时不会刷出无限返工轮
