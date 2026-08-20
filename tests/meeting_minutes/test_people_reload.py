"""Admin-triggered hot reload of the people YAML configuration."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from meeting_minutes_bot.document import MinutesDocumentRenderer
from meeting_minutes_bot.people import PeopleStore
from meeting_minutes_bot.reminder import ReminderScheduler
from meeting_minutes_bot.repository import MeetingRepository
from meeting_minutes_bot.service import RELOAD_COMMAND, MeetingMinutesService
from tests.meeting_minutes.helpers import TEST_TEMPLATE, build_service


NOW = datetime(2026, 8, 5, 12, 0)

INITIAL_PEOPLE_YAML = """
people:
  ou_wu:
    name: 吴傲翔
    department: 商务部-销售组
    template_key: wu_aoxiang
    section_order: 2
    sort_order: 1
admins:
  - ou_admin
""".strip()

REBOUND_PEOPLE_YAML = """
people:
  ou_new:
    name: 新员工
    department: 商务部-销售组
    template_key: wu_aoxiang
    section_order: 2
    sort_order: 1
  ou_yang:
    name: 杨意林
    department: 商务部-销售组
    template_key: yang_yilin
    section_order: 2
    sort_order: 2
admins:
  - ou_admin
""".strip()

MISSING_PLACEHOLDER_YAML = """
people:
  ou_wu:
    name: 吴傲翔
    department: 商务部-销售组
    template_key: not_in_template
    section_order: 2
    sort_order: 1
admins:
  - ou_admin
""".strip()


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def send(self, to: str, message: dict, opts=None):
        self.calls.append((to, message))
        return type("Result", (), {"success": True})()


async def build_store_service(
    root: Path,
) -> tuple[MeetingMinutesService, MeetingRepository, PeopleStore, Path]:
    """Build a service whose people come from a reloadable YAML file."""

    config_path = root / "people.yaml"
    config_path.write_text(INITIAL_PEOPLE_YAML, encoding="utf-8")
    store = PeopleStore.from_path(config_path)
    repository = MeetingRepository(
        f"sqlite+aiosqlite:///{(root / 'meeting.db').as_posix()}"
    )
    await repository.initialize()
    renderer = MinutesDocumentRenderer(
        template_path=TEST_TEMPLATE,
        output_dir=root / "output",
        people=store,
    )
    service = MeetingMinutesService(
        repository=repository,
        people=store,
        renderer=renderer,
        timezone="Asia/Shanghai",
        max_text_length=100,
    )
    return service, repository, store, config_path


def test_reload_command_requires_admin(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        service, repository, _, _ = await build_store_service(project_tmp_dir)
        try:
            result = await service.handle_text(
                message_id="om_r1",
                sender_open_id="ou_wu",
                text=RELOAD_COMMAND,
                received_at=NOW,
            )
            assert "没有执行" in result.text
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_reload_swaps_people_for_all_consumers(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        service, repository, store, config_path = await build_store_service(
            project_tmp_dir
        )
        sender = FakeSender()
        reminder = ReminderScheduler(
            repository=repository, people=store, sender=sender
        )
        try:
            config_path.write_text(REBOUND_PEOPLE_YAML, encoding="utf-8")
            result = await service.handle_text(
                message_id="om_r2",
                sender_open_id="ou_admin",
                text=RELOAD_COMMAND,
                received_at=NOW,
            )

            assert "人员配置已重载" in result.text
            assert "启用人数：2" in result.text
            # 旧 open_id 立即失效，新 open_id 立即可提交。
            rejected = await service.handle_text(
                message_id="om_r3", sender_open_id="ou_wu", text="内容", received_at=NOW
            )
            accepted = await service.handle_text(
                message_id="om_r4", sender_open_id="ou_new", text="内容", received_at=NOW
            )
            assert "尚未绑定" in rejected.text
            assert "已收到" in accepted.text
            # renderer 与 reminder 共享同一 store，提醒名单同步更新。
            assert service.renderer.people is store.directory
            missing = await reminder.missing_people("2026-W32")
            assert {person.open_id for person in missing} == {"ou_yang"}
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_reload_keeps_old_config_on_invalid_yaml(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        service, repository, store, config_path = await build_store_service(
            project_tmp_dir
        )
        try:
            before = store.directory
            config_path.write_text("people: [not, a, mapping]", encoding="utf-8")
            result = await service.handle_text(
                message_id="om_r5",
                sender_open_id="ou_admin",
                text=RELOAD_COMMAND,
                received_at=NOW,
            )
            assert "人员配置重载失败" in result.text
            assert store.directory is before
            still_ok = await service.handle_text(
                message_id="om_r6", sender_open_id="ou_wu", text="内容", received_at=NOW
            )
            assert "已收到" in still_ok.text
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_reload_rejects_enabled_person_missing_placeholder(
    project_tmp_dir: Path,
) -> None:
    async def scenario() -> None:
        service, repository, store, config_path = await build_store_service(
            project_tmp_dir
        )
        try:
            before = store.directory
            config_path.write_text(MISSING_PLACEHOLDER_YAML, encoding="utf-8")
            result = await service.handle_text(
                message_id="om_r7",
                sender_open_id="ou_admin",
                text=RELOAD_COMMAND,
                received_at=NOW,
            )
            assert "人员配置重载失败" in result.text
            assert "not_in_template" in result.text
            assert store.directory is before
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_reload_without_config_path_reports_error(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        # helpers.build_service 直接传入 PeopleDirectory，没有 YAML 路径。
        service, repository = await build_service(project_tmp_dir)
        try:
            result = await service.handle_text(
                message_id="om_r8",
                sender_open_id="ou_admin",
                text=RELOAD_COMMAND,
                received_at=NOW,
            )
            assert "人员配置重载失败" in result.text
            assert "路径未设置" in result.text
        finally:
            await repository.close()

    asyncio.run(scenario())
