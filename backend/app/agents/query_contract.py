"""Shared typed values for structured Agent tool arguments."""

from datetime import date
from typing import Literal


def current_query_reference_date() -> date:
    return date.today()


MarketSegment = Literal["main_board", "chinext", "star_market", "beijing"]
EventStatus = Literal["closed", "failed", "broken_intraday", "all"]
MarketEventType = Literal["limit_up", "limit_down", "broken_board"]
SortField = Literal[
    "board_height",
    "first_limit_time",
    "amount",
    "turnover_rate",
    "break_count",
]
SortOrder = Literal["asc", "desc"]


MARKET_SEGMENT_PREFIXES: dict[str, tuple[str, ...]] = {
    "main_board": ("000", "001", "002", "003", "600", "601", "603", "605"),
    "chinext": ("300", "301"),
    "star_market": ("688", "689"),
    "beijing": ("4", "8", "92"),
}
MARKET_SEGMENT_LABELS = {
    "main_board": "沪深主板",
    "chinext": "创业板",
    "star_market": "科创板",
    "beijing": "北交所",
}
_MARKET_SEGMENT_ALIASES = {
    "main": "main_board", "main_board": "main_board", "主板": "main_board", "沪深主板": "main_board",
    "chinext": "chinext", "创业板": "chinext",
    "star": "star_market", "star_market": "star_market", "科创板": "star_market",
    "beijing": "beijing", "北交所": "beijing", "北证": "beijing",
}
_STATUS_ALIASES: dict[str, EventStatus] = {
    "closed": "closed", "封板": "closed",
    "failed": "failed", "broken": "failed", "炸板": "failed",
    "broken_intraday": "broken_intraday", "opened": "broken_intraday", "开板": "broken_intraday",
    "all": "all", "全部": "all",
}
_MARKET_EVENT_TYPE_ALIASES: dict[str, MarketEventType] = {
    "limit_up": "limit_up", "up_limit": "limit_up", "涨停": "limit_up",
    "limit_down": "limit_down", "down_limit": "limit_down", "跌停": "limit_down",
    "broken_board": "broken_board", "failed_limit_up": "broken_board", "炸板": "broken_board",
}
_SORT_ALIASES: dict[str, SortField] = {
    "board_height": "board_height", "height": "board_height", "板数": "board_height",
    "first_limit_time": "first_limit_time", "seal_time": "first_limit_time", "封板时间": "first_limit_time",
    "amount": "amount", "成交额": "amount",
    "turnover_rate": "turnover_rate", "换手率": "turnover_rate",
    "break_count": "break_count", "炸板次数": "break_count",
}


def normalize_market_segment(value: object) -> MarketSegment | None:
    normalized = str(value or "").strip().lower()
    if not normalized or normalized == "all":
        return None
    return _MARKET_SEGMENT_ALIASES.get(normalized)  # type: ignore[return-value]


def normalize_market_event_type(value: object) -> MarketEventType | None:
    return _MARKET_EVENT_TYPE_ALIASES.get(str(value or "").strip().lower())


def normalize_event_status(value: object) -> EventStatus | None:
    return _STATUS_ALIASES.get(str(value or "").strip().lower())


def normalize_sort_field(value: object) -> SortField | None:
    return _SORT_ALIASES.get(str(value or "").strip().lower())


def normalize_sort_order(value: object) -> SortOrder | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"asc", "ascending", "升序"}:
        return "asc"
    if normalized in {"desc", "descending", "降序"}:
        return "desc"
    return None
