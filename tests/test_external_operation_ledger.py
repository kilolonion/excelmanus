from __future__ import annotations

from excelmanus.workspace.txlog import TxLog


def test_external_receipt_roundtrip_is_durable(tmp_path) -> None:
    log = TxLog(tmp_path)
    payload = {
        "operation_id": "emop_test",
        "tool_name": "memory_save",
        "intent_hash": "abc",
        "status": "external_unverified",
    }

    log.write_external_receipt("emop_test", payload)

    assert log.read_external_receipt("emop_test") == payload

