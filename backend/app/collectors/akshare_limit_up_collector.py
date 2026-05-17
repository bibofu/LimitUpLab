from datetime import date, time
from math import isnan
from typing import Any

import akshare as ak

from app.models import LimitUpEvent


def parse_akshare_trade_date(value: str) -> date:
    if len(value) != 8 or not value.isdigit():
        raise ValueError("date must use YYYYMMDD format")
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def collect_limit_up_events(trade_date: str) -> list[LimitUpEvent]:
    parsed_date = parse_akshare_trade_date(trade_date)
    events_by_key: dict[tuple[date, str], LimitUpEvent] = {}

    for event in _collect_closed_limit_up_events(parsed_date, trade_date):
        events_by_key[(event.trade_date, event.symbol)] = event

    for event in _collect_failed_limit_up_events(parsed_date, trade_date):
        events_by_key.setdefault((event.trade_date, event.symbol), event)

    return sorted(
        events_by_key.values(),
        key=lambda event: (
            event.trade_date,
            event.closed_limit,
            event.board_height,
            event.first_limit_time,
        ),
        reverse=True,
    )


def _collect_closed_limit_up_events(parsed_date: date, trade_date: str) -> list[LimitUpEvent]:
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
                board_height=_parse_board_height(row["涨停统计"]),
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
    text = str(value).strip().zfill(6)
    if len(text) != 6 or not text.isdigit():
        return time(0, 0)
    return time(int(text[:2]), int(text[2:4]), int(text[4:6]))


def _parse_board_height(value: Any) -> int:
    text = str(value).strip()
    if "/" in text:
        _, height = text.split("/", maxsplit=1)
        return max(_safe_int(height), 1)
    return max(_safe_int(text), 1)


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
