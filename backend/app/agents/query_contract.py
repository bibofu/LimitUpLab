"""Canonical query contract for local limit-up event questions."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal


QUERY_CONTRACT_VERSION = "limit-up-query-v2"

MarketSegment = Literal["main_board", "chinext", "star_market", "beijing"]
EventStatus = Literal["closed", "failed", "broken_intraday", "all"]
ResultMode = Literal["list", "count", "summary", "ranking"]
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
    "main": "main_board",
    "main_board": "main_board",
    "主板": "main_board",
    "沪深主板": "main_board",
    "chinext": "chinext",
    "创业板": "chinext",
    "star": "star_market",
    "star_market": "star_market",
    "科创板": "star_market",
    "beijing": "beijing",
    "北交所": "beijing",
    "北证": "beijing",
}

_STATUS_ALIASES: dict[str, EventStatus] = {
    "closed": "closed",
    "封板": "closed",
    "failed": "failed",
    "broken": "failed",
    "炸板": "failed",
    "broken_intraday": "broken_intraday",
    "opened": "broken_intraday",
    "开板": "broken_intraday",
    "all": "all",
    "全部": "all",
}

_SORT_ALIASES: dict[str, SortField] = {
    "board_height": "board_height",
    "height": "board_height",
    "板数": "board_height",
    "first_limit_time": "first_limit_time",
    "seal_time": "first_limit_time",
    "封板时间": "first_limit_time",
    "amount": "amount",
    "成交额": "amount",
    "turnover_rate": "turnover_rate",
    "换手率": "turnover_rate",
    "break_count": "break_count",
    "炸板次数": "break_count",
}


@dataclass(frozen=True)
class LimitUpQueryContract:
    """One validated interpretation shared by planner, policy and tool execution."""

    version: str = QUERY_CONTRACT_VERSION
    trade_date: date | None = None
    board_height: int | None = None
    min_board_height: int | None = None
    highest_only: bool = False
    market: MarketSegment | None = None
    query: str | None = None
    event_status: EventStatus = "closed"
    result_mode: ResultMode = "list"
    sort_by: SortField = "board_height"
    sort_order: SortOrder = "desc"
    limit: int = 30
    exhaustive: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize the contract for traces, prompts and eval assertions."""

        payload = asdict(self)
        payload["trade_date"] = self.trade_date.isoformat() if self.trade_date else None
        return payload

    def to_tool_arguments(self) -> dict[str, Any]:
        """Return canonical arguments accepted by the limit-up event tool."""

        return {
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "board_height": self.board_height,
            "min_board_height": self.min_board_height,
            "highest_only": self.highest_only,
            "market": self.market,
            "query": self.query,
            "event_status": self.event_status,
            "sort_by": self.sort_by,
            "sort_order": self.sort_order,
            "limit": self.limit,
        }


def build_limit_up_query_contract(
    message: str,
    *,
    request_trade_date: date | None = None,
    planner_arguments: dict[str, Any] | None = None,
) -> LimitUpQueryContract:
    """Merge user text, page context and planner arguments into one contract.

    Explicit user wording wins over planner guesses. Planner values remain useful for
    open-ended topic filters that cannot be extracted safely from arbitrary Chinese.
    """

    planner = planner_arguments or {}
    explicit_trade_date = extract_trade_date(message)
    # Date selection is user-controlled. A planner must not turn an undated
    # question into a stale historical query; None lets the tool use its latest data.
    trade_date = explicit_trade_date or request_trade_date

    explicit_board_height, explicit_min_board_height = extract_board_filters(message)
    board_height = (
        explicit_board_height
        if explicit_board_height is not None
        else _bounded_int(planner.get("board_height"), minimum=1, maximum=20)
    )
    min_board_height = (
        explicit_min_board_height
        if explicit_min_board_height is not None
        else _bounded_int(planner.get("min_board_height"), minimum=1, maximum=20)
    )
    if board_height is not None:
        min_board_height = None

    explicit_market = extract_market_segment(message)
    market = explicit_market or normalize_market_segment(planner.get("market"))
    query = extract_topic_query(message) or _clean_query(planner.get("query"))

    explicit_status = extract_event_status(message)
    event_status = explicit_status or normalize_event_status(
        planner.get("event_status") or planner.get("status")
    )
    if event_status is None:
        if _as_bool(planner.get("broken_only")):
            event_status = "broken_intraday"
        elif _as_bool(planner.get("closed_only")) is False:
            event_status = "all"
        else:
            event_status = "closed"

    result_mode = extract_result_mode(message) or normalize_result_mode(
        planner.get("result_mode")
    )
    sort_by, sort_order = extract_sort(message)
    sort_by = sort_by or normalize_sort_field(planner.get("sort_by")) or "board_height"
    sort_order = (
        sort_order
        or normalize_sort_order(planner.get("sort_order"))
        or _default_sort_order(sort_by)
    )
    exhaustive = looks_like_exhaustive_request(message)
    explicit_limit = extract_result_limit(message)
    planner_limit = _bounded_int(planner.get("limit"), minimum=1, maximum=100)
    limit = explicit_limit or planner_limit or 30
    if exhaustive or result_mode == "count":
        limit = 100

    highest_only = "最高板" in message or _as_bool(planner.get("highest_only")) is True
    if highest_only:
        board_height = None
        min_board_height = None
        sort_by = "board_height"
        sort_order = "desc"
        result_mode = "ranking"

    return LimitUpQueryContract(
        trade_date=trade_date,
        board_height=board_height,
        min_board_height=min_board_height,
        highest_only=highest_only,
        market=market,
        query=query,
        event_status=event_status,
        result_mode=result_mode or "list",
        sort_by=sort_by,
        sort_order=sort_order,
        limit=max(1, min(limit, 100)),
        exhaustive=exhaustive,
    )


def extract_trade_date(message: str) -> date | None:
    """Extract a full or shorthand date from common Chinese expressions."""

    normalized = message.strip()
    full_match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", normalized)
    if full_match:
        return _safe_date(*(int(part) for part in full_match.groups()))
    short_match = re.search(r"(?<!\d)(\d{1,2})[./月](\d{1,2})(?:日|号)?", normalized)
    if short_match:
        month, day = (int(part) for part in short_match.groups())
        return _safe_date(date.today().year, month, day)
    return None


def extract_board_filters(message: str) -> tuple[int | None, int | None]:
    """Extract exact board height or the lower bound for all continued boards."""

    if "首板" in message or "一进二候选" in message:
        return 1, None
    numeric = re.search(r"(?<!\d)(\d{1,2})\s*(?:连)?板", message)
    if numeric:
        return int(numeric.group(1)), None
    chinese_numbers = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    for label, height in chinese_numbers.items():
        if f"{label}板" in message or f"{label}连板" in message:
            return height, None
    if "连板" in message or "接力梯队" in message:
        return None, 2
    return None, None


def extract_market_segment(message: str) -> MarketSegment | None:
    """Extract an explicitly named A-share board segment."""

    for terms, segment in (
        (("创业板",), "chinext"),
        (("科创板",), "star_market"),
        (("北交所", "北证"), "beijing"),
        (("沪深主板", "主板"), "main_board"),
    ):
        if any(term in message for term in terms):
            return segment
    return None


def normalize_market_segment(value: object) -> MarketSegment | None:
    """Normalize planner and user market aliases."""

    normalized = str(value or "").strip().lower()
    if not normalized or normalized == "all":
        return None
    return _MARKET_SEGMENT_ALIASES.get(normalized)  # type: ignore[return-value]


def extract_event_status(message: str) -> EventStatus | None:
    """Distinguish closed boards, failed boards and intraday-opened boards."""

    if any(term in message for term in ("涨停和炸板", "全部涨停事件", "所有涨停事件")):
        return "all"
    if any(term in message for term in ("炸板次数", "开板次数")):
        return (
            "closed"
            if any(term in message for term in ("涨停", "首板", "连板"))
            else "broken_intraday"
        )
    if any(term in message for term in ("炸板票", "炸板股", "炸板名单", "未封住", "封板失败")):
        return "failed"
    if message.strip().endswith("炸板") or "哪些炸板" in message:
        return "failed"
    if any(term in message for term in ("曾开板", "开过板", "炸过板", "盘中开板")):
        return "broken_intraday"
    if any(term in message for term in ("涨停", "首板", "连板", "二板", "三板", "最高板")):
        return "closed"
    return None


def normalize_event_status(value: object) -> EventStatus | None:
    """Normalize an LLM-provided event status."""

    normalized = str(value or "").strip().lower()
    return _STATUS_ALIASES.get(normalized)


def extract_result_mode(message: str) -> ResultMode | None:
    """Extract whether the user wants a list, count, summary or ranking."""

    if any(term in message for term in ("多少只", "有几只", "数量", "共几只", "一共几只")):
        return "count"
    if any(term in message for term in ("主要板块", "行业分布", "题材分布", "分类", "概况", "总结")):
        return "summary"
    if re.search(r"(?:top\s*\d+|前\s*\d+|前几)", message, flags=re.IGNORECASE) or any(
        term in message for term in ("最高", "最低", "最早", "最晚", "排序", "排名")
    ):
        return "ranking"
    return "list"


def normalize_result_mode(value: object) -> ResultMode | None:
    """Normalize an LLM-provided result mode."""

    normalized = str(value or "").strip().lower()
    if normalized in {"list", "count", "summary", "ranking"}:
        return normalized  # type: ignore[return-value]
    return None


def extract_sort(message: str) -> tuple[SortField | None, SortOrder | None]:
    """Extract common event sorting requirements from Chinese wording."""

    rules: tuple[tuple[tuple[str, ...], SortField, SortOrder], ...] = (
        (("最早封板", "封板最早", "按封板时间升序"), "first_limit_time", "asc"),
        (("最晚封板", "封板最晚", "按封板时间降序"), "first_limit_time", "desc"),
        (("成交额最小", "成交额最低", "按成交额升序"), "amount", "asc"),
        (("成交额最大", "成交额最高", "按成交额降序"), "amount", "desc"),
        (("换手率最低", "按换手率升序"), "turnover_rate", "asc"),
        (("换手率最高", "按换手率降序"), "turnover_rate", "desc"),
        (("炸板次数最多", "开板次数最多"), "break_count", "desc"),
        (("板数最高", "最高板"), "board_height", "desc"),
    )
    for terms, field, order in rules:
        if any(term in message for term in terms):
            return field, order
    if "成交额" in message and any(
        term in message.lower() for term in ("top", "前", "排名", "排序")
    ):
        return "amount", "desc"
    if "换手率" in message and any(
        term in message.lower() for term in ("top", "前", "排名", "排序")
    ):
        return "turnover_rate", "desc"
    if "按成交额" in message:
        return "amount", "desc"
    if "按换手率" in message:
        return "turnover_rate", "desc"
    if "按封板时间" in message:
        return "first_limit_time", "asc"
    return None, None


def normalize_sort_field(value: object) -> SortField | None:
    """Normalize an LLM-provided sort field."""

    normalized = str(value or "").strip().lower()
    return _SORT_ALIASES.get(normalized)


def normalize_sort_order(value: object) -> SortOrder | None:
    """Normalize an LLM-provided sort direction."""

    normalized = str(value or "").strip().lower()
    if normalized in {"asc", "ascending", "升序"}:
        return "asc"
    if normalized in {"desc", "descending", "降序"}:
        return "desc"
    return None


def looks_like_exhaustive_request(message: str) -> bool:
    """Return whether omission of any matching stock would make the answer wrong."""

    return any(term in message for term in ("所有", "全部", "完整名单", "全名单", "都列出", "列出"))


def extract_result_limit(message: str) -> int | None:
    """Extract a bounded Top-N or list-size request."""

    match = re.search(r"(?:top\s*|前\s*)(\d{1,3})", message, flags=re.IGNORECASE)
    if match is None:
        match = re.search(r"(?:列出|展示|给我)\s*(\d{1,3})\s*只", message)
    if match is None:
        return None
    return max(1, min(int(match.group(1)), 100))


def extract_topic_query(message: str) -> str | None:
    """Extract conservative industry/concept terms; leave ambiguous text to the LLM."""

    compact = re.sub(r"\s+", "", message)
    patterns = (
        r"(?:票|股票|涨停股)(?:里|中|里面)([A-Za-z0-9\u4e00-\u9fff]{1,12}?)(?:相关|题材|概念|板块|行业)",
        r"([A-Za-z0-9\u4e00-\u9fff]{1,12}?)(?:相关|题材|概念|板块|行业)(?:的)?(?:首板|涨停)",
    )
    stop_words = {
        "哪些",
        "什么",
        "所有",
        "全部",
        "主要",
        "今天",
        "今日",
        "最近",
        "创业板",
        "科创板",
        "主板",
    }
    for pattern in patterns:
        match = re.search(pattern, compact, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip("的与和")
            if candidate and candidate not in stop_words:
                return candidate
    return None


def _clean_query(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized[:32] if normalized else None


def _bounded_int(value: object, *, minimum: int, maximum: int) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return max(minimum, min(parsed, maximum))


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _default_sort_order(sort_by: SortField) -> SortOrder:
    return "asc" if sort_by == "first_limit_time" else "desc"
