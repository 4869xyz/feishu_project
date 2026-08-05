"""Feishu long-connection adapter for the meeting-minutes service."""

from __future__ import annotations

import json
import logging
from typing import Any

from lark_channel import FeishuChannel, PolicyConfig

from .service import MeetingMinutesService, ServiceResult
from .settings import MeetingBotSettings


LOGGER = logging.getLogger(__name__)


def create_channel(settings: MeetingBotSettings) -> FeishuChannel:
    return FeishuChannel(
        app_id=settings.app_id,
        app_secret=settings.app_secret,
        policy=PolicyConfig(
            dm_policy="open",
            group_policy="disabled",
            require_mention=False,
            respond_to_mention_all=False,
        ),
    )


def _nested_value(source: object, *names: str) -> object | None:
    current: object | None = source
    for name in names:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(name)
        else:
            current = getattr(current, name, None)
    return current


def message_open_id(message: object) -> str:
    candidates = (
        _nested_value(message, "sender_open_id"),
        _nested_value(message, "sender", "sender_id", "open_id"),
        _nested_value(message, "event", "sender", "sender_id", "open_id"),
    )
    return next((str(value).strip() for value in candidates if value), "")


def message_text(message: object) -> str:
    for name in ("content_text", "text"):
        value = _nested_value(message, name)
        if value is not None:
            return str(value)
    content = _nested_value(message, "content")
    if isinstance(content, dict):
        return str(content.get("text", ""))
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError:
            return content
        if isinstance(decoded, dict):
            return str(decoded.get("text", ""))
    return ""


async def _reply(channel: FeishuChannel, message: Any, text: str) -> None:
    try:
        await channel.send(
            message.chat_id,
            {"text": text},
            {"reply_to": message.message_id},
        )
    except Exception:
        LOGGER.exception("回复纪要消息失败：message_id=%s", message.message_id)


async def _reply_file(channel: FeishuChannel, message: Any, result: ServiceResult) -> None:
    if result.file_path is None:
        return
    try:
        await channel.send(
            message.chat_id,
            {
                "file": {
                    "source": str(result.file_path),
                    "file_name": result.file_path.name,
                }
            },
            {"reply_to": message.message_id},
        )
    except Exception:
        LOGGER.exception("回传纪要 Word 失败：message_id=%s", message.message_id)
        await _reply(channel, message, f"{result.text}\n文件回传失败，请联系管理员。")


async def handle_message(
    channel: FeishuChannel,
    service: MeetingMinutesService,
    message: Any,
) -> None:
    message_type = str(getattr(message, "message_type", "text") or "text").lower()
    if message_type != "text":
        await _reply(channel, message, "当前首版仅支持直接发送文字纪要。")
        return
    sender_open_id = message_open_id(message)
    if not sender_open_id:
        await _reply(channel, message, "无法识别你的飞书账号，请联系管理员。")
        return
    try:
        result = await service.handle_text(
            message_id=str(message.message_id),
            sender_open_id=sender_open_id,
            text=message_text(message),
        )
    except Exception:
        LOGGER.exception("处理纪要消息失败：message_id=%s", message.message_id)
        await _reply(channel, message, "纪要处理失败，记录已保留，请稍后重试。")
        return
    await _reply(channel, message, result.text)
    await _reply_file(channel, message, result)
