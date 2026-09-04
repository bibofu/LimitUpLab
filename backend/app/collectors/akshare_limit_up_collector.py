"""AKShare-backed collector for daily A-share limit-up events.

The collector maps Eastmoney limit-up pools into the internal `LimitUpEvent`
shape. Closed limit-up events are imported first; failed/open-board events are
then added only when they are not already present for the same date and symbol.
"""

from dataclasses import dataclass
from datetime import date, time
from math import isnan
from typing import Any, Literal

import akshare as ak

from app.models import LimitUpEvent
from app.collectors.network import without_proxy


@dataclass(frozen=True)
class LimitUpCollectionResult:
    """Explicit result for the two-source AKShare limit-up collection."""

    status: Literal["ok", "empty", "partial", "error"]
    data_fresh: bool | None
    source_errors: tuple[str, ...]
    payload: list[LimitUpEvent]


def parse_akshare_trade_date(value: str) -> date:
    """Parse AKShare's `YYYYMMDD` trading-date argument into a date object."""

    if len(value) != 8 or not value.isdigit():
        raise ValueError("date must use YYYYMMDD format")
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def collect_limit_up_events(trade_date: str) -> LimitUpCollectionResult:
    """Collect both pools without conflating an empty pool with source failure."""

    parsed_date = parse_akshare_trade_date(trade_date)
    events_by_key: dict[tuple[date, str], LimitUpEvent] = {}

    source_errors: list[str] = []
    successful_sources = 0
    with without_proxy():
        try:
            closed_events = _collect_closed_limit_up_events(parsed_date, trade_date)
            successful_sources += 1
        except Exception as error:  # noqa: BLE001 - preserve cross-source partial data
            closed_events = []
            source_errors.append(f"akshare.closed_limit_pool: {error}")
        try:
            failed_events = _collect_failed_limit_up_events(parsed_date, trade_date)
            successful_sources += 1
        except Exception as error:  # noqa: BLE001 - preserve cross-source partial data
            failed_events = []
            source_errors.append(f"akshare.failed_limit_pool: {error}")

    for event in closed_events:
        events_by_key[(event.trade_date, event.symbol)] = event
    for event in failed_events:
        events_by_key.setdefault((event.trade_date, event.symbol), event)

    events = sorted(
        events_by_key.values(),
        key=lambda event: (
            event.trade_date,
            event.closed_limit,
            event.board_height,
            event.first_limit_time,
        ),
        reverse=True,
    )
    if successful_sources == 0:
        status = "error"
    elif source_errors:
        status = "partial"
    elif not events:
        status = "empty"
    else:
        status = "ok"
    return LimitUpCollectionResult(
        status=status,
        data_fresh=successful_sources > 0,
        source_errors=tuple(source_errors),
        payload=events,
    )


def _collect_closed_limit_up_events(parsed_date: date, trade_date: str) -> list[LimitUpEvent]:
    """Collect stocks that closed at limit-up from Eastmoney's limit-up pool."""

    frame = ak.stock_zt_pool_em(date=trade_date)
    events: list[LimitUpEvent] = []

    for _, row in frame.iterrows():
        break_count = _safe_int(row["炸板次数"])
        events.append(
            LimitUpEvent(
                symbol=str(row["代码"]),
                name=str(row["名称"]),
                trade_date=parsed_date,
                first_limit_time=_parse_hhmmss(row["首次封板时间"]),
                last_limit_time=_parse_hhmmss(row["最后封板时间"]),
                seal_count=max(break_count + 1, 1),
                break_count=break_count,
                closed_limit=True,
                board_height=max(_safe_int(row["连板数"]), 1),
                amount=_safe_float(row["成交额"]),
                turnover_rate=_safe_float(row["换手率"]),
                industry=str(row["所属行业"]),
                concept="",
                next_open_pct=0.0,
                next_high_pct=0.0,
                next_close_pct=0.0,
                three_day_return_pct=0.0,
                five_day_return_pct=0.0,
                continued_next_day=False,
            )
        )

    return events


def _collect_failed_limit_up_events(parsed_date: date, trade_date: str) -> list[LimitUpEvent]:
    """Collect stocks from Eastmoney's failed/open-board observation pool."""

    frame = ak.stock_zt_pool_zbgc_em(date=trade_date)
    events: list[LimitUpEvent] = []

    for _, row in frame.iterrows():
        break_count = _safe_int(row["炸板次数"])
        first_limit_time = _parse_hhmmss(row["首次封板时间"])
        events.append(
            LimitUpEvent(
                symbol=str(row["代码"]),
                name=str(row["名称"]),
                trade_date=parsed_date,
                first_limit_time=first_limit_time,
                last_limit_time=first_limit_time,
                seal_count=max(break_count, 1),
                break_count=break_count,
                closed_limit=False,
                # `涨停统计` such as `10/6` is a rolling-day statistic, not a
                # consecutive board height. An unclosed event has no completed board.
                board_height=1,
                amount=_safe_float(row["成交额"]),
                turnover_rate=_safe_float(row["换手率"]),
                industry=str(row["所属行业"]),
                concept="",
                next_open_pct=0.0,
                next_high_pct=0.0,
                next_close_pct=0.0,
                three_day_return_pct=0.0,
                five_day_return_pct=0.0,
                continued_next_day=False,
            )
        )

    return events


def _parse_hhmmss(value: Any) -> time:
    """Parse AKShare time values such as `092500` into `datetime.time`."""

    text = str(value).strip().zfill(6)
    if len(text) != 6 or not text.isdigit():
        return time(0, 0)
    return time(int(text[:2]), int(text[2:4]), int(text[4:6]))


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if isnan(number) else number
