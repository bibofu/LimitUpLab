"""Ordered, profile-checked dispatch for planner-selected evidence tools.

Handlers own domain argument/evidence contracts. The dispatcher owns profile
enforcement and request isolation; it never imports the chat orchestrator.
"""

from types import MappingProxyType
from typing import Any, Callable

from app.agents.tool_policy import ToolExecution
from app.agents.tools import AgentToolRegistry
from app.models import AgentChatRequest

from . import (
    market,
    post_limit,
    ratings,
    review,
    stocks,
)
from .context import ExecutionState
from .helpers import _tool_error_trace

Handler = Callable[[ExecutionState, str, dict[str, Any]], None]
HANDLERS: MappingProxyType[str, Handler] = MappingProxyType({
    "market_event_pool": market.market_event_pool,
    "market_summary": market.market_summary,
    "market_index_trend": market.market_index_trend,
    "daily_board_promotion": market.daily_board_promotion,
    "sector_performance": market.sector_performance,
    "sector_stock_ranking": market.sector_stock_ranking,
    "hot_stock_ranking": market.hot_stock_ranking,
    "remote_limit_up_pool": market.remote_limit_up_pool,
    "dragon_tiger_list": stocks.dragon_tiger_list,
    "finance_news": stocks.finance_news,
    "stock_news": stocks.stock_news,
    "stock_activity": stocks.stock_activity,
    "web_search": stocks.web_search,
    "limit_up_events": stocks.limit_up_events,
    "stock_kline": stocks.stock_kline,
    "post_limit_screen": post_limit.screen,
    "post_limit_path": post_limit.path,
    "post_limit_statistics": post_limit.statistics,
    "first_board_ratings": ratings.first_board_ratings,
    "first_board_filter": ratings.first_board_filter,
    "first_board_critic": ratings.first_board_critic,
    "prediction_quality_audit": review.prediction_quality_audit,
    "rating_backtest": review.rating_backtest,
    "rating_evaluation": review.rating_evaluation,
    "review_high_score_picks": review.review_high_score_picks,
    "scoring_policy_status": review.scoring_policy_status,
})


def execute_tool_calls(
    tool_calls: list[dict[str, Any]],
    tools: AgentToolRegistry,
    *,
    request: AgentChatRequest,
    context_symbol: str | None = None,
) -> ToolExecution:
    """Execute in plan order so filters can reuse prior rating evidence."""
    frozen_executor = getattr(tools, "execute_frozen_calls", None)
    if callable(frozen_executor):
        return frozen_executor(
            tool_calls,
            request=request,
            context_symbol=context_symbol,
        )
    # State belongs to this request only. Each domain handler adds compact facts
    # for the writer and a trace for persistence/UI inspection; those serve
    # different audiences and should not be confused with raw provider responses.
    state = ExecutionState(tools=tools, request=request, context_symbol=context_symbol)
    for call in tool_calls:
        name, arguments = call["name"], call["arguments"]
        # Recheck availability here even though the planner schema was filtered.
        # This also protects legacy plans and direct callers of the dispatcher.
        if not tools.is_enabled(name):
            error = (
                f"{name} is unavailable in Agent profile {tools.profile}; "
                "the capability is deferred beyond V1."
            )
            state.facts[f"{name}_error"] = error
            state.traces.append(_tool_error_trace(
                name=name, tool_input=arguments,
                summary="当前 V1 不提供该实时能力。", error=error,
            ))
            state.call_names.append(name)
            continue
        handler = HANDLERS.get(name)
        if handler is None:
            error = "Unsupported tool requested by LLM planner."
            state.facts[f"{name}_error"] = error
            state.traces.append(_tool_error_trace(
                name=name, tool_input=arguments,
                summary=f"LLM 请求了未注册工具 {name}，后端已跳过。", error=error,
            ))
            continue
        handler(state, name, arguments)
    return {
        "facts": state.facts,
        "tool_results": state.traces,
        "tool_call_names": state.call_names,
        "references": list(dict.fromkeys(state.references)),
    }
