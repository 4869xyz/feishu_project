"""Receive Feishu messages and persist Excel attachments or table links locally."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime
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
    DownloadedTableExport,
    FeishuTableLink,
    FeishuTableLinkExporter,
    UnsupportedFeishuTableLink,
    WikiTablePermissionError,
    extract_feishu_table_link,
    message_sender_open_id,
    message_texts,
)
from config.settings import ConfigurationError, Settings, load_settings
from services.aggregation_batch_store import (
    AggregationBatchStore,
    RegisteredCloudSource,
)
from services.download_cache import DownloadCacheCleaner
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
CACHE_CLEANUP_COMMAND = "清空下载缓存"
BOT_COMMANDS = AGGREGATION_COMMANDS | {CACHE_CLEANUP_COMMAND}


class SingleInstanceError(RuntimeError):
    """Raised when another listener already owns the runtime lock."""


class RegisteredSourceRefreshError(RuntimeError):
    """Raised when one persistent cloud source cannot provide a fresh workbook."""


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


def _clean_message_texts(message: object) -> list[str]:
    """Return message texts after removing group mention markup."""

    cleaned_texts: list[str] = []
    for text in message_texts(message):
        cleaned = re.sub(r"<at\b[^>]*>.*?</at>", " ", text, flags=re.IGNORECASE)
        cleaned_texts.append(cleaned.strip())
    return cleaned_texts


def _bot_command(message: object) -> str | None:
    """Recognize an exact supported command after removing mention markup."""

    for cleaned in _clean_message_texts(message):
        if cleaned in BOT_COMMANDS:
            return cleaned
        parts = cleaned.split()
        if parts and parts[-1] in BOT_COMMANDS and all(
            part.startswith("@") or part.startswith("_user_") for part in parts[:-1]
        ):
            return parts[-1]
    return None


def _registered_source_command(message: object) -> tuple[str, str] | None:
    """Recognize fixed cloud-source commands and their optional argument."""

    for cleaned in _clean_message_texts(message):
        if cleaned == "云表列表":
            return "list", ""
        add_match = re.fullmatch(r"添加云表(?:\s+|[:：])?(.*)", cleaned)
        if add_match:
            return "add", add_match.group(1).strip()
        remove_match = re.fullmatch(r"移除云表(?:\s+|[:：])?(.*)", cleaned)
        if remove_match:
            return "remove", remove_match.group(1).strip()
    return None


def _batch_identity(message: object) -> tuple[str, str]:
    """Return the chat and sender IDs that isolate one aggregation batch."""

    chat_id = getattr(message, "chat_id", None)
    if not isinstance(chat_id, str) or not chat_id.strip():
        raise ValueError("消息缺少 chat_id")
    return chat_id.strip(), message_sender_open_id(message)


def _format_file_size(size: int) -> str:
    """Return a compact binary file-size string for user replies."""

    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def _refresh_cloud_source_sync(
    table_exporter: FeishuTableLinkExporter,
    batch_store: AggregationBatchStore,
    chat_id: str,
    sender_open_id: str,
    *,
    link: FeishuTableLink,
    source_id: str,
) -> tuple[DownloadedTableExport, Path]:
    """Export, validate, and atomically promote one cloud source cache."""

    staging_path = batch_store.registered_staging_path(
        chat_id, sender_open_id, link.kind, link.token
    )
    latest_path = batch_store.registered_cache_path(
        chat_id, sender_open_id, link.kind, link.token
    )
    try:
        exported = table_exporter.export_link_to_path(
            link,
            staging_path,
            source_file_id=source_id,
        )
        validate_source_workbook(SourceWorkbook(source_id, staging_path))
        batch_store.promote_registered_cache(staging_path, latest_path)
        return exported, latest_path
    finally:
        staging_path.unlink(missing_ok=True)


def _registered_sources_text(
    sources: tuple[RegisteredCloudSource, ...],
) -> str:
    """Format persistent sources for a compact user-facing list."""

    return "\n".join(
        f"{index}. {item.source_id}｜{item.display_name}｜最近刷新 {item.last_success_at}"
        for index, item in enumerate(sources, 1)
    )


def _refresh_error(source: RegisteredCloudSource, exc: Exception) -> str:
    """Return a bounded source-specific refresh failure explanation."""

    prefix = f"{source.display_name}（{source.source_id}）"
    if isinstance(exc, WikiTablePermissionError):
        return f"{prefix}：机器人已失去 Wiki 节点或导出权限"
    if isinstance(exc, UnsupportedFeishuTableLink):
        return f"{prefix}：Wiki 节点已不再指向可导出的电子表格或多维表格"
    if isinstance(exc, FeishuClientError):
        return f"{prefix}：飞书导出或下载失败，请稍后重试"
    return f"{prefix}：{exc}"


async def _handle_registered_source_command(
    channel: FeishuChannel,
    message: Any,
    action: str,
    argument: str,
    download_lock: asyncio.Lock,
    batch_store: AggregationBatchStore,
    table_exporter: FeishuTableLinkExporter,
) -> None:
    """Register, list, or remove one sender's persistent cloud sources."""

    chat_id, sender_open_id = _batch_identity(message)
    async with download_lock:
        registered = batch_store.list_registered_sources(chat_id, sender_open_id)
        if action == "list":
            if not registered:
                await _reply(
                    channel,
                    message,
                    "当前没有固定云表。发送“添加云表 <飞书表格链接>”即可登记。",
                )
                return
            await _reply(
                channel,
                message,
                f"当前共登记 {len(registered)} 份固定云表：\n"
                f"{_registered_sources_text(registered)}",
            )
            return

        if action == "remove":
            if not argument:
                await _reply(
                    channel,
                    message,
                    "请发送“移除云表 <编号>”，编号可通过“云表列表”查看。",
                )
                return
            removed = batch_store.remove_registered_source(
                chat_id, sender_open_id, argument
            )
            if removed is None:
                await _reply(
                    channel,
                    message,
                    f"未找到固定云表：{argument}。请先发送“云表列表”核对编号。",
                )
                return
            cache_path = batch_store.registered_cache_path(
                chat_id,
                sender_open_id,
                removed.kind,
                removed.token,
            )
            try:
                cache_path.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning(
                    "固定云表登记已移除但缓存删除失败：source_id=%s",
                    removed.source_id,
                )
            await _reply(
                channel,
                message,
                f"已移除固定云表：{removed.display_name}（{removed.source_id}）。",
            )
            return

        link = extract_feishu_table_link(message)
        if action != "add" or link is None:
            await _reply(
                channel,
                message,
                "请发送“添加云表 <飞书 Sheets/Wiki 表格链接>”。",
            )
            return
        source_id = batch_store.registered_source_id(link.kind, link.token)
        duplicate = next(
            (item for item in registered if item.source_id == source_id),
            None,
        )
        if duplicate is not None:
            await _reply(
                channel,
                message,
                f"该云表已经登记：{duplicate.display_name}（{duplicate.source_id}）。",
            )
            return

        exported, latest_path = await asyncio.to_thread(
            _refresh_cloud_source_sync,
            table_exporter,
            batch_store,
            chat_id,
            sender_open_id,
            link=link,
            source_id=source_id,
        )
        refreshed_at = datetime.now().isoformat(timespec="seconds")
        result = batch_store.add_registered_source(
            chat_id,
            sender_open_id,
            kind=link.kind,
            token=link.token,
            url=link.url,
            display_name=exported.title,
            cached_path=latest_path,
            refreshed_at=refreshed_at,
        )
        await _reply(
            channel,
            message,
            f"固定云表已登记（共 {result.registered_count} 份）："
            f"{result.source.display_name}（{result.source.source_id}）。"
            "以后发送“汇总”时会先重新获取最新数据。",
        )


async def _handle_cache_cleanup_command(
    channel: FeishuChannel,
    message: Any,
    download_lock: asyncio.Lock,
    batch_store: AggregationBatchStore,
    cache_cleaner: DownloadCacheCleaner,
    cache_admin_open_ids: tuple[str, ...],
) -> None:
    """Authorize and execute one global inactive-cache cleanup."""

    sender_open_id = message_sender_open_id(message)
    if not cache_admin_open_ids:
        await _reply(
            channel,
            message,
            "清空下载缓存命令尚未启用。请先在 .env 配置 FEISHU_CACHE_ADMIN_OPEN_IDS 并重启机器人。",
        )
        return
    if sender_open_id not in cache_admin_open_ids:
        LOGGER.warning(
            "非管理员尝试执行下载缓存清理：message_id=%s",
            getattr(message, "message_id", "unknown"),
        )
        await _reply(channel, message, "你没有执行“清空下载缓存”的权限。")
        return

    async with download_lock:
        active_paths = batch_store.all_active_source_paths()
        result = await asyncio.to_thread(
            cache_cleaner.clear,
            active_source_paths=active_paths,
        )
    reply = (
        f"下载缓存清理完成：删除 {result.deleted_files} 个文件，"
        f"释放 {_format_file_size(result.deleted_bytes)}；"
        f"保留活动批次文件 {result.preserved_active_files} 个。"
    )
    if result.failed_files:
        reply += f"另有 {result.failed_files} 个文件删除失败，请查看日志。"
    await _reply(channel, message, reply)


async def _refresh_registered_workbooks(
    table_exporter: FeishuTableLinkExporter,
    batch_store: AggregationBatchStore,
    chat_id: str,
    sender_open_id: str,
    registered: tuple[RegisteredCloudSource, ...],
) -> tuple[SourceWorkbook, ...]:
    """Refresh registered sources sequentially and return their latest workbooks."""

    refreshed: list[SourceWorkbook] = []
    for item in registered:
        link = FeishuTableLink(kind=item.kind, token=item.token, url=item.url)
        try:
            exported, latest_path = await asyncio.to_thread(
                _refresh_cloud_source_sync,
                table_exporter,
                batch_store,
                chat_id,
                sender_open_id,
                link=link,
                source_id=item.source_id,
            )
        except (
            FeishuClientError,
            OSError,
            SalesAggregationError,
            UnsupportedFeishuTableLink,
            ValueError,
            WikiTablePermissionError,
        ) as exc:
            raise RegisteredSourceRefreshError(_refresh_error(item, exc)) from exc
        batch_store.update_registered_source(
            chat_id,
            sender_open_id,
            item.source_id,
            display_name=exported.title,
            cached_path=latest_path,
            refreshed_at=datetime.now().isoformat(timespec="seconds"),
        )
        refreshed.append(SourceWorkbook(item.source_id, latest_path))
    return tuple(refreshed)


async def _handle_aggregation_command(
    channel: FeishuChannel,
    message: Any,
    command: str,
    download_lock: asyncio.Lock,
    batch_store: AggregationBatchStore,
    sales_template_path: Path,
    table_exporter: FeishuTableLinkExporter,
) -> None:
    """Handle status, clear, and final aggregation commands."""

    chat_id, sender_open_id = _batch_identity(message)
    async with download_lock:
        batch = batch_store.list_sources(chat_id, sender_open_id)
        registered = batch_store.list_registered_sources(chat_id, sender_open_id)
        if command == "汇总状态":
            if not batch and not registered:
                await _reply(channel, message, "当前汇总批次为空。请先上传销售 XLSX 文件。")
                return
            temporary_names = "\n".join(
                f"{index}. {item.display_name}" for index, item in enumerate(batch, 1)
            )
            sections: list[str] = []
            if registered:
                sections.append(
                    f"固定云表 {len(registered)} 份：\n"
                    f"{_registered_sources_text(registered)}"
                )
            if batch:
                sections.append(
                    f"临时文件 {len(batch)} 份：\n{temporary_names}"
                )
            await _reply(
                channel,
                message,
                "当前汇总来源如下（固定云表优先）：\n\n"
                + "\n\n".join(sections),
            )
            return
        if command == "清空汇总":
            removed = batch_store.clear_active(chat_id, sender_open_id)
            suffix = (
                f"固定云表 {len(registered)} 份仍保留。"
                if registered
                else "当前没有固定云表。"
            )
            await _reply(
                channel,
                message,
                f"已清空临时汇总批次，共移除 {removed} 份文件；{suffix}",
            )
            return
        if not batch and not registered:
            await _reply(
                channel,
                message,
                "当前没有可汇总来源。请先上传销售 XLSX，或发送“添加云表 <链接>”。",
            )
            return

        registered_workbooks: tuple[SourceWorkbook, ...] = ()
        if registered:
            await _reply(
                channel,
                message,
                f"正在按登记顺序刷新 {len(registered)} 份固定云表，请稍候。",
            )
            try:
                registered_workbooks = await _refresh_registered_workbooks(
                    table_exporter,
                    batch_store,
                    chat_id,
                    sender_open_id,
                    registered,
                )
            except RegisteredSourceRefreshError as exc:
                LOGGER.warning(
                    "固定云表刷新失败：message_id=%s, error=%s",
                    getattr(message, "message_id", "unknown"),
                    exc,
                )
                await _reply(
                    channel,
                    message,
                    f"汇总已中止，未使用旧缓存：{exc}。固定云表和临时批次均已保留。",
                )
                return

        sources = [*registered_workbooks, *(item.source for item in batch)]
        output_path = batch_store.new_output_path(chat_id, sender_open_id)
        try:
            result = await asyncio.to_thread(
                aggregate_sales_workbooks,
                sources,
                sales_template_path,
                output_path,
            )
        except SalesAggregationError as exc:
            LOGGER.warning(
                "销售汇总失败：message_id=%s, error=%s",
                getattr(message, "message_id", "unknown"),
                exc,
            )
            await _reply(
                channel,
                message,
                f"汇总失败，固定云表和临时批次均已保留：{exc}",
            )
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
            f"签约 {result.signing_detail_count} 条 / {result.signing_total} 元。",
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
    cache_cleaner: DownloadCacheCleaner | None = None,
    cache_admin_open_ids: tuple[str, ...] = (),
) -> None:
    """Handle one direct Excel attachment or supported Feishu table link."""

    registered_command = _registered_source_command(message)
    if registered_command is not None:
        if batch_store is None:
            await _reply(channel, message, "固定云表命令当前不可用。")
            return
        action, argument = registered_command
        try:
            await _handle_registered_source_command(
                channel,
                message,
                action,
                argument,
                download_lock,
                batch_store,
                table_exporter,
            )
        except WikiTablePermissionError:
            await _reply(channel, message, WIKI_PERMISSION_REPLY)
        except UnsupportedFeishuTableLink:
            await _reply(channel, message, "当前链接不是可导出的销售表格。")
        except FeishuClientError:
            LOGGER.warning(
                "固定云表登记导出失败：message_id=%s",
                getattr(message, "message_id", "unknown"),
            )
            await _reply(channel, message, "固定云表导出失败，请稍后重新登记。")
        except SalesAggregationError as exc:
            await _reply(channel, message, f"固定云表未登记：{exc}")
        except (OSError, ValueError, RuntimeError) as exc:
            LOGGER.warning(
                "固定云表命令失败：message_id=%s, error=%s",
                getattr(message, "message_id", "unknown"),
                exc,
            )
            await _reply(channel, message, f"固定云表命令失败：{exc}")
        return

    command = _bot_command(message)
    if command == CACHE_CLEANUP_COMMAND:
        if batch_store is None or cache_cleaner is None:
            await _reply(channel, message, "清空下载缓存命令当前不可用。")
            return
        try:
            await _handle_cache_cleanup_command(
                channel,
                message,
                download_lock,
                batch_store,
                cache_cleaner,
                cache_admin_open_ids,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            LOGGER.warning(
                "处理缓存清理命令失败：message_id=%s, error=%s",
                getattr(message, "message_id", "unknown"),
                exc,
            )
            await _reply(channel, message, f"清空下载缓存失败：{exc}")
        return

    if batch_store is not None and sales_template_path is not None:
        if command in AGGREGATION_COMMANDS:
            try:
                await _handle_aggregation_command(
                    channel,
                    message,
                    command,
                    download_lock,
                    batch_store,
                    sales_template_path,
                    table_exporter,
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
                    validation = await asyncio.to_thread(
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
                        validation.signing_sheet_name,
                        validation.signing_detail_count,
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
            added, signing_sheet_name, signing_count = staged_result
            if added.added:
                text = (
                    f"Excel 已加入当前汇总批次（共 {added.active_count} 份）："
                    f"{attachment.path.name}；签约工作表“{signing_sheet_name}”，"
                    f"签约 {signing_count} 条。上传完成后发送“汇总”。"
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
            added, signing_sheet_name, signing_count = staged_result
            if added.added:
                text = (
                    f"飞书表格已加入当前汇总批次（共 {added.active_count} 份）："
                    f"{table_export.path.name}；签约工作表“{signing_sheet_name}”，"
                    f"签约 {signing_count} 条。上传完成后发送“汇总”。"
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
    cache_cleaner = DownloadCacheCleaner(
        (settings.inbox_dir, settings.archive_dir, batch_store.output_dir),
        protected_paths=(settings.sales_template_path,),
    )
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
            cache_cleaner=cache_cleaner,
            cache_admin_open_ids=settings.cache_admin_open_ids,
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
