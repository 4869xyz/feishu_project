"""Tests for Feishu Sheets/Wiki link recognition and archive naming."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from clients.feishu_client import ExportTaskResult, FeishuPermissionError, WikiNode
from clients.feishu_table_export import (
    FeishuTableLink,
    FeishuTableLinkExporter,
    UnsupportedFeishuTableLink,
    WikiTablePermissionError,
    extract_feishu_table_link,
)


class StubTableClient:
    """In-memory export client that records the workflow requested by the exporter."""

    def __init__(self, *, node: WikiNode | None = None) -> None:
        self.node = node
        self.calls: list[tuple[object, ...]] = []
        self.task_result = ExportTaskResult(
            file_name="导出任务文件.xlsx", file_token="file_exported"
        )
        self.node_error: Exception | None = None
        self.export_error: Exception | None = None

    def get_wiki_node(self, wiki_node_token: str) -> WikiNode:
        self.calls.append(("node", wiki_node_token))
        if self.node_error is not None:
            raise self.node_error
        assert self.node is not None
        return self.node

    def create_export_task(self, document_token: str, document_type: str) -> str:
        self.calls.append(("create", document_token, document_type))
        if self.export_error is not None:
            raise self.export_error
        return "ticket_123"

    def wait_for_export_task(
        self, ticket: str, document_token: str, *, timeout_seconds: float = 90.0
    ) -> ExportTaskResult:
        self.calls.append(("wait", ticket, document_token))
        return self.task_result

    def download_exported_file(
        self, file_token: str, destination: str | Path, *, max_bytes: int
    ) -> int:
        path = Path(destination)
        self.calls.append(("download", file_token, path, max_bytes))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"xlsx-bytes")
        return len(b"xlsx-bytes")


def _message(link: str) -> SimpleNamespace:
    """Build a representative normalized text event from the Channel SDK."""

    return SimpleNamespace(
        message_id="om_abcdefgh",
        sender_open_id="ou_sender_123",
        message_type="text",
        content=json.dumps({"text": link}),
    )


@pytest.mark.parametrize(
    ("url", "kind", "token"),
    [
        (
            "https://example.feishu.cn/sheets/sht_sales_001?sheet=abc#ignored",
            "sheets",
            "sht_sales_001",
        ),
        (
            "https://example.feishu.cn/wiki/wiki_sales_001?from=chat#section",
            "wiki",
            "wiki_sales_001",
        ),
    ],
)
def test_extract_feishu_table_link_excludes_query_and_fragment(
    url: str, kind: str, token: str
) -> None:
    """Only the path token is kept for both supported URL forms."""

    result = extract_feishu_table_link(_message(f"请导出：{url}"))

    assert result is not None
    assert result.kind == kind
    assert result.token == token


def test_sheet_link_exports_using_its_own_token_and_archive_convention(
    project_tmp_dir: Path,
) -> None:
    """A Sheets URL directly creates a sheet XLSX export and sender archive."""

    client = StubTableClient()
    exporter = FeishuTableLinkExporter(
        client,
        project_tmp_dir / "data" / "archive",
        max_bytes=1024,
        now=lambda: datetime(2026, 7, 14, 9, 8, 7),
    )

    result = exporter.export_from_message(
        _message("https://example.feishu.cn/sheets/sht_sales_001?sheet=abc")
    )

    assert result is not None
    assert result.document_type == "sheet"
    assert result.source_file_id == "om_abcdefgh:sheet:sht_sales_001"
    assert result.path == (
        project_tmp_dir
        / "data"
        / "archive"
        / "2026-07"
        / "ou_sender_123"
        / "SUB-20260714-090807-abcdefgh_导出任务文件.xlsx"
    ).resolve()
    assert result.path.read_bytes() == b"xlsx-bytes"
    assert client.calls[0] == ("create", "sht_sales_001", "sheet")


def test_parsed_link_can_refresh_a_caller_owned_latest_path(
    project_tmp_dir: Path,
) -> None:
    """Registered sources reuse export logic without message-specific archives."""

    client = StubTableClient()
    exporter = FeishuTableLinkExporter(
        client,
        project_tmp_dir / "archive",
        max_bytes=1024,
    )
    destination = project_tmp_dir / "registered" / "latest.xlsx"

    result = exporter.export_link_to_path(
        FeishuTableLink(
            kind="sheets",
            token="sht_registered",
            url="https://example.feishu.cn/sheets/sht_registered",
        ),
        destination,
        source_file_id="cloud-stable",
    )

    assert result.path == destination.resolve()
    assert result.source_file_id == "cloud-stable"
    assert result.title == "导出任务文件"
    assert destination.read_bytes() == b"xlsx-bytes"
    assert client.calls[0] == ("create", "sht_registered", "sheet")


@pytest.mark.parametrize("obj_type", ["sheet", "bitable"])
def test_wiki_link_resolves_backing_object_before_export(
    project_tmp_dir: Path, obj_type: str
) -> None:
    """A Wiki token is never passed to export; its resolved object is used instead."""

    client = StubTableClient(
        node=WikiNode(
            obj_type=obj_type,
            obj_token=f"{obj_type}_real_token",
            title="七月销售总表",
        )
    )
    client.task_result = ExportTaskResult(
        file_name="不应采用的导出名称.xlsx", file_token="file_exported"
    )
    exporter = FeishuTableLinkExporter(
        client,
        project_tmp_dir / "data" / "archive",
        max_bytes=1024,
        now=lambda: datetime(2026, 7, 14, 10, 11, 12),
    )

    result = exporter.export_from_message(
        _message("https://example.feishu.cn/wiki/wiki_sales_001#link")
    )

    assert result is not None
    assert result.document_type == obj_type
    assert result.title == "七月销售总表"
    assert result.path.name == "SUB-20260714-101112-abcdefgh_七月销售总表.xlsx"
    assert client.calls[:3] == [
        ("node", "wiki_sales_001"),
        ("create", f"{obj_type}_real_token", obj_type),
        ("wait", "ticket_123", f"{obj_type}_real_token"),
    ]


def test_non_exportable_wiki_type_is_not_sent_to_export(project_tmp_dir: Path) -> None:
    """Wiki documents such as Docs are rejected before an export task is created."""

    client = StubTableClient(
        node=WikiNode(obj_type="docx", obj_token="doc_real_token", title="说明")
    )
    exporter = FeishuTableLinkExporter(client, project_tmp_dir / "archive", max_bytes=1024)

    with pytest.raises(UnsupportedFeishuTableLink):
        exporter.export_from_message(_message("https://example.feishu.cn/wiki/wiki_doc_001"))

    assert client.calls == [("node", "wiki_doc_001")]


@pytest.mark.parametrize("stage", ["node", "export"])
def test_wiki_permission_errors_become_a_dedicated_user_facing_error(
    project_tmp_dir: Path, stage: str
) -> None:
    """Both Wiki resolution and export permission errors use the same reply path."""

    client = StubTableClient(
        node=WikiNode(obj_type="sheet", obj_token="sht_real_token", title="销售")
    )
    if stage == "node":
        client.node_error = FeishuPermissionError("denied")
    else:
        client.export_error = FeishuPermissionError("denied")
    exporter = FeishuTableLinkExporter(client, project_tmp_dir / "archive", max_bytes=1024)

    with pytest.raises(WikiTablePermissionError):
        exporter.export_from_message(_message("https://example.feishu.cn/wiki/wiki_sales_001"))
