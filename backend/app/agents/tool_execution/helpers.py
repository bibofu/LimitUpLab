"""Shared query parsing and compact evidence serialization; no chat orchestration."""

from datetime import date
from typing import Any
import re

from app.agents.query_contract import (
    build_limit_up_query_contract,
    extract_board_filters as contract_board_filters,
)
from app.agents.tool_policy import (
    extract_trade_date as _extract_trade_date,
    looks_like_limit_up_event_question as _looks_like_limit_up_event_question,
)
from app.agents.tools import AgentToolRegistry, compact_first_board_position_groups
from app.models import (
    AgentChatRequest,
    AgentToolTrace,
    FirstBoardRating,
    FirstBoardRatingsResponse,
    LimitUpEvent,
)


class _FirstBoardFilterQuery:
    """Structured filter parsed from a first-board natural-language question."""

    def __init__(self, label: str, aliases: tuple[str, ...]):
        self.label = label
        self.aliases = aliases


def _resolve_tool_stock_target(
    *,
    tools: AgentToolRegistry,
    request: AgentChatRequest,
    argument_value: str | None,
    context_symbol: str | None,
) -> str:
    """Resolve planner, request and conversation stock hints in priority order."""

    for candidate in (
        argument_value,
        request.symbol,
        _extract_symbol_hint(request.message),
        request.message,
        context_symbol,
    ):
        if not candidate:
            continue
        try:
            return tools.resolve_stock_identity(candidate)[0]
        except ValueError:
            continue
    raise ValueError("Cannot resolve the requested stock.")


def _normalize_limit_up_event_arguments(
    request: AgentChatRequest,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Apply Query Contract v2 to planner-proposed event arguments."""

    if not _looks_like_limit_up_event_question(request.message):
        return arguments
    contract = build_limit_up_query_contract(
        request.message,
        request_trade_date=request.trade_date,
        planner_arguments=arguments,
    )
    return contract.to_tool_arguments()


def _tool_error_trace(
    name: str,
    tool_input: dict[str, Any],
    summary: str,
    error: str,
) -> AgentToolTrace:
    """Build a trace for skipped or failed planner tool calls."""

    return AgentToolTrace(
        name=name,
        input=tool_input,
        summary=summary,
        status="error",
        output={},
        error=error,
    )


def _compact_ratings_facts(ratings: FirstBoardRatingsResponse) -> dict[str, Any]:
    """Serialize rating facts into a compact shape for the final LLM answer."""

    return {
        "trade_date": ratings.trade_date.isoformat(),
        "candidate_count": len(ratings.candidates),
        "filtered_out_count": len(ratings.filtered_out),
        "top_candidates": [
            _rating_fact(item) if index < 5 else _brief_rating_fact(item)
            for index, item in enumerate(ratings.candidates[:10])
        ],
        "industry_distribution": _summarize_first_board_industries(ratings.candidates)[:12],
        "position_classification": compact_first_board_position_groups(
            ratings.candidates
        ),
    }


def _extract_symbol_hint(message: str) -> str | None:
    """Extract a six-digit A-share symbol from free-form text."""

    match = re.search(r"(?<!\d)(\d{6})(?!\d)", message)
    return match.group(1) if match else None


def _parse_optional_date(value: object) -> date | None:
    """Parse a date-like value from saved JSON context."""

    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _explicit_request_trade_date(request: AgentChatRequest) -> date | None:
    """Use only a user/API date; undated questions must reach tools as latest."""

    return _extract_trade_date(request.message) or request.trade_date


def _latest_external_trade_date(
    request: AgentChatRequest,
    events: list[LimitUpEvent],
) -> date | None:
    """Pin undated external snapshots to the latest complete local trade date.

    Some providers interpret an omitted date as their previous cached snapshot.
    The user's explicit text date still wins, while stale page context must not
    turn an undated question into a historical query.
    """

    explicit_date = _extract_trade_date(request.message)
    if explicit_date is not None:
        return explicit_date
    return max((event.trade_date for event in events), default=None)


def _parse_optional_int(value: object) -> int | None:
    """Parse an optional integer from LLM tool arguments."""

    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_optional_bool(value: object) -> bool | None:
    """Parse an optional boolean from LLM tool arguments."""

    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _optional_str(value: object) -> str | None:
    """Return a stripped string or None."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _filter_query_from_context(label: str) -> _FirstBoardFilterQuery:
    """Rebuild a filter query from a saved context label."""

    return _extract_first_board_filter(label) or _FirstBoardFilterQuery(
        label=label,
        aliases=(label,),
    )


def _extract_first_board_filter(message: str) -> _FirstBoardFilterQuery | None:
    """Extract a topic or industry filter from first-board chat text."""

    normalized = message.lower()
    known_topics = (
        (
            "\u533b\u836f",
            (
                "\u533b\u836f",
                "\u533b\u7597",
                "\u5236\u836f",
                "\u836f\u4e1a",
                "\u751f\u7269",
                "\u4e2d\u836f",
                "\u5316\u5b66\u5236\u836f",
                "\u533b\u7597\u670d\u52a1",
                "\u533b\u7597\u5668\u68b0",
                "\u75ab\u82d7",
                "cro",
            ),
        ),
        (
            "\u673a\u5668\u4eba",
            ("\u673a\u5668\u4eba", "\u5de5\u4e1a\u6bcd\u673a", "\u81ea\u52a8\u5316"),
        ),
        (
            "\u4eba\u5de5\u667a\u80fd",
            ("\u4eba\u5de5\u667a\u80fd", "\u7b97\u529b", "\u6a21\u578b", "ai"),
        ),
        (
            "\u6d88\u8d39",
            ("\u6d88\u8d39", "\u98df\u54c1", "\u96f6\u552e", "\u767e\u8d27", "\u65c5\u6e38"),
        ),
        (
            "\u65b0\u80fd\u6e90",
            ("\u65b0\u80fd\u6e90", "\u9502\u7535", "\u5149\u4f0f", "\u50a8\u80fd", "\u98ce\u7535"),
        ),
    )
    for label, aliases in known_topics:
        if any(alias.lower() in normalized for alias in aliases):
            return _FirstBoardFilterQuery(label=label, aliases=aliases)

    match = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9]{2,10})(?:\u76f8\u5173|\u884c\u4e1a|\u9898\u6750)",
        message,
    )
    if not match:
        return None
    raw_query = match.group(1)
    for noise in (
        "\u9996\u677f",
        "\u7684\u80a1\u7968",
        "\u80a1\u7968",
        "\u662f",
        "\u6709\u54ea\u4e9b",
    ):
        raw_query = raw_query.replace(noise, "")
    query = raw_query.strip()
    if len(query) < 2:
        return None
    return _FirstBoardFilterQuery(label=query, aliases=(query,))


def _rating_matches_filter(
    rating: FirstBoardRating,
    filter_query: _FirstBoardFilterQuery,
) -> bool:
    """Match a rating against name, industry and concept fields."""

    facts = rating.facts
    searchable = " ".join(
        (facts.name, facts.industry, facts.concept)
    ).lower()
    return any(alias.lower() in searchable for alias in filter_query.aliases)


def _filter_first_board_candidates(
    ratings: FirstBoardRatingsResponse,
    filter_query: _FirstBoardFilterQuery,
) -> list[FirstBoardRating]:
    """Return already-ranked candidates matching a topic filter."""

    return [
        item
        for item in ratings.candidates
        if _rating_matches_filter(item, filter_query)
    ]


def _build_first_board_filter_trace(
    ratings: FirstBoardRatingsResponse,
    filter_query: _FirstBoardFilterQuery,
    matches: list[FirstBoardRating],
) -> AgentToolTrace:
    """Build the compact trace for first-board filter execution."""

    return AgentToolTrace(
        name="first_board_filter",
        input={
            "trade_date": ratings.trade_date.isoformat(),
            "label": filter_query.label,
            "aliases": list(filter_query.aliases),
            "matched_symbols": [item.facts.symbol for item in matches],
        },
        summary=(
            f"\u4ece {len(ratings.candidates)} \u53ea\u9996\u677f\u5019\u9009\u4e2d"
            f"\u547d\u4e2d {len(matches)} \u53ea\u3002"
        ),
    )


def _has_events_for_date(events: list[LimitUpEvent], trade_date: date) -> bool:
    """Return whether local event data exists for a date."""

    return any(event.trade_date == trade_date for event in events)


def _summarize_first_board_industries(
    candidates: list[FirstBoardRating],
) -> list[dict]:
    """Aggregate first-board candidates by industry for LLM consumption."""

    grouped: dict[str, list[FirstBoardRating]] = {}
    for item in candidates:
        industry = item.facts.industry or "\u672a\u6807\u6ce8"
        grouped.setdefault(industry, []).append(item)

    rows = []
    for industry, items in grouped.items():
        top_items = sorted(items, key=lambda item: (-item.score, item.facts.first_limit_time))[:5]
        rows.append(
            {
                "industry": industry,
                "count": len(items),
                "avg_score": round(sum(item.score for item in items) / len(items), 1),
                "top_symbols": [
                    {
                        "symbol": item.facts.symbol,
                        "name": item.facts.name,
                        "rating": item.rating,
                        "score": item.score,
                    }
                    for item in top_items
                ],
            }
        )
    return sorted(rows, key=lambda item: (-item["count"], -item["avg_score"], item["industry"]))[:8]


def _rating_fact(rating: FirstBoardRating | None) -> dict | None:
    """Serialize one rating into compact facts for LLM prompts."""

    if rating is None:
        return None
    facts = rating.facts
    return {
        "symbol": facts.symbol,
        "name": facts.name,
        "industry": facts.industry,
        "concept": facts.concept,
        "rating": rating.rating,
        "score": rating.score,
        "confidence": rating.confidence,
        "first_limit_time": facts.first_limit_time.strftime("%H:%M"),
        "break_count": facts.break_count,
        "amount": facts.amount,
        "turnover_rate": facts.turnover_rate,
        "position": _rating_position_fact(rating),
        "reasons": rating.reasons[:4],
        "risks": rating.risks[:3],
    }


def _brief_rating_fact(rating: FirstBoardRating) -> dict[str, Any]:
    """Serialize enough data to list a candidate without repeating evidence."""

    facts = rating.facts
    return {
        "symbol": facts.symbol,
        "name": facts.name,
        "industry": facts.industry,
        "rating": rating.rating,
        "score": rating.score,
        "confidence": rating.confidence,
        "position": _rating_position_fact(rating),
    }


def _rating_position_fact(rating: FirstBoardRating) -> dict[str, Any] | None:
    """Serialize one candidate's K-line position without its full metric vector."""

    enrichment = rating.facts.enrichment
    position = enrichment.position if enrichment else None
    if position is None:
        return None
    return {
        "regime": position.primary.regime,
        "label": position.primary.label,
        "match_score": position.primary.score,
        "confidence": position.confidence,
        "tags": position.tags[:3],
    }


def _extract_board_height(message: str) -> int | None:
    """Extract requested board height from Chinese limit-up phrases."""

    board_height, _min_board_height = contract_board_filters(message)
    return board_height
