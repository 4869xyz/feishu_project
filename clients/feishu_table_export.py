"""Resolve Feishu Sheets and Wiki links and archive them as XLSX files."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from clients.feishu_client import (
    ExportTaskResult,
    FeishuPermissionError,
    WikiNode,
)


FEISHU_LINK_PATTERN = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
WINDOWS_RESERVED_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_SAFE_COMPONENT_LENGTH = 180
EXPORTABLE_WIKI_TYPES = frozenset({"sheet", "bitable"})


class UnsupportedFeishuTableLink(ValueError):
    """Raised when a recognized Wiki link is not an exportable sales table."""


class WikiTablePermissionError(PermissionError):
    """Raised when the bot lacks access to a Wiki node or its export."""


class TableExportClient(Protocol):
    """Feishu API methods required by the link-to-XLSX export workflow."""

    def get_wiki_node(self, wiki_node_token: str) -> WikiNode:
        """Resolve a Wiki node to its backing document."""

    def create_export_task(self, document_token: str, document_type: str) -> str:
        """Create an XLSX export task."""

    def wait_for_export_task(
        self, ticket: str, document_token: str, *, timeout_seconds: float = 90.0
    ) -> ExportTaskResult:
        """Return the completed export result."""

    def download_exported_file(
        self, file_token: str, destination: str | Path, *, max_bytes: int
    ) -> int:
        """Download a generated export file atomically."""


@dataclass(frozen=True, slots=True)
class FeishuTableLink:
    """A supported Feishu link parsed from a message body."""

    kind: str
    token: str
    url: str


@dataclass(frozen=True, slots=True)
class DownloadedTableExport:
    """A locally archived XLSX generated from a Feishu document link."""

    path: Path
    bytes_written: int
    document_type: str
    title: str
    source_file_id: str


def _value(source: object, name: str) -> Any:
    """Read a field from either an SDK object or a JSON-like mapping."""

    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _decoded_mapping(value: object) -> Mapping[str, Any] | None:
    """Decode JSON text when a Channel SDK field embeds structured content."""

    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _candidate_objects(message: object) -> list[object]:
    """Collect common normalized and raw event containers without recursion loops."""

    candidates: list[object] = []
    pending = [message]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(current)

        for field in ("message", "raw_message", "raw", "event", "data"):
            nested = _value(current, field)
            if nested is not None:
                pending.append(nested)
        for field in ("content", "raw_content", "content_json"):
            decoded = _decoded_mapping(_value(current, field))
            if decoded is not None:
                pending.append(decoded)
    return candidates


def _message_texts(message: object) -> list[str]:
    """Return text candidates from normalized or raw Channel message events."""

    texts: list[str] = []
    for candidate in _candidate_objects(message):
        for field in ("content_text", "text"):
            value = _value(candidate, field)
            if isinstance(value, str) and value.strip():
                texts.append(value)

        content = _value(candidate, "content")
        if isinstance(content, str) and content.strip() and _decoded_mapping(content) is None:
            texts.append(content)
    return texts


def extract_feishu_table_links(message: object) -> tuple[FeishuTableLink, ...]:
    """Find all distinct `/sheets/` and `/wiki/` links in message order.

    ``urlsplit`` separates query strings and fragments before extracting the
    following path segment, so neither can leak into a Feishu document token.
    The same normalized event can expose its text through several SDK fields;
    stable source-identity de-duplication prevents those mirrors (or a repeated
    URL in one command) from registering the same cloud table more than once.
    """

    links: list[FeishuTableLink] = []
    seen: set[tuple[str, str]] = set()
    for text in _message_texts(message):
        for match in FEISHU_LINK_PATTERN.finditer(text):
            url = match.group(0).rstrip(".,;:!?，。；：！？")
            parsed = urlsplit(url)
            hostname = (parsed.hostname or "").lower()
            if not (hostname == "feishu.cn" or hostname.endswith(".feishu.cn")):
                continue

            segments = [segment for segment in parsed.path.split("/") if segment]
            for index, segment in enumerate(segments[:-1]):
                kind = segment.lower()
                if kind not in {"sheets", "wiki"}:
                    continue
                token = segments[index + 1].strip()
                identity = (kind, token)
                if token and identity not in seen:
                    seen.add(identity)
                    links.append(FeishuTableLink(kind=kind, token=token, url=url))
                break
    return tuple(links)


def extract_feishu_table_link(message: object) -> FeishuTableLink | None:
    """Find the first supported Feishu table link for legacy single-link flows."""

    links = extract_feishu_table_links(message)
    return links[0] if links else None


def _message_id(message: object) -> str:
    """Extract an incoming message identifier required by the archive convention."""

    for candidate in _candidate_objects(message):
        value = _value(candidate, "message_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("The Feishu message does not include message_id")


def _sender_open_id(message: object) -> str:
    """Extract a sender open ID from common normalized and raw event shapes."""

    for candidate in _candidate_objects(message):
        for field in ("sender_open_id", "open_id"):
            value = _value(candidate, field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for field in ("sender", "sender_id"):
            sender = _value(candidate, field)
            for sender_field in ("open_id", "user_id"):
                value = _value(sender, sender_field)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    raise ValueError("The Feishu message does not include sender_open_id")


def _safe_component(value: str, *, fallback: str, max_length: int) -> str:
    """Return one Windows-safe filename component without a path separator."""

    normalized = unicodedata.normalize("NFKC", value).replace("\\", "/")
    basename = normalized.rsplit("/", maxsplit=1)[-1]
    cleaned = WINDOWS_RESERVED_FILENAME_CHARS.sub("_", basename).strip(" ._")
    return cleaned[:max_length] or fallback


def _safe_document_title(value: str) -> str:
    """Make a document title safe for the fixed `.xlsx` archive extension."""

    title = value.strip()
    for suffix in (".xlsx", ".xlsm", ".xls"):
        if title.lower().endswith(suffix):
            title = title[: -len(suffix)]
            break
    return _safe_component(title, fallback="飞书表格", max_length=MAX_SAFE_COMPONENT_LENGTH)


class FeishuTableLinkExporter:
    """Convert supported Feishu table links into date-and-sender archives."""

    def __init__(
        self,
        client: TableExportClient,
        archive_dir: str | Path,
        *,
        max_bytes: int,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self.client = client
        self.archive_dir = Path(archive_dir).resolve()
        self.max_bytes = max_bytes
        self._now = now

    def _prepare_export(
        self, link: FeishuTableLink
    ) -> tuple[str, str, str, ExportTaskResult]:
        """Resolve one link and wait until its XLSX export is ready."""

        document_type = "sheet"
        document_token = link.token
        wiki_title = ""
        if link.kind == "wiki":
            try:
                node = self.client.get_wiki_node(link.token)
            except FeishuPermissionError as exc:
                raise WikiTablePermissionError from exc
            if node.obj_type not in EXPORTABLE_WIKI_TYPES:
                raise UnsupportedFeishuTableLink(node.obj_type)
            document_type = node.obj_type
            document_token = node.obj_token
            wiki_title = node.title

        try:
            ticket = self.client.create_export_task(document_token, document_type)
            task_result = self.client.wait_for_export_task(ticket, document_token)
        except FeishuPermissionError as exc:
            if link.kind == "wiki":
                raise WikiTablePermissionError from exc
            raise

        title = _safe_document_title(wiki_title or task_result.file_name)
        return document_type, document_token, title, task_result

    def _download_ready_export(
        self,
        link: FeishuTableLink,
        task_result: ExportTaskResult,
        destination: Path,
    ) -> int:
        """Download one ready export with the existing Wiki permission mapping."""

        try:
            return self.client.download_exported_file(
                task_result.file_token,
                destination,
                max_bytes=self.max_bytes,
            )
        except FeishuPermissionError as exc:
            if link.kind == "wiki":
                raise WikiTablePermissionError from exc
            raise

    def export_link_to_path(
        self,
        link: FeishuTableLink,
        destination: str | Path,
        *,
        source_file_id: str,
    ) -> DownloadedTableExport:
        """Export a previously parsed link to a caller-owned cache path."""

        if not source_file_id.strip():
            raise ValueError("source_file_id must not be empty")
        document_type, _, title, task_result = self._prepare_export(link)
        path = Path(destination).resolve()
        bytes_written = self._download_ready_export(link, task_result, path)
        return DownloadedTableExport(
            path=path,
            bytes_written=bytes_written,
            document_type=document_type,
            title=title,
            source_file_id=source_file_id.strip(),
        )

    def export_from_message(self, message: object) -> DownloadedTableExport | None:
        """Export the first supported text link, or return None for other messages."""

        link = extract_feishu_table_link(message)
        if link is None:
            return None

        document_type, document_token, title, task_result = self._prepare_export(link)
        timestamp = self._now()
        sender = _safe_component(
            _sender_open_id(message), fallback="unknown_sender", max_length=80
        )
        message_suffix = _safe_component(
            _message_id(message)[-8:], fallback="message", max_length=24
        )
        destination = (
            self.archive_dir
            / timestamp.strftime("%Y-%m")
            / sender
            / f"SUB-{timestamp:%Y%m%d-%H%M%S}-{message_suffix}_{title}.xlsx"
        )
        bytes_written = self._download_ready_export(link, task_result, destination)

        return DownloadedTableExport(
            path=destination,
            bytes_written=bytes_written,
            document_type=document_type,
            title=title,
            source_file_id=f"{_message_id(message)}:{document_type}:{document_token}",
        )


def message_sender_open_id(message: object) -> str:
    """Return the sender identity used to isolate aggregation batches."""

    return _sender_open_id(message)


def message_texts(message: object) -> list[str]:
    """Return normalized text candidates for listener command recognition."""

    return _message_texts(message)
