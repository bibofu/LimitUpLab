"""Evaluation Agent for persisted first-board rating predictions."""

from collections import Counter
from datetime import date, datetime, timezone

from app.agents.first_board import FIRST_BOARD_AGENT_VERSION, build_first_board_ratings
from app.models import (
    AgentEvaluationItem,
    AgentEvaluationResponse,
    AgentPrediction,
    FirstBoardOutcome,
    FirstBoardRating,
    LimitUpEvent,
)
from app.repositories import SQLiteFirstBoardRepository


EVALUATION_AGENT_VERSION = "first-board-evaluation-mvp-v1"


def build_agent_evaluation(
    events: list[LimitUpEvent],
    start_date: date,
    end_date: date,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    limit: int = 30,
) -> AgentEvaluationResponse:
    """Persist rating snapshots and evaluate them against ready outcomes."""

    repository = first_board_repository or SQLiteFirstBoardRepository()
    _persist_predictions_for_range(events, start_date, end_date, repository)
    predictions = repository.list_predictions_between(
        start_date,
        end_date,
        scoring_version=FIRST_BOARD_AGENT_VERSION,
    )
    outcomes = {
        (outcome.base_trade_date, outcome.symbol): outcome
        for outcome in repository.list_outcomes_between(start_date, end_date)
    }

    evaluations = [
        _evaluate_prediction(
            prediction=prediction,
            outcome=outcomes.get((prediction.trade_date, prediction.symbol)),
        )
        for prediction in predictions
    ]
    label_counts = dict(Counter(item.evaluation_label for item in evaluations))
    ready_count = sum(1 for item in evaluations if item.outcome_ready)

    return AgentEvaluationResponse(
        start_date=start_date,
        end_date=end_date,
        prediction_count=len(predictions),
        outcome_ready_count=ready_count,
        label_counts=label_counts,
        evaluations=_rank_evaluations(evaluations)[: max(limit, 0)],
        summary=_build_summary(evaluations),
        warnings=_build_warnings(predictions, ready_count),
        generated_by=EVALUATION_AGENT_VERSION,
    )


def _persist_predictions_for_range(
    events: list[LimitUpEvent],
    start_date: date,
    end_date: date,
    repository: SQLiteFirstBoardRepository,
) -> None:
    """Build first-board ratings for each date and save prediction snapshots."""

    trade_dates = sorted(
        {
            event.trade_date
            for event in events
            if start_date <= event.trade_date <= end_date
        }
    )
    persist_agent_predictions_for_dates(
        events=events,
        trade_dates=trade_dates,
        repository=repository,
    )


def persist_agent_predictions_for_dates(
    *,
    events: list[LimitUpEvent],
    trade_dates: list[date],
    repository: SQLiteFirstBoardRepository,
    top_per_day: int | None = None,
) -> int:
    """Persist rating snapshots for selected dates and return their count."""

    predictions: list[AgentPrediction] = []
    now = datetime.now(timezone.utc)
    for trade_date in trade_dates:
        ratings = build_first_board_ratings(
            events=events,
            trade_date=trade_date,
            first_board_repository=repository,
        )
        candidates = (
            ratings.candidates
            if top_per_day is None
            else ratings.candidates[: max(top_per_day, 0)]
        )
        predictions.extend(
            _prediction_from_rating(rating=item, created_at=now)
            for item in candidates
        )
    repository.upsert_predictions(predictions)
    return len(predictions)


def _prediction_from_rating(
    rating: FirstBoardRating,
    created_at: datetime,
) -> AgentPrediction:
    """Create a persisted prediction snapshot from a current rating."""

    facts = rating.facts.model_dump(mode="json")
    prediction_id = (
        f"{rating.facts.trade_date.isoformat()}-"
        f"{rating.facts.symbol}-{FIRST_BOARD_AGENT_VERSION}"
    )
    return AgentPrediction(
        prediction_id=prediction_id,
        trade_date=rating.facts.trade_date,
        symbol=rating.facts.symbol,
        name=rating.facts.name,
        score=rating.score,
        rating=rating.rating,
        confidence=rating.confidence,
        scoring_version=FIRST_BOARD_AGENT_VERSION,
        facts_json=facts,
        reasons=rating.reasons,
        risks=rating.risks,
        created_at=created_at,
    )


def _evaluate_prediction(
    prediction: AgentPrediction,
    outcome: FirstBoardOutcome | None,
) -> AgentEvaluationItem:
    """Classify one prediction after its post-board outcome is known."""

    label = _evaluation_label(prediction, outcome)
    lesson, suggestion = _lesson_and_suggestion(prediction, outcome, label)
    return AgentEvaluationItem(
        prediction_id=prediction.prediction_id,
        trade_date=prediction.trade_date,
        symbol=prediction.symbol,
        name=prediction.name,
        score=prediction.score,
        rating=prediction.rating,
        confidence=prediction.confidence,
        evaluation_label=label,
        outcome_ready=bool(outcome and outcome.outcome_ready),
        promoted_to_second_board=bool(outcome and outcome.promoted_to_second_board),
        next_high_pct=outcome.next_high_pct if outcome else None,
        next_close_pct=outcome.next_close_pct if outcome else None,
        three_day_high_pct=outcome.three_day_high_pct if outcome else None,
        three_day_close_pct=outcome.three_day_close_pct if outcome else None,
        lesson=lesson,
        scoring_suggestion=suggestion,
    )


def _evaluation_label(
    prediction: AgentPrediction,
    outcome: FirstBoardOutcome | None,
) -> str:
    """Assign an Evaluation Agent label from rating and outcome facts."""

    if not outcome or not outcome.outcome_ready:
        return "pending"

    high_rated = prediction.rating in {"A", "B"}
    strong_follow = bool(
        outcome.promoted_to_second_board
        or (outcome.next_high_pct is not None and outcome.next_high_pct >= 5)
        or (outcome.three_day_high_pct is not None and outcome.three_day_high_pct >= 8)
    )
    weak_follow = bool(
        (outcome.next_close_pct is not None and outcome.next_close_pct < 0)
        or (
            outcome.three_day_close_pct is not None
            and outcome.three_day_close_pct < 0
        )
    )

    if high_rated and strong_follow:
        return "success"
    if high_rated and not weak_follow:
        return "partial"
    if high_rated:
        return "miss"
    if not high_rated and strong_follow:
        return "false_negative"
    return "avoid_success"


def _lesson_and_suggestion(
    prediction: AgentPrediction,
    outcome: FirstBoardOutcome | None,
    label: str,
) -> tuple[str, str]:
    """Generate compact lessons and scoring suggestions from one evaluation."""

    if label == "pending":
        return (
            "后续走势尚未缓存，暂时不能评价这条预测。",
            "优先补齐首板后三个交易日 K 线和 outcome。",
        )
    if label == "success":
        return (
            "高评分与后续强表现方向一致。",
            "保留当前正向因子权重，并继续观察同类样本稳定性。",
        )
    if label == "partial":
        return (
            "高评分没有明显失败，但后续强度不足。",
            "检查首封时间、板块热度是否被高估，避免仅靠早盘封板给过高分。",
        )
    if label == "miss":
        return (
            "高评分样本后续表现转弱，属于需要复盘的误判。",
            "提高市场炸板率、数据缺失、相似案例弱表现对 confidence 的惩罚。",
        )
    if label == "false_negative":
        return (
            "低评级样本后续走强，说明可能漏掉了有效信号。",
            "复查题材扩散、成交额区间和首封时间权重，寻找漏判因子。",
        )
    return (
        "低评级样本未出现明显强表现，规避逻辑暂时有效。",
        "维持当前风险惩罚，但继续积累样本避免过拟合。",
    )


def _rank_evaluations(items: list[AgentEvaluationItem]) -> list[AgentEvaluationItem]:
    """Prioritize actionable evaluation examples for UI and chat."""

    label_priority = {
        "miss": 0,
        "false_negative": 1,
        "success": 2,
        "partial": 3,
        "avoid_success": 4,
        "pending": 5,
    }
    return sorted(
        items,
        key=lambda item: (
            label_priority.get(item.evaluation_label, 9),
            -item.score,
            item.trade_date,
            item.symbol,
        ),
    )


def _build_summary(items: list[AgentEvaluationItem]) -> list[str]:
    """Build high-level self-reflection notes."""

    if not items:
        return ["当前区间没有可评价的首板预测。"]

    counts = Counter(item.evaluation_label for item in items)
    ready = [item for item in items if item.outcome_ready]
    notes = [
        f"区间内保存 {len(items)} 条首板评分预测，其中 {len(ready)} 条已有 outcome。",
    ]
    if counts.get("miss"):
        notes.append(f"发现 {counts['miss']} 条高评级弱表现样本，建议优先复盘评分过度乐观来源。")
    if counts.get("false_negative"):
        notes.append(f"发现 {counts['false_negative']} 条低评级走强样本，可能存在漏判因子。")
    if counts.get("success"):
        notes.append(f"{counts['success']} 条高评级样本得到强表现验证，可作为正向案例沉淀。")
    if not ready:
        notes.append("目前 outcome 覆盖不足，Evaluation 只能先保存预测，等待后续走势回填。")
    return notes


def _build_warnings(
    predictions: list[AgentPrediction],
    ready_count: int,
) -> list[str]:
    """Return data-quality warnings for the evaluation response."""

    warnings: list[str] = []
    if predictions and ready_count / len(predictions) < 0.5:
        warnings.append("Outcome coverage is below 50%; evaluation labels are still directional.")
    if not predictions:
        warnings.append("No persisted predictions were generated for the selected range.")
    return warnings
