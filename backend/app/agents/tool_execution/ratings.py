"""Planner tool handlers for ratings; preserve domain-specific evidence contracts."""

from typing import Any

from .context import ExecutionState
from .helpers import (
    _build_first_board_filter_trace,
    _compact_ratings_facts,
    _explicit_request_trade_date,
    _filter_first_board_candidates,
    _filter_query_from_context,
    _has_events_for_date,
    _rating_fact,
    _resolve_tool_stock_target,
    _tool_error_trace,
)


def first_board_ratings(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    trade_date = _explicit_request_trade_date(state.request)
    if trade_date and not _has_events_for_date(state.tools.events, trade_date):
        available_dates = sorted(
            {event.trade_date for event in state.tools.events},
            reverse=True,
        )
        state.facts["first_board_ratings_error"] = {
            "requested_trade_date": trade_date.isoformat(),
            "reason": "No local first-board events for requested date.",
            "latest_local_trade_date": (
                available_dates[0].isoformat() if available_dates else None
            ),
            "available_trade_dates": [
                item.isoformat() for item in available_dates[:20]
            ],
        }
        state.call_names.append(name)
        state.references.append(f"missing_trade_date={trade_date.isoformat()}")
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input={"trade_date": trade_date.isoformat()},
                summary=(
                    f"{trade_date.isoformat()} 本地暂无首板涨停数据，"
                    "已将缺失原因交给 LLM 回答。"
                ),
                error="No local first-board events for requested date.",
            )
        )
        return
    result = state.tools.first_board_ratings(trade_date=trade_date)
    state.latest_ratings_tool = result
    state.latest_ratings = result.output
    state.facts["first_board_ratings"] = _compact_ratings_facts(state.latest_ratings)
    if result.trace_output.get("recommendation_draft"):
        state.facts["first_board_ratings"]["recommendation_draft"] = (
            result.trace_output["recommendation_draft"]
        )
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.append(f"trade_date={state.latest_ratings.trade_date.isoformat()}")


def first_board_filter(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    if state.latest_ratings is None:
        result = state.tools.first_board_ratings(trade_date=None)
        state.latest_ratings_tool = result
        state.latest_ratings = result.output
        state.facts["first_board_ratings"] = _compact_ratings_facts(state.latest_ratings)
        state.traces.append(result.trace())
        state.call_names.append("first_board_ratings")
        state.references.append(f"trade_date={state.latest_ratings.trade_date.isoformat()}")
    query = str(arguments.get("query") or arguments.get("filter") or "").strip()
    filter_query = _filter_query_from_context(query or "\u7528\u6237\u95ee\u53e5")
    matches = _filter_first_board_candidates(state.latest_ratings, filter_query)
    state.facts["first_board_filter"] = {
        "query": filter_query.label,
        "matched_count": len(matches),
        "matches": [_rating_fact(item) for item in matches[:12]],
    }
    state.traces.append(
        _build_first_board_filter_trace(state.latest_ratings, filter_query, matches)
    )
    state.call_names.append(name)
    state.references.append(f"filter={filter_query.label}")


def first_board_critic(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    trade_date = _explicit_request_trade_date(state.request)
    try:
        symbol = _resolve_tool_stock_target(
            tools=state.tools,
            request=state.request,
            argument_value=str(arguments.get("symbol") or "").strip() or None,
            context_symbol=state.context_symbol,
        )
        result = state.tools.first_board_critic(
            symbol=symbol,
            trade_date=trade_date,
        )
    except ValueError as error:
        state.facts["first_board_critic_error"] = str(error)
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input=arguments,
                summary="Critic review failed; the failure reason is passed to the LLM.",
                error=str(error),
            )
        )
        return
    response = result.output
    state.facts["first_board_critic"] = response.model_dump(mode="json")
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(
        [
            f"symbol={response.symbol}",
            f"trade_date={response.trade_date.isoformat()}",
            f"critic_verdict={response.verdict}",
        ]
    )
