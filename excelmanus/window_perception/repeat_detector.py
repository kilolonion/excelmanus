"""窗口读取循环检测器。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RepeatDetector:
    """按 (file, sheet, range, intent) 追踪重复读取次数。"""

    _counter: dict[tuple[str, str, str, str], int] = field(default_factory=dict)

    def record_read(
        self,
        file_path: str,
        sheet_name: str,
        range_ref: str,
        intent_tag: str = "general",
    ) -> int:
        """记录一次读取并返回累计次数。"""
        key = (
            file_path.strip(),
            sheet_name.strip(),
            range_ref.strip().upper(),
            str(intent_tag or "general").strip().lower(),
        )
        if not all(key):
            return 0
        next_count = int(self._counter.get(key, 0)) + 1
        self._counter[key] = next_count
        return next_count

    def record_write(self, file_path: str, sheet_name: str) -> None:
        """写入后清空同文件同工作表下所有读取计数。"""
        normalized_file = file_path.strip()
        normalized_sheet = sheet_name.strip()
        if not normalized_file or not normalized_sheet:
            return
        stale_keys = [
            key
            for key in self._counter
            if key[0] == normalized_file and key[1] == normalized_sheet
        ]
        for key in stale_keys:
            self._counter.pop(key, None)
