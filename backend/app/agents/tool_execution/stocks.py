"""Planner tool handlers for stocks; preserve domain-specific evidence contracts."""

from typing import Any

from app.agents.limit_up_execution import execute_limit_up_query
from app.agents.query_contract import build_limit_up_query_contract
from app.agents.tool_policy import (
    extract_kline_days as _extract_kline_days,
    extract_stock_news_days as _extract_stock_news_days,
)

from .context import ExecutionState
from .helpers import (
    _explicit_request_trade_date,
    _has_events_for_date,
    _latest_external_trade_date,
    _normalize_limit_up_event_arguments,
    _optional_str,
    _parse_optional_date,
    _parse_optional_int,
    _resolve_tool_stock_target,
    _tool_error_trace,
)


# Execute the dragon tiger list evidence step, resolving request arguments and recording its facts
# and trace.
def dragon_tiger_list(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    trade_date = _latest_external_trade_date(state.request, state.tools.events)
    board_type = _optional_str(arguments.get("board_type")) or "all"
    query = _optional_str(arguments.get("query"))
    limit = _parse_optional_int(arguments.get("limit")) or 30
    try:
        result = state.tools.dragon_tiger_list(
            trade_date=trade_date,
            board_type=board_type,
            query=query,
            limit=max(1, min(limit, 100)),
        )
    except Exception as error:  # noqa: BLE001
        state.facts["dragon_tiger_list_error"] = str(error)
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input={
                    "trade_date": trade_date.isoformat() if trade_date else None,
                    "board_type": board_type,
                    "query": query,
                    "limit": limit,
                },
                summary="同花顺龙虎榜查询失败，已将原因交给 LLM。",
                error=str(error),
            )
        )
        state.call_names.append(name)
        return
    state.facts["dragon_tiger_list"] = result.output
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(
        [
            "source=hithink-finance",
            f"trade_date={result.output.get('trade_date')}",
        ]
    )


# Execute the finance news evidence step, resolving request arguments and recording its facts and
# trace.
def finance_news(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    query = _optional_str(arguments.get("query"))
    limit = _parse_optional_int(arguments.get("limit")) or 8
    hours = _parse_optional_int(arguments.get("hours")) or 48
    try:
        result = state.tools.finance_news(
            query=query,
            limit=max(1, min(limit, 12)),
            hours=max(1, min(hours, 168)),
        )
    except Exception as error:  # noqa: BLE001
        state.facts["finance_news_error"] = str(error)
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input={"query": query, "limit": limit, "hours": hours},
                summary="财经快讯聚合失败，已将失败原因交给 LLM。",
                error=str(error),
            )
        )
        state.call_names.append(name)
        return
    response = result.output
    state.facts["finance_news"] = response.model_dump(mode="json")
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(item.url for item in response.items)


# Execute the stock news evidence step, resolving request arguments and recording its facts and
# trace.
def stock_news(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    days = _parse_optional_int(arguments.get("days")) or (
        _extract_stock_news_days(state.request.message)
    )
    limit = _parse_optional_int(arguments.get("limit")) or 10
    try:
        target = _resolve_tool_stock_target(
            tools=state.tools,
            request=state.request,
            argument_value=_optional_str(arguments.get("symbol")),
            context_symbol=state.context_symbol,
        )
        result = state.tools.stock_news(
            target,
            days=max(1, min(days, 30)),
            limit=max(1, min(limit, 20)),
        )
    except Exception as error:  # noqa: BLE001
        state.facts["stock_news_error"] = str(error)
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input={"symbol": arguments.get("symbol"), "days": days, "limit": limit},
                summary="个股资讯查询失败，已将失败原因交给 LLM。",
                error=str(error),
            )
        )
        state.call_names.append(name)
        return
    response = result.output
    state.facts["stock_news"] = response.model_dump(mode="json")
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(item.url for item in response.items)


# Execute the stock activity evidence step, resolving request arguments and recording its facts
# and trace.
def stock_activity(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    days = _parse_optional_int(arguments.get("days")) or (
        _extract_stock_news_days(state.request.message)
    )
    news_limit = _parse_optional_int(arguments.get("news_limit")) or 8
    try:
        target = _resolve_tool_stock_target(
            tools=state.tools,
            request=state.request,
            argument_value=_optional_str(arguments.get("symbol")),
            context_symbol=state.context_symbol,
        )
        result = state.tools.stock_activity(
            target,
            days=max(1, min(days, 30)),
            news_limit=max(1, min(news_limit, 20)),
        )
    except Exception as error:  # noqa: BLE001
        state.facts["stock_activity_error"] = str(error)
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input={
                    "symbol": arguments.get("symbol"),
                    "days": days,
                    "news_limit": news_limit,
                },
                summary="个股近期动态查询失败，已将失败原因交给 LLM。",
                error=str(error),
            )
        )
        state.call_names.append(name)
        return
    response = result.output
    state.facts["stock_activity"] = result.trace_output
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(item.url for item in response.news.items)


# Execute the web search evidence step, resolving request arguments and recording its facts and
# trace.
def web_search(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    query = _optional_str(arguments.get("query")) or state.request.message
    limit = _parse_optional_int(arguments.get("limit")) or 5
    try:
        result = state.tools.web_search(
            query=query,
            limit=max(1, min(limit, 8)),
        )
    except Exception as error:  # noqa: BLE001
        state.facts["web_search_error"] = str(error)
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input={"query": query, "limit": limit},
                summary="通用搜索失败，已将失败原因交给 LLM。",
                error=str(error),
            )
        )
        state.call_names.append(name)
        return
    response = result.output
    state.facts["web_search"] = response.model_dump(mode="json")
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(item.url for item in response.results)


# Execute the limit up events evidence step, resolving request arguments and recording its facts
# and trace.
def limit_up_events(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    arguments = _normalize_limit_up_event_arguments(
        state.request,
        arguments,
    )
    trade_date = _parse_optional_date(arguments.get("trade_date"))
    if trade_date and not _has_events_for_date(state.tools.events, trade_date):
        available_dates = sorted(
            {event.trade_date for event in state.tools.events},
            reverse=True,
        )
        state.facts["limit_up_events_error"] = {
            "requested_trade_date": trade_date.isoformat(),
            "reason": "No local limit-up events for requested date.",
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
                tool_input=arguments,
                summary=f"{trade_date.isoformat()} 本地暂无涨停事件数据。",
                error="No local limit-up events for requested date.",
            )
        )
        return
    contract = build_limit_up_query_contract(
        state.request.message,
        request_trade_date=state.request.trade_date,
        planner_arguments=arguments,
    )
    result, facts = execute_limit_up_query(state.tools, contract)
    state.facts["limit_up_events"] = facts
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.append(f"trade_date={result.trace_output.get('trade_date')}")


# Execute the stock kline evidence step, resolving request arguments and recording its facts and
# trace.
def stock_kline(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    days = _parse_optional_int(arguments.get("days")) or _extract_kline_days(
        state.request.message
    )
    end_date = _explicit_request_trade_date(state.request)
    try:
        raw_symbol = _resolve_tool_stock_target(
            tools=state.tools,
            request=state.request,
            argument_value=_optional_str(
                arguments.get("symbol") or arguments.get("query")
            ),
            context_symbol=state.context_symbol,
        )
        result = state.tools.stock_kline(
            symbol=raw_symbol,
            days=max(5, min(days, 60)),
            end_date=end_date,
        )
    except Exception as error:  # noqa: BLE001
        state.facts["stock_kline_error"] = str(error)
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input=arguments,
                summary="K线工具查询失败，已将原因交给 LLM。",
                error=str(error),
            )
        )
        state.call_names.append(name)
        return
    response = result.output
    state.facts["stock_kline"] = response.model_dump(mode="json")
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(
        [
            f"symbol={response.symbol}",
            f"data_as_of={response.data_as_of.isoformat()}",
        ]
    )
