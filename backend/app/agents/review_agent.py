"""LLM tool-driven Review Agent for high-score first-board picks."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any

from app.models import (
    AgentEvaluationItem,
    AgentPrediction,
    AgentToolTrace,
    LimitUpEvent,
    ReviewAgentPostBar,
    ReviewAgentPick,
    ReviewAgentReportResponse,
    ReviewPromotionComparison,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.evaluation_agent import build_agent_evaluation
from app.services.llm_provider import LLMProvider, get_llm_provider
from app.services.outcome_completeness import build_top10_outcome_completeness


REVIEW_AGENT_VERSION = "review-agent-tool-use-v4"


@dataclass(frozen=True)
class ReviewToolResult:
    """Internal Review Agent tool result."""

    name: str
    input: dict[str, Any]
    output: dict[str, Any]
    summary: str
    status: str = "success"
    error: str | None = None

    def trace(self) -> AgentToolTrace:
        """Return a compact frontend/debug trace."""

        return AgentToolTrace(
            name=self.name,
            input=self.input,
            output=self.output,
            summary=self.summary,
            status=self.status,  # type: ignore[arg-type]
            error=self.error,
        )


class ReviewAgentToolbox:
    """Tools available to the Review Agent planner."""

    def __init__(
        self,
        *,
        events: list[LimitUpEvent],
        repository: SQLiteFirstBoardRepository,
        start_date: date,
        end_date: date,
        min_score: float,
        top_per_day: int,
        follow_days: int,
    ):
        self.events = events
        self.repository = repository
        self.start_date = start_date
        self.end_date = end_date
        self.min_score = min_score
        self.top_per_day = top_per_day
        self.follow_days = follow_days
        self._evaluations: list[AgentEvaluationItem] | None = None
        self._predictions: dict[str, AgentPrediction] | None = None

    def daily_high_score_picks(self) -> ReviewToolResult:
        """Return daily top-scored prediction snapshots in the period."""

        picks = self._high_score_evaluations()
        return ReviewToolResult(
            name="daily_high_score_picks",
            input={
                "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat(),
                "min_score": self.min_score,
                "top_per_day": self.top_per_day,
            },
            output={
                "count": len(picks),
                "picks": [_pick_summary(item) for item in picks[:20]],
            },
            summary=f"Loaded {len(picks)} daily top-scored first-board picks.",
        )

    def pick_outcomes(self) -> ReviewToolResult:
        """Return post-board outcomes for daily top-scored picks."""

        picks = self._high_score_evaluations()
        ready = [item for item in picks if item.outcome_ready]
        return ReviewToolResult(
            name="pick_outcomes",
            input={
                "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat(),
                "min_score": self.min_score,
                "top_per_day": self.top_per_day,
                "follow_days": self.follow_days,
            },
            output={
                "sample_size": len(picks),
                "outcome_ready_count": len(ready),
                "success_count": sum(1 for item in picks if item.evaluation_label == "success"),
                "failed_count": sum(1 for item in picks if item.evaluation_label == "miss"),
                "pending_count": sum(1 for item in picks if item.evaluation_label == "pending"),
                "outcomes": [_outcome_summary(item) for item in picks[:20]],
            },
            summary=f"Reviewed outcomes for {len(picks)} picks; {len(ready)} are ready.",
        )

    def compare_success_failure_features(self) -> ReviewToolResult:
        """Compare original prediction features between successes and failures."""

        picks = self._high_score_evaluations()
        comparison = _build_feature_comparison(picks, self._prediction_lookup())
        return ReviewToolResult(
            name="compare_success_failure_features",
            input={
                "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat(),
                "min_score": self.min_score,
                "top_per_day": self.top_per_day,
            },
            output=comparison,
            summary=(
                f"Compared {comparison['success_count']} successful and "
                f"{comparison['failed_count']} failed high-score picks."
            ),
        )

    def compare_top10_market_promotion(self) -> ReviewToolResult:
        """Compare daily Top10 promotion with the full first-board cohort."""

        comparisons = _build_promotion_comparisons(
            events=self.events,
            picks=self._high_score_evaluations(),
            end_date=self.end_date,
        )
        aggregate = _aggregate_promotion_comparisons(comparisons)
        return ReviewToolResult(
            name="compare_top10_market_promotion",
            input={
                "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat(),
                "top_per_day": self.top_per_day,
            },
            output={
                **aggregate,
                "daily_comparisons": [
                    item.model_dump(mode="json") for item in comparisons
                ],
            },
            summary=(
                "Compared daily Top10 first-to-second promotion with all "
                f"market first boards across {aggregate['promotion_ready_date_count']} ready dates."
            ),
        )

    def prediction_for(self, prediction_id: str) -> AgentPrediction | None:
        """Return the immutable prediction snapshot used by one evaluation."""

        return self._prediction_lookup().get(prediction_id)

    def feature_comparison(self) -> dict[str, Any]:
        """Return a deterministic success/failure feature comparison."""

        return _build_feature_comparison(
            self._high_score_evaluations(),
            self._prediction_lookup(),
        )

    def _prediction_lookup(self) -> dict[str, AgentPrediction]:
        if self._predictions is None:
            self._predictions = {
                item.prediction_id: item
                for item in self.repository.list_predictions_between(
                    self.start_date,
                    self.end_date,
                )
            }
        return self._predictions

    def _high_score_evaluations(self) -> list[AgentEvaluationItem]:
        if self._evaluations is None:
            response = build_agent_evaluation(
                events=self.events,
                start_date=self.start_date,
                end_date=self.end_date,
                first_board_repository=self.repository,
                limit=500,
            )
            by_date: dict[date, list[AgentEvaluationItem]] = {}
            for item in response.evaluations:
                if item.score < self.min_score:
                    continue
                by_date.setdefault(item.trade_date, []).append(item)
            selected: list[AgentEvaluationItem] = []
            for trade_date in sorted(by_date):
                daily_items = sorted(
                    by_date[trade_date],
                    key=lambda item: (-item.score, -item.confidence, item.symbol),
                )
                selected.extend(daily_items[: max(self.top_per_day, 0)])
            self._evaluations = selected
        return self._evaluations


def build_review_agent_report(
    *,
    events: list[LimitUpEvent],
    start_date: date,
    end_date: date,
    repository: SQLiteFirstBoardRepository | None = None,
    min_score: float = 85,
    top_per_day: int = 10,
    follow_days: int = 5,
    provider: LLMProvider | None = None,
) -> ReviewAgentReportResponse:
    """Run the Review Agent with LLM-planned tools and return a structured report."""

    active_repository = repository or SQLiteFirstBoardRepository()
    toolbox = ReviewAgentToolbox(
        events=events,
        repository=active_repository,
        start_date=start_date,
        end_date=end_date,
        min_score=min_score,
        top_per_day=top_per_day,
        follow_days=follow_days,
    )
    active_provider = provider or get_llm_provider()
    tool_names = _plan_review_tools(active_provider, start_date, end_date, min_score)
    tool_results = [_run_review_tool(toolbox, name) for name in tool_names]
    facts = {result.name: result.output for result in tool_results if result.status == "success"}
    picks = _review_picks_from_toolbox(toolbox)
    promotion_comparisons = _build_promotion_comparisons(
        events=events,
        picks=picks,
        end_date=end_date,
    )
    feature_comparison = toolbox.feature_comparison()
    completeness = build_top10_outcome_completeness(
        events=events,
        repository=active_repository,
        as_of_date=end_date,
        tracking_days=max(follow_days, 0) + 1,
        top_per_day=top_per_day,
    )
    fallback = _fallback_report(
        start_date=start_date,
        end_date=end_date,
        picks=picks,
        tool_results=[result.trace() for result in tool_results],
        warnings=[
            "LLM review unavailable; deterministic fallback generated from tool facts.",
            *completeness.warnings,
        ],
        feature_comparison=feature_comparison,
        promotion_comparisons=promotion_comparisons,
    )
    try:
        content = active_provider.generate(
            _review_report_system_prompt(),
            _review_report_user_prompt(start_date, end_date, min_score, facts),
        ).content
        payload = _extract_json_object(content)
        report = _report_from_payload(
            payload=payload,
            start_date=start_date,
            end_date=end_date,
            picks=picks,
            tool_results=[result.trace() for result in tool_results],
            feature_comparison=feature_comparison,
            promotion_comparisons=promotion_comparisons,
        )
        report.warnings.extend(completeness.warnings)
        return report
    except Exception as error:
        fallback.warnings.append(f"Review LLM unavailable: {error}")
        return fallback


def _plan_review_tools(
    provider: LLMProvider,
    start_date: date,
    end_date: date,
    min_score: float,
) -> list[str]:
    """Ask the LLM planner which review tools are needed."""

    available = [
        "daily_high_score_picks",
        "pick_outcomes",
        "compare_top10_market_promotion",
        "compare_success_failure_features",
    ]
    try:
        result = provider.generate(
            "You are a Review Agent planner. Return JSON only.",
            (
                "Goal: review high-score A-share first-board picks and improve taste. "
                f"Period: {start_date.isoformat()} to {end_date.isoformat()}, min_score={min_score}. "
                f"Available tools: {available}. "
                'Return {"tool_calls":[{"name":"daily_high_score_picks"}, ...]}.'
            ),
        )
        payload = _extract_json_object(result.content)
        requested = [
            str(item.get("name"))
            for item in payload.get("tool_calls", [])
            if isinstance(item, dict) and item.get("name") in available
        ]
        if requested:
            return list(dict.fromkeys(requested))
    except Exception:
        pass
    return available


def _run_review_tool(toolbox: ReviewAgentToolbox, name: str) -> ReviewToolResult:
    """Execute one Review Agent tool by name."""

    try:
        if name == "daily_high_score_picks":
            return toolbox.daily_high_score_picks()
        if name == "pick_outcomes":
            return toolbox.pick_outcomes()
        if name == "compare_top10_market_promotion":
            return toolbox.compare_top10_market_promotion()
        if name == "compare_success_failure_features":
            return toolbox.compare_success_failure_features()
        return ReviewToolResult(
            name=name,
            input={},
            output={},
            summary=f"Unknown review tool: {name}",
            status="error",
            error="unknown tool",
        )
    except Exception as error:
        return ReviewToolResult(
            name=name,
            input={},
            output={},
            summary=f"Review tool {name} failed.",
            status="error",
            error=str(error),
        )


def _review_picks_from_toolbox(toolbox: ReviewAgentToolbox) -> list[ReviewAgentPick]:
    picks: list[ReviewAgentPick] = []
    for item in toolbox._high_score_evaluations():
        prediction = toolbox.prediction_for(item.prediction_id)
        post_bars = _post_bars_for_pick(toolbox, item)
        expected_count = _expected_post_bar_count(toolbox, item.trade_date)
        picks.append(
            ReviewAgentPick(
                trade_date=item.trade_date,
                symbol=item.symbol,
                name=item.name,
                score=item.score,
                rating=item.rating,
                confidence=item.confidence,
                prediction_source=item.prediction_source,
                data_as_of=item.data_as_of,
                evaluation_label=item.evaluation_label,
                outcome_ready=item.outcome_ready,
                promoted_to_second_board=item.promoted_to_second_board,
                next_high_pct=item.next_high_pct,
                next_close_pct=item.next_close_pct,
                next_open_to_high_pct=item.next_open_to_high_pct,
                next_open_to_low_pct=item.next_open_to_low_pct,
                next_open_to_close_pct=item.next_open_to_close_pct,
                three_day_high_pct=item.three_day_high_pct,
                three_day_close_pct=item.three_day_close_pct,
                three_day_open_to_close_pct=item.three_day_open_to_close_pct,
                max_drawdown_from_next_open_3d=item.max_drawdown_from_next_open_3d,
                reasons=list(prediction.reasons) if prediction else [item.lesson],
                risks=list(prediction.risks) if prediction else [item.scoring_suggestion],
                post_bars=post_bars,
                expected_post_bar_count=expected_count,
                post_bar_cache_complete=len(post_bars) >= expected_count,
            )
        )
    return picks


def _expected_post_bar_count(toolbox: ReviewAgentToolbox, trade_date: date) -> int:
    """Return base day plus currently elapsed follow-up trading days."""

    available_dates = {
        event.trade_date
        for event in toolbox.events
        if trade_date <= event.trade_date <= toolbox.end_date
    }
    return min(max(toolbox.follow_days, 0) + 1, len(available_dates))


def _post_bars_for_pick(
    toolbox: ReviewAgentToolbox,
    item: AgentEvaluationItem,
) -> list[ReviewAgentPostBar]:
    """Return cached base-day plus follow-up bars for a reviewed pick."""

    bars = toolbox.repository.list_post_bars(
        item.symbol,
        item.trade_date,
        limit=max(toolbox.follow_days, 0) + 1,
    )
    base_close = bars[0].close if bars else None
    return [
        ReviewAgentPostBar(
            trade_date=bar.trade_date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            change_pct=bar.change_pct,
            return_from_base_pct=(
                ((bar.close - base_close) / base_close) * 100
                if base_close
                else None
            ),
        )
        for bar in bars
    ]


def _build_promotion_comparisons(
    *,
    events: list[LimitUpEvent],
    picks: list[AgentEvaluationItem] | list[ReviewAgentPick],
    end_date: date,
) -> list[ReviewPromotionComparison]:
    """Build daily Top-pick and full-market first-to-second comparisons."""

    events_by_date: dict[date, dict[str, LimitUpEvent]] = {}
    for event in events:
        if event.trade_date <= end_date:
            events_by_date.setdefault(event.trade_date, {})[event.symbol] = event
    available_dates = sorted(events_by_date)
    next_dates = {
        trade_date: available_dates[index + 1]
        for index, trade_date in enumerate(available_dates[:-1])
    }
    picks_by_date: dict[date, list[AgentEvaluationItem | ReviewAgentPick]] = {}
    for pick in picks:
        picks_by_date.setdefault(pick.trade_date, []).append(pick)

    comparisons: list[ReviewPromotionComparison] = []
    for trade_date in sorted(picks_by_date):
        daily_picks = picks_by_date[trade_date]
        base_events = events_by_date.get(trade_date, {})
        first_boards = [
            event
            for event in base_events.values()
            if event.closed_limit and event.board_height == 1
        ]
        next_trade_date = next_dates.get(trade_date)
        outcome_ready = bool(
            next_trade_date
            and 1 <= (next_trade_date - trade_date).days <= 4
        )
        if not outcome_ready or next_trade_date is None:
            comparisons.append(
                ReviewPromotionComparison(
                    trade_date=trade_date,
                    next_trade_date=next_trade_date,
                    outcome_ready=False,
                    top_pick_sample_size=len(daily_picks),
                    top_pick_promoted_count=0,
                    market_first_board_sample_size=len(first_boards),
                    market_promoted_count=0,
                )
            )
            continue

        next_events = events_by_date[next_trade_date]

        def promoted(symbol: str) -> bool:
            event = next_events.get(symbol)
            return bool(event and event.closed_limit and event.board_height == 2)

        top_promoted_count = sum(promoted(pick.symbol) for pick in daily_picks)
        market_promoted_count = sum(promoted(event.symbol) for event in first_boards)
        top_rate = _optional_review_rate(top_promoted_count, len(daily_picks))
        market_rate = _optional_review_rate(
            market_promoted_count,
            len(first_boards),
        )
        comparisons.append(
            ReviewPromotionComparison(
                trade_date=trade_date,
                next_trade_date=next_trade_date,
                outcome_ready=True,
                top_pick_sample_size=len(daily_picks),
                top_pick_promoted_count=top_promoted_count,
                top_pick_promotion_rate=top_rate,
                market_first_board_sample_size=len(first_boards),
                market_promoted_count=market_promoted_count,
                market_promotion_rate=market_rate,
                promotion_rate_delta=(
                    round(top_rate - market_rate, 4)
                    if top_rate is not None and market_rate is not None
                    else None
                ),
            )
        )
    return comparisons


def _aggregate_promotion_comparisons(
    comparisons: list[ReviewPromotionComparison],
) -> dict[str, Any]:
    """Aggregate only date cohorts whose following trading day is available."""

    ready = [item for item in comparisons if item.outcome_ready]
    top_sample_size = sum(item.top_pick_sample_size for item in ready)
    top_promoted_count = sum(item.top_pick_promoted_count for item in ready)
    market_sample_size = sum(item.market_first_board_sample_size for item in ready)
    market_promoted_count = sum(item.market_promoted_count for item in ready)
    top_rate = _optional_review_rate(top_promoted_count, top_sample_size)
    market_rate = _optional_review_rate(market_promoted_count, market_sample_size)
    return {
        "promotion_ready_date_count": len(ready),
        "top_pick_promotion_sample_size": top_sample_size,
        "top_pick_promoted_count": top_promoted_count,
        "top_pick_promotion_rate": top_rate,
        "market_promotion_sample_size": market_sample_size,
        "market_promoted_count": market_promoted_count,
        "market_promotion_rate": market_rate,
        "promotion_rate_delta": (
            round(top_rate - market_rate, 4)
            if top_rate is not None and market_rate is not None
            else None
        ),
    }


def _optional_review_rate(count: int, sample_size: int) -> float | None:
    """Return a rounded rate for a non-empty review cohort."""

    return round(count / sample_size, 4) if sample_size else None


def _fallback_report(
    *,
    start_date: date,
    end_date: date,
    picks: list[ReviewAgentPick],
    tool_results: list[AgentToolTrace],
    warnings: list[str],
    feature_comparison: dict[str, Any] | None = None,
    promotion_comparisons: list[ReviewPromotionComparison] | None = None,
) -> ReviewAgentReportResponse:
    ready = [item for item in picks if item.outcome_ready]
    successes = [item for item in picks if item.evaluation_label == "success"]
    failures = [item for item in picks if item.evaluation_label == "miss"]
    pending = [item for item in picks if item.evaluation_label == "pending"]
    report_warnings = list(warnings)
    incomplete_cache = [item for item in picks if not item.post_bar_cache_complete]
    if incomplete_cache:
        report_warnings.append(
            f"Post-bar cache is incomplete for {len(incomplete_cache)} reviewed picks."
        )
    historical_count = sum(
        item.prediction_source == "historical_backtest" for item in picks
    )
    if historical_count:
        report_warnings.append(
            f"{historical_count} 条记录来自历史回测快照，不计作真实前向预测。"
        )
    comparison = feature_comparison or {}
    successful_patterns = _string_list(comparison.get("successful_patterns"))
    failed_patterns = _string_list(comparison.get("failed_patterns"))
    scoring_bias = _string_list(comparison.get("scoring_bias"))
    adjustment_suggestions = _string_list(
        comparison.get("adjustment_suggestions")
    )
    comparison_findings = _string_list(comparison.get("main_findings"))
    promotion_items = promotion_comparisons or []
    promotion_summary = _aggregate_promotion_comparisons(promotion_items)
    promotion_finding: list[str] = []
    if (
        promotion_summary["top_pick_promotion_rate"] is not None
        and promotion_summary["market_promotion_rate"] is not None
    ):
        promotion_finding.append(
            f"每日评分 Top10 的1进2为 "
            f"{promotion_summary['top_pick_promoted_count']}/"
            f"{promotion_summary['top_pick_promotion_sample_size']}"
            f"（{promotion_summary['top_pick_promotion_rate']:.1%}），同期全部首板为 "
            f"{promotion_summary['market_promoted_count']}/"
            f"{promotion_summary['market_promotion_sample_size']}"
            f"（{promotion_summary['market_promotion_rate']:.1%}）。"
        )
    return ReviewAgentReportResponse(
        start_date=start_date,
        end_date=end_date,
        sample_size=len(picks),
        success_count=len(successes),
        failed_count=len(failures),
        pending_count=len(pending),
        **promotion_summary,
        promotion_comparisons=promotion_items,
        main_findings=[
            f"高分首板样本 {len(picks)} 只，其中 {len(ready)} 只有后续走势可复盘。",
            f"成功 {len(successes)} 只，失败 {len(failures)} 只，待观察 {len(pending)} 只。",
            *promotion_finding,
            *comparison_findings,
        ],
        successful_patterns=successful_patterns
        or _top_texts(reason for item in successes for reason in item.reasons),
        failed_patterns=failed_patterns
        or _top_texts(reason for item in failures for reason in item.reasons),
        scoring_bias=scoring_bias
        or _top_texts(risk for item in failures for risk in item.risks),
        adjustment_suggestions=adjustment_suggestions
        or _top_texts(risk for item in failures for risk in item.risks),
        confidence=0.45 if not ready else 0.68,
        reviewed_picks=picks[:100],
        tool_results=tool_results,
        warnings=report_warnings,
        generated_by=REVIEW_AGENT_VERSION,
    )


def _report_from_payload(
    *,
    payload: dict[str, Any],
    start_date: date,
    end_date: date,
    picks: list[ReviewAgentPick],
    tool_results: list[AgentToolTrace],
    feature_comparison: dict[str, Any] | None = None,
    promotion_comparisons: list[ReviewPromotionComparison] | None = None,
) -> ReviewAgentReportResponse:
    fallback = _fallback_report(
        start_date=start_date,
        end_date=end_date,
        picks=picks,
        tool_results=tool_results,
        warnings=[],
        feature_comparison=feature_comparison,
        promotion_comparisons=promotion_comparisons,
    )
    return ReviewAgentReportResponse(
        start_date=start_date,
        end_date=end_date,
        sample_size=len(picks),
        success_count=sum(1 for item in picks if item.evaluation_label == "success"),
        failed_count=sum(1 for item in picks if item.evaluation_label == "miss"),
        pending_count=sum(1 for item in picks if item.evaluation_label == "pending"),
        promotion_ready_date_count=fallback.promotion_ready_date_count,
        top_pick_promotion_sample_size=fallback.top_pick_promotion_sample_size,
        top_pick_promoted_count=fallback.top_pick_promoted_count,
        top_pick_promotion_rate=fallback.top_pick_promotion_rate,
        market_promotion_sample_size=fallback.market_promotion_sample_size,
        market_promoted_count=fallback.market_promoted_count,
        market_promotion_rate=fallback.market_promotion_rate,
        promotion_rate_delta=fallback.promotion_rate_delta,
        promotion_comparisons=fallback.promotion_comparisons,
        main_findings=_string_list(payload.get("main_findings")) or fallback.main_findings,
        successful_patterns=_merge_texts(
            fallback.successful_patterns,
            _string_list(payload.get("successful_patterns")),
        ),
        failed_patterns=_merge_texts(
            fallback.failed_patterns,
            _string_list(payload.get("failed_patterns")),
        ),
        scoring_bias=_merge_texts(
            fallback.scoring_bias,
            _string_list(payload.get("scoring_bias")),
        ),
        adjustment_suggestions=_merge_texts(
            fallback.adjustment_suggestions,
            _string_list(payload.get("adjustment_suggestions")),
        ),
        confidence=float(payload.get("confidence") or fallback.confidence),
        reviewed_picks=picks[:100],
        tool_results=tool_results,
        warnings=fallback.warnings,
        generated_by=REVIEW_AGENT_VERSION,
    )


def _review_report_system_prompt() -> str:
    return (
        "You are LimitUpLab's Review Agent. Use only tool facts. "
        "Review high-score first-board picks, explain what worked and failed, "
        "and suggest scoring taste adjustments. Lead each success/failure summary "
        "with stock-selection traits such as dominant themes, industries and float "
        "market-cap distribution before discussing seal structure or outcomes. "
        "Return JSON only. "
        "Do not give buy/sell advice, target prices, positions, or return promises."
    )


def _review_report_user_prompt(
    start_date: date,
    end_date: date,
    min_score: float,
    facts: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "period": [start_date.isoformat(), end_date.isoformat()],
            "min_score": min_score,
            "tool_facts": facts,
            "required_json_shape": {
                "main_findings": ["string"],
                "successful_patterns": ["string"],
                "failed_patterns": ["string"],
                "scoring_bias": ["string"],
                "adjustment_suggestions": ["string"],
                "confidence": 0.0,
            },
        },
        ensure_ascii=False,
    )


def _pick_summary(item: AgentEvaluationItem) -> dict[str, Any]:
    return {
        "trade_date": item.trade_date.isoformat(),
        "symbol": item.symbol,
        "name": item.name,
        "score": item.score,
        "rating": item.rating,
        "confidence": item.confidence,
        "prediction_source": item.prediction_source,
        "data_as_of": item.data_as_of.isoformat(),
        "label": item.evaluation_label,
    }


def _outcome_summary(item: AgentEvaluationItem) -> dict[str, Any]:
    return {
        **_pick_summary(item),
        "outcome_ready": item.outcome_ready,
        "promoted_to_second_board": item.promoted_to_second_board,
        "next_high_pct": item.next_high_pct,
        "next_close_pct": item.next_close_pct,
        "next_open_to_high_pct": item.next_open_to_high_pct,
        "next_open_to_low_pct": item.next_open_to_low_pct,
        "next_open_to_close_pct": item.next_open_to_close_pct,
        "three_day_high_pct": item.three_day_high_pct,
        "three_day_close_pct": item.three_day_close_pct,
        "three_day_open_to_close_pct": item.three_day_open_to_close_pct,
        "max_drawdown_from_next_open_3d": item.max_drawdown_from_next_open_3d,
    }


def _build_feature_comparison(
    evaluations: list[AgentEvaluationItem],
    predictions: dict[str, AgentPrediction],
) -> dict[str, Any]:
    """Build descriptive success/failure statistics from immutable inputs."""

    profiles = [
        profile
        for item in evaluations
        if item.evaluation_label in {"success", "miss"}
        and (profile := _review_feature_profile(item, predictions.get(item.prediction_id)))
    ]
    success = [item for item in profiles if item["label"] == "success"]
    failed = [item for item in profiles if item["label"] == "miss"]
    result: dict[str, Any] = {
        "success_count": len(success),
        "failed_count": len(failed),
        "main_findings": [],
        "successful_patterns": [],
        "failed_patterns": [],
        "scoring_bias": [],
        "adjustment_suggestions": [],
    }
    if len(success) < 3 or len(failed) < 3:
        result["main_findings"] = [
            "成功组或失败组少于 3 只，暂不提炼数值特征，避免把个例误当规律。"
        ]
        return result

    success_avg = _profile_averages(success)
    failed_avg = _profile_averages(failed)
    result["main_findings"] = [
        f"以下对比基于成功组 {len(success)} 只、失败组 {len(failed)} 只，"
        "属于近期样本的描述性统计，不代表稳定因果。"
    ]

    success_selection = _selection_profile_pattern(success)
    failed_selection = _selection_profile_pattern(failed)
    if success_selection:
        result["successful_patterns"].append(f"选股画像：{success_selection}。")
    if failed_selection:
        result["failed_patterns"].append(f"选股画像：{failed_selection}。")

    seal_success = _seal_pattern("成功组", success_avg)
    seal_failed = _seal_pattern("失败组", failed_avg)
    if seal_success and seal_failed:
        result["successful_patterns"].append(f"封板结构：{seal_success}。")
        result["failed_patterns"].append(f"封板结构：{seal_failed}。")

    structure_success = _structure_pattern("成功组", success_avg)
    structure_failed = _structure_pattern("失败组", failed_avg)
    if structure_success and structure_failed:
        result["successful_patterns"].append(
            f"趋势与扩散：{structure_success}。"
        )
        result["failed_patterns"].append(f"趋势与扩散：{structure_failed}。")

    outcome_success = _outcome_pattern("成功组", success_avg)
    outcome_failed = _outcome_pattern("失败组", failed_avg)
    if outcome_success and outcome_failed:
        result["successful_patterns"].append(f"后续兑现：{outcome_success}。")
        result["failed_patterns"].append(f"风险表现：{outcome_failed}。")

    success_score = success_avg.get("score")
    failed_score = failed_avg.get("score")
    if success_score is not None and failed_score is not None:
        result["scoring_bias"].append(
            f"成功组平均评分 {success_score:.1f}，失败组 {failed_score:.1f}；"
            "两组都进入每日 Top10，说明现有总分仍需增强对结构差异的区分。"
        )
    result["adjustment_suggestions"] = [
        "优先检验早封、少炸板、行业扩散、近 20 日趋势和量比的交叉项，"
        "通过滚动样本外评估后再调整权重。",
        "对多项特征同时弱于近期成功组的候选降低置信度，避免用单一阈值直接下结论。",
    ]
    result["metrics"] = {"success": success_avg, "failed": failed_avg}
    return result


def _review_feature_profile(
    evaluation: AgentEvaluationItem,
    prediction: AgentPrediction | None,
) -> dict[str, Any] | None:
    if prediction is None:
        return None
    facts = prediction.facts_json
    enrichment = facts.get("enrichment")
    if not isinstance(enrichment, dict):
        enrichment = {}
    position = enrichment.get("position")
    if not isinstance(position, dict):
        position = {}
    primary_position = position.get("primary")
    if not isinstance(primary_position, dict):
        primary_position = {}
    return {
        "label": evaluation.evaluation_label,
        "score": prediction.score,
        "confidence": prediction.confidence,
        "first_limit_minutes": _time_to_minutes(facts.get("first_limit_time")),
        "break_count": _number(facts.get("break_count")),
        "turnover_rate": _number(facts.get("turnover_rate")),
        "industry": _category(facts.get("industry")),
        "concept": _category(facts.get("concept")),
        "position_label": _category(primary_position.get("label")),
        "industry_limit_up_count": _number(
            facts.get("same_industry_limit_up_count")
        ),
        "float_market_cap": _number(enrichment.get("float_market_cap")),
        "return_20d_pct": _number(enrichment.get("return_20d_pct")),
        "volume_ratio_5d": _number(enrichment.get("volume_ratio_5d")),
        "popularity_rank": _number(enrichment.get("popularity_rank")),
        "promotion": 1.0 if evaluation.promoted_to_second_board else 0.0,
        "next_open_to_close_pct": evaluation.next_open_to_close_pct,
        "max_drawdown_from_next_open_3d": (
            evaluation.max_drawdown_from_next_open_3d
        ),
    }


def _profile_averages(profiles: list[dict[str, Any]]) -> dict[str, float]:
    keys = {
        key
        for profile in profiles
        for key, value in profile.items()
        if key != "label" and isinstance(value, (int, float))
    }
    averages: dict[str, float] = {}
    for key in keys:
        values = [
            float(item[key])
            for item in profiles
            if isinstance(item.get(key), (int, float))
        ]
        if len(values) >= 2:
            averages[key] = sum(values) / len(values)
    return averages


def _selection_profile_pattern(profiles: list[dict[str, Any]]) -> str | None:
    """Summarize what kinds of stocks were selected in one outcome group."""

    if not profiles:
        return None
    themes = _top_categories(profiles, "concept")
    industries = _top_categories(profiles, "industry")
    positions = _top_categories(profiles, "position_label")
    market_caps = sorted(
        value / 100_000_000
        for item in profiles
        if (value := _number(item.get("float_market_cap"))) is not None and value > 0
    )
    parts: list[str] = []
    if positions:
        parts.append(f"位置类型 {positions}")
    if themes:
        parts.append(f"主要题材 {themes}")
    if industries:
        parts.append(
            f"主要行业 {industries}"
            if themes
            else f"题材/行业集中在 {industries}"
        )
    if market_caps:
        cap_text = f"流通市值中位数 {median(market_caps):.1f} 亿元"
        if len(market_caps) >= 4:
            lower = _percentile(market_caps, 0.25)
            upper = _percentile(market_caps, 0.75)
            cap_text += f"，中间 50% 位于 {lower:.1f}-{upper:.1f} 亿元"
        else:
            cap_text += f"，范围 {market_caps[0]:.1f}-{market_caps[-1]:.1f} 亿元"
        if len(market_caps) < len(profiles):
            cap_text += f"（{len(market_caps)}/{len(profiles)} 只有市值数据）"
        parts.append(cap_text)
    else:
        parts.append("流通市值数据不足")
    return "；".join(parts) if parts else None


def _top_categories(
    profiles: list[dict[str, Any]],
    key: str,
    limit: int = 3,
) -> str:
    values = [
        value
        for item in profiles
        if (value := _category(item.get(key))) is not None
    ]
    if not values:
        return ""
    counts = Counter(values)
    return "、".join(
        f"{name} {count}只"
        for name, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
    )


def _percentile(values: list[float], ratio: float) -> float:
    """Return a linearly interpolated percentile from sorted values."""

    position = (len(values) - 1) * ratio
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(values) - 1)
    fraction = position - lower_index
    return values[lower_index] + (values[upper_index] - values[lower_index]) * fraction


def _seal_pattern(label: str, averages: dict[str, float]) -> str | None:
    first_limit = averages.get("first_limit_minutes")
    break_count = averages.get("break_count")
    if first_limit is None or break_count is None:
        return None
    return (
        f"{label}平均首封约 {_format_minutes(first_limit)}，"
        f"平均炸板 {break_count:.2f} 次"
    )


def _structure_pattern(label: str, averages: dict[str, float]) -> str | None:
    industry_count = averages.get("industry_limit_up_count")
    return_20d = averages.get("return_20d_pct")
    volume_ratio = averages.get("volume_ratio_5d")
    if industry_count is None or return_20d is None or volume_ratio is None:
        return None
    return (
        f"{label}同行业涨停平均 {industry_count:.1f} 只、"
        f"近 20 日涨幅 {return_20d:+.1f}%、5 日量比 {volume_ratio:.2f}"
    )


def _outcome_pattern(label: str, averages: dict[str, float]) -> str | None:
    promotion = averages.get("promotion")
    next_return = averages.get("next_open_to_close_pct")
    drawdown = averages.get("max_drawdown_from_next_open_3d")
    if promotion is None or next_return is None or drawdown is None:
        return None
    return (
        f"{label}晋级率 {promotion:.1%}、次日开盘至收盘平均 "
        f"{next_return:+.2f}%、三日最大回撤平均 {drawdown:+.2f}%"
    )


def _time_to_minutes(value: Any) -> float | None:
    text = str(value or "")
    try:
        hours, minutes = text.split(":", maxsplit=2)[:2]
        return float(int(hours) * 60 + int(minutes))
    except (TypeError, ValueError):
        return None


def _format_minutes(value: float) -> str:
    rounded = int(round(value))
    return f"{rounded // 60:02d}:{rounded % 60:02d}"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _category(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "unknown"}:
        return None
    if text in {"-", "--", "未知", "未分类", "其他"}:
        return None
    return text


def _extract_json_object(content: str) -> dict[str, Any]:
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("LLM did not return a JSON object")
    payload = json.loads(content[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM JSON payload must be an object")
    return payload


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:8]


def _merge_texts(primary: list[str], secondary: list[str]) -> list[str]:
    return _top_texts([*primary, *secondary])


def _top_texts(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= 5:
            break
    return result
