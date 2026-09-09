"""Planner tool handlers for market; preserve domain-specific evidence contracts."""

from typing import Any

from app.agents.query_contract import (
    build_market_event_query_contract,
    extract_result_limit,
    looks_like_exhaustive_request as _looks_like_exhaustive_list_request,
)
from app.agents.tool_policy import (
    QuestionSignals as _QuestionSignals,
    extract_market_index_days as _extract_market_index_days,
    extract_sector_query as _extract_sector_query,
    extract_sector_trend_days as _extract_sector_trend_days,
    looks_like_broad_sector_ranking_question as _looks_like_broad_sector_ranking_question,
)
from app.models import MarketIndexTrendFacts, MarketSummary

from .context import ExecutionState
from .helpers import (
    _explicit_request_trade_date,
    _extract_board_height,
    _optional_str,
    _parse_optional_bool,
    _parse_optional_int,
    _tool_error_trace,
)


def market_event_pool(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    try:
        contract = build_market_event_query_contract(
            state.request.message,
            request_trade_date=state.request.trade_date,
            planner_arguments=arguments,
        )
        result = state.tools.market_event_pool(
            event_type=contract.event_type,
            trade_date=contract.trade_date,
            market=contract.market,
            query=contract.query,
            result_mode=contract.result_mode,
            limit=contract.limit,
        )
        result.input["query_contract"] = contract.to_dict()
        result.trace_output["query_contract"] = contract.to_dict()
        if result.trace_output.get("event_type") != contract.event_type:
            raise ValueError(
                "Market event facts do not match the requested event type"
            )
    except Exception as error:  # noqa: BLE001
        state.facts["market_event_pool_error"] = str(error)
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input=arguments,
                summary="市场事件名单查询失败，已保留明确的失败原因。",
                error=str(error),
            )
        )
        state.call_names.append(name)
        return
    state.facts["market_event_pool"] = result.trace_output
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(
        [
            f"trade_date={result.trace_output.get('trade_date')}",
            f"event_type={contract.event_type}",
            f"source={result.trace_output.get('source')}",
        ]
    )


def market_summary(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    include_limit_down = bool(arguments.get("include_limit_down")) or (
        _QuestionSignals.from_message(state.request.message).market_environment
    )
    result = (
        state.tools.market_summary(include_limit_down=True)
        if include_limit_down
        else state.tools.market_summary()
    )
    summary: MarketSummary = result.output
    state.facts["market_summary"] = result.trace_output
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.append(f"trade_date={summary.trade_date.isoformat()}")
    if summary.limit_down_source:
        state.references.append(f"limit_down_source={summary.limit_down_source}")


def market_index_trend(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    days = _parse_optional_int(arguments.get("days")) or (
        _extract_market_index_days(state.request.message)
    )
    end_date = _explicit_request_trade_date(state.request)
    try:
        result = state.tools.market_index_trend(
            days=max(2, min(days, 20)),
            end_date=end_date,
        )
    except Exception as error:  # noqa: BLE001
        state.facts["market_index_trend_error"] = str(error)
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input={
                    "days": days,
                    "end_date": end_date.isoformat() if end_date else None,
                },
                summary="大盘指数区间走势查询失败，已将失败原因交给 LLM。",
                error=str(error),
            )
        )
        state.call_names.append(name)
        return
    response: MarketIndexTrendFacts = result.output
    state.facts["market_index_trend"] = response.model_dump(mode="json")
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(
        [
            f"index_data_as_of={response.data_as_of.isoformat()}",
            *[
                f"index_source={item.source}"
                for item in response.indices
            ],
        ]
    )


def daily_board_promotion(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    days = _parse_optional_int(arguments.get("days")) or 5
    end_date = _explicit_request_trade_date(state.request)
    result = state.tools.daily_board_promotion(
        days=max(1, min(days, 60)),
        end_date=end_date,
    )
    state.facts["daily_board_promotion"] = result.trace_output
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(
        f"promotion_trade_date={item.trade_date.isoformat()}"
        for item in result.output
    )


def sector_performance(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    broad_sector_ranking = _looks_like_broad_sector_ranking_question(
        state.request.message
    )
    sector = None if broad_sector_ranking else _optional_str(
        arguments.get("sector")
    )
    if sector is None:
        sector = _extract_sector_query(state.request.message)
    trade_date = _explicit_request_trade_date(state.request)
    try:
        result = state.tools.sector_performance(
            sector=sector,
            trade_date=trade_date,
        )
    except Exception as error:  # noqa: BLE001
        state.facts["sector_performance_error"] = str(error)
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input={
                    "sector": sector,
                    "trade_date": trade_date.isoformat() if trade_date else None,
                },
                summary="板块行情查询失败，已将失败原因交给 LLM。",
                error=str(error),
            )
        )
        state.call_names.append(name)
        return
    response = result.output
    if broad_sector_ranking and (
        response.sector_name is not None or not response.top_sectors
    ):
        error = "全市场板块榜单返回了不匹配的单板块结果"
        state.facts["sector_performance_error"] = error
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input={"sector": None},
                summary="板块榜单结果未通过语义校验。",
                error=error,
            )
        )
        state.call_names.append(name)
        return
    state.facts["sector_performance"] = response.model_dump(mode="json")
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(
        [
            f"sector={response.sector_name or 'industry-ranking'}",
            f"data_as_of={response.data_as_of.isoformat()}",
            *[f"source={source}" for source in response.sources],
        ]
    )


def sector_stock_ranking(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    sector = _optional_str(arguments.get("sector"))
    if sector is None:
        sector = _extract_sector_query(state.request.message)
    days = _parse_optional_int(arguments.get("days")) or _extract_sector_trend_days(
        state.request.message
    )
    limit = extract_result_limit(state.request.message) or 10
    end_date = _explicit_request_trade_date(state.request)
    if not sector:
        error = "缺少需要查询的行业或概念名称"
        state.facts["sector_stock_ranking_error"] = error
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input={"sector": None, "days": days, "limit": limit},
                summary="板块成分股走势查询失败，已将失败原因交给 LLM。",
                error=error,
            )
        )
        state.call_names.append(name)
        return
    try:
        result = state.tools.sector_stock_ranking(
            sector=sector,
            days=max(5, min(days, 60)),
            limit=max(1, min(limit, 20)),
            end_date=end_date,
        )
    except Exception as error:  # noqa: BLE001
        state.facts["sector_stock_ranking_error"] = str(error)
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input={
                    "sector": sector,
                    "days": days,
                    "limit": limit,
                    "end_date": end_date.isoformat() if end_date else None,
                },
                summary="板块成分股走势查询失败，已将失败原因交给 LLM。",
                error=str(error),
            )
        )
        state.call_names.append(name)
        return
    response = result.output
    state.facts["sector_stock_ranking"] = response.model_dump(mode="json")
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(
        [
            f"sector={response.sector_name}",
            f"data_as_of={response.data_as_of.isoformat()}",
            *[f"source={source}" for source in response.sources],
        ]
    )


def hot_stock_ranking(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    period = _optional_str(arguments.get("period")) or "day"
    limit = (
        extract_result_limit(state.request.message)
        or _parse_optional_int(arguments.get("limit"))
        or 20
    )
    requested_source = _optional_str(arguments.get("source")) or "auto"
    enrich_performance = bool(arguments.get("enrich_performance")) or (
        _QuestionSignals.from_message(state.request.message).market_environment
    )
    if "同花顺" in state.request.message:
        requested_source = "tonghuashun"
    elif "东方财富" in state.request.message:
        requested_source = "eastmoney"
    try:
        hot_stock_arguments: dict[str, Any] = {
            "period": period,
            "limit": max(1, min(limit, 100)),
            "source": requested_source,
        }
        if enrich_performance:
            hot_stock_arguments["enrich_performance"] = True
        result = state.tools.hot_stock_ranking(**hot_stock_arguments)
    except Exception as error:  # noqa: BLE001
        state.facts["hot_stock_ranking_error"] = str(error)
        state.traces.append(
            _tool_error_trace(
                name=name,
            tool_input={
                "period": period,
                "limit": limit,
                "source": requested_source,
            },
                summary="同花顺热股榜查询失败，已将原因交给 LLM。",
                error=str(error),
            )
        )
        state.call_names.append(name)
        return
    state.facts["hot_stock_ranking"] = result.output
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(
        [
            f"source={result.output.get('source')}",
            f"captured_at={result.output.get('captured_at')}",
            f"data_fresh={result.output.get('data_fresh')}",
        ]
    )


def remote_limit_up_pool(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    trade_date = _explicit_request_trade_date(state.request)
    board_height = (
        _parse_optional_int(arguments.get("board_height"))
        or _extract_board_height(state.request.message)
    )
    limit = _parse_optional_int(arguments.get("limit")) or (
        100 if _looks_like_exhaustive_list_request(state.request.message) else 30
    )
    exclude_st = _parse_optional_bool(arguments.get("exclude_st"))
    exclude_new = _parse_optional_bool(arguments.get("exclude_new"))
    try:
        result = state.tools.remote_limit_up_pool(
            trade_date=trade_date,
            board_height=board_height,
            query=_optional_str(arguments.get("query")),
            exclude_st=True if exclude_st is None else exclude_st,
            exclude_new=True if exclude_new is None else exclude_new,
            limit=max(1, min(limit, 100)),
        )
    except Exception as error:  # noqa: BLE001
        state.facts["remote_limit_up_pool_error"] = str(error)
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input=arguments,
                summary="同花顺涨停池查询失败，已将原因交给 LLM。",
                error=str(error),
            )
        )
        state.call_names.append(name)
        return
    state.facts["remote_limit_up_pool"] = result.output
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(
        [
            "source=hithink-finance",
            f"trade_date={result.output.get('trade_date')}",
        ]
    )
