"""Shared typed values for structured Agent tool arguments."""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo


def current_query_reference_time() -> datetime:
    """Use the exchange timezone even when the server runs outside China."""
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def current_query_reference_date() -> date:
    return current_query_reference_time().date()


MarketSegment = Literal["main_board", "chinext", "star_market", "beijing"]
EventStatus = Literal["closed", "unsealed", "intraday_broken", "all"]
MarketEventType = Literal[
    "limit_up", "limit_down", "unsealed", "intraday_broken"
]
SortField = Literal[
    "symbol",
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
# "炸板" alone is ambiguous: it can mean "touched but did not close at the
# limit" (unsealed) or "opened at least once" (intraday_broken, including
# resealed). Migrate the legacy words to explicit states instead of keeping
# one overloaded token.
_STATUS_ALIASES: dict[str, EventStatus] = {
    "closed": "closed", "sealed": "closed", "封板": "closed", "收盘封住": "closed",
    "unsealed": "unsealed", "failed": "unsealed", "broken": "unsealed",
    "炸板": "unsealed", "未回封": "unsealed", "炸板未回封": "unsealed",
    "intraday_broken": "intraday_broken", "broken_intraday": "intraday_broken",
    "opened": "intraday_broken", "开板": "intraday_broken", "曾开板": "intraday_broken",
    "all": "all", "全部": "all",
}
_MARKET_EVENT_TYPE_ALIASES: dict[str, MarketEventType] = {
    "limit_up": "limit_up", "up_limit": "limit_up", "涨停": "limit_up",
    "limit_down": "limit_down", "down_limit": "limit_down", "跌停": "limit_down",
    "unsealed": "unsealed", "broken_board": "unsealed", "failed_limit_up": "unsealed",
    "failed": "unsealed", "炸板": "unsealed", "未回封": "unsealed",
    "intraday_broken": "intraday_broken", "broken_intraday": "intraday_broken",
    "opened": "intraday_broken", "开板": "intraday_broken",
}
_SORT_ALIASES: dict[str, SortField] = {
    "symbol": "symbol", "code": "symbol", "股票代码": "symbol", "代码": "symbol",
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
