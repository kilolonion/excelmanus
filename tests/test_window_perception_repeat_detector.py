"""RepeatDetector 单元测试。"""

from excelmanus.window_perception.repeat_detector import RepeatDetector


def test_record_read_counts_by_triplet() -> None:
    detector = RepeatDetector()
    assert detector.record_read("a.xlsx", "Sheet1", "A1:B2") == 1
    assert detector.record_read("a.xlsx", "Sheet1", "A1:B2") == 2
    assert detector.record_read("a.xlsx", "Sheet1", "C1:D2") == 1
    assert detector.record_read("a.xlsx", "Sheet2", "A1:B2") == 1


def test_record_write_resets_same_file_sheet_only() -> None:
    detector = RepeatDetector()
    detector.record_read("a.xlsx", "Sheet1", "A1:B2")
    detector.record_read("a.xlsx", "Sheet1", "C1:D2")
    detector.record_read("a.xlsx", "Sheet2", "A1:B2")
    detector.record_read("b.xlsx", "Sheet1", "A1:B2")

    detector.record_write("a.xlsx", "Sheet1")

    assert detector.record_read("a.xlsx", "Sheet1", "A1:B2") == 1
    assert detector.record_read("a.xlsx", "Sheet1", "C1:D2") == 1
    assert detector.record_read("a.xlsx", "Sheet2", "A1:B2") == 2
    assert detector.record_read("b.xlsx", "Sheet1", "A1:B2") == 2


def test_record_read_counts_isolated_by_intent() -> None:
    detector = RepeatDetector()
    assert detector.record_read("a.xlsx", "Sheet1", "A1:B2", intent_tag="aggregate") == 1
    assert detector.record_read("a.xlsx", "Sheet1", "A1:B2", intent_tag="validate") == 1
    assert detector.record_read("a.xlsx", "Sheet1", "A1:B2", intent_tag="aggregate") == 2
