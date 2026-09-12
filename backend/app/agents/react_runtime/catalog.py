"""Reviewed temporal/collection contracts, not inferred from argument spelling."""

from copy import deepcopy
from dataclasses import dataclass
import inspect

from app.agents.tools import AgentToolRegistry


@dataclass(frozen=True)
class Contract:
    time_mode: str
    dates: tuple[str, ...] = ()
    collection: str | None = None
    notes: str = ""


CATALOG = {
    "market_summary": Contract("latest_local", notes="Only the latest local market date; not historical."),
    "market_index_trend": Contract("historical", ("end_date",), "indices"),
    "daily_board_promotion": Contract("historical", ("end_date",), "items"),
    "sector_performance": Contract("historical_or_live", ("trade_date",), "top_sectors"),
    "sector_stock_ranking": Contract("historical", ("end_date",), "stocks", "Constituent coverage may be partial; inspect coverage before market-wide claims."),
    "hot_stock_ranking": Contract("current", collection="items", notes="Captured current ranking, not a historical ranking."),
    "dragon_tiger_list": Contract("historical", ("trade_date",), "items"),
    "remote_limit_up_pool": Contract("historical_or_live", ("trade_date",), "items"),
    "first_board_ratings": Contract("historical", ("trade_date",), "top_candidates", "Rating candidate pool is not all limit-up stocks."),
    "market_event_pool": Contract("historical", ("trade_date",), "items", "Event identities/counts; amount may be unavailable. For amount-sorted local limit-up/failed events use limit_up_events."),
    "limit_up_events": Contract("historical", ("trade_date",), "events", "Includes amount (CNY), turnover_rate (%), closed_limit. failed means not sealed at close; broken_intraday also includes resealed stocks."),
    "first_board_filter": Contract("historical", ("trade_date",), "items", "Literal case-insensitive keyword filter over rated name/symbol/industry/concept."),
    "stock_kline": Contract("historical", ("end_date",), "bars", "return_Nd_pct=(last close / close N trading intervals earlier - 1)*100. N displayed bars span N-1 intervals, not N intervals. trend is an MA-based label, not a claim that every recent day rose."),
    "post_limit_screen": Contract("historical", ("data_as_of",), "candidates"),
    "post_limit_path": Contract("historical", ("anchor_date", "data_as_of"), "path"),
    "post_limit_statistics": Contract("historical", ("data_as_of",)),
    "prediction_quality_audit": Contract("historical", ("start_date", "end_date")),
    "rating_backtest": Contract("historical", ("start_date", "end_date")),
    "first_board_critic": Contract("historical", ("trade_date",)),
    "rating_evaluation": Contract("historical", ("start_date", "end_date"), "items"),
    "review_high_score_picks": Contract("historical", ("start_date", "end_date")),
    "scoring_policy_status": Contract("current", notes="Current policy configuration, not a historical policy snapshot."),
    "finance_news": Contract("current", collection="items", notes="Lookback hours from retrieval time, not historical as-of retrieval."),
    "stock_news": Contract("current", collection="items", notes="Lookback calendar days from retrieval time; not historical news as-of."),
    "stock_activity": Contract("latest_local_and_current", notes="Combines latest available local K-lines with current news. Not historical as-of evidence."),
    "web_search": Contract("retrieved_now", collection="items", notes="Search retrieval is current; publication date must be checked separately."),
}


def schemas(registry):
    result = {}
    for original in registry.schemas():
        if original.name not in CATALOG:
            raise ValueError(f"Missing reviewed ReAct contract: {original.name}")
        schema = deepcopy(original.model_dump())
        name, args = original.name, schema["args_schema"]
        method = getattr(AgentToolRegistry, name, None)
        if method and not name.startswith("post_limit_"):
            required = set(args.get("required", []))
            required.update(k for k, p in inspect.signature(method).parameters.items()
                            if k != "self" and p.default is inspect.Parameter.empty)
            args["required"] = sorted(required)
        if name == "stock_kline":
            args["properties"]["symbol"] = {"type": "string", "description": "One code or exact name. Multiple stocks require independent calls."}
        if name == "first_board_filter":
            args["properties"]["trade_date"] = {"type": ["string", "null"], "description": "YYYY-MM-DD rating date."}
        if name == "post_limit_path":
            args["required"] = ["symbol"]
        contract = CATALOG[name]
        if contract.time_mode in {"current", "latest_local", "latest_local_and_current"}:
            args["properties"]["requested_as_of"] = {
                "type": ["string", "null"],
                "description": "If the user requests an as-of date, supply it for temporal capability validation. Unsupported historical scope is rejected, never rewritten.",
            }
        args["additionalProperties"] = False
        schema["description"] += f" Time capability: {contract.time_mode}. {contract.notes}"
        result[name] = schema
    return result
