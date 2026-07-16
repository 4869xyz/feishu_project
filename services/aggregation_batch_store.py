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


@dataclass(frozen=True, slots=True)
class RegisteredCloudSource:
    '''One persistent Feishu table source refreshed before aggregation.'''

    source_id: str
    kind: str
    token: str
    url: str
    display_name: str
    cached_path: Path
    last_success_at: str


@dataclass(frozen=True, slots=True)
class AddRegisteredSourceResult:
    '''Outcome of idempotently registering one persistent cloud source.'''

    added: bool
    source: RegisteredCloudSource
    registered_count: int


class AggregationBatchStore:
    """Store active source order and seen IDs in atomic JSON files."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.state_dir = self.root_dir / "state"
        self.output_dir = self.root_dir / "output"
        self.registered_dir = self.root_dir / "registered"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.registered_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _owner_key(chat_id: str, sender_open_id: str) -> str:
        identity = f"{chat_id}\0{sender_open_id}".encode("utf-8")
        return hashlib.sha256(identity).hexdigest()[:24]

    def _state_path(self, chat_id: str, sender_open_id: str) -> Path:
        return self.state_dir / f"{self._owner_key(chat_id, sender_open_id)}.json"

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "version": 2,
            "seen_source_ids": [],
            "active_sources": [],
            "registered_sources": [],
        }

    def _load(self, chat_id: str, sender_open_id: str) -> dict[str, Any]:
        path = self._state_path(chat_id, sender_open_id)
        if not path.is_file():
            return self._empty_state()
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法读取汇总批次状态：{path.name}") from exc
        version = state.get("version")
        if version == 1:
            state = {
                "version": 2,
                "seen_source_ids": state.get("seen_source_ids"),
                "active_sources": state.get("active_sources"),
                "registered_sources": [],
            }
            self._validate_state(state, path.name)
            self._save(chat_id, sender_open_id, state)
            return state
        if version != 2:
            raise RuntimeError(f"不支持的汇总批次状态版本：{path.name}")
        self._validate_state(state, path.name)
        return state

    @staticmethod
    def _validate_state(state: dict[str, Any], filename: str) -> None:
        """Reject malformed persisted collections before callers use them."""

        if not isinstance(state.get("seen_source_ids"), list) or not isinstance(
            state.get("active_sources"), list
        ) or not isinstance(state.get("registered_sources"), list):
            raise RuntimeError(f"汇总批次状态结构无效：{filename}")

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

    @staticmethod
    def registered_source_id(kind: str, token: str) -> str:
        """Return a stable user-facing identifier for one Feishu source link."""

        normalized_kind = kind.strip().lower()
        normalized_token = token.strip()
        if normalized_kind not in {"sheets", "wiki"}:
            raise ValueError("固定云表类型必须是 sheets 或 wiki")
        if not normalized_token:
            raise ValueError("固定云表 Token 不能为空")
        digest = hashlib.sha256(
            f"{normalized_kind}\0{normalized_token}".encode("utf-8")
        ).hexdigest()[:12]
        return f"cloud-{digest}"

    def registered_cache_path(
        self,
        chat_id: str,
        sender_open_id: str,
        kind: str,
        token: str,
    ) -> Path:
        """Return the stable latest-cache path for one registered source."""

        source_id = self.registered_source_id(kind, token)
        directory = self.registered_dir / self._owner_key(chat_id, sender_open_id)
        return directory / source_id / "latest.xlsx"

    def registered_staging_path(
        self,
        chat_id: str,
        sender_open_id: str,
        kind: str,
        token: str,
    ) -> Path:
        """Return a unique sibling path validated before latest is replaced."""

        latest = self.registered_cache_path(chat_id, sender_open_id, kind, token)
        latest.parent.mkdir(parents=True, exist_ok=True)
        return latest.with_name(f".latest.{uuid4().hex}.staging.xlsx")

    @staticmethod
    def promote_registered_cache(staging_path: Path, latest_path: Path) -> None:
        """Atomically replace a registered source cache after validation."""

        latest_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_path, latest_path)

    @staticmethod
    def _registered_from_item(item: dict[str, Any]) -> RegisteredCloudSource:
        return RegisteredCloudSource(
            source_id=item["source_id"],
            kind=item["kind"],
            token=item["token"],
            url=item["url"],
            display_name=item["display_name"],
            cached_path=Path(item["cached_path"]).resolve(),
            last_success_at=item["last_success_at"],
        )

    def list_registered_sources(
        self, chat_id: str, sender_open_id: str
    ) -> tuple[RegisteredCloudSource, ...]:
        """Return persistent cloud sources in registration order."""

        state = self._load(chat_id, sender_open_id)
        result: list[RegisteredCloudSource] = []
        for item in state["registered_sources"]:
            if not isinstance(item, dict):
                raise RuntimeError("固定云表状态结构无效")
            try:
                result.append(self._registered_from_item(item))
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("固定云表状态结构无效") from exc
        return tuple(result)

    def add_registered_source(
        self,
        chat_id: str,
        sender_open_id: str,
        *,
        kind: str,
        token: str,
        url: str,
        display_name: str,
        cached_path: Path,
        refreshed_at: str,
    ) -> AddRegisteredSourceResult:
        """Register one cloud source once for an isolated owner."""

        state = self._load(chat_id, sender_open_id)
        source_id = self.registered_source_id(kind, token)
        for item in state["registered_sources"]:
            if item.get("source_id") == source_id:
                source = self._registered_from_item(item)
                return AddRegisteredSourceResult(
                    False, source, len(state["registered_sources"])
                )
        item = {
            "source_id": source_id,
            "kind": kind.strip().lower(),
            "token": token.strip(),
            "url": url.strip(),
            "display_name": display_name.strip() or source_id,
            "cached_path": str(cached_path.resolve()),
            "last_success_at": refreshed_at,
        }
        state["registered_sources"].append(item)
        self._save(chat_id, sender_open_id, state)
        return AddRegisteredSourceResult(
            True,
            self._registered_from_item(item),
            len(state["registered_sources"]),
        )

    def update_registered_source(
        self,
        chat_id: str,
        sender_open_id: str,
        source_id: str,
        *,
        display_name: str,
        cached_path: Path,
        refreshed_at: str,
    ) -> RegisteredCloudSource:
        """Record metadata from the latest successful refresh."""

        state = self._load(chat_id, sender_open_id)
        for item in state["registered_sources"]:
            if item.get("source_id") != source_id:
                continue
            item["display_name"] = display_name.strip() or source_id
            item["cached_path"] = str(cached_path.resolve())
            item["last_success_at"] = refreshed_at
            self._save(chat_id, sender_open_id, state)
            return self._registered_from_item(item)
        raise KeyError(f"固定云表不存在：{source_id}")

    def remove_registered_source(
        self, chat_id: str, sender_open_id: str, source_id: str
    ) -> RegisteredCloudSource | None:
        """Remove one owner's registration while leaving generated outputs intact."""

        state = self._load(chat_id, sender_open_id)
        normalized = source_id.strip().lower()
        for index, item in enumerate(state["registered_sources"]):
            if str(item.get("source_id", "")).lower() != normalized:
                continue
            removed = self._registered_from_item(item)
            del state["registered_sources"][index]
            self._save(chat_id, sender_open_id, state)
            return removed
        return None

    def all_active_source_paths(self) -> frozenset[Path]:
        """Return every active or registered source path protected from cleanup."""

        active: set[Path] = set()
        for path in self.state_dir.glob("*.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"无法读取汇总批次状态：{path.name}") from exc
            version = state.get("version")
            if version not in {1, 2} or not isinstance(
                state.get("active_sources"), list
            ):
                raise RuntimeError(f"汇总批次状态结构无效：{path.name}")
            for item in state["active_sources"]:
                source_path = item.get("path") if isinstance(item, dict) else None
                if not isinstance(source_path, str) or not source_path.strip():
                    raise RuntimeError(f"汇总批次状态结构无效：{path.name}")
                active.add(Path(source_path).resolve())
            registered = state.get("registered_sources", [])
            if not isinstance(registered, list):
                raise RuntimeError(f"汇总批次状态结构无效：{path.name}")
            for item in registered:
                source_path = item.get("cached_path") if isinstance(item, dict) else None
                if not isinstance(source_path, str) or not source_path.strip():
                    raise RuntimeError(f"汇总批次状态结构无效：{path.name}")
                active.add(Path(source_path).resolve())
        return frozenset(active)

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
