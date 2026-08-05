"""Run the independent meeting-minutes bot with ``python -m``."""

from __future__ import annotations

import asyncio
import logging

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
    channel = create_channel(settings)

    async def on_message(message: object) -> None:
        await handle_message(channel, service, message)

    async def on_error(error: Exception) -> None:
        LOGGER.error("周例会纪要机器人长连接异常：%s", error)

    channel.on("message", on_message)
    channel.on("error", on_error)
    LOGGER.info(
        "正在连接周例会纪要机器人；数据库：%s；输出目录：%s",
        settings.database_url,
        settings.output_dir,
    )
    try:
        await channel.connect()
    finally:
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
        PeopleConfigurationError,
        MinutesTemplateError,
        MeetingBotSingleInstanceError,
    ) as exc:
        print(f"启动失败：{exc}")
    except KeyboardInterrupt:
        print("\n周例会纪要机器人已停止。")
