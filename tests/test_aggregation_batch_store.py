"""Tests for persistent and idempotent aggregation batches."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from services.aggregation_batch_store import AggregationBatchStore
from services.sales_workbook_aggregator import SourceWorkbook


def test_batch_store_preserves_order_and_seen_ids(project_tmp_dir: Path) -> None:
    """Active order persists while cleared source IDs cannot be redelivered."""

    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    first_path = project_tmp_dir / "first.xlsx"
    second_path = project_tmp_dir / "second.xlsx"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")

    first = store.add_source(
        "chat",
        "sender",
        SourceWorkbook("source-1", first_path),
        display_name="一.xlsx",
    )
    second = store.add_source(
        "chat",
        "sender",
        SourceWorkbook("source-2", second_path),
        display_name="二.xlsx",
    )

    assert first.added is True and first.active_count == 1
    assert second.added is True and second.active_count == 2
    assert [item.source.source_file_id for item in store.list_sources("chat", "sender")] == [
        "source-1",
        "source-2",
    ]

    assert store.clear_active("chat", "sender") == 2
    duplicate = store.add_source(
        "chat",
        "sender",
        SourceWorkbook("source-1", first_path),
        display_name="一.xlsx",
    )
    assert duplicate.added is False
    assert store.list_sources("chat", "sender") == ()


def test_batch_store_isolates_owners_and_builds_xlsx_path(project_tmp_dir: Path) -> None:
    """Different senders in one chat never share active source files."""

    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    source_path = project_tmp_dir / "source.xlsx"
    source_path.write_bytes(b"source")
    store.add_source(
        "chat",
        "sender-a",
        SourceWorkbook("source", source_path),
        display_name="source.xlsx",
    )

    assert len(store.list_sources("chat", "sender-a")) == 1
    assert store.list_sources("chat", "sender-b") == ()
    output = store.new_output_path(
        "chat", "sender-a", now=datetime(2026, 7, 15, 9, 8, 7)
    )
    assert output.suffix == ".xlsx"
    assert output.parent.parent.name == "2026-07"


def test_all_active_source_paths_covers_every_owner(project_tmp_dir: Path) -> None:
    """Global cleanup protection includes active files from every persisted batch."""

    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    first_path = project_tmp_dir / "first.xlsx"
    second_path = project_tmp_dir / "second.xlsx"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    store.add_source(
        "chat-a",
        "sender-a",
        SourceWorkbook("source-a", first_path),
        display_name="first.xlsx",
    )
    store.add_source(
        "chat-b",
        "sender-b",
        SourceWorkbook("source-b", second_path),
        display_name="second.xlsx",
    )

    assert store.all_active_source_paths() == frozenset(
        (first_path.resolve(), second_path.resolve())
    )

    store.clear_active("chat-a", "sender-a")

    assert store.all_active_source_paths() == frozenset((second_path.resolve(),))
