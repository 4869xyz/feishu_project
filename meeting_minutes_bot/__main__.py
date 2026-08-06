"""Run the independent meeting-minutes bot with ``python -m``."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging

from .attachments import (
    AttachmentConfigurationError,
    AttachmentProcessor,
    LocalRapidOcrEngine,
)
from .document import MinutesDocumentRenderer, MinutesTemplateError
from .listener import create_channel, handle_message
from .people import PeopleConfigurationError, load_people
from .repository import MeetingRepository
from .runtime import (
    INSTANCE_LOCK_FILENAME,
    MeetingBotSingleInstanceError,
    configure_logging,
    single_instance_lock,
)
from .retention import RetentionCleaner
from .service import MeetingMinutesService
from .settings import MeetingBotConfigurationError, load_settings


LOGGER = logging.getLogger(__name__)


async def run(settings) -> None:
    configure_logging(settings)
    people = load_people(settings.people_config_path)
    renderer = MinutesDocumentRenderer(
        template_path=settings.template_path,
        output_dir=settings.output_dir,
        people=people,
    )
    repository = MeetingRepository(settings.database_url)
    await repository.initialize()
    service = MeetingMinutesService(
        repository=repository,
        people=people,
        renderer=renderer,
        timezone=settings.timezone,
        max_text_length=settings.max_text_length,
    )
    attachment_processor = AttachmentProcessor(
        ocr=LocalRapidOcrEngine(),
        max_bytes=settings.max_attachment_bytes,
        max_pdf_pages=settings.max_pdf_pages,
    )
    channel = create_channel(settings)
    cleaner = RetentionCleaner(repository=repository, settings=settings)

    async def on_message(message: object) -> None:
        await handle_message(channel, service, message, attachment_processor)

    async def on_error(error: Exception) -> None:
        LOGGER.error("周例会纪要机器人长连接异常：%s", error)

    channel.on("message", on_message)
    channel.on("error", on_error)
    LOGGER.info(
        "正在连接周例会纪要机器人；数据库：%s；输出目录：%s",
        settings.database_url,
        settings.output_dir,
    )
    await cleaner.run_once()
    cleanup_task = asyncio.create_task(
        cleaner.run_forever(), name="meeting-minutes-retention-cleanup"
    )
    try:
        await channel.connect()
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        await repository.close()


def main() -> None:
    settings = load_settings()
    lock_path = settings.log_dir / INSTANCE_LOCK_FILENAME
    with single_instance_lock(lock_path):
        asyncio.run(run(settings))


if __name__ == "__main__":
    try:
        main()
    except (
        MeetingBotConfigurationError,
        AttachmentConfigurationError,
        PeopleConfigurationError,
        MinutesTemplateError,
        MeetingBotSingleInstanceError,
    ) as exc:
        print(f"启动失败：{exc}")
    except KeyboardInterrupt:
        print("\n周例会纪要机器人已停止。")
