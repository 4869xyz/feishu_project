"""Receive Feishu messages and save direct Excel attachments into data/inbox."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from lark_channel import FeishuChannel

from clients.feishu_attachment import (
    ExcelAttachmentDownloader,
    UnsupportedExcelAttachment,
)
from clients.feishu_client import FeishuClient, FeishuClientError
from config.settings import ConfigurationError, Settings, load_settings


LOGGER = logging.getLogger(__name__)


def _configure_logging(settings: Settings) -> None:
    """Configure credential-safe operational logging for the listener."""

    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=(
            logging.StreamHandler(),
            logging.FileHandler(
                settings.log_dir / "feishu_bot_listener.log",
                encoding="utf-8",
            ),
        ),
        force=True,
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


async def handle_message(
    channel: FeishuChannel,
    downloader: ExcelAttachmentDownloader,
    message: Any,
    download_lock: asyncio.Lock,
) -> None:
    """Download one Excel attachment without blocking the Channel event loop."""

    try:
        async with download_lock:
            result = await asyncio.to_thread(downloader.download_from_message, message)
    except UnsupportedExcelAttachment as exc:
        await _reply(channel, message, f"未下载附件：{exc}")
        return
    except FeishuClientError as exc:
        LOGGER.warning(
            "下载飞书附件失败：message_id=%s, error=%s",
            getattr(message, "message_id", "unknown"),
            exc,
        )
        await _reply(channel, message, "附件下载失败，请稍后重新发送该 Excel 文件。")
        return
    except Exception:
        LOGGER.exception(
            "处理飞书消息失败：message_id=%s", getattr(message, "message_id", "unknown")
        )
        await _reply(channel, message, "处理附件时发生本地错误，请稍后重试。")
        return

    if result is None:
        await _reply(channel, message, "已收到消息。请直接发送 .xlsx、.xls 或 .xlsm 文件。")
        return

    if result.already_present:
        text = f"该 Excel 已在本地收件箱中，无需重复下载：{result.path.name}"
    else:
        text = f"Excel 已下载到本地收件箱：{result.path.name}（{result.bytes_written} 字节）"
    LOGGER.info("附件已就绪：path=%s, already_present=%s", result.path, result.already_present)
    await _reply(channel, message, text)


async def main() -> None:
    """Connect the bot and dispatch direct Excel attachment downloads."""

    settings = load_settings()
    _configure_logging(settings)

    channel = FeishuChannel(app_id=settings.app_id, app_secret=settings.app_secret)
    downloader = ExcelAttachmentDownloader(
        FeishuClient(settings),
        settings.inbox_dir,
        max_bytes=settings.max_download_bytes,
    )
    download_lock = asyncio.Lock()

    async def on_message(message: Any) -> None:
        await handle_message(channel, downloader, message, download_lock)

    async def on_error(error: Exception) -> None:
        LOGGER.error("飞书长连接发生异常：%s", error)

    channel.on("message", on_message)
    channel.on("error", on_error)

    LOGGER.info("正在连接飞书开放平台；Excel 收件箱：%s", settings.inbox_dir)
    await channel.connect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConfigurationError as exc:
        print(f"配置错误：{exc}")
    except KeyboardInterrupt:
        print("\n机器人已停止。")
