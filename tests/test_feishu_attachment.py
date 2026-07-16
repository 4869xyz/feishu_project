"""Tests for direct Excel attachment parsing and local inbox handling."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clients.feishu_attachment import (
    ExcelAttachmentDownloader,
    UnsupportedExcelAttachment,
    extract_file_message,
)


class StubResourceClient:
    """In-memory downloader that records its requested Feishu resource."""

    def __init__(self, content: bytes = b"excel-bytes") -> None:
        self.content = content
        self.calls: list[tuple[str, str, Path, int]] = []

    def download_message_resource(
        self,
        message_id: str,
        file_key: str,
        destination: str | Path,
        *,
        max_bytes: int,
    ) -> int:
        path = Path(destination)
        self.calls.append((message_id, file_key, path, max_bytes))
        path.write_bytes(self.content)
        return len(self.content)


def _file_message(file_name: str = "销售数据.xlsx") -> SimpleNamespace:
    """Build a representative normalized Channel SDK file-message object."""

    return SimpleNamespace(
        message_id="om_message_123",
        message_type="file",
        content=json.dumps({"file_key": "file_abc", "file_name": file_name}),
    )


def test_extract_file_message_reads_json_content() -> None:
    """A direct file event exposes the ID, key, and original file name."""

    attachment = extract_file_message(_file_message())

    assert attachment is not None
    assert attachment.message_id == "om_message_123"
    assert attachment.file_key == "file_abc"
    assert attachment.file_name == "销售数据.xlsx"


def test_extract_file_message_ignores_text_message() -> None:
    """Ordinary messages do not trigger a resource download."""

    message = SimpleNamespace(
        message_id="om_text",
        message_type="text",
        content='{"text":"hello"}',
    )

    assert extract_file_message(message) is None


def test_downloads_excel_into_safe_message_specific_name(project_tmp_dir: Path) -> None:
    """Incoming Excel files are stored under the configured inbox without traversal."""

    client = StubResourceClient()
    downloader = ExcelAttachmentDownloader(
        client,
        project_tmp_dir / "data" / "inbox",
        max_bytes=1024,
    )

    result = downloader.download_from_message(_file_message("..\\财务汇总.xlsx"))

    assert result is not None
    assert result.path.parent == (project_tmp_dir / "data" / "inbox").resolve()
    assert result.path.name == "om_message_123__财务汇总.xlsx"
    assert result.path.read_bytes() == b"excel-bytes"
    assert result.already_present is False
    assert result.source_file_id == "om_message_123:file_abc"
    assert client.calls == [("om_message_123", "file_abc", result.path, 1024)]


def test_repeated_message_does_not_download_twice(project_tmp_dir: Path) -> None:
    """A redelivered Feishu event reuses the already complete local file."""

    client = StubResourceClient()
    downloader = ExcelAttachmentDownloader(client, project_tmp_dir / "inbox", max_bytes=1024)

    first = downloader.download_from_message(_file_message())
    second = downloader.download_from_message(_file_message())

    assert first is not None and second is not None
    assert first.already_present is False
    assert second.already_present is True
    assert len(client.calls) == 1


@pytest.mark.parametrize("file_name", ["销售数据.csv", "无扩展名", ".."])
def test_non_excel_attachments_are_rejected(
    project_tmp_dir: Path,
    file_name: str,
) -> None:
    """Only the explicit Excel extension allowlist reaches the download client."""

    client = StubResourceClient()
    downloader = ExcelAttachmentDownloader(client, project_tmp_dir / "inbox", max_bytes=1024)

    with pytest.raises(UnsupportedExcelAttachment):
        downloader.download_from_message(_file_message(file_name))

    assert client.calls == []
