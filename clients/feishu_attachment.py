"""Parse and safely persist Excel attachments received by the Feishu bot."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


SUPPORTED_EXCEL_SUFFIXES = frozenset({".xlsx", ".xls", ".xlsm"})
WINDOWS_RESERVED_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_SAFE_FILENAME_LENGTH = 180


class UnsupportedExcelAttachment(ValueError):
    """Raised when a file message is not one of the supported Excel formats."""


class MessageResourceDownloader(Protocol):
    """Minimal interface needed to download a Feishu message resource."""

    def download_message_resource(
        self,
        message_id: str,
        file_key: str,
        destination: str | Path,
        *,
        max_bytes: int,
    ) -> int:
        """Write a message resource to ``destination`` and return its size."""


@dataclass(frozen=True, slots=True)
class FileMessage:
    """The resource metadata required by Feishu's message download endpoint."""

    message_id: str
    file_key: str
    file_name: str


@dataclass(frozen=True, slots=True)
class DownloadedAttachment:
    """Result of accepting an Excel attachment for the local inbox."""

    path: Path
    bytes_written: int
    already_present: bool


def _value(source: object, name: str) -> Any:
    """Read one field from either an SDK object or a JSON-like mapping."""

    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _mapping_from_json(value: object) -> Mapping[str, Any] | None:
    """Return a decoded mapping when an SDK field contains JSON text."""

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
    """Collect common normalized and raw message containers from Channel SDKs."""

    candidates: list[object] = []
    pending = [message]
    seen: set[int] = set()
    nested_fields = ("message", "raw_message", "raw", "event", "data")
    content_fields = ("content", "raw_content", "content_json")

    while pending:
        current = pending.pop(0)
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(current)

        for field in nested_fields:
            nested = _value(current, field)
            if nested is not None:
                pending.append(nested)
        for field in content_fields:
            decoded = _mapping_from_json(_value(current, field))
            if decoded is not None:
                pending.append(decoded)
    return candidates


def extract_file_message(message: object) -> FileMessage | None:
    """Extract a direct file-message payload from a normalized SDK message.

    The Channel SDK normalizes messages, while the original Feishu event keeps
    file metadata inside ``message.content`` JSON.  Supporting both forms lets
    the listener remain compatible with either representation.
    """

    message_id = _value(message, "message_id")
    if not isinstance(message_id, str) or not message_id.strip():
        for candidate in _candidate_objects(message):
            candidate_id = _value(candidate, "message_id")
            if isinstance(candidate_id, str) and candidate_id.strip():
                message_id = candidate_id
                break
    if not isinstance(message_id, str) or not message_id.strip():
        return None

    for candidate in _candidate_objects(message):
        message_type = _value(candidate, "message_type") or _value(
            candidate, "msg_type"
        )
        file_key = _value(candidate, "file_key")
        file_name = _value(candidate, "file_name")
        if not isinstance(file_key, str) or not isinstance(file_name, str):
            continue
        if message_type is not None and str(message_type).lower() != "file":
            continue
        if file_key.strip() and file_name.strip():
            return FileMessage(
                message_id=message_id.strip(),
                file_key=file_key.strip(),
                file_name=file_name.strip(),
            )
    return None


def _safe_filename(file_name: str) -> str:
    """Make a user-provided filename safe for a Windows local inbox."""

    normalized = unicodedata.normalize("NFKC", file_name).replace("\\", "/")
    basename = Path(normalized).name.strip().strip(".")
    basename = WINDOWS_RESERVED_FILENAME_CHARS.sub("_", basename).strip()
    if not basename or basename in {".", ".."}:
        raise UnsupportedExcelAttachment("附件文件名无效")

    suffix = Path(basename).suffix.lower()
    if suffix not in SUPPORTED_EXCEL_SUFFIXES:
        allowed = "、".join(sorted(SUPPORTED_EXCEL_SUFFIXES))
        raise UnsupportedExcelAttachment(f"仅支持 Excel 附件：{allowed}")

    stem = Path(basename).stem.strip().rstrip(".") or "excel_attachment"
    max_stem_length = MAX_SAFE_FILENAME_LENGTH - len(suffix)
    return f"{stem[:max_stem_length]}{suffix}"


def _safe_message_id(message_id: str) -> str:
    """Use the message ID as a collision-resistant but filesystem-safe prefix."""

    normalized = WINDOWS_RESERVED_FILENAME_CHARS.sub("_", message_id).strip(" ._")
    return normalized[:80] or "message"


class ExcelAttachmentDownloader:
    """Accept direct Excel file messages into a local inbox without overwrites."""

    def __init__(
        self,
        client: MessageResourceDownloader,
        inbox_dir: str | Path,
        *,
        max_bytes: int,
    ) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes 必须是正整数")
        self.client = client
        self.inbox_dir = Path(inbox_dir).resolve()
        self.max_bytes = max_bytes

    def download_from_message(self, message: object) -> DownloadedAttachment | None:
        """Download a supported Excel attachment, or return ``None`` for text."""

        file_message = extract_file_message(message)
        if file_message is None:
            return None

        safe_name = _safe_filename(file_message.file_name)
        destination = self.inbox_dir / (
            f"{_safe_message_id(file_message.message_id)}__{safe_name}"
        )
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

        if destination.is_file() and destination.stat().st_size > 0:
            return DownloadedAttachment(
                path=destination,
                bytes_written=destination.stat().st_size,
                already_present=True,
            )

        bytes_written = self.client.download_message_resource(
            file_message.message_id,
            file_message.file_key,
            destination,
            max_bytes=self.max_bytes,
        )
        return DownloadedAttachment(
            path=destination,
            bytes_written=bytes_written,
            already_present=False,
        )
