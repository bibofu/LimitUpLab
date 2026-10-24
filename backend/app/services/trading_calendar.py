"""Resolve the latest closed A-share trading day from a verified calendar."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.collectors.trading_calendar_collector import (
    TradingCalendarSnapshot,
    collect_a_share_calendar,
)
from app.models import MarketCalendarFacts


SHANGHAI = ZoneInfo("Asia/Shanghai")
CLOSE_TIME = time(15, 0)
PRE_OPEN_TIME = time(9, 30)
CALENDAR_LOOKBACK_DAYS = 60
CALENDAR_LOOKAHEAD_DAYS = 10
COVERAGE_WARNING = (
    "交易日历覆盖不足，无法确认今天是否为交易日；"
    "latest_closed_trade_date仅代表今天之前最近一个已知交易日。"
)

CalendarLoader = Callable[[date, date], TradingCalendarSnapshot]


def build_market_calendar_facts(
    *,
    now: datetime | None = None,
    loader: CalendarLoader = collect_a_share_calendar,
    local_data_through: date | None = None,
) -> MarketCalendarFacts:
    """Return the latest closed session without treating local data as a calendar."""

    reference = _as_shanghai(now)
    today = reference.date()
    query_start = today - timedelta(days=CALENDAR_LOOKBACK_DAYS)
    query_end = today + timedelta(days=CALENDAR_LOOKAHEAD_DAYS)
    try:
        snapshot = loader(query_start, query_end)
    except Exception as error:  # noqa: BLE001 - report the gap instead of guessing
        return MarketCalendarFacts(
            as_of=reference,
            today=today,
            latest_closed_trade_date=None,
            previous_trade_date=None,
            next_trade_date=None,
            session_status="unverified",
            close_time=_close_time_text(),
            calendar_source="unavailable",
            calendar_verified=False,
            calendar_coverage_start=None,
            calendar_coverage_end=None,
            local_data_through=local_data_through,
            data_missing=[
                f"交易日历不可用：{type(error).__name__}；未使用本地事件日期推断交易日。"
            ],
        )

    dates = tuple(sorted({
        day for day in snapshot.dates if query_start <= day <= query_end
    }))
    today_is_trade_date = today in dates
    calendar_verified = today_is_trade_date or snapshot.coverage_verified
    session_status = _session_status(
        reference=reference,
        today=today,
        today_is_trade_date=today_is_trade_date,
        calendar_verified=calendar_verified,
    )
    latest = _latest_closed_date(
        reference=reference,
        today=today,
        dates=dates,
        today_is_trade_date=today_is_trade_date,
    )
    warnings = list(snapshot.warnings)
    if not calendar_verified:
        warnings.append(COVERAGE_WARNING)
    if not dates:
        warnings.append("交易日历未返回任何日期。")

    return MarketCalendarFacts(
        as_of=reference,
        today=today,
        latest_closed_trade_date=latest,
        previous_trade_date=_previous_trade_date(dates, latest),
        next_trade_date=(
            _next_trade_date(dates, latest, today)
            if calendar_verified
            else None
        ),
        session_status=session_status,
        close_time=_close_time_text(),
        calendar_source=snapshot.source,
        calendar_verified=calendar_verified,
        calendar_coverage_start=dates[0] if dates else None,
        calendar_coverage_end=dates[-1] if dates else None,
        local_data_through=local_data_through,
        data_missing=list(dict.fromkeys(warnings)),
    )


def _as_shanghai(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(SHANGHAI)
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI)


def _session_status(
    *,
    reference: datetime,
    today: date,
    today_is_trade_date: bool,
    calendar_verified: bool,
) -> str:
    if not calendar_verified:
        return "unverified"
    if not today_is_trade_date:
        return "non_trading_day"
    current = reference.time()
    if current < PRE_OPEN_TIME:
        return "pre_open"
    if current < CLOSE_TIME:
        return "open"
    return "closed"


def _latest_closed_date(
    *,
    reference: datetime,
    today: date,
    dates: tuple[date, ...],
    today_is_trade_date: bool,
) -> date | None:
    if today_is_trade_date and reference.time() >= CLOSE_TIME:
        return today
    prior = [day for day in dates if day < today]
    return max(prior) if prior else None


def _previous_trade_date(
    dates: tuple[date, ...], latest: date | None,
) -> date | None:
    if latest is None:
        return None
    prior = [day for day in dates if day < latest]
    return max(prior) if prior else None


def _next_trade_date(
    dates: tuple[date, ...], latest: date | None, today: date,
) -> date | None:
    if latest is not None:
        following = [day for day in dates if day > latest]
        return min(following) if following else None
    following = [day for day in dates if day >= today]
    return min(following) if following else None


def _close_time_text() -> str:
    return CLOSE_TIME.strftime("%H:%M")
