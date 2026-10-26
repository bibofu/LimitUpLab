"""Tool registry and schemas for the first-board Agent."""

import os
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from app.agents.first_board import build_first_board_ratings
from app.agents.dragon_tiger_summary import build_dragon_tiger_overview
from app.agents.tool_contracts import AgentToolSchema
from app.agents.tool_result import ToolResult
from app.agents.tool_schemas_market import MARKET_TOOL_SCHEMAS
from app.agents.tool_schemas_news import NEWS_TOOL_SCHEMAS
from app.agents.tool_schemas_research import RESEARCH_TOOL_SCHEMAS
from app.agents.tool_schemas_review import REVIEW_TOOL_SCHEMAS
from app.agents.event_query import (
    filter_limit_up_events,
    group_limit_up_events,
    sort_limit_up_events as _sort_limit_up_events,
)
from app.agents.query_contract import (
    MARKET_SEGMENT_LABELS,
    MARKET_SEGMENT_PREFIXES,
    current_query_reference_time,
    normalize_market_event_type,
    normalize_event_status,
    normalize_market_segment,
    normalize_sort_field,
    normalize_sort_order,
)
from app.collectors import (
    HithinkFinanceCollector,
    collect_eastmoney_hot_stock_ranking,
    collect_limit_down_pool,
    collect_market_index_trends,
)
from app.collectors.first_board_enrichment_collector import (
    collect_preferred_dragon_tiger_snapshot,
)
from app.models import (
    DailyBoardPromotionStat,
    FinanceNewsFacts,
    FirstBoardRating,
    LimitUpEvent,
    MarketCalendarFacts,
    MarketIndexTrendFacts,
    SectorPerformanceFacts,
    SectorStockRankingFacts,
    StockActivityFacts,
    StockKLineFacts,
    StockNewsFacts,
)
from app.repositories import (
    FirstBoardRepository,
    RecommendationIntelligenceRepository,
    StockNewsRepository,
)
from app.services.analysis import (
    enrich_board_promotion_openings,
    events_for_date,
    summarize_market,
)
from app.services.daily_promotion import build_daily_promotion_report
from app.services.finance_news import collect_finance_news
from app.services.sector_performance import build_sector_performance
from app.services.sector_stock_ranking import build_sector_stock_ranking
from app.services.stock_kline import build_stock_kline_facts
from app.services.stock_financials import build_stock_financials_facts
from app.services.stock_profile import build_stock_profile
from app.services.index_research import (
    build_index_constituents,
    build_index_quote,
    search_indices,
)
from app.services.recent_limit_up_returns import build_recent_limit_up_returns
from app.agents.kline_presentation import kline_agent_view
from app.post_limit_query_contract import PostLimitQueryContract
from app.repositories.post_limit_repository import load_post_limit_dataset
from app.services.post_limit import (
    build_post_limit_path,
    build_post_limit_screen,
    build_post_limit_statistics,
)
from app.services.stock_news import collect_stock_news
from app.services.trading_calendar import build_market_calendar_facts
from app.agents.review_agent import build_review_agent_report


TOOL_CONTRACT_VERSION = "agent-tools-v4"


TOOL_SCHEMAS = [
    *MARKET_TOOL_SCHEMAS,
    *RESEARCH_TOOL_SCHEMAS,
    *REVIEW_TOOL_SCHEMAS,
    *NEWS_TOOL_SCHEMAS,
]


V1_AGENT_PROFILE = "v1_close_review"
EXTENDED_AGENT_PROFILE = "extended"
V1_CLOSED_MARKET_TOOL_NAMES = frozenset(
    {
        "trading_calendar",
        "market_summary",
        "market_index_trend",
        "sector_performance",
        "sector_stock_ranking",
        "hot_stock_ranking",
        "finance_news",
        "stock_news",
        "stock_activity",
        "daily_board_promotion",
        "dragon_tiger_list",
        "first_board_ratings",
        "market_event_pool",
        "limit_up_events",
        "first_board_filter",
        "stock_kline",
        "stock_profile",
        "index_search",
        "index_quote",
        "index_constituents",
        "recent_limit_up_returns",
        "post_limit_screen",
        "post_limit_path",
        "post_limit_statistics",
        "review_high_score_picks",
    }
)

_SESSION_STATUS_LABELS = {
    "pre_open": "今日未开盘",
    "open": "今日开市中",
    "closed": "今日已收盘",
    "non_trading_day": "今日非交易日",
    "unverified": "今天是否为交易日尚未验证",
}

_AVAILABILITY_LABELS = {
    "requested_date_in_future": "请求日期在未来",
    "market_not_closed": "今日尚未收盘",
    "closing_data_pending": "收盘数据尚未落定",
    "non_trading_day": "该日期不是交易日",
}


def _calendar_trace(facts: MarketCalendarFacts | None) -> dict[str, Any] | None:
    """Keep the event-pool availability decision traceable without full payload."""

    if facts is None:
        return None
    return {
        "source": facts.calendar_source,
        "verified": facts.calendar_verified,
        "session_status": facts.session_status,
        "latest_closed_trade_date": (
            facts.latest_closed_trade_date.isoformat()
            if facts.latest_closed_trade_date is not None
            else None
        ),
    }


def resolve_agent_profile(profile: str | None = None) -> str:
    """Resolve the active Agent product profile from an explicit value or env."""

    resolved = (profile or os.getenv("LIMITUPLAB_AGENT_PROFILE") or V1_AGENT_PROFILE)
    normalized = resolved.strip().lower().replace("-", "_")
    aliases = {
        "v1": V1_AGENT_PROFILE,
        "v1_close_review": V1_AGENT_PROFILE,
        "extended": EXTENDED_AGENT_PROFILE,
        "v2_preview": EXTENDED_AGENT_PROFILE,
    }
    if normalized not in aliases:
        raise ValueError(
            "LIMITUPLAB_AGENT_PROFILE must be v1_close_review or extended"
        )
    return aliases[normalized]


class AgentToolRegistry:
    """Typed registry of tools available to the chat Agent."""

    def __init__(
        self,
        events: list[LimitUpEvent],
        first_board_repository: FirstBoardRepository | None = None,
        hithink_collector: HithinkFinanceCollector | None = None,
        profile: str | None = None,
    ):
        """Create a registry bound to current request data dependencies."""

        self.events = events
        self.first_board_repository = first_board_repository or FirstBoardRepository()
        self.hithink_collector = hithink_collector or HithinkFinanceCollector()
        self.profile = resolve_agent_profile(profile)
        self._stock_name_directory: dict[str, str] | None = None

    @property
    def enabled_tool_names(self) -> frozenset[str]:
        """Return tool names available to the ReAct tool gateway."""

        if self.profile == EXTENDED_AGENT_PROFILE:
            return frozenset(schema.name for schema in TOOL_SCHEMAS)
        return V1_CLOSED_MARKET_TOOL_NAMES

    def is_enabled(self, tool_name: str) -> bool:
        """Return whether one tool is callable in the active product profile."""

        return tool_name in self.enabled_tool_names

    def schemas(self) -> list[AgentToolSchema]:
        """Return the LLM-facing tool schemas."""

        return [
            schema for schema in TOOL_SCHEMAS if self.is_enabled(schema.name)
        ]


    def trading_calendar(self) -> ToolResult:
        """Return the latest closed trading day from the verified calendar."""

        local_data_through = max(
            (event.trade_date for event in self.events),
            default=None,
        )
        response: MarketCalendarFacts = build_market_calendar_facts(
            local_data_through=local_data_through,
        )
        trace_output = response.model_dump(mode="json")
        latest = (
            response.latest_closed_trade_date.isoformat()
            if response.latest_closed_trade_date is not None
            else "未知"
        )
        summary = (
            f"上海时间 {response.as_of.isoformat()}；"
            f"{_SESSION_STATUS_LABELS[response.session_status]}；"
            f"最新已收盘交易日 {latest}。"
        )
        if response.local_data_through is not None:
            summary += f" 本地涨停事件数据截至 {response.local_data_through.isoformat()}。"
        if response.data_missing:
            summary += " " + "；".join(response.data_missing)
        return ToolResult(
            name="trading_calendar",
            input={},
            output=response,
            summary=summary,
            trace_output=trace_output,
            result_status="partial" if response.data_missing else "ok",
        )

    def market_summary(self, *, include_limit_down: bool = False) -> ToolResult:
        """Return the latest objective local market facts."""

        summary = summarize_market(self.events)
        if include_limit_down:
            try:
                limit_down = collect_limit_down_pool(summary.trade_date)
                summary = summary.model_copy(
                    update={
                        "limit_down_count": len(limit_down.items),
                        "limit_down_source": limit_down.source,
                    }
                )
            except Exception:  # noqa: BLE001
                pass
        trace_output = {
            "trade_date": summary.trade_date.isoformat(),
            "limit_up_count": summary.limit_up_count,
            "first_board_count": summary.first_board_count,
            "continued_board_count": summary.continued_board_count,
            "touched_limit_count": summary.limit_up_count + summary.unsealed_count,
            "intraday_opened_count": summary.failed_count,
            "intraday_opened_rate": summary.failed_limit_up_rate,
            "unsealed_count": summary.unsealed_count,
            "unsealed_rate": summary.unsealed_rate,
            "rate_basis": "intraday_opened_count / touched_limit_count; unsealed_count / touched_limit_count; ratios are 0..1",
            "limit_down_count": summary.limit_down_count,
            "limit_down_source": summary.limit_down_source,
            "max_board_height": summary.max_board_height,
            "hot_industries": summary.hot_industries[:5],
            "hot_concepts": [{
                "name": item.name,
                "limit_up_count": item.limit_up_count,
                "intraday_opened_count": item.failed_count,
            } for item in summary.hot_concepts[:5]],
        }
        down_text = (
            f"，跌停{summary.limit_down_count}只"
            if summary.limit_down_count is not None
            else "，跌停数据暂缺"
        )
        return ToolResult(
            name="market_summary",
            input={"include_limit_down": include_limit_down},
            output=trace_output,
            summary=(
                f"{summary.trade_date.isoformat()} 涨停{summary.limit_up_count}只，"
                f"首板{summary.first_board_count}只，连板{summary.continued_board_count}只，"
                f"未回封{summary.unsealed_count}只{down_text}。"
            ),
            trace_output=trace_output,
        )

    def market_index_trend(
        self,
        *,
        days: int = 5,
        end_date: date | None = None,
    ) -> ToolResult:
        """Return a date-aligned multi-index trend window."""

        available_dates = sorted({event.trade_date for event in self.events})
        resolved_end_date = end_date or (
            available_dates[-1] if available_dates else date.today()
        )
        response: MarketIndexTrendFacts = collect_market_index_trends(
            days=max(2, min(days, 20)),
            end_date=resolved_end_date,
        )
        trace_output = response.model_dump(mode="json")
        return ToolResult(
            name="market_index_trend",
            input={
                "days": response.requested_days,
                "end_date": resolved_end_date.isoformat(),
            },
            output=response,
            summary=(
                f"截至 {response.data_as_of.isoformat()} 的近 "
                f"{response.requested_days} 个交易日指数走势："
                + "，".join(
                    f"{item.name}{item.return_pct:+.2f}%" for item in response.indices
                )
                + "。"
            ),
            trace_output=trace_output,
        )

    def daily_board_promotion(
        self,
        days: int = 5,
        end_date: date | None = None,
    ) -> ToolResult:
        """Return empirical daily board-promotion rates from local close data."""

        report = build_daily_promotion_report(
            self.events,
            days=max(1, min(days, 60)),
            end_date=end_date,
        )
        stats: list[DailyBoardPromotionStat] = report.items
        promoted_symbols = sorted(
            {
                stock.symbol
                for stat in stats
                for stock in stat.promoted_stocks
            }
        )
        daily_bars = self.first_board_repository.list_daily_bars_for_symbols(
            promoted_symbols,
            end_date=stats[-1].trade_date if stats else end_date,
        )
        stats = enrich_board_promotion_openings(stats, daily_bars)
        trace_output = {
            "requested_days": days,
            "end_date": end_date.isoformat() if end_date else None,
            "observed_days": len(stats),
            "latest_event_date": report.latest_event_date.isoformat() if report.latest_event_date else None,
            "data_missing": report.warnings,
            "items": [item.model_dump(mode="json") for item in stats],
        }
        latest = stats[-1] if stats else None
        summary = (
            f"最近 {len(stats)} 个可计算交易日；"
            f"{latest.trade_date.isoformat()} 总晋级 "
            f"{latest.promoted_count}/{latest.sample_size}（{latest.probability:.1%}）。"
            if latest
            else "本地相邻交易日数据不足，暂时无法计算每日晋级率。"
        )
        return ToolResult(
            name="daily_board_promotion",
            input={
                "days": days,
                "end_date": end_date.isoformat() if end_date else None,
            },
            output=stats,
            summary=summary + (" " + "；".join(report.warnings) if report.warnings else ""),
            trace_output=trace_output,
            result_status="partial" if report.warnings else ("ok" if stats else "empty"),
        )

    def sector_performance(
        self,
        sector: str | None = None,
        trade_date: date | None = None,
    ) -> ToolResult:
        """Return on-demand sector ranking, breadth and trend facts."""

        latest_local_date = max(
            (item.trade_date for item in self.events),
            default=None,
        )
        response: SectorPerformanceFacts = build_sector_performance(
            sector=sector,
            trade_date=trade_date or latest_local_date,
        )
        trace_output = response.model_dump(mode="json")
        if response.matched_sectors:
            matches = "、".join(
                f"{item.sector_name}({item.change_pct:+.2f}%)"
                for item in response.matched_sectors
            )
            summary = (
                f"{response.data_as_of.isoformat()} 与"
                f"{response.requested_sector or response.sector_name}匹配的板块："
                f"{matches}。"
            )
        elif response.sector_name:
            label = "概念" if response.sector_type == "concept" else "行业"
            parts = [
                f"{response.data_as_of.isoformat()} {response.sector_name}{label}板块"
            ]
            if response.change_pct is not None:
                parts.append(f"涨跌幅{response.change_pct}%")
            if response.rank is not None and response.sector_count:
                parts.append(f"{label}排名{response.rank}/{response.sector_count}")
            if response.up_count is not None and response.down_count is not None:
                parts.append(f"上涨{response.up_count}家、下跌{response.down_count}家")
            summary = "，".join(parts) + "。"
        else:
            leaders = "、".join(
                f"{item.sector_name}({item.change_pct:+.2f}%)"
                for item in response.top_sectors[:3]
            )
            summary = (
                f"{response.data_as_of.isoformat()} 行业强弱榜：{leaders}。"
                if leaders else
                f"{response.trade_date.isoformat()} 行业强弱榜未完整取得，详见数据缺口。"
            )
        return ToolResult(
            name="sector_performance",
            input={
                "sector": sector,
                "trade_date": trade_date.isoformat() if trade_date else None,
            },
            output=response,
            summary=summary,
            trace_output=trace_output,
            result_status="partial" if response.data_missing or not response.data_fresh else "ok",
            data_fresh=response.data_fresh,
        )

    def sector_stock_ranking(
        self,
        sector: str,
        days: int = 20,
        limit: int = 10,
        end_date: date | None = None,
    ) -> ToolResult:
        """Rank one sector's constituents by completed daily trend facts."""

        latest_local_date = max(
            (item.trade_date for item in self.events),
            default=date.today(),
        )
        resolved_end_date = end_date or latest_local_date
        response: SectorStockRankingFacts = build_sector_stock_ranking(
            sector=sector,
            end_date=resolved_end_date,
            days=days,
            limit=limit,
            collector=self.hithink_collector,
            repository=self.first_board_repository,
        )
        leaders = "、".join(
            f"{item.name}({item.symbol})" for item in response.items[:3]
        ) or "暂无可排名成分股"
        summary = (
            f"截至{response.data_as_of.isoformat()}，{response.sector_name}板块"
            f"共{response.member_count}只成分股，完成{response.analyzed_count}只趋势比较；"
            f"排名靠前：{leaders}。"
        )
        return ToolResult(
            name="sector_stock_ranking",
            input={
                "sector": sector,
                "days": response.requested_days,
                "limit": response.requested_limit,
                "end_date": resolved_end_date.isoformat(),
            },
            output=response,
            summary=summary,
            trace_output=response.model_dump(mode="json"),
        )

    def hot_stock_ranking(
        self,
        period: str = "day",
        limit: int = 20,
        source: str = "auto",
        enrich_performance: bool = False,
    ) -> ToolResult:
        """Return a fresh popularity ranking with explicit provider semantics."""

        requested_limit = max(1, min(limit, 100))
        normalized_source = source if source in {"auto", "tonghuashun", "eastmoney"} else "auto"
        snapshot = None
        if normalized_source != "eastmoney":
            try:
                snapshot = self.hithink_collector.collect_hot_stocks(
                    period=period,
                    limit=requested_limit,
                )
            except Exception:  # noqa: BLE001
                if normalized_source != "auto" or period != "day":
                    raise
        if normalized_source == "eastmoney" or (
            normalized_source == "auto" and period == "day"
            and (snapshot is None or not snapshot.items)
        ):
            snapshot = collect_eastmoney_hot_stock_ranking(
                limit=requested_limit,
                name_resolver=self.hithink_collector.collect_a_share_symbol_names,
            )
        quote_facts: dict[str, Any] = {}
        quote_captured_at: datetime | None = None
        if enrich_performance:
            try:
                quote_snapshot = self.hithink_collector.collect_market_snapshots(
                    [item.thscode for item in snapshot.items[:20] if item.thscode]
                )
                quote_facts = {
                    item.symbol: item for item in quote_snapshot.items
                }
                quote_captured_at = quote_snapshot.captured_at.astimezone(timezone.utc)
            except Exception:  # noqa: BLE001
                pass
        items = []
        for item in snapshot.items:
            serialized = asdict(item)
            quote = quote_facts.get(item.symbol)
            serialized.update(
                {
                    # A source's intraday/current change is not a comparison
                    # against the previous trading day's matching snapshot.
                    # Keep real source zeros; expose the unqueried baseline
                    # separately so it can never be rendered as a zero change.
                    "previous_trade_day_rank": None,
                    "previous_trade_day_rank_change": None,
                    "last_price": quote.last_price if quote else None,
                    "change_pct": quote.change_pct if quote else None,
                    "turnover_yi": (
                        round(quote.turnover / 100_000_000, 2)
                        if quote and quote.turnover is not None
                        else None
                    ),
                }
            )
            items.append(serialized)
        captured_at = snapshot.captured_at.astimezone(timezone.utc)
        captured_at_beijing = captured_at.astimezone(timezone(timedelta(hours=8)))
        age_seconds = max(
            0,
            round((datetime.now(timezone.utc) - captured_at).total_seconds()),
        )
        payload = {
            "source": snapshot.source,
            "source_label": "东方财富" if snapshot.source == "eastmoney" else "同花顺",
            "captured_at": captured_at.isoformat(),
            "captured_at_beijing": captured_at_beijing.isoformat(),
            "quote_captured_at": (
                quote_captured_at.isoformat() if quote_captured_at else None
            ),
            "data_fresh": age_seconds <= 900,
            "age_seconds": age_seconds,
            "period": getattr(snapshot, "period", "current"),
            "rank_change_reference": "provider_defined",
            "rank_change_comparison_date": None,
            "previous_trade_day": None,
            "metric_definitions": {
                "rank_change": (
                    "来源直接返回的排名变化，比较时点和日期未确认；"
                    "保留来源的真实0，但不能解释为相较上一交易日持平。"
                ),
                "rank_trend": (
                    "来源变化口径下的排名趋势；比较时点未确认，不能用于判断相较上一交易日升降。"
                ),
                "previous_trade_day_rank": (
                    "同一来源上一交易日的可比排名；本工具仅查询当前快照，未获取该基准。"
                ),
                "previous_trade_day_rank_change": (
                    "同一来源上一交易日排名减去当前排名；"
                    "没有取得上一交易日可比排名时为null，不得用rank_change替代或填0。"
                ),
            },
            "field_availability": {
                "previous_trade_day_rank": "数据未成功获取：未取得上一交易日可比排名",
                "previous_trade_day_rank_change": "数据未成功获取：缺少上一交易日排名基准",
            },
            "requested_count": requested_limit,
            "count": len(items),
            "complete": len(items) >= requested_limit,
            "items": items,
        }
        leaders = "、".join(
            f"{item.name}({item.symbol})第{item.rank}名"
            for item in snapshot.items[:5]
        )
        return ToolResult(
            name="hot_stock_ranking",
            input={
                "period": period,
                "limit": requested_limit,
                "source": normalized_source,
                "enrich_performance": enrich_performance,
            },
            output=payload,
            summary=(
                f"{payload['source_label']}热股榜返回 {len(items)}/{requested_limit} 只"
                f"{f'：{leaders}' if leaders else '。'}"
            ),
            trace_output=payload,
        )

    def dragon_tiger_list(
        self,
        *,
        trade_date: date | None = None,
        board_type: str = "all",
        query: str | None = None,
        limit: int | None = None,
    ) -> ToolResult:
        """Return THS-first Dragon-Tiger facts with a dated all-board fallback."""

        snapshot = collect_preferred_dragon_tiger_snapshot(
            trade_date=trade_date,
            board_type=board_type,
            query=query,
            limit=limit,
            collector=self.hithink_collector,
        )
        snapshot_date = snapshot.trade_date.isoformat() if snapshot.trade_date else None
        items = [{**asdict(item), "trade_date": snapshot_date} for item in snapshot.items]
        payload = {
            "source": snapshot.source,
            "trade_date": snapshot_date,
            "board_type": snapshot.board_type,
            "stock_count": snapshot.stock_count,
            "matched_count": snapshot.matched_count if snapshot.matched_count is not None else len(items),
            "returned_count": len(items),
            "source_truncated": snapshot.source_truncated,
            "items": items,
        }
        payload["overview"] = build_dragon_tiger_overview(payload, query=query)
        names = "、".join(
            f"{item.name}({item.symbol})"
            for item in snapshot.items[:5]
        )
        return ToolResult(
            name="dragon_tiger_list",
            input={
                "trade_date": trade_date.isoformat() if trade_date else None,
                "board_type": board_type,
                "query": query,
                "limit": limit,
            },
            output=payload,
            summary=(
                f"{payload['trade_date'] or '最新'} "
                f"{'东方财富' if snapshot.source == 'eastmoney' else '同花顺'}"
                f"龙虎榜命中 {len(items)} 条"
                f"{f'：{names}' if names else '。'}"
            ),
            trace_output=payload,
        )

    def finance_news(
        self,
        query: str | None = None,
        limit: int = 8,
        hours: int = 48,
    ) -> ToolResult:
        """Return recent structured financial-news evidence."""

        response: FinanceNewsFacts = collect_finance_news(
            query=query,
            limit=limit,
            hours=hours,
        )
        trace_output = response.model_dump(mode="json")
        sources = "、".join(response.sources)
        return ToolResult(
            name="finance_news",
            input={"query": query, "limit": limit, "hours": hours},
            output=response,
            summary=(
                f"{sources or '财经数据源'} 聚合到 {len(response.items)} 条"
                f"近 {response.window_hours} 小时财经快讯。"
            ),
            trace_output=trace_output,
        )

    def stock_news(
        self,
        symbol: str,
        days: int = 7,
        limit: int = 10,
    ) -> ToolResult:
        """Return cached, source-attributed news for one resolved stock."""

        resolved_symbol, resolved_name = self.resolve_stock_identity(symbol)
        response: StockNewsFacts = collect_stock_news(
            symbol=resolved_symbol,
            name=resolved_name,
            days=max(1, min(days, 30)),
            limit=max(1, min(limit, 20)),
            repository=StockNewsRepository(
                self.first_board_repository.database_url
            ),
        )
        trace_output = response.model_dump(mode="json")
        return ToolResult(
            name="stock_news",
            input={
                "symbol": resolved_symbol,
                "days": response.window_days,
                "limit": limit,
            },
            output=response,
            summary=(
                f"{resolved_name}({resolved_symbol}) 最近 {response.window_days} 天"
                f"获取到 {len(response.items)} 条个股资讯，缓存状态 {response.cache_status}。"
            ),
            trace_output=trace_output,
        )

    def stock_activity(
        self,
        symbol: str,
        days: int = 7,
        news_limit: int = 8,
    ) -> ToolResult:
        """Assemble one stock's after-close trend, events, enrichment and news."""

        resolved_symbol, resolved_name = self.resolve_stock_identity(symbol)
        fetched_at = datetime.now(timezone.utc)
        data_missing: list[str] = []
        kline: StockKLineFacts | None = None
        try:
            kline = self.stock_kline(
                resolved_symbol,
                days=20,
            ).output
        except Exception as error:  # noqa: BLE001
            data_missing.append(f"K 线数据：{error}")

        stock_news_result = self.stock_news(
            resolved_symbol,
            days=days,
            limit=news_limit,
        )
        news: StockNewsFacts = stock_news_result.output
        data_missing.extend(news.data_missing)

        # The key compares trade date. reverse=True reverses the resulting order.
        matching_events = sorted(
            (event for event in self.events if event.symbol == resolved_symbol),
            key=lambda event: event.trade_date,
            reverse=True,
        )
        recent_events = [
            {
                "trade_date": event.trade_date.isoformat(),
                "board_height": event.board_height,
                "closed_limit": event.closed_limit,
                "first_limit_time": event.first_limit_time.strftime("%H:%M"),
                "break_count": event.break_count,
                "industry": event.industry,
                "concept": event.concept,
            }
            for event in matching_events[:5]
        ]
        rating_context: dict[str, Any] = {}
        for event in matching_events:
            enrichment = self.first_board_repository.get_enrichment(
                resolved_symbol,
                event.trade_date,
            )
            if enrichment is None:
                continue
            rating_context = {
                "trade_date": enrichment.trade_date.isoformat(),
                "float_market_cap": enrichment.float_market_cap,
                "volume_ratio_5d": enrichment.volume_ratio_5d,
                "return_20d_pct": enrichment.return_20d_pct,
                "recent_limit_up_count_20d": enrichment.recent_limit_up_count_20d,
                "dragon_tiger_on_list": enrichment.dragon_tiger_on_list,
                "dragon_tiger_net_buy_amount": enrichment.dragon_tiger_net_buy_amount,
                "dragon_tiger_reason": enrichment.dragon_tiger_reason,
                "popularity_rank": enrichment.popularity_rank,
                "popularity_rank_change": enrichment.popularity_rank_change,
                "popularity_snapshot_at": (
                    enrichment.popularity_snapshot_at.isoformat()
                    if enrichment.popularity_snapshot_at
                    else None
                ),
                "position": (
                    enrichment.position.model_dump(mode="json")
                    if enrichment.position
                    else None
                ),
            }
            data_missing.extend(enrichment.data_missing)
            break
        if not recent_events:
            data_missing.append("本地没有该股票的近期涨停事件。")
        if not rating_context:
            data_missing.append("本地没有该股票的首板评分补充快照。")

        response = StockActivityFacts(
            symbol=resolved_symbol,
            name=resolved_name,
            fetched_at=fetched_at,
            data_as_of=kline.data_as_of if kline else None,
            kline=kline,
            recent_limit_up_events=recent_events,
            rating_context=rating_context,
            news=news,
            data_missing=list(dict.fromkeys(data_missing)),
        )
        trace_output = response.model_dump(mode="json")
        if isinstance(trace_output.get("kline"), dict):
            trace_output["kline"].pop("bars", None)
        return ToolResult(
            name="stock_activity",
            input={
                "symbol": resolved_symbol,
                "days": days,
                "news_limit": news_limit,
            },
            output=response,
            summary=(
                f"已汇总 {resolved_name}({resolved_symbol}) 的收盘走势、"
                f"{len(recent_events)} 条涨停记录和 {len(news.items)} 条个股资讯。"
            ),
            trace_output=trace_output,
        )

    def stock_profile(
        self,
        symbol: str,
        include_market_cap: bool = False,
    ) -> ToolResult:
        """Return one stock's company profile and optional current market-cap snapshot."""

        resolved_symbol, resolved_name = self.resolve_stock_identity(symbol)
        payload = build_stock_profile(
            resolved_symbol,
            include_market_cap=include_market_cap,
            fallback_name=resolved_name,
        )
        missing = list(payload.get("data_missing") or [])
        summary_parts = [f"{payload.get('name')}({resolved_symbol}) 公司资料"]
        if payload.get("industry"):
            summary_parts.append(
                f"行业 {payload['industry']}（{payload.get('industry_source') or '来源未标注'}）"
            )
        if payload.get("main_business"):
            summary_parts.append("主营业务已返回")
        if payload.get("total_market_cap") is not None:
            summary_parts.append("总市值已返回")
        if payload.get("float_market_cap") is not None:
            summary_parts.append("流通市值已返回")
        if missing:
            shown = "；".join(missing[:4])
            if len(missing) > 4:
                shown += f" 等 {len(missing)} 项"
            summary_parts.append(f"数据缺口：{shown}")
        status = str(payload.get("status") or "error")
        source_errors = tuple(payload.get("source_errors") or [])
        return ToolResult(
            name="stock_profile",
            input={
                "symbol": resolved_symbol,
                "include_market_cap": include_market_cap,
            },
            output=payload,
            summary="；".join(summary_parts) + "。",
            trace_output=payload,
            status="error" if status == "error" else "success",
            error="；".join(source_errors) if status == "error" else None,
            result_status=status,
            source_errors=source_errors,
        )

    def first_board_ratings(
        self,
        trade_date: date | None = None,
        symbols: list[str] | None = None,
    ) -> ToolResult:
        """Return explainable first-board ratings."""

        # Prefer the published snapshot so a historical question sees the rating
        # recorded for that date. Recalculation is explicitly labeled calculated;
        # it must not be presented as a prediction that was actually published.

        target_date = trade_date or max(
            (item.trade_date for item in self.events),
            default=None,
        )
        ratings = (
            self.first_board_repository.get_live_prediction_snapshot(target_date)
            if target_date is not None
            else None
        )
        persisted_snapshot = ratings is not None
        if ratings is None:
            ratings = build_first_board_ratings(
                events=self.events,
                trade_date=trade_date,
                first_board_repository=self.first_board_repository,
            ).model_copy(
                update={
                    "snapshot_source": "calculated",
                    "data_as_of": target_date,
                }
            )
        snapshot_candidate_count = len(ratings.candidates)
        filtered_out_count = len(ratings.filtered_out)
        recorded_eligible_count = ratings.universe_count - filtered_out_count
        if recorded_eligible_count < snapshot_candidate_count:
            recorded_eligible_count = None
        not_selected_count = (recorded_eligible_count - snapshot_candidate_count
                              if recorded_eligible_count is not None else None)
        source_truncated = (True if not_selected_count and not_selected_count > 0 else None)
        if not persisted_snapshot:
            recorded_eligible_count = snapshot_candidate_count
            not_selected_count = 0
            source_truncated = False
        requested_symbols = list(dict.fromkeys(symbols or []))[:20]
        if requested_symbols:
            allowed_symbols = set(requested_symbols)
            ratings = ratings.model_copy(
                update={
                    "candidates": [
                        item
                        for item in ratings.candidates
                        if item.facts.symbol in allowed_symbols
                    ]
                }
            )
        top = ratings.candidates[0] if ratings.candidates else None
        top_summary = (
            f"最高分 {top.facts.name}({top.facts.symbol}) {top.rating}/{top.score:.1f}"
            if top
            else "暂无入池候选"
        )
        intelligence = _recommendation_intelligence_by_symbol(
            self.first_board_repository.database_url,
            strategy="relay",
        )
        trace_output = {
            "trade_date": ratings.trade_date.isoformat(),
            "snapshot_source": ratings.snapshot_source,
            "data_as_of": ratings.data_as_of.isoformat() if ratings.data_as_of else None,
            "candidate_count": len(ratings.candidates),
            "returned_candidate_count": len(ratings.candidates),
            "snapshot_candidate_count": snapshot_candidate_count,
            "snapshot_top_limit": ratings.snapshot_top_limit,
            "selection_scope": "persisted_snapshot" if persisted_snapshot else "calculated_full_pool",
            "requested_symbols": requested_symbols,
            "universe_count": ratings.universe_count,
            "filtered_out_count": filtered_out_count,
            "recorded_eligible_candidate_count": recorded_eligible_count,
            "not_selected_count": not_selected_count,
            "source_truncated": source_truncated,
            "candidate_pool_complete": (False if requested_symbols or source_truncated
                                        else None if persisted_snapshot else True),
            "scope_note": (
                "返回历史已固化候选快照，不能称为完整评级合格池。snapshot_top_limit来自固化批次记录；"
                "recorded_eligible_candidate_count是快照记录的universe_count减filtered_out_count，"
                "not_selected_count是其中未保存为候选的数量，不是硬筛排除数。"
                "完整池覆盖无法证明时为null；truncated=false仅说明本次传输未裁切，不证明源快照完整。"
                if persisted_snapshot else "返回按本地当日全部事件重新计算的完整评级候选池；若指定symbols，返回范围再限制为所选标的。"
            ),
            "top_candidates": [
                {
                    "symbol": item.facts.symbol,
                    "name": item.facts.name,
                    "rating": item.rating,
                    "score": item.score,
                    "confidence": item.confidence,
                    "break_count": item.facts.break_count,
                    "industry": item.facts.industry,
                    "latest_intelligence": intelligence.get(item.facts.symbol),
                }
                for item in ratings.candidates[:5]
            ],
            "position_classification": compact_first_board_position_groups(
                ratings.candidates
            ),
        }
        if trade_date is None:
            recommendation_draft = _recommendation_draft_facts(
                self.first_board_repository.database_url,
                strategy="relay",
                expected_base_date=ratings.trade_date,
            )
            if recommendation_draft is not None:
                trace_output["recommendation_draft"] = recommendation_draft
        return ToolResult(
            name="first_board_ratings",
            input={
                "trade_date": trade_date.isoformat() if trade_date else None,
                "symbols": requested_symbols or None,
            },
            output=ratings,
            summary=(
                f"{ratings.trade_date.isoformat()} {'已固化首板评级快照' if persisted_snapshot else '重新计算的首板评级候选'}"
                f"返回{len(ratings.candidates)}只，"
                f"{top_summary}。"
            ),
            trace_output=trace_output,
        )

    def market_event_pool(
        self,
        *,
        event_type: str,
        trade_date: date | None = None,
        market: str | None = None,
        query: str | None = None,
        result_mode: str = "list",
        limit: int = 30,
    ) -> ToolResult:
        """Return one normalized completed-day price-limit event pool."""

        effective_type = normalize_market_event_type(event_type)
        if effective_type is None:
            raise ValueError(f"Unsupported market event type: {event_type}")
        if result_mode not in {"list", "count", "summary", "ranking"}:
            raise ValueError(f"Unsupported market event result mode: {result_mode}")
        reference_time = current_query_reference_time()
        target_date = trade_date or max(
            (event.trade_date for event in self.events),
            default=reference_time.date(),
        )
        effective_market = normalize_market_segment(market)
        effective_limit = max(1, min(limit, 100))
        source = "local-limit-up-events"

        # A completed-day question cannot treat an unobserved future session
        # as a verified zero. Today's status comes from the trading calendar;
        # the 15:30 cutoff still reflects daily data consolidation readiness.
        today = reference_time.date()
        calendar_facts: MarketCalendarFacts | None = None
        calendar_warning: str | None = None
        unavailable_reason = None
        if target_date > today:
            unavailable_reason = "requested_date_in_future"
        elif target_date == today:
            try:
                calendar_facts = build_market_calendar_facts(
                    now=reference_time,
                    local_data_through=max(
                        (event.trade_date for event in self.events),
                        default=None,
                    ),
                )
            except Exception as error:  # noqa: BLE001 - fall back without guessing
                calendar_warning = (
                    f"交易日历不可用：{type(error).__name__}；按本地时间判断收盘状态。"
                )
            if calendar_facts is not None:
                if calendar_facts.session_status == "non_trading_day":
                    unavailable_reason = "non_trading_day"
                elif calendar_facts.session_status == "unverified":
                    calendar_warning = (
                        "交易日历未确认今天是否为交易日；按本地时间判断收盘状态。"
                    )
                    if reference_time.time() < time(15, 0):
                        unavailable_reason = "market_not_closed"
                    elif reference_time.time() < time(15, 30):
                        unavailable_reason = "closing_data_pending"
                elif calendar_facts.session_status in {"pre_open", "open"}:
                    unavailable_reason = "market_not_closed"
                elif (
                    calendar_facts.session_status == "closed"
                    and reference_time.time() < time(15, 30)
                ):
                    unavailable_reason = "closing_data_pending"
            elif reference_time.time() < time(15, 0):
                unavailable_reason = "market_not_closed"
            elif reference_time.time() < time(15, 30):
                unavailable_reason = "closing_data_pending"
        if unavailable_reason is not None:
            data_missing = [unavailable_reason]
            if calendar_warning:
                data_missing.append(calendar_warning)
            payload = {
                "trade_date": target_date.isoformat(),
                "event_type": effective_type,
                "market": effective_market,
                "query": query,
                "result_mode": result_mode,
                "matched_count": None,
                "returned_count": 0,
                "items": [],
                "availability": unavailable_reason,
                "checked_at": reference_time.isoformat(),
                "data_missing": data_missing,
                "calendar": _calendar_trace(calendar_facts),
                "complete": False,
                "source": "completed-day-availability",
            }
            summary = (
                f"{target_date.isoformat()} 收盘事件数据尚不可用，数量未知："
                f"{_AVAILABILITY_LABELS.get(unavailable_reason, unavailable_reason)}。"
            )
            if calendar_warning:
                summary += " " + calendar_warning
            return ToolResult(
                name="market_event_pool",
                input={
                    "event_type": effective_type,
                    "trade_date": target_date.isoformat(),
                    "market": effective_market,
                    "query": query,
                    "result_mode": result_mode,
                    "limit": effective_limit,
                },
                output=payload,
                trace_output=payload,
                result_status="partial",
                summary=summary,
            )

        if effective_type == "limit_down":
            snapshot = collect_limit_down_pool(target_date)
            if snapshot.trade_date != target_date:
                raise ValueError(
                    "Limit-down source date does not match the requested trade date"
                )
            source = snapshot.source
            items = [
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "event_type": effective_type,
                    "change_pct": item.change_pct,
                    "industry": item.industry,
                }
                for item in snapshot.items
            ]
            if effective_market is not None:
                prefixes = MARKET_SEGMENT_PREFIXES[effective_market]
                items = [item for item in items if item["symbol"].startswith(prefixes)]
            if query:
                normalized_query = query.strip().lower()
                items = [
                    item
                    for item in items
                    if normalized_query in item["symbol"].lower()
                    or normalized_query in item["name"].lower()
                    or normalized_query in str(item.get("industry") or "").lower()
                ]
            # The key compares `item.get('change_pct') is None`, then `item.get('change_pct') or
            # 0.0`, then symbol.
            items = sorted(
                items,
                key=lambda item: (
                    item.get("change_pct") is None,
                    item.get("change_pct") or 0.0,
                    item["symbol"],
                ),
            )
            matched_count = len(items)
        else:
            status = "closed" if effective_type == "limit_up" else "failed"
            legacy_result = self.limit_up_events(
                trade_date=target_date,
                market=effective_market,
                query=query,
                event_status=status,
                limit=100,
            )
            items = [
                {
                    "symbol": event.symbol,
                    "name": event.name,
                    "event_type": effective_type,
                    "change_pct": None,
                    "industry": event.industry,
                    "concept": event.concept,
                    "board_height": event.board_height,
                    "first_limit_time": event.first_limit_time.strftime("%H:%M"),
                    "break_count": event.break_count,
                    "closed_limit": event.closed_limit,
                }
                for event in legacy_result.output
            ]
            # The event list is bounded, but its metadata counts the full
            # filtered set. Preserve that count for both count and list modes.
            matched_count = legacy_result.trace_output["matched_count"]

        visible_items = [] if result_mode == "count" else items[:effective_limit]
        labels = {
            "limit_up": "涨停",
            "limit_down": "跌停",
            "broken_board": "炸板未回封",
        }
        event_label = labels[effective_type]
        data_missing = [calendar_warning] if calendar_warning else []
        trace_output = {
            "trade_date": target_date.isoformat(),
            "event_type": effective_type,
            "event_label": event_label,
            "market": effective_market,
            "market_label": MARKET_SEGMENT_LABELS.get(effective_market or "") or None,
            "query": query,
            "result_mode": result_mode,
            "matched_count": matched_count,
            "returned_count": len(visible_items),
            "items": visible_items,
            "source": source,
            "data_missing": data_missing,
            "calendar": _calendar_trace(calendar_facts),
        }
        summary = f"{target_date.isoformat()} {event_label}查询命中 {matched_count} 只。"
        if calendar_warning:
            summary += " " + calendar_warning
        return ToolResult(
            name="market_event_pool",
            input={
                "event_type": effective_type,
                "trade_date": target_date.isoformat(),
                "market": effective_market,
                "query": query,
                "result_mode": result_mode,
                "limit": effective_limit,
            },
            output=trace_output,
            summary=summary,
            trace_output=trace_output,
            result_status="partial" if calendar_warning else "ok",
        )

    def limit_up_events(
        self,
        trade_date: date | None = None,
        board_height: int | None = None,
        min_board_height: int | None = None,
        highest_only: bool = False,
        market: str | None = None,
        query: str | None = None,
        broken_only: bool | None = None,
        closed_only: bool | None = None,
        event_status: str | None = None,
        recent_trade_days: int = 1,
        group_by: str | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        limit: int = 30,
    ) -> ToolResult:
        """Return filtered limit-up events for general limit-up questions."""

        effective_recent_days = max(1, min(recent_trade_days, 20))
        available_dates = sorted(
            {
                event.trade_date
                for event in self.events
                if trade_date is None or event.trade_date <= trade_date
            }
        )
        selected_dates = available_dates[-effective_recent_days:]
        if effective_recent_days == 1:
            target_events = events_for_date(self.events, trade_date)
            if trade_date is not None:
                selected_dates = [trade_date] if trade_date in available_dates else []
        else:
            selected_date_set = set(selected_dates)
            target_events = [
                event for event in self.events if event.trade_date in selected_date_set
            ]
        effective_market = normalize_market_segment(market)
        effective_status = normalize_event_status(event_status)
        if effective_status is None:
            if broken_only:
                effective_status = "broken_intraday"
            elif closed_only is False:
                effective_status = "all"
            else:
                effective_status = "closed"
        effective_closed_only = effective_status == "closed"
        target_events = filter_limit_up_events(
            target_events,
            board_height=board_height,
            min_board_height=min_board_height,
            market=effective_market,
            event_status=effective_status,
            query=query,
            highest_only=highest_only,
        )

        effective_sort_by = normalize_sort_field(sort_by) or "board_height"
        effective_sort_order = normalize_sort_order(sort_order) or (
            "asc" if effective_sort_by in {"first_limit_time", "symbol"} else "desc"
        )
        target_events = _sort_limit_up_events(
            target_events,
            sort_by=effective_sort_by,
            sort_order=effective_sort_order,
        )
        matched_count = len(target_events)
        unique_stock_count = len({event.symbol for event in target_events})
        sector_summary, unclassified_event_count = group_limit_up_events(
            target_events, group_by=group_by, limit=limit,
        )
        target_events = target_events[: max(1, min(limit, 100))]
        if effective_recent_days == 1 and trade_date is not None:
            trade_date_text = trade_date.isoformat()
        elif selected_dates:
            trade_date_text = selected_dates[-1].isoformat()
        elif trade_date is not None:
            trade_date_text = trade_date.isoformat()
        elif self.events:
            trade_date_text = max(event.trade_date for event in self.events).isoformat()
        else:
            trade_date_text = ""
        board_text = f"{board_height}板" if board_height is not None else "涨停"
        market_text = MARKET_SEGMENT_LABELS.get(effective_market or "", "")
        names = "、".join(f"{event.name}({event.symbol})" for event in target_events[:5])
        trace_output = {
            "trade_date": trade_date_text,
            "start_trade_date": selected_dates[0].isoformat() if selected_dates else None,
            "recent_trade_days": effective_recent_days,
            "selected_trade_day_count": len(selected_dates),
            "group_by": group_by,
            "market": effective_market,
            "market_label": market_text or None,
            "event_status": effective_status,
            "highest_only": highest_only,
            "sort_by": effective_sort_by,
            "sort_order": effective_sort_order,
            "matched_count": matched_count,
            "unique_stock_count": unique_stock_count,
            "returned_count": len(target_events),
            "sector_summary": sector_summary,
            "unclassified_event_count": unclassified_event_count,
            "events": [
                {
                    "symbol": event.symbol,
                    "name": event.name,
                    "board_height": event.board_height,
                    "board_height_text": (
                        f"{event.board_height}板" if event.closed_limit else "炸板未回封"
                    ),
                    "industry": event.industry,
                    "concept": event.concept,
                    "first_limit_time": event.first_limit_time.strftime("%H:%M"),
                    "break_count": event.break_count,
                    "closed_limit": event.closed_limit,
                }
                for event in target_events
            ],
        }
        return ToolResult(
            name="limit_up_events",
            input={
                "trade_date": trade_date.isoformat() if trade_date else None,
                "board_height": board_height,
                "min_board_height": min_board_height,
                "highest_only": highest_only,
                "market": effective_market,
                "query": query,
                "broken_only": broken_only,
                "closed_only": effective_closed_only,
                "event_status": effective_status,
                "recent_trade_days": effective_recent_days,
                "group_by": group_by,
                "sort_by": effective_sort_by,
                "sort_order": effective_sort_order,
                "limit": limit,
            },
            output=target_events,
            summary=(
                f"{trade_date_text} {market_text}{board_text}查询命中 {matched_count} "
                f"{'条事件' if group_by else '只'}"
                f"{f'：{names}' if names else '。'}"
            ),
            trace_output=trace_output,
        )

    def recent_limit_up_returns(
        self,
        end_date: date | None = None,
        recent_trade_days: int = 7,
        return_trade_days: int = 5,
    ) -> ToolResult:
        """Compute the full local event cohort before model-side selection."""

        cutoff = end_date or max((event.trade_date for event in self.events), default=None)
        payload = build_recent_limit_up_returns(
            load_post_limit_dataset(cutoff, database_url=self.first_board_repository.database_url),
            end_date=cutoff, recent_trade_days=recent_trade_days,
            return_trade_days=return_trade_days,
        )
        return ToolResult(
            name="recent_limit_up_returns",
            input={"end_date": cutoff.isoformat() if cutoff else None,
                   "recent_trade_days": recent_trade_days, "return_trade_days": return_trade_days},
            output=payload, trace_output=payload, result_status=payload["status"],
            summary=(f"近{recent_trade_days}个本地交易日已记录的收盘涨停候选共{payload['candidate_count']}只，"
                     f"{payload['analyzed_count']}只可计算{return_trade_days}个交易日收盘价涨幅，"
                     f"{payload['missing_count']}只数据不完整；范围不代表全市场。"),
        )

    def stock_kline(
        self,
        symbol: str,
        days: int = 20,
        end_date: date | None = None,
    ) -> ToolResult:
        """Return local-first K-line facts for a stock trend question."""

        try:
            resolved_symbol = self.resolve_stock_symbol(symbol)
        except ValueError:
            resolved_symbol = self.resolve_stock_identity(symbol)[0]
        available_dates = sorted({event.trade_date for event in self.events})
        resolved_end_date = end_date or (
            available_dates[-1] if available_dates else date.today()
        )
        response: StockKLineFacts = build_stock_kline_facts(
            symbol=resolved_symbol,
            days=max(5, min(days, 60)),
            end_date=resolved_end_date,
            repository=self.first_board_repository,
        )
        scoped_returns = kline_agent_view(response.model_dump(mode="json"))
        trace_output = {
            "symbol": response.symbol,
            "requested_days": response.requested_days,
            "requested_end_date": response.requested_end_date.isoformat(),
            "data_as_of": response.data_as_of.isoformat(),
            "data_fresh": response.data_fresh,
            "trend": response.trend,
            "latest_close": response.latest_close,
            "display_window_return": scoped_returns["display_window_return"],
            "return_windows": scoped_returns["return_windows"],
            "bar_count": len(response.bars),
        }
        return ToolResult(
            name="stock_kline",
            input={
                "symbol": resolved_symbol,
                "days": response.requested_days,
                "end_date": resolved_end_date.isoformat(),
            },
            output=response,
            summary=(
                f"{resolved_symbol} K-line through {response.data_as_of.isoformat()}: "
                f"trend={response.trend}, close={response.latest_close}; "
                f"displayed observations={len(response.bars)}. Return scopes are in display_window_return/return_windows."
            ),
            trace_output=trace_output,
        )

    def stock_financials(
        self,
        symbol: str,
        statement: str,
        period: str = "quarterly",
        limit: int = 6,
    ) -> ToolResult:
        """Return normalized financial statements for one stock."""

        resolved_symbol, stock_name = self.resolve_stock_identity(symbol)
        if stock_name == resolved_symbol:
            stock_name = None
        payload = build_stock_financials_facts(
            symbol=resolved_symbol,
            statement=statement,
            period=period,
            limit=limit,
            stock_name=stock_name,
            collector=self.hithink_collector,
        )
        summary = payload.pop("summary")
        rows = payload.get("rows") or []
        return ToolResult(
            name="stock_financials",
            input={
                "symbol": resolved_symbol,
                "statement": statement,
                "period": period,
                "limit": limit,
            },
            output=payload,
            summary=summary,
            trace_output=payload,
            result_status="ok" if rows else "empty",
        )

    def index_search(
        self,
        query: str,
        category: str | None = None,
        limit: int = 20,
    ) -> ToolResult:
        """Resolve index names or codes to a deterministic thscode."""

        payload = search_indices(
            query=query,
            category=category,
            limit=limit,
            collector=self.hithink_collector,
        )
        summary = payload.pop("summary")
        rows = payload.get("rows") or []
        return ToolResult(
            name="index_search",
            input={"query": query, "category": category, "limit": limit},
            output=payload,
            summary=summary,
            trace_output=payload,
            result_status="ok" if rows else "empty",
        )

    def index_quote(
        self,
        index: str,
        days: int | None = None,
        end_date: str | None = None,
    ) -> ToolResult:
        """Return one index snapshot and optional daily-history facts."""

        payload = build_index_quote(
            index=index,
            days=days,
            end_date=end_date,
            collector=self.hithink_collector,
        )
        summary = payload.pop("summary")
        has_data = (
            payload.get("snapshot_last_price") is not None
            or bool(payload.get("rows"))
        )
        return ToolResult(
            name="index_quote",
            input={"index": index, "days": days, "end_date": end_date},
            output=payload,
            summary=summary,
            trace_output=payload,
            result_status="ok" if has_data else "empty",
        )

    def index_constituents(
        self,
        index: str,
        limit: int = 1000,
    ) -> ToolResult:
        """Return one index's constituent list with explicit coverage."""

        payload = build_index_constituents(
            index=index,
            limit=limit,
            collector=self.hithink_collector,
        )
        summary = payload.pop("summary")
        rows = payload.get("rows") or []
        return ToolResult(
            name="index_constituents",
            input={"index": index, "limit": limit},
            output=payload,
            summary=summary,
            trace_output=payload,
            result_status="ok" if rows else "empty",
        )

    def post_limit_screen(self, contract: PostLimitQueryContract) -> ToolResult:
        """Return a read-only post-limit shape screen."""

        payload = build_post_limit_screen(
            load_post_limit_dataset(
                contract.data_as_of,
                database_url=self.first_board_repository.database_url,
            ),
            contract,
        )
        return ToolResult(
            name="post_limit_screen",
            input=contract.to_dict(),
            output=payload,
            summary=(
                f"{payload.get('data_as_of')} {payload.get('matched_count', 0)}只符合"
                f"涨停后形态，可评价覆盖率{payload.get('coverage_ratio', 0):.1%}。"
            ),
            trace_output={
                "data_as_of": payload.get("data_as_of"),
                "matched_count": payload.get("matched_count", 0),
                "evaluable_count": payload.get("evaluable_count", 0),
                "pool_count": payload.get("pool_count", 0),
                "coverage_ratio": payload.get("coverage_ratio", 0),
                "rule_version": payload.get("rule_version"),
                "candidates": [
                    {
                        "symbol": item.get("symbol"),
                        "name": item.get("name"),
                        "anchor_date": item.get("anchor_date"),
                    }
                    for item in payload.get("candidates", [])
                ],
                "data_missing": payload.get("data_missing", []),
            },
            result_status=(
                "partial" if payload.get("data_missing")
                else "empty" if not payload.get("matched_count")
                else "ok"
            ),
            data_fresh=payload.get("data_as_of") == payload.get("latest_data_date"),
        )

    def post_limit_path(self, contract: PostLimitQueryContract, symbol: str) -> ToolResult:
        """Return an annotated path from a resolved stock's limit-up anchor."""

        resolved_symbol = self.resolve_stock_identity(symbol)[0]
        payload = build_post_limit_path(
            load_post_limit_dataset(
                contract.data_as_of,
                database_url=self.first_board_repository.database_url,
            ),
            contract,
            symbol=resolved_symbol,
        )
        return ToolResult(
            name="post_limit_path",
            input={**contract.to_dict(), "symbol": resolved_symbol},
            output=payload,
            summary=(
                f"{resolved_symbol} 涨停后路径返回"
                f"{len(payload.get('path', []))}个交易日。"
            ),
            trace_output={
                "symbol": resolved_symbol,
                "data_as_of": payload.get("data_as_of"),
                "anchor": payload.get("anchor"),
                "path_count": len(payload.get("path", [])),
                "data_missing": payload.get("data_missing", []),
            },
            result_status=(
                "partial" if payload.get("data_missing")
                else "empty" if not payload.get("path")
                else "ok"
            ),
            data_fresh=payload.get("data_as_of") == payload.get("latest_data_date"),
        )

    def post_limit_statistics(self, contract: PostLimitQueryContract) -> ToolResult:
        """Return bounded, recomputed historical post-limit statistics."""

        payload = build_post_limit_statistics(
            load_post_limit_dataset(
                contract.data_as_of,
                database_url=self.first_board_repository.database_url,
            ),
            contract,
        )
        return ToolResult(
            name="post_limit_statistics",
            input=contract.to_dict(),
            output=payload,
            summary=(
                f"涨停后历史研究包含{payload.get('complete_sample_count', 0)}个完整样本、"
                f"{payload.get('complete_signal_date_count', 0)}个信号日。"
            ),
            trace_output={
                "data_as_of": payload.get("data_as_of"),
                "complete_sample_count": payload.get("complete_sample_count", 0),
                "complete_signal_date_count": payload.get("complete_signal_date_count", 0),
                "sample_quality": payload.get("sample_quality"),
                "rule_version": payload.get("rule_version"),
                "data_missing": payload.get("data_missing", []),
            },
            result_status=(
                "partial" if payload.get("data_missing")
                else "empty" if not payload.get("complete_sample_count")
                else "ok"
            ),
            data_fresh=False,
        )

    def resolve_stock_symbol(self, value: str) -> str:
        """Resolve a six-digit symbol or local stock name to a symbol."""

        normalized = value.strip()
        lowered = normalized.lower()
        if lowered.startswith(("sh", "sz")):
            normalized = normalized[2:]
        if len(normalized) == 6 and normalized.isdigit():
            return normalized

        compact = normalized.replace(" ", "")
        exact = {
            event.symbol
            for event in self.events
            if event.name.replace(" ", "") == compact
        }
        if len(exact) == 1:
            return exact.pop()
        contained = {
            event.symbol
            for event in self.events
            if event.name.replace(" ", "") in compact
        }
        if len(contained) == 1:
            return contained.pop()
        raise ValueError(f"Cannot resolve stock symbol from: {value}")

    def resolve_stock_identity(self, value: str) -> tuple[str, str]:
        """Resolve a stock and retain its latest locally known display name."""

        try:
            symbol = self.resolve_stock_symbol(value)
        except ValueError:
            compact = value.strip().replace(" ", "")
            directory = self._load_stock_name_directory()
            exact = [
                (symbol, name)
                for symbol, name in directory.items()
                if name.replace(" ", "") == compact
            ]
            # The key compares `len(item[1])`. reverse=True reverses the resulting order.
            contained = sorted(
                (
                    (symbol, name)
                    for symbol, name in directory.items()
                    if len(name.replace(" ", "")) >= 2
                    and name.replace(" ", "") in compact
                ),
                key=lambda item: len(item[1]),
                reverse=True,
            )
            candidates = exact or contained
            if not candidates:
                raise ValueError(f"Cannot resolve stock identity from: {value}") from None
            longest_length = len(candidates[0][1])
            longest = [item for item in candidates if len(item[1]) == longest_length]
            if len(longest) != 1:
                raise ValueError(f"Ambiguous stock identity: {value}") from None
            return longest[0]
        # The key compares trade date. reverse=True reverses the resulting order.
        matching = sorted(
            (event for event in self.events if event.symbol == symbol),
            key=lambda event: event.trade_date,
            reverse=True,
        )
        if matching:
            return symbol, matching[0].name
        compact = value.strip().replace(" ", "")
        if compact and not compact.isdigit() and len(compact) <= 20:
            return symbol, compact
        return symbol, self._load_stock_name_directory().get(symbol, symbol)

    def _load_stock_name_directory(self) -> dict[str, str]:
        """Load the bounded A-share name directory once per Agent request."""

        if self._stock_name_directory is not None:
            return self._stock_name_directory
        try:
            self._stock_name_directory = (
                self.hithink_collector.collect_a_share_symbol_names()
            )
        except Exception:  # noqa: BLE001
            self._stock_name_directory = {}
        return self._stock_name_directory

    def review_high_score_picks(
        self,
        start_date: date,
        end_date: date,
        min_score: float = 0,
        top_per_day: int = 10,
        as_of_date: date | None = None,
    ) -> ToolResult:
        """Run Review Agent over high-score picks and post-board outcomes."""

        latest_local_date = max((item.trade_date for item in self.events), default=None)
        observation_date = as_of_date or latest_local_date or end_date
        if observation_date < end_date:
            raise ValueError("观察截止日不得早于候选结束日；请确认候选日期范围和本地数据覆盖。")
        if latest_local_date is not None and observation_date > latest_local_date:
            raise ValueError(f"观察截止日超出本地已完成数据覆盖，最晚可用日期为{latest_local_date.isoformat()}。")
        response = build_review_agent_report(
            events=self.events,
            start_date=start_date,
            end_date=end_date,
            repository=self.first_board_repository,
            min_score=min_score,
            top_per_day=max(1, min(top_per_day, 20)),
            as_of_date=observation_date,
        )
        trace_output = {
            "start_date": response.start_date.isoformat(),
            "end_date": response.end_date.isoformat(),
            "as_of_date": observation_date.isoformat(),
            "horizon_coverage": response.horizon_coverage,
            "grouping_definitions": response.grouping_definitions,
            "prediction_source_counts": response.prediction_source_counts,
            "time_cohort_counts": response.time_cohort_counts,
            "evaluation_label_counts": response.evaluation_label_counts,
            "sample_size": response.sample_size,
            "success_count": response.success_count,
            "failed_count": response.failed_count,
            "pending_count": response.pending_count,
            "promotion_ready_date_count": response.promotion_ready_date_count,
            "top_pick_promotion_sample_size": response.top_pick_promotion_sample_size,
            "top_pick_promoted_count": response.top_pick_promoted_count,
            "top_pick_promotion_rate": response.top_pick_promotion_rate,
            "market_promotion_sample_size": response.market_promotion_sample_size,
            "market_promoted_count": response.market_promoted_count,
            "market_promotion_rate": response.market_promotion_rate,
            "promotion_rate_delta": response.promotion_rate_delta,
            "promotion_comparisons": [
                item.model_dump(mode="json")
                for item in response.promotion_comparisons
            ],
            "main_findings": response.main_findings[:3],
            "adjustment_suggestions": response.adjustment_suggestions[:3],
        }
        return ToolResult(
            name="review_high_score_picks",
            input={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "min_score": min_score,
                "top_per_day": top_per_day,
                "as_of_date": as_of_date.isoformat() if as_of_date else None,
            },
            output=response,
            summary=(
                f"Review Agent checked {response.sample_size} high-score picks "
                f"from {response.start_date.isoformat()} to {response.end_date.isoformat()}, "
                f"with observations through {observation_date.isoformat()}."
            ),
            trace_output=trace_output,
        )


def _recommendation_intelligence_by_symbol(
    database_url: str | None,
    *,
    strategy: str,
) -> dict[str, dict[str, Any]]:
    """Return compact latest evidence without expanding full news payloads."""

    response = RecommendationIntelligenceRepository(
        database_url
    ).get_latest()
    if response is None:
        return {}
    return {
        item.symbol: {
            "refreshed_at": item.refreshed_at.isoformat(),
            "sector": item.sector,
            "position_label": item.position_label,
            "rule_rank": item.rule_rank,
            "rule_score": item.rule_score,
            "base_rank": item.base_rank,
            "rank": item.rank,
            "base_score": item.base_score,
            "draft_score": item.draft_score,
            "facts_cutoff_at": (
                item.facts_cutoff_at.isoformat()
                if item.facts_cutoff_at
                else None
            ),
            "close_information_adjustment": item.close_information_adjustment,
            "close_information_reasons": item.close_information_reasons,
            "news_adjustment": item.news_adjustment,
            "financial_adjustment": item.financial_adjustment,
            "dragon_tiger_adjustment": item.dragon_tiger_adjustment,
            "popularity_adjustment": item.popularity_adjustment,
            "dynamic_adjustment": item.dynamic_adjustment,
            "dragon_tiger_on_list": item.dragon_tiger_on_list,
            "dragon_tiger_is_new": item.dragon_tiger_is_new,
            "dragon_tiger_net_buy_amount": item.dragon_tiger_net_buy_amount,
            "dragon_tiger_source": item.dragon_tiger_source,
            "popularity_base_rank": item.popularity_base_rank,
            "popularity_rank": item.popularity_rank,
            "popularity_rank_change": item.popularity_rank_change,
            "popularity_snapshot_at": (
                item.popularity_snapshot_at.isoformat()
                if item.popularity_snapshot_at
                else None
            ),
            "popularity_source": item.popularity_source,
            "update_reasons": item.update_reasons,
            "current_price": item.current_price,
            "change_pct": item.change_pct,
            "turnover": item.turnover,
            "latest_news": [
                {
                    "title": news.title,
                    "published_at": news.published_at.isoformat(),
                    "source": news.source,
                    "item_type": news.item_type,
                    "url": news.url,
                }
                for news in item.latest_news[:2]
            ],
            "financial_report": (
                item.financial_report.model_dump(mode="json")
                if item.financial_report
                else None
            ),
            "data_missing": item.data_missing,
        }
        for item in response.items
        if item.strategy == strategy
    }


def _recommendation_draft_facts(
    database_url: str | None,
    *,
    strategy: str,
    expected_base_date: date,
) -> dict[str, Any] | None:
    """Return the latest replaceable news and financial research ranking."""

    response = RecommendationIntelligenceRepository(database_url).get_latest()
    if response is None:
        return None
    # The key compares rank, then symbol.
    candidates = sorted(
        (
            item
            for item in response.items
            if item.strategy == strategy
            and item.base_trade_date == expected_base_date
        ),
        key=lambda item: (item.rank, item.symbol),
    )[:10]
    if not candidates:
        return None
    base_date = response.relay_base_date
    return {
        "stage": "draft",
        "base_date": base_date.isoformat() if base_date else None,
        "refreshed_at": response.refreshed_at.isoformat(),
        "status": response.status,
        "candidates": [item.model_dump(mode="json") for item in candidates],
        "warnings": response.warnings,
    }


def compact_first_board_position_groups(
    candidates: list[FirstBoardRating],
) -> dict[str, Any]:
    """Group the complete rated candidate pool by pre-board K-line position."""

    grouped: dict[tuple[str, str], list[tuple[FirstBoardRating, Any]]] = {}
    missing: list[FirstBoardRating] = []
    for candidate in candidates:
        enrichment = candidate.facts.enrichment
        position = enrichment.position if enrichment else None
        if position is None:
            missing.append(candidate)
            continue
        key = (position.primary.regime, position.primary.label)
        grouped.setdefault(key, []).append((candidate, position))

    groups: list[dict[str, Any]] = []
    for (regime, label), entries in grouped.items():
        # The key compares score (negated for descending order), then symbol.
        ordered = sorted(
            entries,
            key=lambda entry: (-entry[0].score, entry[0].facts.symbol),
        )
        groups.append(
            {
                "regime": regime,
                "label": label,
                "count": len(ordered),
                "avg_score": round(
                    sum(candidate.score for candidate, _ in ordered) / len(ordered),
                    1,
                ),
                "candidates": [
                    {
                        "symbol": candidate.facts.symbol,
                        "name": candidate.facts.name,
                        "industry": candidate.facts.industry,
                        "rating": candidate.rating,
                        "score": candidate.score,
                        "position_match_score": position.primary.score,
                        "position_confidence": position.confidence,
                        "tags": position.tags[:3],
                    }
                    for candidate, position in ordered
                ],
            }
        )

    # The key compares count (negated for descending order), then avg score (negated for
    # descending order), then label.
    groups.sort(
        key=lambda item: (-item["count"], -item["avg_score"], item["label"])
    )
    # The key compares score (negated for descending order), then symbol.
    return {
        "scope": "rated_first_board_candidate_pool",
        "scope_note": (
            "仅统计通过 ST、板块、新股/次新和最低成交额过滤的首板评级候选；"
            "位置指首板前 K 线所处阶段，不是首封时间。"
        ),
        "candidate_count": len(candidates),
        "classified_count": len(candidates) - len(missing),
        "missing_count": len(missing),
        "groups": groups,
        "missing_candidates": [
            {
                "symbol": candidate.facts.symbol,
                "name": candidate.facts.name,
                "rating": candidate.rating,
                "score": candidate.score,
            }
            for candidate in sorted(
                missing,
                key=lambda item: (-item.score, item.facts.symbol),
            )
        ],
    }
