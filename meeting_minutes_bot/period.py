"""Meeting-period calculations."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def local_datetime(value: datetime | None, timezone: str) -> datetime:
    zone = ZoneInfo(timezone)
    if value is None:
        return datetime.now(zone)
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def meeting_period(value: datetime | None, timezone: str = "Asia/Shanghai") -> str:
    local = local_datetime(value, timezone)
    year, week, _ = local.isocalendar()
    return f"{year}-W{week:02d}"


def period_label(period: str) -> str:
    year, week = period.split("-W", 1)
    return f"{year}年第{int(week)}周"
