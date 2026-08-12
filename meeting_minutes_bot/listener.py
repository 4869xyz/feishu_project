"""Feishu long-connection adapter for the meeting-minutes service."""

from __future__ import annotations

import json
import logging
import math
from typing import Any

from lark_channel import (
    ChannelConfig,
    FeishuChannel,
    InboundConfig,
    MediaCacheConfig,
    PolicyConfig,
)

from pathlib import Path

from .attachments import (
    AttachmentProcessingError,
    AttachmentProcessor,
    message_attachment_resource,
    validate_resource_type,
)
from .service import MeetingMinutesService, ServiceResult
from .settings import MeetingBotSettings


LOGGER = logging.getLogger(__name__)


def create_channel(settings: MeetingBotSettings) -> FeishuChannel:
    attachment_dir = settings.attachment_dir or settings.data_dir / "attachments"
    return FeishuChannel(
        config=ChannelConfig(
            app_id=settings.app_id,
            app_secret=settings.app_secret,
            policy=PolicyConfig(
                dm_policy="open",
                group_policy="disabled",
                require_mention=False,
                respond_to_mention_all=False,
            ),
            inbound=InboundConfig(
                media_max_mb=max(
                    1, math.ceil(settings.max_attachment_bytes / (1024 * 1024))
                )
            ),
            media_cache=MediaCacheConfig(
                enabled=True,
                root_dir=attachment_dir,
                ttl_seconds=settings.attachment_cache_ttl_seconds,
                max_entries=128,
                max_bytes=settings.attachment_cache_max_bytes,
                max_file_bytes=settings.max_attachment_bytes,
                image_max_bytes=settings.max_attachment_bytes,
            ),
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
        _nested_value(message, "sender_id"),
        _nested_value(message, "sender", "open_id"),
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


def message_kind(message: object) -> str:
    """Resolve legacy and lark-channel 1.2 normalized content types."""

    for name in ("raw_content_type", "message_type", "msg_type"):
        value = _nested_value(message, name)
        if value:
            return str(value).strip().lower()
    value = _nested_value(message, "content", "kind")
    if value:
        return str(value).strip().lower()
    try:
        resource = message_attachment_resource(message)
    except AttachmentProcessingError:
        return "attachment"
    return resource.type if resource is not None else "text"


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
    attachment_processor: AttachmentProcessor | None = None,
) -> None:
    sender_open_id = message_open_id(message)
    if not sender_open_id:
        await _reply(channel, message, "无法识别你的飞书账号，请联系管理员。")
        return

    kind = message_kind(message)
    if kind in {"text", "post"}:
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
        return

    try:
        resource = message_attachment_resource(message)
        if resource is None:
            if attachment_processor is None:
                await _reply(channel, message, "附件识别功能尚未初始化，请联系管理员。")
                return
            raise AttachmentProcessingError(
                "无法读取该消息中的附件资源，请重新发送图片或受支持的文件。"
            )
        file_name = resource.file_name or ""
        config_kind = service.classify_config_upload(sender_open_id, file_name)
        is_admin_config = config_kind is not None
        if not is_admin_config:
            rejection = service.submission_rejection(sender_open_id)
            if rejection is not None:
                await _reply(channel, message, rejection.text)
                return
            if attachment_processor is None:
                await _reply(channel, message, "附件识别功能尚未初始化，请联系管理员。")
                return
        validate_resource_type(resource, allow_admin_config=is_admin_config)
        if is_admin_config:
            await _reply(channel, message, "已收到配置文件，正在校验并热更新，请稍候……")
        else:
            await _reply(channel, message, "已收到附件，正在本地识别，请稍候……")
        cached = await channel.resolve_resource_to_cache(
            message_id=str(message.message_id), resource=resource
        )
        if getattr(cached, "decision", "") != "cached" or not getattr(
            cached, "path", None
        ):
            reason = getattr(cached, "reason", None) or "附件下载失败"
            raise AttachmentProcessingError(str(reason))
        cached_path = Path(cached.path)
        if is_admin_config:
            result = await service.handle_config_upload(
                sender_open_id=sender_open_id,
                file_name=file_name,
                source_path=cached_path,
                kind=config_kind,
            )
        else:
            assert attachment_processor is not None
            attachment = await attachment_processor.extract(cached_path, resource)
            result = await service.handle_attachment(
                message_id=str(message.message_id),
                sender_open_id=sender_open_id,
                attachment=attachment,
            )
    except AttachmentProcessingError as exc:
        await _reply(channel, message, f"附件识别失败：{exc}")
        return
    except Exception:
        LOGGER.exception("处理纪要附件失败：message_id=%s", message.message_id)
        await _reply(channel, message, "附件处理失败，请重新发送或联系管理员。")
        return
    await _reply(channel, message, result.text)
