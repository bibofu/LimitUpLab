"""Shanghai-day boundaries shared by review sampling and budget accounting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def shanghai_day_start(now: datetime | None = None) -> datetime:
    """Return the current Shanghai day's start in UTC."""

    current = (now or datetime.now(timezone.utc)).astimezone(SHANGHAI)
    return current.replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).astimezone(timezone.utc)


def shanghai_next_day_start(now: datetime | None = None) -> datetime:
    """Return the next Shanghai day's start in UTC."""

    return shanghai_day_start(now) + timedelta(days=1)
