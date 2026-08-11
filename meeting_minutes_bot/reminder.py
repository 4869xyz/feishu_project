"""Sunday submission reminders for people still missing weekly minutes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
import logging
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .people import PeopleDirectory, PeopleStore, Person, ensure_store
from .period import meeting_period, period_label
from .repository import MeetingRepository


LOGGER = logging.getLogger(__name__)
SLOT_SUNDAY_17 = "sunday_17"
SLOT_SUNDAY_20 = "sunday_20"
SLOT_SATURDAY_10 = "saturday_10"

# 正式生产配置（测完后改回）：
REMINDER_WEEKDAY = 6  # Monday=0 ... Sunday=6
DEFAULT_SLOTS = ((SLOT_SUNDAY_17, time(17, 0)), (SLOT_SUNDAY_20, time(20, 0)))

# # 临时联调：周六 10:00 提醒未提交人员（2026-08-08 测试用）
# REMINDER_WEEKDAY = 5  # Saturday
# DEFAULT_SLOTS: tuple[tuple[str, time], ...] = (
#     (SLOT_SATURDAY_10, time(10, 0)),
# )
POLL_SECONDS = 30.0
MAX_SLEEP_SECONDS = 60 * 60


class ReminderSender(Protocol):
    async def send(self, to: str, message: dict[str, Any], opts: Any = None) -> Any:
        """Send a proactive outbound message to a Feishu open_id."""


@dataclass(frozen=True, slots=True)
class ReminderSlotResult:
    period: str
    slot: str
    attempted: int
    sent: int
    failed: int
    skipped: bool = False


def reminder_message(slot: str, period: str) -> str:
    label = period_label(period)
    if slot == SLOT_SUNDAY_20:
        return (
            f"【周例会纪要再次提醒】{label}你仍未提交周例会纪要，"
            "请尽快私聊本机器人提交。"
        )
    return (
        f"【周例会纪要提醒】{label}你尚未提交周例会纪要。"
        "请尽快私聊本机器人发送文字或受支持附件。"
    )


def _localize(now: datetime, timezone: str) -> datetime:
    zone = ZoneInfo(timezone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def due_slots(
    now: datetime,
    *,
    timezone: str,
    slots: tuple[tuple[str, time], ...] = DEFAULT_SLOTS,
    weekday: int = REMINDER_WEEKDAY,
) -> tuple[str, ...]:
    """Return reminder slot names that have already become due for the local day."""

    local = _localize(now, timezone)
    if local.weekday() != weekday:
        return ()
    current_time = local.time().replace(tzinfo=None)
    return tuple(
        slot_name for slot_name, slot_time in slots if current_time >= slot_time
    )


def seconds_until_next_slot(
    now: datetime,
    *,
    timezone: str,
    slots: tuple[tuple[str, time], ...] = DEFAULT_SLOTS,
    weekday: int = REMINDER_WEEKDAY,
) -> float:
    """Sleep budget until the next configured reminder slot."""

    local = _localize(now, timezone)
    zone = ZoneInfo(timezone)
    candidates: list[datetime] = []
    for day_offset in range(0, 8):
        day = (local + timedelta(days=day_offset)).date()
        if day.weekday() != weekday:
            continue
        for _, slot_time in slots:
            candidate = datetime.combine(day, slot_time, tzinfo=zone)
            if candidate > local:
                candidates.append(candidate)
    if not candidates:
        return float(MAX_SLEEP_SECONDS)
    delay = (min(candidates) - local).total_seconds()
    return max(1.0, min(delay, float(MAX_SLEEP_SECONDS)))


class ReminderScheduler:
    """Background reminder worker owned by the meeting-minutes bot."""

    def __init__(
        self,
        *,
        repository: MeetingRepository,
        people: PeopleDirectory | PeopleStore,
        sender: ReminderSender,
        timezone: str = "Asia/Shanghai",
        slots: tuple[tuple[str, time], ...] = DEFAULT_SLOTS,
        weekday: int = REMINDER_WEEKDAY,
        poll_seconds: float = POLL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self._people_store = ensure_store(people)
        self.sender = sender
        self.timezone = timezone
        self.slots = slots
        self.weekday = weekday
        self.poll_seconds = poll_seconds
        self._clock = clock or (lambda: datetime.now(ZoneInfo(timezone)))

    @property
    def people(self) -> PeopleDirectory:
        return self._people_store.directory

    def _now(self) -> datetime:
        return _localize(self._clock(), self.timezone)

    async def missing_people(self, period: str) -> tuple[Person, ...]:
        submitted = await self.repository.submitted_open_ids(period)
        return tuple(
            person
            for person in self.people.enabled_people
            if person.open_id not in submitted
        )

    async def run_slot(
        self, slot: str, *, now: datetime | None = None
    ) -> ReminderSlotResult:
        current = now or self._now()
        period = meeting_period(current, self.timezone)
        reservation = await self.repository.claim_reminder_run(
            period=period,
            slot=slot,
        )
        if reservation is None:
            return ReminderSlotResult(
                period=period, slot=slot, attempted=0, sent=0, failed=0, skipped=True
            )

        recipients = await self.missing_people(period)
        text = reminder_message(slot, period)
        attempted = sent = failed = 0
        for person in recipients:
            attempted += 1
            try:
                result = await self.sender.send(person.open_id, {"text": text})
                if getattr(result, "success", True) is False:
                    raise RuntimeError(getattr(result, "error", None) or "发送失败")
                sent += 1
            except Exception:
                failed += 1
                LOGGER.exception(
                    "周日提醒发送失败：period=%s slot=%s open_id=%s",
                    period,
                    slot,
                    person.open_id,
                )

        await self.repository.finish_reminder_run(
            reservation.id,
            status="COMPLETED",
            attempted=attempted,
            sent=sent,
            failed=failed,
        )
        LOGGER.info(
            "周日提醒完成：period=%s slot=%s attempted=%d sent=%d failed=%d",
            period,
            slot,
            attempted,
            sent,
            failed,
        )
        return ReminderSlotResult(
            period=period,
            slot=slot,
            attempted=attempted,
            sent=sent,
            failed=failed,
        )

    async def run_due(
        self, *, now: datetime | None = None
    ) -> tuple[ReminderSlotResult, ...]:
        current = now or self._now()
        results: list[ReminderSlotResult] = []
        for slot_name in due_slots(
            current,
            timezone=self.timezone,
            slots=self.slots,
            weekday=self.weekday,
        ):
            results.append(await self.run_slot(slot_name, now=current))
        return tuple(results)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("提醒调度出现未预期异常，将在下一周期重试")
            delay = seconds_until_next_slot(
                self._now(),
                timezone=self.timezone,
                slots=self.slots,
                weekday=self.weekday,
            )
            await asyncio.sleep(min(delay, self.poll_seconds))
