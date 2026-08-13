"""LLM tool-driven Review Agent for high-score first-board picks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.models import (
    AgentEvaluationItem,
    AgentToolTrace,
    LimitUpEvent,
    ReviewAgentPostBar,
    ReviewAgentPick,
    ReviewAgentReportResponse,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.evaluation_agent import build_agent_evaluation
from app.services.llm_provider import LLMProvider, get_llm_provider


REVIEW_AGENT_VERSION = "review-agent-tool-use-v1"


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
        """Compare lessons and suggestions between successful and failed picks."""

        picks = self._high_score_evaluations()
        successes = [item for item in picks if item.evaluation_label == "success"]
        failures = [item for item in picks if item.evaluation_label == "miss"]
        return ReviewToolResult(
            name="compare_success_failure_features",
            input={
                "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat(),
                "min_score": self.min_score,
                "top_per_day": self.top_per_day,
            },
            output={
                "success_count": len(successes),
                "failed_count": len(failures),
                "success_reasons": _top_texts(reason for item in successes for reason in item.lesson.split("；")),
                "failure_lessons": _top_texts(item.lesson for item in failures),
                "failure_suggestions": _top_texts(item.scoring_suggestion for item in failures),
            },
            summary=f"Compared {len(successes)} successful and {len(failures)} failed high-score picks.",
        )

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
    fallback = _fallback_report(
        start_date=start_date,
        end_date=end_date,
        picks=picks,
        tool_results=[result.trace() for result in tool_results],
        warnings=["LLM review unavailable; deterministic fallback generated from tool facts."],
    )
    try:
        content = active_provider.generate(
            _review_report_system_prompt(),
            _review_report_user_prompt(start_date, end_date, min_score, facts),
        ).content
        payload = _extract_json_object(content)
        return _report_from_payload(
            payload=payload,
            start_date=start_date,
            end_date=end_date,
            picks=picks,
            tool_results=[result.trace() for result in tool_results],
        )
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
                evaluation_label=item.evaluation_label,
                outcome_ready=item.outcome_ready,
                promoted_to_second_board=item.promoted_to_second_board,
                next_high_pct=item.next_high_pct,
                next_close_pct=item.next_close_pct,
                three_day_high_pct=item.three_day_high_pct,
                three_day_close_pct=item.three_day_close_pct,
                reasons=[item.lesson],
                risks=[item.scoring_suggestion],
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


def _fallback_report(
    *,
    start_date: date,
    end_date: date,
    picks: list[ReviewAgentPick],
    tool_results: list[AgentToolTrace],
    warnings: list[str],
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
    return ReviewAgentReportResponse(
        start_date=start_date,
        end_date=end_date,
        sample_size=len(picks),
        success_count=len(successes),
        failed_count=len(failures),
        pending_count=len(pending),
        main_findings=[
            f"高分首板样本 {len(picks)} 只，其中 {len(ready)} 只有后续走势可复盘。",
            f"成功 {len(successes)} 只，失败 {len(failures)} 只，待观察 {len(pending)} 只。",
        ],
        successful_patterns=_top_texts(reason for item in successes for reason in item.reasons),
        failed_patterns=_top_texts(reason for item in failures for reason in item.reasons),
        scoring_bias=_top_texts(risk for item in failures for risk in item.risks),
        adjustment_suggestions=_top_texts(risk for item in failures for risk in item.risks),
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
) -> ReviewAgentReportResponse:
    fallback = _fallback_report(
        start_date=start_date,
        end_date=end_date,
        picks=picks,
        tool_results=tool_results,
        warnings=[],
    )
    return ReviewAgentReportResponse(
        start_date=start_date,
        end_date=end_date,
        sample_size=len(picks),
        success_count=sum(1 for item in picks if item.evaluation_label == "success"),
        failed_count=sum(1 for item in picks if item.evaluation_label == "miss"),
        pending_count=sum(1 for item in picks if item.evaluation_label == "pending"),
        main_findings=_string_list(payload.get("main_findings")) or fallback.main_findings,
        successful_patterns=_string_list(payload.get("successful_patterns")) or fallback.successful_patterns,
        failed_patterns=_string_list(payload.get("failed_patterns")) or fallback.failed_patterns,
        scoring_bias=_string_list(payload.get("scoring_bias")) or fallback.scoring_bias,
        adjustment_suggestions=_string_list(payload.get("adjustment_suggestions"))
        or fallback.adjustment_suggestions,
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
        "and suggest scoring taste adjustments. Return JSON only. "
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
        "label": item.evaluation_label,
    }


def _outcome_summary(item: AgentEvaluationItem) -> dict[str, Any]:
    return {
        **_pick_summary(item),
        "outcome_ready": item.outcome_ready,
        "promoted_to_second_board": item.promoted_to_second_board,
        "next_high_pct": item.next_high_pct,
        "next_close_pct": item.next_close_pct,
        "three_day_high_pct": item.three_day_high_pct,
        "three_day_close_pct": item.three_day_close_pct,
    }


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
