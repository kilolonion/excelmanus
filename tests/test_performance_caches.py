"""Regression tests for bounded workbook/CSV read caches."""

from __future__ import annotations

from pathlib import Path


def test_open_snapshot_reuses_unchanged_file(monkeypatch, tmp_path: Path) -> None:
    from excelmanus.workbook import snapshot
    from excelmanus.workspace.refs import WorkspaceRef

    source = tmp_path / "rows.csv"
    source.write_text("a,b\n1,one\n2,two\n", encoding="utf-8")
    workspace = WorkspaceRef.from_root(tmp_path / "workspace")
    calls = 0
    original = snapshot._read_and_convert

    def counted(path, ref):
        nonlocal calls
        calls += 1
        return original(path, ref)

    snapshot._snapshot_cache._entries.clear()
    snapshot._snapshot_cache._bytes = 0
    monkeypatch.setattr(snapshot, "_read_and_convert", counted)

    first = snapshot.open_snapshot_at(
        source, relative="rows.csv", workspace=workspace,
    )
    second = snapshot.open_snapshot_at(
        source, relative="rows.csv", workspace=workspace,
    )
    assert first.content_version == second.content_version
    assert calls == 1

    source.write_text("a,b\n1,one\n2,two!\n", encoding="utf-8")
    changed = snapshot.open_snapshot_at(
        source, relative="rows.csv", workspace=workspace,
    )
    assert changed.content_version != first.content_version
    assert calls == 2


def test_csv_index_reads_requested_window_from_cached_index(tmp_path: Path) -> None:
    from excelmanus.workbook.csv_index import csv_index
    from excelmanus.workbook.snapshot import open_snapshot_at
    from excelmanus.workspace.refs import WorkspaceRef

    source = tmp_path / "rows.csv"
    source.write_text(
        "name,value\n" + "".join(f"row-{i},{i}\n" for i in range(1000)),
        encoding="utf-8",
    )
    workspace = WorkspaceRef.from_root(tmp_path / "workspace")
    snap = open_snapshot_at(source, relative="rows.csv", workspace=workspace)
    index = csv_index(snap)
    rows = list(index.window(1, 6))
    assert index.rows == 1001
    assert rows[0] == ["name", "value"]
    assert rows[-1] == ["row-4", "4"]
