"""Tests for listener-level replies around table-link export outcomes."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from clients.feishu_attachment import DownloadedAttachment
from clients.feishu_client import FeishuClientError
from clients.feishu_table_export import (
    DownloadedTableExport,
    FeishuTableLink,
    WikiTablePermissionError,
)
from feishu_bot_listener import (
    CredentialSafeFormatter,
    SingleInstanceError,
    WIKI_PERMISSION_REPLY,
    _configure_logging,
    _create_channel,
    _single_instance_lock,
    handle_message,
)
from services.aggregation_batch_store import AggregationBatchStore
from services.download_cache import CacheCleanupResult
from services.sales_workbook_aggregator import (
    AggregationResult,
    SourceValidationResult,
    SourceWorkbook,
)


class StubAttachmentDownloader:
    """Return no attachment so the listener reaches the link exporter."""

    def download_from_message(self, message: object) -> None:
        return None


class PermissionDeniedExporter:
    """Make the Wiki export branch deterministic without an HTTP client."""

    def export_from_message(self, message: object) -> None:
        raise WikiTablePermissionError


class RefreshingTableExporter:
    """Write deterministic latest files or raise a configured refresh error."""

    def __init__(self, *, payload: bytes = b"fresh-xlsx") -> None:
        self.payload = payload
        self.error: Exception | None = None
        self.errors_by_token: dict[str, Exception] = {}
        self.titles_by_token: dict[str, str] = {}
        self.calls: list[tuple[FeishuTableLink, Path, str]] = []

    def export_from_message(self, message: object) -> None:
        return None

    def export_link_to_path(
        self,
        link: FeishuTableLink,
        destination: str | Path,
        *,
        source_file_id: str,
    ) -> DownloadedTableExport:
        path = Path(destination)
        self.calls.append((link, path, source_file_id))
        error = self.errors_by_token.get(link.token, self.error)
        if error is not None:
            raise error
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.payload)
        return DownloadedTableExport(
            path=path,
            bytes_written=len(self.payload),
            document_type="sheet",
            title=self.titles_by_token.get(link.token, "固定销售表"),
            source_file_id=source_file_id,
        )


class ReturningAttachmentDownloader:
    """Return one deterministic downloaded XLSX."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def download_from_message(self, message: object) -> DownloadedAttachment:
        return DownloadedAttachment(
            path=self.path,
            bytes_written=self.path.stat().st_size,
            already_present=False,
            source_file_id="message:file",
        )


class RecordingChannel:
    """Capture replies sent by the listener."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], dict[str, str]]] = []

    async def send(
        self, chat_id: str, content: dict[str, str], options: dict[str, str]
    ) -> None:
        self.calls.append((chat_id, content, options))


class RecordingCacheCleaner:
    """Record cleanup input and return a deterministic successful result."""

    def __init__(self) -> None:
        self.active_source_paths: frozenset[Path] | None = None

    def clear(self, *, active_source_paths) -> CacheCleanupResult:
        self.active_source_paths = frozenset(Path(path) for path in active_source_paths)
        return CacheCleanupResult(
            deleted_files=3,
            deleted_bytes=1536,
            preserved_active_files=len(self.active_source_paths),
            failed_files=0,
        )


def test_wiki_permission_reply_is_explicit() -> None:
    """Users receive the prescribed two-layer Wiki permission guidance."""

    channel = RecordingChannel()
    message = SimpleNamespace(chat_id="oc_chat", message_id="om_message")

    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            PermissionDeniedExporter(),
            message,
            asyncio.Lock(),
        )
    )

    assert channel.calls == [
        (
            "oc_chat",
            {"text": WIKI_PERMISSION_REPLY},
            {"reply_to": "om_message"},
        )
    ]


def test_channel_policy_requires_direct_bot_mention_in_groups() -> None:
    """Group messages are admitted only when they mention this bot directly."""

    settings = SimpleNamespace(app_id="cli_test", app_secret="test-secret")

    policy = _create_channel(settings).get_policy()

    assert policy.dm_policy == "open"
    assert policy.group_policy == "open"
    assert policy.require_mention is True
    assert policy.respond_to_mention_all is False


def test_credential_safe_formatter_redacts_websocket_credentials() -> None:
    """Connection URLs never expose temporary access keys or tickets."""

    formatter = CredentialSafeFormatter("%(message)s")
    record = logging.LogRecord(
        "Lark",
        logging.INFO,
        __file__,
        1,
        "connected to wss://example/ws?access_key=secret-key&ticket=secret-ticket&aid=1",
        (),
        None,
    )

    formatted = formatter.format(record)

    assert "secret-key" not in formatted
    assert "secret-ticket" not in formatted
    assert "access_key=***" in formatted
    assert "ticket=***" in formatted


def test_logging_uses_one_root_pipeline_for_lark(project_tmp_dir) -> None:
    """The SDK logger has no duplicate handler and suppresses URL-bearing INFO."""

    root_logger = logging.getLogger()
    lark_logger = logging.getLogger("Lark")
    httpx_logger = logging.getLogger("httpx")
    old_root_handlers = list(root_logger.handlers)
    old_root_level = root_logger.level
    old_lark_handlers = list(lark_logger.handlers)
    old_lark_level = lark_logger.level
    old_lark_propagate = lark_logger.propagate
    old_httpx_level = httpx_logger.level
    settings = SimpleNamespace(log_level="DEBUG", log_dir=project_tmp_dir)

    try:
        _configure_logging(settings)

        assert lark_logger.handlers == []
        assert lark_logger.propagate is True
        assert lark_logger.level == logging.WARNING
        assert httpx_logger.level == logging.WARNING
    finally:
        configured_handlers = list(root_logger.handlers)
        root_logger.handlers = old_root_handlers
        root_logger.setLevel(old_root_level)
        for handler in configured_handlers:
            handler.close()
        lark_logger.handlers = old_lark_handlers
        lark_logger.setLevel(old_lark_level)
        lark_logger.propagate = old_lark_propagate
        httpx_logger.setLevel(old_httpx_level)


def test_single_instance_lock_rejects_second_owner(project_tmp_dir) -> None:
    """A second listener cannot start while the first holds the lock."""

    lock_path = project_tmp_dir / "listener.lock"

    with _single_instance_lock(lock_path):
        with pytest.raises(SingleInstanceError, match="已经在运行"):
            with _single_instance_lock(lock_path):
                pytest.fail("second listener unexpectedly acquired the lock")

    with _single_instance_lock(lock_path):
        pass


def test_aggregation_command_returns_xlsx_and_clears_batch(
    project_tmp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The explicit command aggregates the staged order and replies with a file."""

    channel = RecordingChannel()
    message = SimpleNamespace(
        chat_id="oc_chat",
        message_id="om_command",
        sender_open_id="ou_sender",
        content_text="汇总",
    )
    source_path = project_tmp_dir / "source.xlsx"
    source_path.write_bytes(b"source")
    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    store.add_source(
        "oc_chat",
        "ou_sender",
        SourceWorkbook("source-id", source_path),
        display_name="销售.xlsx",
    )

    def fake_aggregate(sources, template_path, output_path):
        Path(output_path).write_bytes(b"xlsx-result")
        return AggregationResult(
            output_path=Path(output_path),
            source_count=len(sources),
            signing_detail_count=2,
            signing_total=Decimal(100),
        )

    monkeypatch.setattr("feishu_bot_listener.aggregate_sales_workbooks", fake_aggregate)
    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            PermissionDeniedExporter(),
            message,
            asyncio.Lock(),
            batch_store=store,
            sales_template_path=project_tmp_dir / "template.xlsx",
        )
    )

    assert "file" in channel.calls[0][1]
    assert channel.calls[0][1]["file"]["source"].endswith(".xlsx")
    assert "汇总完成" in channel.calls[1][1]["text"]
    assert "回款" not in channel.calls[1][1]["text"]
    assert store.list_sources("oc_chat", "ou_sender") == ()


def test_registered_cloud_source_command_lifecycle(
    project_tmp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sender can add, list, and remove a validated persistent source."""

    channel = RecordingChannel()
    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    exporter = RefreshingTableExporter()
    monkeypatch.setattr(
        "feishu_bot_listener.validate_source_workbook",
        lambda source: SourceValidationResult("签约情况", 4),
    )
    add_message = SimpleNamespace(
        chat_id="oc_chat",
        message_id="om_add",
        sender_open_id="ou_sender",
        content_text=(
            "添加云表 https://example.feishu.cn/sheets/sht_registered?sheet=abc"
        ),
    )

    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            exporter,
            add_message,
            asyncio.Lock(),
            batch_store=store,
            sales_template_path=project_tmp_dir / "template.xlsx",
        )
    )

    registered = store.list_registered_sources("oc_chat", "ou_sender")
    assert len(registered) == 1
    assert registered[0].display_name == "固定销售表"
    assert registered[0].cached_path.read_bytes() == b"fresh-xlsx"
    add_reply = channel.calls[-1][1]["text"]
    assert "固定云表已登记" in add_reply
    assert "编号 1" in add_reply
    assert registered[0].source_id not in add_reply

    list_message = SimpleNamespace(
        chat_id="oc_chat",
        message_id="om_list",
        sender_open_id="ou_sender",
        content_text="云表列表",
    )
    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            exporter,
            list_message,
            asyncio.Lock(),
            batch_store=store,
            sales_template_path=project_tmp_dir / "template.xlsx",
        )
    )
    list_reply = channel.calls[-1][1]["text"]
    assert "1. 固定销售表" in list_reply
    assert registered[0].source_id not in list_reply

    remove_message = SimpleNamespace(
        chat_id="oc_chat",
        message_id="om_remove",
        sender_open_id="ou_sender",
        content_text="移除云表 1",
    )
    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            exporter,
            remove_message,
            asyncio.Lock(),
            batch_store=store,
            sales_template_path=project_tmp_dir / "template.xlsx",
        )
    )
    assert store.list_registered_sources("oc_chat", "ou_sender") == ()
    assert registered[0].cached_path.exists() is False
    assert "已移除固定云表" in channel.calls[-1][1]["text"]


def test_add_registered_cloud_sources_accepts_multiple_links_in_one_command(
    project_tmp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed Sheets/Wiki links are registered once in their message order."""

    channel = RecordingChannel()
    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    exporter = RefreshingTableExporter()
    exporter.titles_by_token = {
        "sht_first": "第一份销售表",
        "wik_second": "第二份销售表",
        "sht_third": "第三份销售表",
    }
    monkeypatch.setattr(
        "feishu_bot_listener.validate_source_workbook",
        lambda source: SourceValidationResult("签约情况", 4),
    )
    message = SimpleNamespace(
        chat_id="oc_chat",
        message_id="om_batch_add",
        sender_open_id="ou_sender",
        content_text=(
            '<at user_id="ou_bot">机器人</at> 添加云表\n'
            "https://example.feishu.cn/sheets/sht_first\n"
            "https://example.feishu.cn/wiki/wik_second "
            "https://example.feishu.cn/sheets/sht_third"
        ),
    )

    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            exporter,
            message,
            asyncio.Lock(),
            batch_store=store,
            sales_template_path=project_tmp_dir / "template.xlsx",
        )
    )

    registered = store.list_registered_sources("oc_chat", "ou_sender")
    assert [source.token for source in registered] == [
        "sht_first",
        "wik_second",
        "sht_third",
    ]
    assert [source.display_name for source in registered] == [
        "第一份销售表",
        "第二份销售表",
        "第三份销售表",
    ]
    reply = channel.calls[-1][1]["text"]
    assert "成功 3 份，已存在 0 份，失败 0 份；当前共 3 份" in reply
    assert "1. 第一份销售表" in reply
    assert "2. 第二份销售表" in reply
    assert "3. 第三份销售表" in reply


def test_batch_add_skips_existing_source_and_continues_after_one_failure(
    project_tmp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One duplicate or failed export does not roll back other new sources."""

    channel = RecordingChannel()
    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    existing_cache = store.registered_cache_path(
        "oc_chat", "ou_sender", "sheets", "sht_existing"
    )
    existing_cache.parent.mkdir(parents=True, exist_ok=True)
    existing_cache.write_bytes(b"old")
    store.add_registered_source(
        "oc_chat",
        "ou_sender",
        kind="sheets",
        token="sht_existing",
        url="https://example.feishu.cn/sheets/sht_existing",
        display_name="已登记销售表",
        cached_path=existing_cache,
        refreshed_at="2026-07-20T10:00:00",
    )
    exporter = RefreshingTableExporter()
    exporter.errors_by_token["sht_failed"] = FeishuClientError("secret token")
    exporter.titles_by_token["sht_new"] = "新增销售表"
    monkeypatch.setattr(
        "feishu_bot_listener.validate_source_workbook",
        lambda source: SourceValidationResult("签约情况", 4),
    )
    message = SimpleNamespace(
        chat_id="oc_chat",
        message_id="om_partial_batch_add",
        sender_open_id="ou_sender",
        content_text=(
            "添加云表 "
            "https://example.feishu.cn/sheets/sht_existing "
            "https://example.feishu.cn/sheets/sht_failed "
            "https://example.feishu.cn/sheets/sht_new"
        ),
    )

    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            exporter,
            message,
            asyncio.Lock(),
            batch_store=store,
            sales_template_path=project_tmp_dir / "template.xlsx",
        )
    )

    registered = store.list_registered_sources("oc_chat", "ou_sender")
    assert [source.token for source in registered] == ["sht_existing", "sht_new"]
    reply = channel.calls[-1][1]["text"]
    assert "成功 1 份，已存在 1 份，失败 1 份；当前共 2 份" in reply
    assert "1. 已登记销售表" in reply
    assert "2. 新增销售表" in reply
    assert "第 2 个链接：飞书导出或下载失败，请稍后重试" in reply
    assert "secret token" not in reply


def test_registered_cloud_sources_reorder_validate_and_clear(
    project_tmp_dir: Path,
) -> None:
    """Numbers represent current order and clear leaves temporary files untouched."""

    channel = RecordingChannel()
    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    caches: list[Path] = []
    for index in range(1, 4):
        token = f"sht_{index}"
        cache_path = store.registered_cache_path(
            "oc_chat", "ou_sender", "sheets", token
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(f"cache-{index}".encode("ascii"))
        caches.append(cache_path)
        store.add_registered_source(
            "oc_chat",
            "ou_sender",
            kind="sheets",
            token=token,
            url=f"https://example.feishu.cn/sheets/{token}",
            display_name=f"{index}号表",
            cached_path=cache_path,
            refreshed_at=f"2026-07-20T10:0{index}:00",
        )
    temporary_path = project_tmp_dir / "temporary.xlsx"
    temporary_path.write_bytes(b"temporary")
    store.add_source(
        "oc_chat",
        "ou_sender",
        SourceWorkbook("temporary-id", temporary_path),
        display_name="临时.xlsx",
    )

    def send(command: str, message_id: str) -> None:
        asyncio.run(
            handle_message(
                channel,
                StubAttachmentDownloader(),
                PermissionDeniedExporter(),
                SimpleNamespace(
                    chat_id="oc_chat",
                    message_id=message_id,
                    sender_open_id="ou_sender",
                    content_text=command,
                ),
                asyncio.Lock(),
                batch_store=store,
                sales_template_path=project_tmp_dir / "template.xlsx",
            )
        )

    send("云表排序 3 3 1", "om_invalid_order")
    assert "云表排序未执行" in channel.calls[-1][1]["text"]
    assert [
        item.display_name
        for item in store.list_registered_sources("oc_chat", "ou_sender")
    ] == ["1号表", "2号表", "3号表"]

    send("云表排序 3 1 2", "om_order")
    assert "云表顺序已更新" in channel.calls[-1][1]["text"]
    assert [
        item.display_name
        for item in store.list_registered_sources("oc_chat", "ou_sender")
    ] == ["3号表", "1号表", "2号表"]
    assert "1. 3号表" in channel.calls[-1][1]["text"]

    send("清空云表", "om_clear")
    assert channel.calls[-1][1]["text"] == "已清空 3 份固定云表。"
    assert store.list_registered_sources("oc_chat", "ou_sender") == ()
    assert len(store.list_sources("oc_chat", "ou_sender")) == 1
    assert all(not cache.exists() for cache in caches)

    send("清空云表", "om_clear_again")
    assert channel.calls[-1][1]["text"] == "当前没有固定云表。"


def test_remove_registered_cloud_source_accepts_legacy_internal_id(
    project_tmp_dir: Path,
) -> None:
    """Previously documented cloud IDs remain accepted during transition."""

    channel = RecordingChannel()
    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    cache_path = store.registered_cache_path(
        "oc_chat", "ou_sender", "sheets", "sht_legacy"
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"cache")
    registered = store.add_registered_source(
        "oc_chat",
        "ou_sender",
        kind="sheets",
        token="sht_legacy",
        url="https://example.feishu.cn/sheets/sht_legacy",
        display_name="旧编号云表",
        cached_path=cache_path,
        refreshed_at="2026-07-20T10:00:00",
    ).source

    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            PermissionDeniedExporter(),
            SimpleNamespace(
                chat_id="oc_chat",
                message_id="om_remove_legacy",
                sender_open_id="ou_sender",
                content_text=f"移除云表 {registered.source_id}",
            ),
            asyncio.Lock(),
            batch_store=store,
            sales_template_path=project_tmp_dir / "template.xlsx",
        )
    )

    assert store.list_registered_sources("oc_chat", "ou_sender") == ()
    assert "已移除固定云表" in channel.calls[-1][1]["text"]


def test_clear_registered_cloud_sources_reports_cache_delete_failure(
    project_tmp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed local unlink does not restore a cleared cloud registration."""

    channel = RecordingChannel()
    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    cache_path = store.registered_cache_path(
        "oc_chat", "ou_sender", "sheets", "sht_locked"
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"cache")
    store.add_registered_source(
        "oc_chat",
        "ou_sender",
        kind="sheets",
        token="sht_locked",
        url="https://example.feishu.cn/sheets/sht_locked",
        display_name="缓存被占用的云表",
        cached_path=cache_path,
        refreshed_at="2026-07-20T10:00:00",
    )
    original_unlink = Path.unlink

    def fail_latest_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.resolve() == cache_path.resolve():
            raise PermissionError("cache is in use")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_latest_unlink)
    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            PermissionDeniedExporter(),
            SimpleNamespace(
                chat_id="oc_chat",
                message_id="om_clear_locked",
                sender_open_id="ou_sender",
                content_text="清空云表",
            ),
            asyncio.Lock(),
            batch_store=store,
            sales_template_path=project_tmp_dir / "template.xlsx",
        )
    )

    assert store.list_registered_sources("oc_chat", "ou_sender") == ()
    assert cache_path.exists()
    reply = channel.calls[-1][1]["text"]
    assert "1 份本地缓存删除失败" in reply
    assert "不会再参与汇总" in reply


def test_aggregation_refreshes_registered_sources_before_temporary_files(
    project_tmp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh registered caches lead the source order and persist after success."""

    channel = RecordingChannel()
    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    latest = store.registered_cache_path(
        "oc_chat", "ou_sender", "sheets", "sht_registered"
    )
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_bytes(b"old-cache")
    registered = store.add_registered_source(
        "oc_chat",
        "ou_sender",
        kind="sheets",
        token="sht_registered",
        url="https://example.feishu.cn/sheets/sht_registered",
        display_name="固定销售表",
        cached_path=latest,
        refreshed_at="2026-07-16T09:00:00",
    ).source
    temporary_path = project_tmp_dir / "temporary.xlsx"
    temporary_path.write_bytes(b"temporary")
    store.add_source(
        "oc_chat",
        "ou_sender",
        SourceWorkbook("temporary-id", temporary_path),
        display_name="临时销售.xlsx",
    )
    exporter = RefreshingTableExporter(payload=b"new-cache")
    monkeypatch.setattr(
        "feishu_bot_listener.validate_source_workbook",
        lambda source: SourceValidationResult("签约情况", 3),
    )
    captured_ids: list[str] = []

    def fake_aggregate(sources, template_path, output_path):
        captured_ids.extend(source.source_file_id for source in sources)
        Path(output_path).write_bytes(b"xlsx-result")
        return AggregationResult(
            output_path=Path(output_path),
            source_count=len(sources),
            signing_detail_count=5,
            signing_total=Decimal(200),
        )

    monkeypatch.setattr("feishu_bot_listener.aggregate_sales_workbooks", fake_aggregate)
    message = SimpleNamespace(
        chat_id="oc_chat",
        message_id="om_refresh",
        sender_open_id="ou_sender",
        content_text="汇总",
    )

    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            exporter,
            message,
            asyncio.Lock(),
            batch_store=store,
            sales_template_path=project_tmp_dir / "template.xlsx",
        )
    )

    assert captured_ids == [registered.source_id, "temporary-id"]
    assert latest.read_bytes() == b"new-cache"
    assert len(store.list_registered_sources("oc_chat", "ou_sender")) == 1
    assert store.list_sources("oc_chat", "ou_sender") == ()
    assert "正在按登记顺序刷新 1 份固定云表" in channel.calls[0][1]["text"]
    assert "file" in channel.calls[1][1]
    assert "汇总完成" in channel.calls[2][1]["text"]


def test_registered_refresh_failure_never_uses_old_cache(
    project_tmp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed fresh export aborts aggregation and preserves the last cache."""

    channel = RecordingChannel()
    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    latest = store.registered_cache_path(
        "oc_chat", "ou_sender", "sheets", "sht_registered"
    )
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_bytes(b"last-good-cache")
    store.add_registered_source(
        "oc_chat",
        "ou_sender",
        kind="sheets",
        token="sht_registered",
        url="https://example.feishu.cn/sheets/sht_registered",
        display_name="固定销售表",
        cached_path=latest,
        refreshed_at="2026-07-16T09:00:00",
    )
    exporter = RefreshingTableExporter()
    exporter.error = FeishuClientError("network unavailable")
    aggregate_called = False

    def unexpected_aggregate(*args, **kwargs):
        nonlocal aggregate_called
        aggregate_called = True
        raise AssertionError("stale cache must not be aggregated")

    monkeypatch.setattr(
        "feishu_bot_listener.aggregate_sales_workbooks",
        unexpected_aggregate,
    )
    message = SimpleNamespace(
        chat_id="oc_chat",
        message_id="om_refresh_failed",
        sender_open_id="ou_sender",
        content_text="汇总",
    )

    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            exporter,
            message,
            asyncio.Lock(),
            batch_store=store,
            sales_template_path=project_tmp_dir / "template.xlsx",
        )
    )

    assert aggregate_called is False
    assert latest.read_bytes() == b"last-good-cache"
    assert len(store.list_registered_sources("oc_chat", "ou_sender")) == 1
    assert "未使用旧缓存" in channel.calls[-1][1]["text"]
    assert all("file" not in content for _, content, _ in channel.calls)


def test_aggregation_status_is_isolated_by_sender(project_tmp_dir: Path) -> None:
    """A sender only sees files in their own batch inside a shared chat."""

    channel = RecordingChannel()
    source_path = project_tmp_dir / "source.xlsx"
    source_path.write_bytes(b"source")
    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    store.add_source(
        "oc_chat",
        "ou_other",
        SourceWorkbook("source-id", source_path),
        display_name="其他人的销售.xlsx",
    )
    message = SimpleNamespace(
        chat_id="oc_chat",
        message_id="om_status",
        sender_open_id="ou_sender",
        content_text="汇总状态",
    )

    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            PermissionDeniedExporter(),
            message,
            asyncio.Lock(),
            batch_store=store,
            sales_template_path=project_tmp_dir / "template.xlsx",
        )
    )

    assert channel.calls[-1][1]["text"] == "当前汇总批次为空。请先上传销售 XLSX 文件。"


def test_staged_attachment_reply_names_selected_signing_sheet(
    project_tmp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Users can see which fuzzy-matched signing worksheet was staged."""

    channel = RecordingChannel()
    source_path = project_tmp_dir / "销售.xlsx"
    source_path.write_bytes(b"xlsx")
    message = SimpleNamespace(
        chat_id="oc_chat",
        message_id="om_upload",
        sender_open_id="ou_sender",
    )
    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    monkeypatch.setattr(
        "feishu_bot_listener.validate_source_workbook",
        lambda source: SourceValidationResult("2026签约情况", 3),
    )

    asyncio.run(
        handle_message(
            channel,
            ReturningAttachmentDownloader(source_path),
            PermissionDeniedExporter(),
            message,
            asyncio.Lock(),
            batch_store=store,
            sales_template_path=project_tmp_dir / "template.xlsx",
        )
    )

    reply = channel.calls[-1][1]["text"]
    assert "签约工作表“2026签约情况”" in reply
    assert "签约 3 条" in reply
    assert "回款" not in reply


def test_cache_cleanup_command_is_admin_only_and_preserves_all_active_sources(
    project_tmp_dir: Path,
) -> None:
    """An authorized administrator starts global cleanup with all active paths protected."""

    channel = RecordingChannel()
    store = AggregationBatchStore(project_tmp_dir / "aggregation")
    source_path = project_tmp_dir / "active.xlsx"
    source_path.write_bytes(b"active")
    store.add_source(
        "another-chat",
        "another-sender",
        SourceWorkbook("source-id", source_path),
        display_name="active.xlsx",
    )
    cleaner = RecordingCacheCleaner()
    message = SimpleNamespace(
        chat_id="oc_chat",
        message_id="om_cleanup",
        sender_open_id="ou_admin",
        content_text="清空下载缓存",
    )

    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            PermissionDeniedExporter(),
            message,
            asyncio.Lock(),
            batch_store=store,
            cache_cleaner=cleaner,
            cache_admin_open_ids=("ou_admin",),
        )
    )

    assert cleaner.active_source_paths == frozenset((source_path.resolve(),))
    reply = channel.calls[-1][1]["text"]
    assert "删除 3 个文件" in reply
    assert "释放 1.50 KB" in reply
    assert "保留活动批次文件 1 个" in reply


def test_cache_cleanup_command_rejects_non_admin(project_tmp_dir: Path) -> None:
    """An unlisted sender cannot invoke any cache deletion."""

    channel = RecordingChannel()
    cleaner = RecordingCacheCleaner()
    message = SimpleNamespace(
        chat_id="oc_chat",
        message_id="om_cleanup",
        sender_open_id="ou_not_admin",
        content_text="清空下载缓存",
    )

    asyncio.run(
        handle_message(
            channel,
            StubAttachmentDownloader(),
            PermissionDeniedExporter(),
            message,
            asyncio.Lock(),
            batch_store=AggregationBatchStore(project_tmp_dir / "aggregation"),
            cache_cleaner=cleaner,
            cache_admin_open_ids=("ou_admin",),
        )
    )

    assert cleaner.active_source_paths is None
    assert channel.calls[-1][1]["text"] == "你没有执行“清空下载缓存”的权限。"
