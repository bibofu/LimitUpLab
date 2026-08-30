"""Central policy engine for grounding Agent answers with required tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, TypedDict

from app.agents.query_contract import (
    build_limit_up_query_contract,
    extract_board_filters as contract_board_filters,
    extract_result_limit,
    extract_market_segment as contract_market_segment,
    extract_trade_date as contract_trade_date,
)
from app.agents.tools import (
    AgentToolRegistry,
    ToolResult,
    compact_first_board_position_groups,
    compact_prediction_quality_audit,
)
from app.models import (
    AgentChatRequest,
    AgentToolTrace,
    FirstBoardRating,
    FirstBoardRatingsResponse,
)


class ToolExecution(TypedDict):
    """Mutable result accumulated while executing an LLM tool plan."""

    facts: dict[str, Any]
    tool_results: list[AgentToolTrace]
    tool_call_names: list[str]
    references: list[str]


@dataclass(frozen=True)
class QuestionSignals:
    """Small set of deterministic signals used only as Agent guardrails."""

    requested_date: date | None
    market_environment: bool
    market_index_trend: bool
    sector_performance: bool
    hot_stock_ranking: bool
    finance_news: bool
    web_search: bool
    daily_board_promotion: bool
    limit_up_events: bool
    first_board_facts: bool
    rating_explanation: bool
    stock_kline: bool
    prediction_quality: bool
    rating_backtest: bool
    critic: bool
    evaluation: bool
    review: bool
    scoring_policy: bool

    @classmethod
    def from_message(
        cls,
        message: str,
        capabilities: tuple[str, ...] = (),
    ) -> "QuestionSignals":
        """Parse guardrail signals once instead of across many repair functions."""

        capability_set = set(capabilities)

        prediction_quality = looks_like_prediction_quality_question(message)
        rating_backtest = (
            looks_like_rating_backtest_question(message) and not prediction_quality
        )
        scoring_policy = (
            looks_like_scoring_policy_question(message) and not prediction_quality
        )
        review = looks_like_review_question(message) and not scoring_policy
        evaluation = (
            looks_like_evaluation_question(message)
            and not review
            and not scoring_policy
            and not prediction_quality
        )
        finance_news = (
            "finance_news" in capability_set
            or looks_like_finance_news_question(message)
        )
        daily_board_promotion = (
            "board_promotion" in capability_set
            or looks_like_daily_board_promotion_question(message)
        )
        market_environment = (
            "market_environment" in capability_set
            or looks_like_market_environment_question(message)
        )
        market_index_trend = (
            "market_index_trend" in capability_set
            or looks_like_market_index_trend_question(message)
            or market_environment
        )
        return cls(
            requested_date=extract_trade_date(message),
            market_environment=market_environment,
            market_index_trend=market_index_trend,
            sector_performance=(
                "sector_performance" in capability_set
                or looks_like_sector_performance_question(message)
                or market_environment
            ),
            hot_stock_ranking=(
                "popularity" in capability_set
                or looks_like_hot_stock_question(message)
                or market_environment
            ),
            finance_news=finance_news,
            web_search=(
                "web_research" in capability_set
                or looks_like_web_search_question(message)
            ) and not finance_news,
            daily_board_promotion=daily_board_promotion,
            limit_up_events=(
                "limit_up_pool" in capability_set
                or looks_like_limit_up_event_question(message)
            ),
            first_board_facts=(
                "first_board_rating" in capability_set
                or looks_like_first_board_facts_question(message)
            ),
            rating_explanation=(
                "first_board_rating" in capability_set
                or looks_like_rating_explain_question(message)
            ),
            stock_kline=(
                (
                    "stock_trend" in capability_set
                    or looks_like_stock_kline_question(message)
                )
                and not market_index_trend
            ),
            prediction_quality=(
                "prediction_quality" in capability_set or prediction_quality
            ),
            rating_backtest=("rating_backtest" in capability_set or rating_backtest),
            critic=(
                "rating_critic" in capability_set
                or looks_like_critic_question(message)
            ),
            evaluation=("rating_evaluation" in capability_set or evaluation),
            review=("prediction_review" in capability_set or review),
            scoring_policy=("scoring_policy" in capability_set or scoring_policy),
        )

    @property
    def needs_domain_facts(self) -> bool:
        """Return whether a direct LLM answer would need local evidence."""

        return any(
            (
                self.sector_performance,
                self.market_environment,
                self.market_index_trend,
                self.hot_stock_ranking,
                self.finance_news,
                self.web_search,
                self.daily_board_promotion,
                self.limit_up_events,
                self.first_board_facts,
                self.rating_explanation,
                self.stock_kline,
                self.prediction_quality,
                self.rating_backtest,
                self.critic,
                self.evaluation,
                self.review,
                self.scoring_policy,
            )
        )

    @property
    def needs_local_event_date(self) -> bool:
        """Return whether date availability depends on the local limit-up store."""

        return any(
            (
                self.market_environment,
                self.limit_up_events,
                self.daily_board_promotion,
                self.first_board_facts,
                self.rating_explanation,
                self.prediction_quality,
                self.rating_backtest,
                self.critic,
                self.evaluation,
                self.review,
                self.scoring_policy,
            )
        )


RepairAction = Callable[[AgentChatRequest, QuestionSignals, ToolExecution, str | None], None]


@dataclass(frozen=True)
class ToolRepairRule:
    """Declarative contract for one required-tool policy."""

    name: str
    tool_name: str
    reason: str
    matches: Callable[[QuestionSignals], bool]
    repair: RepairAction


class AgentToolPolicyEngine:
    """Reconcile an LLM tool plan with minimum domain evidence requirements."""

    def __init__(
        self,
        tools: AgentToolRegistry,
        *,
        compact_ratings: Callable[[FirstBoardRatingsResponse], dict[str, Any]] | None = None,
    ) -> None:
        self.tools = tools
        self.compact_ratings = compact_ratings or _default_compact_ratings

    def requires_grounding(
        self,
        request: AgentChatRequest,
        capabilities: tuple[str, ...] = (),
    ) -> bool:
        """Return whether planner direct-answer mode must be rejected."""

        return QuestionSignals.from_message(
            request.message,
            capabilities,
        ).needs_domain_facts

    def resolve_stock_target(
        self,
        request: AgentChatRequest,
        *,
        context_symbol: str | None = None,
    ) -> str | None:
        """Resolve a stock from explicit request data, text, or recent context."""

        candidates = (
            request.symbol,
            _extract_symbol_hint(request.message),
            request.message,
            context_symbol,
        )
        for candidate in candidates:
            if not candidate:
                continue
            try:
                return self.tools.resolve_stock_symbol(candidate)
            except ValueError:
                continue
        return None

    def reconcile(
        self,
        *,
        request: AgentChatRequest,
        execution: ToolExecution,
        context_symbol: str | None = None,
        capabilities: tuple[str, ...] = (),
    ) -> list[str]:
        """Apply all matching rules and return names of repaired tools."""

        signals = QuestionSignals.from_message(request.message, capabilities)
        if not signals.needs_domain_facts:
            return []

        if self._requested_date_is_missing(signals):
            if not _has_tool_outcome(execution, "limit_up_event_dates"):
                self._repair_data_availability(request, signals, execution, context_symbol)
                return ["limit_up_event_dates"]
            return []

        repaired: list[str] = []
        for rule in self._rules():
            if not self.tools.is_enabled(rule.tool_name):
                continue
            if not rule.matches(signals) or _has_tool_outcome(execution, rule.tool_name):
                continue
            try:
                before = len(execution["tool_results"])
                rule.repair(request, signals, execution, context_symbol)
                if len(execution["tool_results"]) == before:
                    continue
                _mark_latest_trace_as_repair(execution, rule)
                repaired.append(rule.tool_name)
            except Exception as error:  # noqa: BLE001
                self._record_error(
                    execution,
                    rule=rule,
                    tool_input={"message": request.message},
                    summary=f"Policy repair for {rule.tool_name} failed.",
                    error=str(error),
                )
                repaired.append(rule.tool_name)
        return repaired

    def _rules(self) -> tuple[ToolRepairRule, ...]:
        """Return ordered rules; earlier tools may provide facts for later rules."""

        return (
            ToolRepairRule(
                name="market-summary-grounding",
                tool_name="market_summary",
                reason=(
                    "A broad market-environment answer requires completed limit-up, "
                    "unsealed and limit-down facts."
                ),
                matches=lambda signals: signals.market_environment,
                repair=self._repair_market_summary,
            ),
            ToolRepairRule(
                name="market-index-trend-grounding",
                tool_name="market_index_trend",
                reason=(
                    "A multi-day broad-market trend claim requires date-aligned "
                    "major-index history."
                ),
                matches=lambda signals: signals.market_index_trend,
                repair=self._repair_market_index_trend,
            ),
            ToolRepairRule(
                name="hot-stock-ranking-grounding",
                tool_name="hot_stock_ranking",
                reason="A current popularity ranking requires a fresh provider snapshot.",
                matches=lambda signals: signals.hot_stock_ranking,
                repair=self._repair_hot_stock_ranking,
            ),
            ToolRepairRule(
                name="finance-news-grounding",
                tool_name="finance_news",
                reason=(
                    "A broad current financial-news digest requires timestamped "
                    "structured finance feeds."
                ),
                matches=lambda signals: signals.finance_news,
                repair=self._repair_finance_news,
            ),
            ToolRepairRule(
                name="sector-performance-grounding",
                tool_name="sector_performance",
                reason="A whole-sector performance claim requires live sector market facts.",
                matches=lambda signals: signals.sector_performance,
                repair=self._repair_sector_performance,
            ),
            ToolRepairRule(
                name="web-search-grounding",
                tool_name="web_search",
                reason="The question asks for current external news or public-web evidence.",
                matches=lambda signals: signals.web_search,
                repair=self._repair_web_search,
            ),
            ToolRepairRule(
                name="daily-board-promotion-grounding",
                tool_name="daily_board_promotion",
                reason=(
                    "A daily promotion-rate claim requires adjacent local close cohorts."
                ),
                matches=lambda signals: signals.daily_board_promotion,
                repair=self._repair_daily_board_promotion,
            ),
            ToolRepairRule(
                name="limit-up-events-required",
                tool_name="limit_up_events",
                reason="The question requires the complete raw limit-up event set.",
                matches=lambda signals: signals.limit_up_events,
                repair=self._repair_limit_up_events,
            ),
            ToolRepairRule(
                name="rating-facts-required",
                tool_name="first_board_ratings",
                reason="The question requires candidate or rating facts.",
                matches=lambda signals: (
                    signals.first_board_facts or signals.rating_explanation
                ),
                repair=self._repair_ratings,
            ),
            ToolRepairRule(
                name="stock-trend-grounding",
                tool_name="stock_kline",
                reason="A single-stock trend answer requires fresh K-line facts.",
                matches=lambda signals: signals.stock_kline,
                repair=self._repair_stock_kline,
            ),
            ToolRepairRule(
                name="prediction-quality-audit-grounding",
                tool_name="prediction_quality_audit",
                reason=(
                    "A prediction-quality claim requires source-aware coverage, "
                    "maturity and baseline facts."
                ),
                matches=lambda signals: signals.prediction_quality,
                repair=self._repair_prediction_quality,
            ),
            ToolRepairRule(
                name="rating-backtest-grounding",
                tool_name="rating_backtest",
                reason="A rating-quality claim requires historical backtest facts.",
                matches=lambda signals: signals.rating_backtest,
                repair=self._repair_rating_backtest,
            ),
            ToolRepairRule(
                name="critic-grounding",
                tool_name="first_board_critic",
                reason="A reliability challenge requires supporting and opposing evidence.",
                matches=lambda signals: signals.critic,
                repair=self._repair_critic,
            ),
            ToolRepairRule(
                name="evaluation-grounding",
                tool_name="rating_evaluation",
                reason="A prediction review requires persisted outcomes and evaluation facts.",
                matches=lambda signals: signals.evaluation,
                repair=self._repair_evaluation,
            ),
            ToolRepairRule(
                name="scoring-policy-grounding",
                tool_name="scoring_policy_status",
                reason=(
                    "A scoring-strategy or self-improvement claim requires the active "
                    "Champion, Challenger and out-of-sample gate facts."
                ),
                matches=lambda signals: signals.scoring_policy,
                repair=self._repair_scoring_policy,
            ),
            ToolRepairRule(
                name="high-score-review-grounding",
                tool_name="review_high_score_picks",
                reason="A high-score review requires tracked picks and follow-up outcomes.",
                matches=lambda signals: signals.review,
                repair=self._repair_review,
            ),
        )

    def _requested_date_is_missing(self, signals: QuestionSignals) -> bool:
        requested_date = signals.requested_date
        return (
            signals.needs_local_event_date
            and requested_date is not None
            and requested_date not in {event.trade_date for event in self.tools.events}
        )

    def _repair_data_availability(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del request, context_symbol
        requested_date = signals.requested_date
        if requested_date is None:
            return
        available_dates = sorted(
            {event.trade_date for event in self.tools.events},
            reverse=True,
        )
        output = {
            "requested_trade_date": requested_date.isoformat(),
            "latest_local_trade_date": (
                available_dates[0].isoformat() if available_dates else None
            ),
            "available_trade_dates": [item.isoformat() for item in available_dates[:20]],
        }
        trace = AgentToolTrace(
            name="limit_up_event_dates",
            input={"requested_trade_date": requested_date.isoformat()},
            summary=f"本地没有 {requested_date.isoformat()}，已返回可用交易日列表。",
            output={
                **output,
                "policy_repair": {
                    "rule": "requested-date-availability",
                    "reason": "The requested trade date is absent from local data.",
                },
            },
        )
        execution["facts"]["limit_up_event_dates"] = output
        execution["tool_results"].append(trace)
        execution["tool_call_names"].append("limit_up_event_dates")
        _extend_references(
            execution,
            [f"missing_trade_date={requested_date.isoformat()}"],
        )

    def _repair_ratings(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del context_symbol
        trade_date = request.trade_date or signals.requested_date
        result = self.tools.first_board_ratings(trade_date=trade_date)
        ratings: FirstBoardRatingsResponse = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="first_board_ratings",
            fact_value=self.compact_ratings(ratings),
            references=[f"trade_date={ratings.trade_date.isoformat()}"],
        )

    def _repair_daily_board_promotion(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del context_symbol
        result = self.tools.daily_board_promotion(
            days=extract_promotion_days(request.message),
            end_date=request.trade_date or signals.requested_date,
        )
        items = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="daily_board_promotion",
            fact_value={
                "observed_days": len(items),
                "items": [item.model_dump(mode="json") for item in items],
            },
            references=[
                f"promotion_trade_date={item.trade_date.isoformat()}"
                for item in items
            ],
        )

    def _repair_sector_performance(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del context_symbol
        result = self.tools.sector_performance(
            sector=extract_sector_query(request.message),
            trade_date=request.trade_date or signals.requested_date,
        )
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="sector_performance",
            fact_value=response.model_dump(mode="json"),
            references=[
                f"sector={response.sector_name or 'industry-ranking'}",
                f"data_as_of={response.data_as_of.isoformat()}",
                *[f"source={source}" for source in response.sources],
            ],
        )

    def _repair_market_summary(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del request, signals, context_symbol
        result = self.tools.market_summary(include_limit_down=True)
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="market_summary",
            fact_value=result.trace_output,
            references=[
                f"trade_date={response.trade_date.isoformat()}",
                *(
                    [f"limit_down_source={response.limit_down_source}"]
                    if response.limit_down_source
                    else []
                ),
            ],
        )

    def _repair_market_index_trend(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del context_symbol
        result = self.tools.market_index_trend(
            days=extract_market_index_days(request.message),
            end_date=request.trade_date or signals.requested_date,
        )
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="market_index_trend",
            fact_value=response.model_dump(mode="json"),
            references=[
                f"index_data_as_of={response.data_as_of.isoformat()}",
                *[f"index_source={item.source}" for item in response.indices],
            ],
        )

    def _repair_web_search(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del signals, context_symbol
        result = self.tools.web_search(query=request.message, limit=5)
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="web_search",
            fact_value=response.model_dump(mode="json"),
            references=[item.url for item in response.results],
        )

    def _repair_finance_news(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del request, signals, context_symbol
        result = self.tools.finance_news(query=None, limit=8, hours=48)
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="finance_news",
            fact_value=response.model_dump(mode="json"),
            references=[item.url for item in response.items],
        )

    def _repair_hot_stock_ranking(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del context_symbol
        source = "auto"
        if "同花顺" in request.message:
            source = "tonghuashun"
        elif "东方财富" in request.message:
            source = "eastmoney"
        arguments: dict[str, Any] = {
            "period": "day",
            "limit": extract_result_limit(request.message) or 20,
            "source": source,
        }
        if signals.market_environment:
            arguments["enrich_performance"] = True
        result = self.tools.hot_stock_ranking(
            **arguments,
        )
        payload = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="hot_stock_ranking",
            fact_value=payload,
            references=[
                f"source={payload.get('source')}",
                f"captured_at={payload.get('captured_at')}",
                f"data_fresh={payload.get('data_fresh')}",
            ],
        )

    def _repair_limit_up_events(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del context_symbol
        contract = build_limit_up_query_contract(
            request.message,
            request_trade_date=request.trade_date or signals.requested_date,
        )
        result = self.tools.limit_up_events(
            trade_date=contract.trade_date,
            board_height=contract.board_height,
            min_board_height=contract.min_board_height,
            highest_only=contract.highest_only,
            market=contract.market,
            query=contract.query,
            event_status=contract.event_status,
            sort_by=contract.sort_by,
            sort_order=contract.sort_order,
            limit=contract.limit,
        )
        result.input["query_contract"] = contract.to_dict()
        result.trace_output["query_contract"] = contract.to_dict()
        payload = {
            "trade_date": result.trace_output.get("trade_date"),
            "market": result.trace_output.get("market"),
            "market_label": result.trace_output.get("market_label"),
            "matched_count": result.trace_output.get("matched_count"),
            "returned_count": result.trace_output.get("returned_count"),
            "query_contract": contract.to_dict(),
            "events": result.trace_output.get("events", []),
        }
        self._record_success(
            execution,
            result=result,
            fact_name="limit_up_events",
            fact_value=payload,
            references=[f"trade_date={payload['trade_date']}"],
        )

    def _repair_stock_kline(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del signals
        target = self.resolve_stock_target(request, context_symbol=context_symbol)
        if target is None:
            raise ValueError("Cannot resolve the requested stock symbol.")
        result = self.tools.stock_kline(
            symbol=target,
            days=extract_kline_days(request.message),
            end_date=request.trade_date,
        )
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="stock_kline",
            fact_value=response.model_dump(mode="json"),
            references=[
                f"symbol={response.symbol}",
                f"data_as_of={response.data_as_of.isoformat()}",
            ],
        )

    def _repair_rating_backtest(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del request, signals, context_symbol
        start_date, end_date = self._default_date_range()
        result = self.tools.rating_backtest(
            start_date=start_date,
            end_date=end_date,
            failure_limit=8,
        )
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="rating_backtest",
            fact_value=response.model_dump(mode="json"),
            references=[
                f"start_date={response.start_date.isoformat()}",
                f"end_date={response.end_date.isoformat()}",
            ],
        )

    def _repair_prediction_quality(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del request, signals, context_symbol
        available_dates = sorted({event.trade_date for event in self.tools.events})
        if not available_dates:
            raise ValueError("No local limit-up events available.")
        result = self.tools.prediction_quality_audit(
            start_date=available_dates[0],
            end_date=available_dates[-1],
            top_k=10,
        )
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="prediction_quality_audit",
            fact_value=compact_prediction_quality_audit(response),
            references=[
                f"start_date={response.start_date.isoformat()}",
                f"end_date={response.end_date.isoformat()}",
                f"scoring_version={response.audited_scoring_version}",
            ],
        )

    def _repair_critic(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        target = self._resolve_first_board_target(
            request,
            signals,
            execution,
            context_symbol,
        )
        if target is None:
            raise ValueError("Cannot resolve a stock and first-board date for critic review.")
        symbol, trade_date = target
        result = self.tools.first_board_critic(
            symbol=symbol,
            trade_date=trade_date,
        )
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="first_board_critic",
            fact_value=response.model_dump(mode="json"),
            references=[
                f"symbol={response.symbol}",
                f"trade_date={response.trade_date.isoformat()}",
                f"critic_verdict={response.verdict}",
            ],
        )

    def _repair_evaluation(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del request, signals, context_symbol
        start_date, end_date = self._default_date_range()
        result = self.tools.rating_evaluation(
            start_date=start_date,
            end_date=end_date,
            limit=30,
        )
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="rating_evaluation",
            fact_value=response.model_dump(mode="json"),
            references=[
                f"start_date={response.start_date.isoformat()}",
                f"end_date={response.end_date.isoformat()}",
            ],
        )

    def _repair_review(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del request, signals, context_symbol
        available_dates = sorted({event.trade_date for event in self.tools.events})
        if not available_dates:
            raise ValueError("No local limit-up events available.")
        end_date = available_dates[-1]
        start_date = available_dates[max(0, len(available_dates) - 6)]
        result = self.tools.review_high_score_picks(
            start_date=start_date,
            end_date=end_date,
            min_score=0,
            top_per_day=10,
        )
        response = result.output
        self._record_success(
            execution,
            result=result,
            fact_name="review_high_score_picks",
            fact_value=response.model_dump(mode="json"),
            references=[
                f"start_date={response.start_date.isoformat()}",
                f"end_date={response.end_date.isoformat()}",
                f"review_sample_size={response.sample_size}",
            ],
        )

    def _repair_scoring_policy(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> None:
        del request, signals, context_symbol
        result = self.tools.scoring_policy_status()
        payload = result.output
        champion = payload.get("champion") or {}
        latest = payload.get("latest_optimization") or {}
        challenger = latest.get("challenger_policy") or {}
        self._record_success(
            execution,
            result=result,
            fact_name="scoring_policy_status",
            fact_value=payload,
            references=[
                f"scoring_version={champion.get('version')}",
                f"challenger_version={challenger.get('version')}",
            ],
        )

    def _resolve_first_board_target(
        self,
        request: AgentChatRequest,
        signals: QuestionSignals,
        execution: ToolExecution,
        context_symbol: str | None,
    ) -> tuple[str, date] | None:
        facts = execution["facts"]
        rating_facts = facts.get("first_board_ratings")
        filter_facts = facts.get("first_board_filter")
        fact_date = None
        if isinstance(rating_facts, dict):
            fact_date = _parse_date(rating_facts.get("trade_date"))
        trade_date = (
            request.trade_date
            or signals.requested_date
            or fact_date
            or max((event.trade_date for event in self.tools.events), default=None)
        )
        if trade_date is None:
            return None

        explicit = self.resolve_stock_target(request, context_symbol=context_symbol)
        if explicit:
            return explicit, trade_date

        candidates: list[dict[str, Any]] = []
        if isinstance(filter_facts, dict):
            candidates.extend(
                item for item in filter_facts.get("matches", []) if isinstance(item, dict)
            )
        if isinstance(rating_facts, dict):
            candidates.extend(
                item
                for item in rating_facts.get("top_candidates", [])
                if isinstance(item, dict)
            )
        if len(candidates) == 1 and candidates[0].get("symbol"):
            return str(candidates[0]["symbol"]), trade_date
        return None

    def _default_date_range(self) -> tuple[date, date]:
        available_dates = sorted({event.trade_date for event in self.tools.events})
        if not available_dates:
            raise ValueError("No local limit-up events available.")
        return available_dates[max(0, len(available_dates) - 20)], available_dates[-1]

    @staticmethod
    def _record_success(
        execution: ToolExecution,
        *,
        result: ToolResult,
        fact_name: str,
        fact_value: Any,
        references: list[str],
        prepend: bool = False,
    ) -> None:
        execution["facts"][fact_name] = fact_value
        trace = result.trace()
        if prepend:
            execution["tool_results"].insert(0, trace)
            execution["tool_call_names"].insert(0, result.name)
        else:
            execution["tool_results"].append(trace)
            execution["tool_call_names"].append(result.name)
        _extend_references(execution, references)

    @staticmethod
    def _record_error(
        execution: ToolExecution,
        *,
        rule: ToolRepairRule,
        tool_input: dict[str, Any],
        summary: str,
        error: str,
    ) -> None:
        execution["facts"][f"{rule.tool_name}_error"] = error
        execution["tool_results"].append(
            AgentToolTrace(
                name=rule.tool_name,
                input=tool_input,
                summary=summary,
                status="error",
                output={
                    "policy_repair": {
                        "rule": rule.name,
                        "reason": rule.reason,
                    }
                },
                error=error,
            )
        )
        execution["tool_call_names"].append(rule.tool_name)


def extract_trade_date(message: str) -> date | None:
    """Extract a date from common Chinese and numeric expressions."""

    return contract_trade_date(message)


def extract_kline_days(message: str) -> int:
    """Extract a bounded trading-day window, defaulting to 20 days."""

    match = re.search(r"(?:最近|近)?\s*(\d{1,2})\s*(?:个?交易日|天|日)", message)
    if match is None:
        return 20
    return max(5, min(int(match.group(1)), 60))


def extract_market_index_days(message: str) -> int:
    """Extract a bounded broad-index window in trading days."""

    compact = re.sub(r"\s+", "", message)
    if any(term in compact for term in ("近一周", "最近一周", "过去一周", "本周")):
        return 5
    if any(term in compact for term in ("近两周", "最近两周", "过去两周")):
        return 10
    if any(term in compact for term in ("近一个月", "最近一个月", "过去一个月", "近一月")):
        return 20
    match = re.search(r"(?:最近|近|过去)?(\d{1,2})(?:个)?(?:交易日|天|日)", compact)
    if match:
        return max(2, min(int(match.group(1)), 20))
    return 5


def looks_like_market_index_trend_question(message: str) -> bool:
    """Return whether a question asks for broad-index multi-day performance."""

    compact = re.sub(r"\s+", "", message).lower()
    index_terms = (
        "大盘",
        "指数",
        "沪指",
        "上证",
        "深证成指",
        "创业板指",
        "a股走势",
        "a股大盘",
    )
    trend_terms = (
        "走势",
        "趋势",
        "表现",
        "涨跌",
        "行情",
        "近一周",
        "最近一周",
        "本周",
        "近一个月",
    )
    return any(term in compact for term in index_terms) and any(
        term in compact for term in trend_terms
    )


def looks_like_first_board_facts_question(message: str) -> bool:
    """Return whether the question needs the rated candidate pool, not a raw list."""

    return looks_like_first_board_position_question(message) or any(
        term in message
        for term in (
            "首板评分",
            "首板评级",
            "评分靠前",
            "评分最高",
            "高分候选",
            "候选池",
            "候选评分",
        )
    )


def looks_like_first_board_position_question(message: str) -> bool:
    """Return whether position means a pre-board K-line regime classification."""

    if "首板" not in message or "位置" not in message:
        return False
    return not any(
        term in message
        for term in ("首封时间", "封板时间", "几点封板", "几点涨停")
    )


def looks_like_limit_up_event_question(message: str) -> bool:
    """Return whether the question needs raw limit-up events rather than ratings."""

    if looks_like_daily_board_promotion_question(message):
        return False
    if looks_like_first_board_position_question(message):
        return False
    if any(
        term in message
        for term in (
            "评分",
            "评级",
            "候选",
            "回测",
            "复盘",
            "相似",
            "为什么",
            "权重",
            "策略",
        )
    ):
        return False
    return any(
        term in message
        for term in ("涨停", "首板", "连板", "二板", "三板", "炸板", "最高板")
    )


def looks_like_daily_board_promotion_question(message: str) -> bool:
    """Return whether the question asks for empirical board-promotion rates."""

    promotion_terms = ("晋级率", "晋级概率", "晋级情况", "晋级成功率")
    board_terms = ("涨停", "首板", "连板", "二板", "三板", "接力")
    scoring_policy_terms = ("Champion", "Challenger", "冠军", "挑战者", "评分策略")
    if any(term.lower() in message.lower() for term in scoring_policy_terms):
        return False
    stock_detail_question = "晋级" in message and any(
        term in message
        for term in ("哪些票", "哪些股票", "哪些个股", "股票有哪些", "票有哪些")
    )
    return (
        any(term in message for term in promotion_terms)
        and any(term in message for term in board_terms)
    ) or stock_detail_question


def extract_promotion_days(message: str) -> int:
    """Extract a bounded number of recent promotion observations."""

    match = re.search(r"(?:最近|近|过去)?\s*(\d{1,2})\s*(?:个?交易日|天|日)", message)
    return max(1, min(int(match.group(1)), 60)) if match else 5


def extract_board_filters(message: str) -> tuple[int | None, int | None]:
    """Extract exact or minimum board-height filters for policy repair."""

    return contract_board_filters(message)


def extract_market_segment(message: str) -> str | None:
    """Extract an explicit A-share board segment from the user question."""

    return contract_market_segment(message)


def extract_sector_query(message: str) -> str | None:
    """Extract a named industry from common Chinese sector questions."""

    compact = re.sub(r"\s+", "", message)
    patterns = (
        r"(?:今天|今日|最近|近期)?(.{1,16}?)(?:板块|行业)(?:今天|今日)?(?:表现|走势|行情|涨跌|强弱|资金|怎么样|如何|为何|为什么)",
        r"(?:今天|今日|最近|近期)?(.{1,16}?)(?:板块|行业)",
    )
    generic = {
        "哪些",
        "什么",
        "哪个",
        "行业",
        "板块",
        "热门",
        "强势",
        "弱势",
        "A股",
        "a股",
    }
    for pattern in patterns:
        matched = re.search(pattern, compact)
        if not matched:
            continue
        candidate = matched.group(1)
        candidate = re.sub(r"^(?:请问|看看|分析一下|分析|总结一下)", "", candidate)
        if candidate and candidate not in generic and len(candidate) <= 12:
            return candidate
    return None


def looks_like_market_environment_question(message: str) -> bool:
    """Return whether a question requests a broad current market review."""

    compact = re.sub(r"[\s，。！？,.!?]", "", message).lower()
    explicit_terms = (
        "市场环境",
        "市场情况",
        "盘面环境",
        "盘面情况",
        "市场全貌",
        "市场概况",
        "市场综述",
    )
    if any(term in compact for term in explicit_terms):
        return True
    return any(
        phrase in compact
        for phrase in (
            "今天市场怎么样",
            "今日市场怎么样",
            "今天a股怎么样",
            "今日a股怎么样",
            "现在市场怎么样",
        )
    )


def looks_like_sector_performance_question(message: str) -> bool:
    """Return whether text asks about whole-sector market performance."""

    if not any(term in message for term in ("板块", "行业")):
        return False
    asks_performance = any(
        term in message
        for term in (
            "表现",
            "走势",
            "行情",
            "涨跌",
            "强弱",
            "资金流",
            "净流入",
            "成交额",
            "领涨",
            "领跌",
            "涨得",
            "跌得",
            "上涨",
            "下跌",
        )
    )
    if not asks_performance:
        return False
    if any(term in message for term in ("首板", "涨停", "评分", "高分票")):
        return "板块表现" in message or "行业表现" in message
    return True


def looks_like_hot_stock_question(message: str) -> bool:
    """Return whether the user asks for a current stock-popularity ranking."""

    compact = re.sub(r"\s+", "", message).lower()
    explicit_ranking = any(
        term in compact
        for term in (
            "热股榜",
            "热门股票",
            "热门股",
            "人气榜",
            "人气排名",
            "热度排名",
            "哪些股票热门",
        )
    )
    stock_terms = ("股票", "个股", "哪些票", "什么票", "票比较")
    popularity_terms = ("热门", "人气高", "热度高", "关注度高")
    return explicit_ranking or (
        any(term in compact for term in stock_terms)
        and any(term in compact for term in popularity_terms)
    )


def looks_like_web_search_question(message: str) -> bool:
    """Return whether an answer needs current public-web evidence."""

    if any(
        term in message
        for term in (
            "新闻",
            "消息",
            "资讯",
            "公告",
            "政策",
            "研报",
            "舆情",
            "催化",
            "异动原因",
            "上涨原因",
            "下跌原因",
            "大涨原因",
            "大跌原因",
        )
    ):
        return True
    return "为什么" in message and any(
        term in message for term in ("上涨", "下跌", "大涨", "大跌", "异动")
    )


def looks_like_finance_news_question(message: str) -> bool:
    """Return whether the user asks for a broad current financial-news digest."""

    compact = re.sub(r"[\s，。！？,.!?]", "", message).lower()
    if "财经" in compact and any(term in compact for term in ("新闻", "快讯", "资讯", "消息")):
        return True
    if any(
        phrase in compact
        for phrase in (
            "今天有什么新闻",
            "今日有什么新闻",
            "最近有什么新闻",
            "有什么最新新闻",
            "最新市场快讯",
            "今日市场快讯",
            "最新市场新闻",
        )
    ):
        return not any(term in compact for term in ("个股", "公司", "板块", "关于"))
    return False


def looks_like_stock_kline_question(message: str) -> bool:
    """Return whether a question asks about one stock's price trend."""

    lowered = message.lower()
    asks_trend = any(
        term in lowered
        for term in (
            "k线",
            "k-line",
            "走势",
            "趋势",
            "均线",
            "量能",
            "成交量",
            "最近涨跌",
            "近期涨跌",
        )
    )
    return asks_trend and not ("高分" in message and "后续走势" in message)


def looks_like_rating_backtest_question(message: str) -> bool:
    """Return whether a question asks for aggregate rating backtesting."""

    explicit = any(
        term in message
        for term in (
            "回测",
            "准不准",
            "准吗",
            "自我评价",
            "评分效果",
            "评级表现",
            "失败样本",
        )
    )
    return explicit or (
        "表现怎么样" in message
        and any(term in message for term in ("评分", "评级", "预测", "高分票"))
    )


def looks_like_prediction_quality_question(message: str) -> bool:
    """Return whether a claim needs the source-aware prediction audit."""

    lowered = message.lower()
    return any(
        term in lowered
        for term in (
            "预测质量审计",
            "预测质量",
            "结果覆盖率",
            "outcome覆盖",
            "样本完整",
            "样本成熟",
            "基线对比",
            "随机基线",
            "封板基线",
            "v3准备",
            "v3 准备",
            "评分v3",
            "评分 v3",
        )
    )


def looks_like_evaluation_question(message: str) -> bool:
    """Return whether a question asks for persisted prediction evaluation."""

    explicit = any(
        term in message.lower()
        for term in (
            "复盘",
            "预测",
            "评分最高的票表现",
            "昨天评分最高",
            "哪些评分错了",
            "误判",
            "漏判",
            "因子有效",
            "因子失效",
            "自我改进",
            "evaluation",
        )
    )
    return explicit or (
        "表现怎么样" in message
        and any(term in message for term in ("评分", "高分票", "推荐票"))
    )


def looks_like_review_question(message: str) -> bool:
    """Return whether a question asks to review recent high-score picks."""

    return any(
        term in message
        for term in (
            "高分票",
            "高评分",
            "首板审美",
            "审美",
            "最近评分",
            "后续走势",
            "走得怎么样",
            "规则改进",
            "权重",
            "偏差",
            "复盘高分",
            "复盘一下高分",
        )
    )


def looks_like_scoring_policy_question(message: str) -> bool:
    """Return whether a question asks how rating weights evolve or are activated."""

    lowered = message.lower()
    return any(
        term in lowered
        for term in (
            "评分策略",
            "评分权重",
            "权重优化",
            "权重更新",
            "策略迭代",
            "策略版本",
            "自动学习",
            "自主学习",
            "自动调参",
            "champion",
            "challenger",
        )
    )


def looks_like_critic_question(message: str) -> bool:
    """Return whether a question asks for rating criticism or reliability."""

    return any(
        term in message.lower()
        for term in (
            "靠谱",
            "可信",
            "可靠",
            "质疑",
            "反驳",
            "打脸",
            "高估",
            "低估",
            "过度乐观",
            "critic",
            "critique",
        )
    )


def looks_like_rating_explain_question(message: str) -> bool:
    """Return whether a question needs original candidate rating facts."""

    if (
        looks_like_rating_backtest_question(message)
        or looks_like_prediction_quality_question(message)
        or looks_like_evaluation_question(message)
        or looks_like_review_question(message)
        or looks_like_scoring_policy_question(message)
    ):
        return False
    return any(
        term in message.lower()
        for term in ("评分", "评级", "高分", "低分", "分高", "分低", "score")
    )


def _has_tool_outcome(execution: ToolExecution, tool_name: str) -> bool:
    facts = execution["facts"]
    return tool_name in facts or f"{tool_name}_error" in facts


def _extend_references(execution: ToolExecution, references: list[str]) -> None:
    execution["references"] = list(
        dict.fromkeys([*execution["references"], *references])
    )


def _mark_latest_trace_as_repair(
    execution: ToolExecution,
    rule: ToolRepairRule,
) -> None:
    trace = execution["tool_results"][-1]
    trace.output = {
        **trace.output,
        "policy_repair": {
            "rule": rule.name,
            "reason": rule.reason,
        },
    }


def _extract_symbol_hint(message: str) -> str | None:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", message)
    return match.group(1) if match else None


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _default_compact_ratings(ratings: FirstBoardRatingsResponse) -> dict[str, Any]:
    """Provide a compact fallback serializer for standalone policy tests."""

    return {
        "trade_date": ratings.trade_date.isoformat(),
        "candidate_count": len(ratings.candidates),
        "filtered_out_count": len(ratings.filtered_out),
        "top_candidates": [_compact_rating(item) for item in ratings.candidates[:10]],
        "position_classification": compact_first_board_position_groups(
            ratings.candidates
        ),
    }


def _compact_rating(rating: FirstBoardRating) -> dict[str, Any]:
    return {
        "symbol": rating.facts.symbol,
        "name": rating.facts.name,
        "industry": rating.facts.industry,
        "rating": rating.rating,
        "score": rating.score,
        "confidence": rating.confidence,
    }
