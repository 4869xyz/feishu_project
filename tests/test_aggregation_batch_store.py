"""Tests for persistent and idempotent aggregation batches."""

from __future__ import annotations

from datetime import datetime
import json
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


def test_v1_state_is_migrated_without_losing_active_sources(
    project_tmp_dir: Path,
) -> None:
    """Reading a legacy state persists v2 with an empty registered collection."""

    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    source_path = project_tmp_dir / "legacy.xlsx"
    source_path.write_bytes(b"legacy")
    state_path = store._state_path("chat", "sender")
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "seen_source_ids": ["legacy-id"],
                "active_sources": [
                    {
                        "source_file_id": "legacy-id",
                        "path": str(source_path),
                        "display_name": "legacy.xlsx",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert len(store.list_sources("chat", "sender")) == 1
    assert store.list_registered_sources("chat", "sender") == ()
    migrated = json.loads(state_path.read_text(encoding="utf-8"))
    assert migrated["version"] == 2
    assert migrated["registered_sources"] == []


def test_registered_sources_persist_update_remove_and_isolate_owner(
    project_tmp_dir: Path,
) -> None:
    """Persistent cloud sources keep order and remain isolated by sender."""

    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    first_path = store.registered_cache_path(
        "chat", "sender", "sheets", "sht_first"
    )
    second_path = store.registered_cache_path(
        "chat", "sender", "wiki", "wiki_second"
    )
    first_path.parent.mkdir(parents=True, exist_ok=True)
    second_path.parent.mkdir(parents=True, exist_ok=True)
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")

    first = store.add_registered_source(
        "chat",
        "sender",
        kind="sheets",
        token="sht_first",
        url="https://example.feishu.cn/sheets/sht_first",
        display_name="一号表",
        cached_path=first_path,
        refreshed_at="2026-07-16T10:00:00",
    )
    second = store.add_registered_source(
        "chat",
        "sender",
        kind="wiki",
        token="wiki_second",
        url="https://example.feishu.cn/wiki/wiki_second",
        display_name="二号表",
        cached_path=second_path,
        refreshed_at="2026-07-16T10:01:00",
    )

    assert first.added is True and first.registered_count == 1
    assert second.added is True and second.registered_count == 2
    assert store.list_registered_sources("chat", "other") == ()
    assert [item.display_name for item in store.list_registered_sources("chat", "sender")] == [
        "一号表",
        "二号表",
    ]

    updated = store.update_registered_source(
        "chat",
        "sender",
        first.source.source_id,
        display_name="一号表（最新）",
        cached_path=first_path,
        refreshed_at="2026-07-16T11:00:00",
    )
    assert updated.display_name == "一号表（最新）"
    assert first_path.resolve() in store.all_active_source_paths()

    removed = store.remove_registered_source(
        "chat", "sender", second.source.source_id.upper()
    )
    assert removed is not None and removed.display_name == "二号表"
    assert [item.source_id for item in store.list_registered_sources("chat", "sender")] == [
        first.source.source_id
    ]
