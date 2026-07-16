"""Receive Feishu messages and persist Excel attachments or table links locally."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import logging
import os
from pathlib import Path
import re
from typing import Any

from lark_channel import FeishuChannel, PolicyConfig

from clients.feishu_attachment import (
    ExcelAttachmentDownloader,
    UnsupportedExcelAttachment,
)
from clients.feishu_client import FeishuClient, FeishuClientError
from clients.feishu_table_export import (
    FeishuTableLinkExporter,
    UnsupportedFeishuTableLink,
    WikiTablePermissionError,
    message_sender_open_id,
    message_texts,
)
from config.settings import ConfigurationError, Settings, load_settings
from services.aggregation_batch_store import AggregationBatchStore
from services.sales_workbook_aggregator import (
    SalesAggregationError,
    SourceWorkbook,
    aggregate_sales_workbooks,
    validate_source_workbook,
)


LOGGER = logging.getLogger(__name__)
INSTANCE_LOCK_FILENAME = "feishu_bot_listener.lock"
_SENSITIVE_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:access_key|ticket|access_token|app_secret)=)[^&\s\]]+"
)
WIKI_PERMISSION_REPLY = (
    "已收到 Wiki 表格链接，但机器人没有读取该知识库节点的权限。\n\n"
    "请确认当前应用已加入该知识库或文档，并拥有节点阅读和云文档导出权限，然后重新发送链接。"
)
AGGREGATION_COMMANDS = frozenset({"汇总", "汇总状态", "清空汇总"})


class SingleInstanceError(RuntimeError):
    """Raised when another listener already owns the runtime lock."""


class CredentialSafeFormatter(logging.Formatter):
    """Redact credentials that third-party SDKs embed in formatted URLs."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return _SENSITIVE_QUERY_VALUE.sub(r"\1***", formatted)


def _configure_logging(settings: Settings) -> None:
    """Configure credential-safe operational logging for the listener."""

    formatter = CredentialSafeFormatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    handlers: tuple[logging.Handler, ...] = (
        logging.StreamHandler(),
        logging.FileHandler(
            settings.log_dir / "feishu_bot_listener.log",
            encoding="utf-8",
        ),
    )
    for handler in handlers:
        handler.setFormatter(formatter)

    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        handlers=handlers,
        force=True,
    )

    # The SDK installs its own stdout handler and also propagates to root.
    # Removing that handler makes every Lark record pass through one pipeline.
    lark_logger = logging.getLogger("Lark")
    for handler in tuple(lark_logger.handlers):
        lark_logger.removeHandler(handler)
        handler.close()
    lark_logger.propagate = True
    lark_logger.setLevel(logging.WARNING)

    # httpx INFO records include request URLs and user identifiers.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _lock_file(handle: Any) -> None:
    """Acquire a non-blocking, one-byte process lock on an open file."""

    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: Any) -> None:
    """Release the platform-specific process lock held by ``handle``."""

    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _single_instance_lock(lock_path: Path):
    """Hold a cross-platform lock for the listener process lifetime."""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    if lock_path.stat().st_size == 0:
        handle.write(b"\0")
        handle.flush()

    try:
        _lock_file(handle)
    except OSError as exc:
        handle.close()
        raise SingleInstanceError(
            "飞书机器人监听器已经在运行；请先停止旧实例后再启动。"
        ) from exc

    try:
        yield
    finally:
        try:
            _unlock_file(handle)
        finally:
            handle.close()


def _create_channel(settings: Settings) -> FeishuChannel:
    """Create a channel with explicit direct-message and group admission rules."""

    return FeishuChannel(
        app_id=settings.app_id,
        app_secret=settings.app_secret,
        policy=PolicyConfig(
            dm_policy="open",
            group_policy="open",
            require_mention=True,
            respond_to_mention_all=False,
        ),
    )


async def _reply(channel: FeishuChannel, message: Any, text: str) -> None:
    """Send a best-effort text reply associated with the incoming message."""

    try:
        await channel.send(
            message.chat_id,
            {"text": text},
            {"reply_to": message.message_id},
        )
    except Exception:
        LOGGER.exception(
            "回复飞书消息失败：message_id=%s", getattr(message, "message_id", "unknown")
        )


async def _reply_file(channel: FeishuChannel, message: Any, path: Path) -> bool:
    """Upload one generated XLSX as a reply and report whether it succeeded."""

    try:
        result = await channel.send(
            message.chat_id,
            {"file": {"source": str(path), "file_name": path.name}},
            {"reply_to": message.message_id},
        )
        return result is None or bool(getattr(result, "success", True))
    except Exception:
        LOGGER.exception(
            "回传汇总文件失败：message_id=%s, file=%s",
            getattr(message, "message_id", "unknown"),
            path.name,
        )
        return False


def _aggregation_command(message: object) -> str | None:
    """Recognize an exact aggregation command after removing mention markup."""

    for text in message_texts(message):
        cleaned = re.sub(r"<at\b[^>]*>.*?</at>", " ", text, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        if cleaned in AGGREGATION_COMMANDS:
            return cleaned
        parts = cleaned.split()
        if parts and parts[-1] in AGGREGATION_COMMANDS and all(
            part.startswith("@") or part.startswith("_user_") for part in parts[:-1]
        ):
            return parts[-1]
    return None


def _batch_identity(message: object) -> tuple[str, str]:
    """Return the chat and sender IDs that isolate one aggregation batch."""

    chat_id = getattr(message, "chat_id", None)
    if not isinstance(chat_id, str) or not chat_id.strip():
        raise ValueError("消息缺少 chat_id")
    return chat_id.strip(), message_sender_open_id(message)


async def _handle_aggregation_command(
    channel: FeishuChannel,
    message: Any,
    command: str,
    download_lock: asyncio.Lock,
    batch_store: AggregationBatchStore,
    sales_template_path: Path,
) -> None:
    """Handle status, clear, and final aggregation commands."""

    chat_id, sender_open_id = _batch_identity(message)
    async with download_lock:
        batch = batch_store.list_sources(chat_id, sender_open_id)
        if command == "汇总状态":
            if not batch:
                await _reply(channel, message, "当前汇总批次为空。请先上传销售 XLSX 文件。")
                return
            names = "\n".join(
                f"{index}. {item.display_name}" for index, item in enumerate(batch, 1)
            )
            await _reply(
                channel,
                message,
                f"当前汇总批次共 {len(batch)} 份文件，处理顺序如下：\n{names}",
            )
            return
        if command == "清空汇总":
            removed = batch_store.clear_active(chat_id, sender_open_id)
            await _reply(channel, message, f"已清空当前汇总批次，共移除 {removed} 份文件。")
            return
        if not batch:
            await _reply(channel, message, "当前汇总批次为空。请先上传销售 XLSX 文件。")
            return

        output_path = batch_store.new_output_path(chat_id, sender_open_id)
        try:
            result = await asyncio.to_thread(
                aggregate_sales_workbooks,
                [item.source for item in batch],
                sales_template_path,
                output_path,
            )
        except SalesAggregationError as exc:
            LOGGER.warning(
                "销售汇总失败：message_id=%s, error=%s",
                getattr(message, "message_id", "unknown"),
                exc,
            )
            await _reply(channel, message, f"汇总失败，当前批次已保留：{exc}")
            return

        if not await _reply_file(channel, message, result.output_path):
            await _reply(
                channel,
                message,
                f"汇总文件已生成但回传失败，当前批次已保留：{result.output_path.name}",
            )
            return
        batch_store.clear_active(chat_id, sender_open_id)
        await _reply(
            channel,
            message,
            "汇总完成："
            f"{result.source_count} 份源文件，"
            f"签约 {result.signing_detail_count} 条 / {result.signing_total} 元，"
            f"回款 {result.repayment_detail_count} 条 / "
            f"{result.repayment_current_year_total} 元。",
        )
async def handle_message(
    channel: FeishuChannel,
    attachment_downloader: ExcelAttachmentDownloader,
    table_exporter: FeishuTableLinkExporter,
    message: Any,
    download_lock: asyncio.Lock,
    *,
    batch_store: AggregationBatchStore | None = None,
    sales_template_path: Path | None = None,
) -> None:
    """Handle one direct Excel attachment or supported Feishu table link."""

    if batch_store is not None and sales_template_path is not None:
        command = _aggregation_command(message)
        if command is not None:
            try:
                await _handle_aggregation_command(
                    channel,
                    message,
                    command,
                    download_lock,
                    batch_store,
                    sales_template_path,
                )
            except (OSError, ValueError, RuntimeError) as exc:
                LOGGER.warning(
                    "处理汇总命令失败：message_id=%s, error=%s",
                    getattr(message, "message_id", "unknown"),
                    exc,
                )
                await _reply(channel, message, f"汇总命令处理失败：{exc}")
            return

    staged_result = None
    try:
        async with download_lock:
            attachment = await asyncio.to_thread(
                attachment_downloader.download_from_message, message
            )
            table_export = None
            if attachment is None:
                table_export = await asyncio.to_thread(
                    table_exporter.export_from_message, message
                )
            if batch_store is not None and sales_template_path is not None:
                downloaded = attachment or table_export
                if downloaded is not None:
                    source = SourceWorkbook(downloaded.source_file_id, downloaded.path)
                    signing_count, repayment_count = await asyncio.to_thread(
                        validate_source_workbook, source
                    )
                    chat_id, sender_open_id = _batch_identity(message)
                    staged_result = batch_store.add_source(
                        chat_id,
                        sender_open_id,
                        source,
                        display_name=downloaded.path.name,
                    )
                    staged_result = (
                        staged_result,
                        signing_count,
                        repayment_count,
                    )
    except UnsupportedExcelAttachment as exc:
        await _reply(channel, message, f"未下载附件：{exc}")
        return
    except WikiTablePermissionError:
        await _reply(channel, message, WIKI_PERMISSION_REPLY)
        return
    except UnsupportedFeishuTableLink:
        await _reply(channel, message, "当前链接不是可导出的销售表格。")
        return
    except FeishuClientError as exc:
        LOGGER.warning(
            "下载或导出飞书文件失败：message_id=%s, error=%s",
            getattr(message, "message_id", "unknown"),
            exc,
        )
        await _reply(channel, message, "文件下载或导出失败，请稍后重新发送该 Excel 文件或表格链接。")
        return
    except SalesAggregationError as exc:
        LOGGER.warning(
            "下载文件未通过销售表格校验：message_id=%s, error=%s",
            getattr(message, "message_id", "unknown"),
            exc,
        )
        await _reply(channel, message, f"文件未加入汇总批次：{exc}")
        return
    except (OSError, ValueError) as exc:
        LOGGER.warning(
            "处理飞书消息的本地数据无效：message_id=%s, error=%s",
            getattr(message, "message_id", "unknown"),
            exc,
        )
        await _reply(channel, message, "处理消息时缺少必要信息，请稍后重新发送。")
        return
    except Exception:
        LOGGER.exception(
            "处理飞书消息失败：message_id=%s", getattr(message, "message_id", "unknown")
        )
        await _reply(channel, message, "处理文件时发生本地错误，请稍后重试。")
        return

    if attachment is not None:
        if staged_result is not None:
            added, signing_count, repayment_count = staged_result
            if added.added:
                text = (
                    f"Excel 已加入当前汇总批次（共 {added.active_count} 份）："
                    f"{attachment.path.name}；签约 {signing_count} 条，"
                    f"回款 {repayment_count} 条。上传完成后发送“汇总”。"
                )
            else:
                text = f"该 Excel 已处理过，不会重复加入汇总批次：{attachment.path.name}"
            await _reply(channel, message, text)
            return
        if attachment.already_present:
            text = f"该 Excel 已在本地收件箱中，无需重复下载：{attachment.path.name}"
        else:
            text = (
                "Excel 已下载到本地收件箱："
                f"{attachment.path.name}（{attachment.bytes_written} 字节）"
            )
        LOGGER.info(
            "附件已就绪：path=%s, already_present=%s",
            attachment.path,
            attachment.already_present,
        )
        await _reply(channel, message, text)
        return

    if table_export is not None:
        if staged_result is not None:
            added, signing_count, repayment_count = staged_result
            if added.added:
                text = (
                    f"飞书表格已加入当前汇总批次（共 {added.active_count} 份）："
                    f"{table_export.path.name}；签约 {signing_count} 条，"
                    f"回款 {repayment_count} 条。上传完成后发送“汇总”。"
                )
            else:
                text = f"该飞书表格已处理过，不会重复加入汇总批次：{table_export.path.name}"
            await _reply(channel, message, text)
            return
        LOGGER.info(
            "飞书表格已归档：path=%s, type=%s",
            table_export.path,
            table_export.document_type,
        )
        await _reply(
            channel,
            message,
            f"飞书表格已导出到本地归档：{table_export.path.name}（{table_export.bytes_written} 字节）",
        )
        return

    await _reply(
        channel,
        message,
        "已收到消息。请直接发送 .xlsx、.xls、.xlsm 附件，或飞书 Sheets/Wiki 表格链接。",
    )


async def _run_listener(settings: Settings) -> None:
    """Connect one configured listener until it is stopped."""

    channel = _create_channel(settings)
    client = FeishuClient(settings)
    attachment_downloader = ExcelAttachmentDownloader(
        client,
        settings.inbox_dir,
        max_bytes=settings.max_download_bytes,
    )
    table_exporter = FeishuTableLinkExporter(
        client,
        settings.archive_dir,
        max_bytes=settings.max_download_bytes,
    )
    batch_store = AggregationBatchStore(settings.aggregation_dir)
    download_lock = asyncio.Lock()

    async def on_message(message: Any) -> None:
        await handle_message(
            channel,
            attachment_downloader,
            table_exporter,
            message,
            download_lock,
            batch_store=batch_store,
            sales_template_path=settings.sales_template_path,
        )

    async def on_error(error: Exception) -> None:
        LOGGER.error("飞书长连接发生异常：%s", error)

    channel.on("message", on_message)
    channel.on("error", on_error)

    LOGGER.info(
        "正在连接飞书开放平台；Excel 收件箱：%s；表格归档：%s；汇总目录：%s",
        settings.inbox_dir,
        settings.archive_dir,
        settings.aggregation_dir,
    )
    await channel.connect()


async def main() -> None:
    """Run exactly one bot listener for the configured project instance."""

    settings = load_settings()
    _configure_logging(settings)

    lock_path = settings.log_dir / INSTANCE_LOCK_FILENAME
    with _single_instance_lock(lock_path):
        await _run_listener(settings)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConfigurationError as exc:
        print(f"配置错误：{exc}")
    except SingleInstanceError as exc:
        print(f"启动失败：{exc}")
    except KeyboardInterrupt:
        print("\n机器人已停止。")
