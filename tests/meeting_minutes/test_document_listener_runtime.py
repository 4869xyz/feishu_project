from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document

from feishu_bot_listener import INSTANCE_LOCK_FILENAME as SALES_LOCK_FILENAME
from meeting_minutes_bot.document import MinutesDocumentRenderer, MinutesTemplateError
from meeting_minutes_bot.listener import create_channel, handle_message, message_open_id, message_text
from meeting_minutes_bot.runtime import INSTANCE_LOCK_FILENAME, single_instance_lock
from meeting_minutes_bot.settings import MeetingBotSettings
from tests.meeting_minutes.helpers import build_service, people_directory


class FakeChannel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []

    async def send(self, chat_id: str, content: dict, options: dict) -> None:
        self.calls.append((chat_id, content, options))


def test_template_must_contain_every_enabled_person(project_tmp_dir: Path) -> None:
    template = project_tmp_dir / "bad.docx"
    document = Document()
    document.add_paragraph("{{ wu_aoxiang }}")
    document.save(template)

    with pytest.raises(MinutesTemplateError, match="yang_yilin"):
        MinutesDocumentRenderer(
            template_path=template,
            output_dir=project_tmp_dir / "output",
            people=people_directory(),
        )


def test_listener_extracts_normalized_and_raw_message_shapes() -> None:
    normalized = SimpleNamespace(sender_open_id="ou_one", content_text="正文")
    raw = SimpleNamespace(
        sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_two")),
        content='{"text": "另一段"}',
    )
    assert message_open_id(normalized) == "ou_one"
    assert message_text(normalized) == "正文"
    assert message_open_id(raw) == "ou_two"
    assert message_text(raw) == "另一段"


def test_listener_replies_to_text_and_rejects_files(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        service, repository = await build_service(project_tmp_dir)
        channel = FakeChannel()
        try:
            await handle_message(
                channel,
                service,
                SimpleNamespace(
                    chat_id="oc_chat",
                    message_id="om_text",
                    sender_open_id="ou_yang",
                    content_text="完成工作",
                    message_type="text",
                ),
            )
            await handle_message(
                channel,
                service,
                SimpleNamespace(
                    chat_id="oc_chat",
                    message_id="om_file",
                    sender_open_id="ou_yang",
                    message_type="file",
                ),
            )
            assert "已收到" in channel.calls[0][1]["text"]
            assert "仅支持直接发送文字" in channel.calls[1][1]["text"]
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_channel_disables_group_messages(project_tmp_dir: Path) -> None:
    settings = MeetingBotSettings(
        app_id="meeting-app",
        app_secret="secret",
        database_url="sqlite+aiosqlite:///:memory:",
        people_config_path=project_tmp_dir / "people.yaml",
        template_path=project_tmp_dir / "template.docx",
        data_dir=project_tmp_dir / "data",
        output_dir=project_tmp_dir / "data" / "output",
        log_dir=project_tmp_dir / "logs",
        timezone="Asia/Shanghai",
        max_text_length=20000,
        log_level="INFO",
    )
    channel = create_channel(settings)
    policy = channel.get_policy()
    assert policy.dm_policy == "open"
    assert policy.group_policy == "disabled"


def test_runtime_lock_is_distinct_from_sales_bot(project_tmp_dir: Path) -> None:
    assert INSTANCE_LOCK_FILENAME != SALES_LOCK_FILENAME
    sales_lock = project_tmp_dir / SALES_LOCK_FILENAME
    meeting_lock = project_tmp_dir / INSTANCE_LOCK_FILENAME
    from feishu_bot_listener import _single_instance_lock

    with _single_instance_lock(sales_lock):
        with single_instance_lock(meeting_lock):
            assert sales_lock.is_file() and meeting_lock.is_file()
