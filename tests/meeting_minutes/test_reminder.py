from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from meeting_minutes_bot.models import MeetingReminderRun, utc_now
from meeting_minutes_bot.people import Person
from meeting_minutes_bot.period import meeting_period
from meeting_minutes_bot.reminder import (
    SLOT_SUNDAY_17,
    SLOT_SUNDAY_20,
    ReminderScheduler,
    due_slots,
    reminder_message,
    seconds_until_next_slot,
)
from meeting_minutes_bot.repository import MeetingRepository
from meeting_minutes_bot.settings import MeetingBotConfigurationError, load_settings
from tests.meeting_minutes.helpers import build_service, people_directory


SHANGHAI = ZoneInfo("Asia/Shanghai")
# 单元测试固定验证正式周日双槽，不依赖当前联调默认值。
PROD_WEEKDAY = 6
PROD_SLOTS = (
    (SLOT_SUNDAY_17, time(17, 0)),
    (SLOT_SUNDAY_20, time(20, 0)),
)


class FakeSender:
    def __init__(self, *, fail_ids: set[str] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail_ids = fail_ids or set()

    async def send(self, to: str, message: dict, opts=None):
        self.calls.append((to, message))
        if to in self.fail_ids:
            raise RuntimeError("send failed")
        return type("Result", (), {"success": True})()


def sunday_at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 9, hour, minute, tzinfo=SHANGHAI)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 8, 8, 17, 0, tzinfo=SHANGHAI), ()),
        (sunday_at(16, 59), ()),
        (sunday_at(17, 0), (SLOT_SUNDAY_17,)),
        (sunday_at(19, 59), (SLOT_SUNDAY_17,)),
        (sunday_at(20, 0), (SLOT_SUNDAY_17, SLOT_SUNDAY_20)),
    ],
)
def test_due_slots_only_fire_on_sunday_after_times(moment, expected) -> None:
    assert (
        due_slots(
            moment,
            timezone="Asia/Shanghai",
            slots=PROD_SLOTS,
            weekday=PROD_WEEKDAY,
        )
        == expected
    )


def test_seconds_until_next_slot_caps_and_targets_sunday() -> None:
    friday = datetime(2026, 8, 7, 10, 0, tzinfo=SHANGHAI)
    delay = seconds_until_next_slot(
        friday,
        timezone="Asia/Shanghai",
        slots=PROD_SLOTS,
        weekday=PROD_WEEKDAY,
    )
    assert 1 <= delay <= 3600


def test_reminder_message_differs_by_slot() -> None:
    first = reminder_message(SLOT_SUNDAY_17, "2026-W32")
    second = reminder_message(SLOT_SUNDAY_20, "2026-W32")
    assert "尚未提交" in first and "再次提醒" not in first
    assert "再次提醒" in second and "仍未提交" in second


def test_reminder_enabled_setting_defaults_and_parses(project_tmp_dir: Path) -> None:
    base = {
        "MEETING_BOT_FEISHU_APP_ID": "meeting-app",
        "MEETING_BOT_FEISHU_APP_SECRET": "meeting-secret",
        "MEETING_BOT_PEOPLE_CONFIG_PATH": str(project_tmp_dir / "people.yaml"),
        "MEETING_BOT_TEMPLATE_PATH": str(project_tmp_dir / "template.docx"),
    }
    settings = load_settings(env_file=None, environ=base, project_root=project_tmp_dir)
    assert settings.reminder_enabled is True

    disabled = load_settings(
        env_file=None,
        environ={**base, "MEETING_BOT_REMINDER_ENABLED": "false"},
        project_root=project_tmp_dir,
    )
    assert disabled.reminder_enabled is False

    with pytest.raises(MeetingBotConfigurationError, match="MEETING_BOT_REMINDER_ENABLED"):
        load_settings(
            env_file=None,
            environ={**base, "MEETING_BOT_REMINDER_ENABLED": "maybe"},
            project_root=project_tmp_dir,
        )


def test_scheduler_skips_non_sunday(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        service, repository = await build_service(project_tmp_dir)
        sender = FakeSender()
        scheduler = ReminderScheduler(
            repository=repository,
            people=people_directory(),
            sender=sender,
            timezone="Asia/Shanghai",
            slots=PROD_SLOTS,
            weekday=PROD_WEEKDAY,
            clock=lambda: datetime(2026, 8, 8, 20, 0, tzinfo=SHANGHAI),
        )
        try:
            results = await scheduler.run_due()
            assert results == ()
            assert sender.calls == []
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_first_slot_only_notifies_missing_enabled_people(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        service, repository = await build_service(project_tmp_dir)
        await service.handle_text(
            message_id="msg-yang",
            sender_open_id="ou_yang",
            text="已提交内容",
            received_at=sunday_at(10),
        )
        sender = FakeSender()
        scheduler = ReminderScheduler(
            repository=repository,
            people=people_directory(),
            sender=sender,
            timezone="Asia/Shanghai",
            slots=PROD_SLOTS,
            weekday=PROD_WEEKDAY,
            clock=lambda: sunday_at(17, 5),
        )
        try:
            results = await scheduler.run_due()
            assert len(results) == 1
            assert results[0].slot == SLOT_SUNDAY_17
            assert results[0].attempted == 1
            assert results[0].sent == 1
            assert [open_id for open_id, _ in sender.calls] == ["ou_wu"]
            assert "尚未提交" in sender.calls[0][1]["text"]
            assert "停用员工" not in sender.calls[0][1]["text"]
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_second_slot_rechecks_missing_and_uses_followup_copy(
    project_tmp_dir: Path,
) -> None:
    async def scenario() -> None:
        service, repository = await build_service(project_tmp_dir)
        sender = FakeSender()
        people = people_directory()
        first = ReminderScheduler(
            repository=repository,
            people=people,
            sender=sender,
            timezone="Asia/Shanghai",
            slots=PROD_SLOTS,
            weekday=PROD_WEEKDAY,
            clock=lambda: sunday_at(17, 1),
        )
        try:
            await first.run_due()
            assert {open_id for open_id, _ in sender.calls} == {"ou_wu", "ou_yang"}

            await service.handle_text(
                message_id="msg-wu-late",
                sender_open_id="ou_wu",
                text="晚交内容",
                received_at=sunday_at(18),
            )
            sender.calls.clear()
            second = ReminderScheduler(
                repository=repository,
                people=people,
                sender=sender,
                timezone="Asia/Shanghai",
                slots=PROD_SLOTS,
                weekday=PROD_WEEKDAY,
                clock=lambda: sunday_at(20, 1),
            )
            results = await second.run_due()
            assert [result.slot for result in results] == [
                SLOT_SUNDAY_17,
                SLOT_SUNDAY_20,
            ]
            assert results[0].skipped is True
            assert results[1].sent == 1
            assert [open_id for open_id, _ in sender.calls] == ["ou_yang"]
            assert "再次提醒" in sender.calls[0][1]["text"]
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_completed_slot_is_not_resent(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        _, repository = await build_service(project_tmp_dir)
        sender = FakeSender()
        scheduler = ReminderScheduler(
            repository=repository,
            people=people_directory(),
            sender=sender,
            timezone="Asia/Shanghai",
            slots=PROD_SLOTS,
            weekday=PROD_WEEKDAY,
            clock=lambda: sunday_at(17, 10),
        )
        try:
            first = await scheduler.run_slot(SLOT_SUNDAY_17)
            second = await scheduler.run_slot(SLOT_SUNDAY_17)
            assert first.skipped is False and first.sent == 2
            assert second.skipped is True
            assert len(sender.calls) == 2
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_stale_processing_run_can_retry_only_current_missing(
    project_tmp_dir: Path,
) -> None:
    async def scenario() -> None:
        service, repository = await build_service(project_tmp_dir)
        period = meeting_period(sunday_at(17), "Asia/Shanghai")
        stale = utc_now() - timedelta(minutes=11)
        async with repository.sessions.begin() as session:
            session.add(
                MeetingReminderRun(
                    meeting_period=period,
                    slot=SLOT_SUNDAY_17,
                    status="PROCESSING",
                    created_at=stale,
                    updated_at=stale,
                )
            )
        await service.handle_text(
            message_id="msg-yang-done",
            sender_open_id="ou_yang",
            text="已交",
            received_at=sunday_at(16),
        )
        sender = FakeSender()
        scheduler = ReminderScheduler(
            repository=repository,
            people=people_directory(),
            sender=sender,
            timezone="Asia/Shanghai",
            slots=PROD_SLOTS,
            weekday=PROD_WEEKDAY,
            clock=lambda: sunday_at(17, 30),
        )
        try:
            result = await scheduler.run_slot(SLOT_SUNDAY_17)
            assert result.skipped is False
            assert result.sent == 1
            assert [open_id for open_id, _ in sender.calls] == ["ou_wu"]
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_fresh_processing_run_is_not_stolen(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        _, repository = await build_service(project_tmp_dir)
        period = meeting_period(sunday_at(17), "Asia/Shanghai")
        claim = await repository.claim_reminder_run(period=period, slot=SLOT_SUNDAY_17)
        assert claim is not None
        sender = FakeSender()
        scheduler = ReminderScheduler(
            repository=repository,
            people=people_directory(),
            sender=sender,
            timezone="Asia/Shanghai",
            slots=PROD_SLOTS,
            weekday=PROD_WEEKDAY,
            clock=lambda: sunday_at(17, 5),
        )
        try:
            result = await scheduler.run_slot(SLOT_SUNDAY_17)
            assert result.skipped is True
            assert sender.calls == []
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_single_send_failure_does_not_block_others(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        _, repository = await build_service(project_tmp_dir)
        sender = FakeSender(fail_ids={"ou_wu"})
        scheduler = ReminderScheduler(
            repository=repository,
            people=people_directory(),
            sender=sender,
            timezone="Asia/Shanghai",
            slots=PROD_SLOTS,
            weekday=PROD_WEEKDAY,
            clock=lambda: sunday_at(17, 0),
        )
        try:
            result = await scheduler.run_slot(SLOT_SUNDAY_17)
            assert result.attempted == 2
            assert result.sent == 1
            assert result.failed == 1
            run = await repository.reminder_run(period=result.period, slot=SLOT_SUNDAY_17)
            assert run is not None
            assert run.status == "COMPLETED"
            assert run.failed == 1
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_admin_in_enabled_people_also_receives_reminder(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        _, repository = await build_service(project_tmp_dir)
        people = people_directory()
        admin = Person(
            open_id="ou_admin",
            name="管理员",
            department="管理部",
            template_key="general_manager",
            section_order=1,
            sort_order=1,
            role="admin",
        )
        # Replace disabled person slot's template_key conflict by building new directory.
        from meeting_minutes_bot.people import PeopleDirectory

        directory = PeopleDirectory(
            people=(people.people[0], people.people[1], admin),
            admins=frozenset({"ou_admin"}),
        )
        sender = FakeSender()
        scheduler = ReminderScheduler(
            repository=repository,
            people=directory,
            sender=sender,
            timezone="Asia/Shanghai",
            slots=PROD_SLOTS,
            weekday=PROD_WEEKDAY,
            clock=lambda: sunday_at(17, 0),
        )
        try:
            result = await scheduler.run_slot(SLOT_SUNDAY_17)
            assert result.sent == 3
            assert {open_id for open_id, _ in sender.calls} == {
                "ou_wu",
                "ou_yang",
                "ou_admin",
            }
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_custom_slot_injection_for_manual_checks(project_tmp_dir: Path) -> None:
    async def scenario() -> None:
        _, repository = await build_service(project_tmp_dir)
        sender = FakeSender()
        now = sunday_at(12, 0)
        scheduler = ReminderScheduler(
            repository=repository,
            people=people_directory(),
            sender=sender,
            timezone="Asia/Shanghai",
            slots=((SLOT_SUNDAY_17, time(12, 0)),),
            weekday=PROD_WEEKDAY,
            clock=lambda: now,
        )
        try:
            results = await scheduler.run_due()
            assert len(results) == 1
            assert results[0].sent == 2
        finally:
            await repository.close()

    asyncio.run(scenario())
