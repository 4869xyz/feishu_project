"""Persistent per-chat, per-sender source batches for sales aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from services.sales_workbook_aggregator import SourceWorkbook


@dataclass(frozen=True, slots=True)
class BatchSource:
    """One source staged in an active aggregation batch."""

    source: SourceWorkbook
    display_name: str


@dataclass(frozen=True, slots=True)
class AddSourceResult:
    """Outcome of idempotently staging one source."""

    added: bool
    active_count: int


class AggregationBatchStore:
    """Store active source order and seen IDs in atomic JSON files."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.state_dir = self.root_dir / "state"
        self.output_dir = self.root_dir / "output"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _owner_key(chat_id: str, sender_open_id: str) -> str:
        identity = f"{chat_id}\0{sender_open_id}".encode("utf-8")
        return hashlib.sha256(identity).hexdigest()[:24]

    def _state_path(self, chat_id: str, sender_open_id: str) -> Path:
        return self.state_dir / f"{self._owner_key(chat_id, sender_open_id)}.json"

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {"version": 1, "seen_source_ids": [], "active_sources": []}

    def _load(self, chat_id: str, sender_open_id: str) -> dict[str, Any]:
        path = self._state_path(chat_id, sender_open_id)
        if not path.is_file():
            return self._empty_state()
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法读取汇总批次状态：{path.name}") from exc
        if state.get("version") != 1:
            raise RuntimeError(f"不支持的汇总批次状态版本：{path.name}")
        if not isinstance(state.get("seen_source_ids"), list) or not isinstance(
            state.get("active_sources"), list
        ):
            raise RuntimeError(f"汇总批次状态结构无效：{path.name}")
        return state

    def _save(self, chat_id: str, sender_open_id: str, state: dict[str, Any]) -> None:
        path = self._state_path(chat_id, sender_open_id)
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temp.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def add_source(
        self,
        chat_id: str,
        sender_open_id: str,
        source: SourceWorkbook,
        *,
        display_name: str,
    ) -> AddSourceResult:
        """Stage a new stable source ID once across all batches for this owner."""

        state = self._load(chat_id, sender_open_id)
        seen = set(state["seen_source_ids"])
        if source.source_file_id in seen:
            return AddSourceResult(False, len(state["active_sources"]))
        state["seen_source_ids"].append(source.source_file_id)
        state["active_sources"].append(
            {
                "source_file_id": source.source_file_id,
                "path": str(source.path),
                "display_name": display_name,
            }
        )
        self._save(chat_id, sender_open_id, state)
        return AddSourceResult(True, len(state["active_sources"]))

    def list_sources(self, chat_id: str, sender_open_id: str) -> tuple[BatchSource, ...]:
        """Return active sources in their persisted upload order."""

        state = self._load(chat_id, sender_open_id)
        result: list[BatchSource] = []
        for item in state["active_sources"]:
            result.append(
                BatchSource(
                    source=SourceWorkbook(item["source_file_id"], item["path"]),
                    display_name=item.get("display_name") or Path(item["path"]).name,
                )
            )
        return tuple(result)

    def clear_active(self, chat_id: str, sender_open_id: str) -> int:
        """Discard active sources while retaining the redelivery ledger."""

        state = self._load(chat_id, sender_open_id)
        count = len(state["active_sources"])
        state["active_sources"] = []
        self._save(chat_id, sender_open_id, state)
        return count

    def new_output_path(
        self,
        chat_id: str,
        sender_open_id: str,
        *,
        now: datetime | None = None,
    ) -> Path:
        """Return a collision-resistant XLSX output path for an active batch."""

        timestamp = now or datetime.now()
        owner = self._owner_key(chat_id, sender_open_id)
        directory = self.output_dir / timestamp.strftime("%Y-%m") / owner
        directory.mkdir(parents=True, exist_ok=True)
        return directory / (
            f"2026年销售数据汇总-{timestamp:%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.xlsx"
        )
