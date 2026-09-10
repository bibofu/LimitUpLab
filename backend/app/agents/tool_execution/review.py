"""Planner tool handlers for review; preserve domain-specific evidence contracts."""

from typing import Any

from app.agents.chat_answer_validation import _extract_high_score_review_days
from app.agents.chat_templates import _looks_like_high_score_promotion_question
from app.agents.tools import compact_prediction_quality_audit

from .context import ExecutionState
from .helpers import (
    _optional_str,
    _parse_optional_date,
    _parse_optional_int,
    _tool_error_trace,
)


# Execute the prediction quality audit evidence step, resolving request arguments and recording
# its facts and trace.
def prediction_quality_audit(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    available_dates = sorted({event.trade_date for event in state.tools.events})
    if not available_dates:
        state.facts["prediction_quality_audit_error"] = (
            "No local limit-up events available."
        )
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input=arguments,
                summary="本地没有涨停数据，无法执行预测质量审计。",
                error="No local limit-up events available.",
            )
        )
        return
    start_date = (
        _parse_optional_date(arguments.get("start_date"))
        or available_dates[0]
    )
    end_date = (
        _parse_optional_date(arguments.get("end_date"))
        or available_dates[-1]
    )
    top_k = _parse_optional_int(arguments.get("top_k")) or 10
    result = state.tools.prediction_quality_audit(
        start_date=start_date,
        end_date=end_date,
        scoring_version=_optional_str(arguments.get("scoring_version")),
        top_k=max(3, min(top_k, 30)),
    )
    response = result.output
    state.facts["prediction_quality_audit"] = compact_prediction_quality_audit(
        response
    )
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(
        [
            f"start_date={response.start_date.isoformat()}",
            f"end_date={response.end_date.isoformat()}",
            f"scoring_version={response.audited_scoring_version}",
        ]
    )


# Execute the rating backtest evidence step, resolving request arguments and recording its facts
# and trace.
def rating_backtest(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    available_dates = sorted({event.trade_date for event in state.tools.events})
    if not available_dates:
        state.facts["rating_backtest_error"] = "No local limit-up events available."
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input=arguments,
                summary="本地没有涨停数据，无法执行评分回测。",
                error="No local limit-up events available.",
            )
        )
        return
    end_date = _parse_optional_date(arguments.get("end_date")) or available_dates[-1]
    start_date = _parse_optional_date(arguments.get("start_date")) or available_dates[
        max(0, len(available_dates) - 20)
    ]
    failure_limit = int(arguments.get("failure_limit") or 8)
    result = state.tools.rating_backtest(
        start_date=start_date,
        end_date=end_date,
        failure_limit=max(0, min(failure_limit, 30)),
    )
    response = result.output
    state.facts["rating_backtest"] = response.model_dump(mode="json")
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(
        [
            f"start_date={response.start_date.isoformat()}",
            f"end_date={response.end_date.isoformat()}",
        ]
    )


# Execute the rating evaluation evidence step, resolving request arguments and recording its facts
# and trace.
def rating_evaluation(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    available_dates = sorted({event.trade_date for event in state.tools.events})
    if not available_dates:
        state.facts["rating_evaluation_error"] = "No local limit-up events available."
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input=arguments,
                summary="No local limit-up events; evaluation cannot run.",
                error="No local limit-up events available.",
            )
        )
        return
    end_date = _parse_optional_date(arguments.get("end_date")) or available_dates[-1]
    start_date = _parse_optional_date(arguments.get("start_date")) or available_dates[
        max(0, len(available_dates) - 20)
    ]
    limit = int(arguments.get("limit") or 30)
    result = state.tools.rating_evaluation(
        start_date=start_date,
        end_date=end_date,
        limit=max(1, min(limit, 100)),
    )
    response = result.output
    state.facts["rating_evaluation"] = response.model_dump(mode="json")
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(
        [
            f"start_date={response.start_date.isoformat()}",
            f"end_date={response.end_date.isoformat()}",
        ]
    )


# Execute the review high score picks evidence step, resolving request arguments and recording its
# facts and trace.
def review_high_score_picks(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    available_dates = sorted({event.trade_date for event in state.tools.events})
    if not available_dates:
        state.facts["review_high_score_picks_error"] = "No local limit-up events available."
        state.traces.append(
            _tool_error_trace(
                name=name,
                tool_input=arguments,
                summary="No local limit-up events; Review Agent cannot run.",
                error="No local limit-up events available.",
            )
        )
        return
    end_date = _parse_optional_date(arguments.get("end_date")) or available_dates[-1]
    start_date = _parse_optional_date(arguments.get("start_date")) or available_dates[
        max(0, len(available_dates) - 6)
    ]
    min_score_value = arguments.get("min_score")
    min_score = float(min_score_value) if min_score_value is not None else 0
    if _looks_like_high_score_promotion_question(state.request.message):
        min_score = 0
        review_days = _extract_high_score_review_days(state.request.message)
        end_date = available_dates[-1]
        start_date = available_dates[
            max(0, len(available_dates) - review_days - 1)
        ]
    top_per_day = _parse_optional_int(arguments.get("top_per_day")) or 10
    result = state.tools.review_high_score_picks(
        start_date=start_date,
        end_date=end_date,
        min_score=max(0, min(min_score, 100)),
        top_per_day=max(1, min(top_per_day, 20)),
    )
    response = result.output
    state.facts["review_high_score_picks"] = response.model_dump(mode="json")
    state.traces.append(result.trace())
    state.call_names.append(name)
    state.references.extend(
        [
            f"start_date={response.start_date.isoformat()}",
            f"end_date={response.end_date.isoformat()}",
            f"review_sample_size={response.sample_size}",
        ]
    )


# Execute the scoring policy status evidence step, resolving request arguments and recording its
# facts and trace.
def scoring_policy_status(state: ExecutionState, name: str, arguments: dict[str, Any]) -> None:
    result = state.tools.scoring_policy_status()
    payload = result.output
    state.facts["scoring_policy_status"] = payload
    state.traces.append(result.trace())
    state.call_names.append(name)
    champion = payload.get("champion") or {}
    latest = payload.get("latest_optimization") or {}
    challenger = latest.get("challenger_policy") or {}
    state.references.extend(
        [
            f"scoring_version={champion.get('version')}",
            f"challenger_version={challenger.get('version')}",
        ]
    )
