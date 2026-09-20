"""Index CSV logical records once per immutable snapshot, including quoted newlines."""

import csv
import io
import sys
from array import array
from dataclasses import dataclass

from excelmanus.workbook.read_cache import ReadCache


@dataclass(frozen=True)
class CsvIndex:
    text: str
    offsets: array
    columns: int
    separator: str

    @property
    def rows(self) -> int:
        return len(self.offsets) - 1

    def window(self, start: int, end: int):
        """One-based, inclusive row range; parse only the requested records."""
        end = min(end, self.rows)
        if start > end:
            return iter(())
        text = self.text[self.offsets[start - 1]:self.offsets[end]]
        return csv.reader(io.StringIO(text, newline=""), delimiter=self.separator)


_indexes: ReadCache[CsvIndex] = ReadCache(max_bytes=32 * 1024 * 1024, max_entries=8)


def csv_index(snapshot) -> CsvIndex:
    from excelmanus.workbook.snapshot import _decode_text_bytes

    def build() -> tuple[CsvIndex, int]:
        text = _decode_text_bytes(snapshot.read_bytes())
        stream = io.StringIO(text, newline="")
        offsets = array("Q", [0])
        columns = 0
        for row in csv.reader(stream, delimiter=snapshot.csv_separator()):
            offsets.append(stream.tell())
            columns = max(columns, len(row))
        index = CsvIndex(text, offsets, columns, snapshot.csv_separator())
        return index, sys.getsizeof(text) + sys.getsizeof(offsets)

    return _indexes.get_or_create((snapshot.id.key(), snapshot.suffix), build)
