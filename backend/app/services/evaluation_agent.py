"""Evaluation Agent for persisted first-board rating predictions."""

from collections import Counter
from datetime import date, datetime, timezone
from typing import Literal

from app.models import (
    AgentEvaluationItem,
    AgentEvaluationResponse,
    AgentPrediction,
    FirstBoardOutcome,
    FirstBoardRating,
    LimitUpEvent,
)
from app.repositories import SQLiteFirstBoardRepository, SQLiteScoringPolicyRepository
from app.services.scoring_policy import DEFAULT_SCORING_POLICY_VERSION


EVALUATION_AGENT_VERSION = "first-board-evaluation-mvp-v1"


def build_agent_evaluation(
    events: list[LimitUpEvent],
    start_date: date,
    end_date: date,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    limit: int = 30,
) -> AgentEvaluationResponse:
    """Evaluate existing immutable rating snapshots against ready outcomes."""

    repository = first_board_repository or SQLiteFirstBoardRepository()
    champion = SQLiteScoringPolicyRepository(repository.database_path).get_champion()
    scoring_version = (
        champion.version if champion else DEFAULT_SCORING_POLICY_VERSION
    )
    predictions = select_canonical_prediction_snapshots(
        repository.list_predictions_between(start_date, end_date),
        preferred_scoring_version=scoring_version,
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
        source_counts=dict(Counter(item.prediction_source for item in predictions)),
        label_counts=label_counts,
        evaluations=_rank_evaluations(evaluations)[: max(limit, 0)],
        summary=_build_summary(evaluations),
        warnings=_build_warnings(predictions, ready_count),
        generated_by=EVALUATION_AGENT_VERSION,
    )


def persist_agent_predictions_for_dates(
    *,
    events: list[LimitUpEvent],
    trade_dates: list[date],
    repository: SQLiteFirstBoardRepository,
    top_per_day: int | None = None,
    prediction_source: Literal["live", "historical_backtest"] = "historical_backtest",
    data_as_of: date | None = None,
) -> int:
    """Persist immutable rating snapshots with an explicit provenance label."""

    historical_predictions: list[AgentPrediction] = []
    inserted = 0
    now = datetime.now(timezone.utc)
    from app.agents.first_board import build_first_board_ratings

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
        predictions = [
            _prediction_from_rating(
                rating=item,
                created_at=now,
                prediction_source=prediction_source,
                data_as_of=data_as_of or item.facts.trade_date,
                scoring_version=ratings.generated_by,
            )
            for item in candidates
        ]
        if prediction_source == "live":
            inserted += repository.persist_live_prediction_snapshot(
                ratings=ratings.model_copy(update={"candidates": candidates}),
                predictions=predictions,
                top_limit=(
                    len(candidates)
                    if top_per_day is None
                    else max(top_per_day, 0)
                ),
                data_as_of=data_as_of or trade_date,
                created_at=now,
            )
        else:
            historical_predictions.extend(predictions)
    inserted += repository.upsert_predictions(historical_predictions)
    return inserted


def _prediction_from_rating(
    rating: FirstBoardRating,
    created_at: datetime,
    prediction_source: Literal["live", "historical_backtest"],
    data_as_of: date,
    scoring_version: str,
) -> AgentPrediction:
    """Create a persisted prediction snapshot from a current rating."""

    facts = rating.facts.model_dump(mode="json")
    prediction_id = (
        f"{rating.facts.trade_date.isoformat()}-"
        f"{rating.facts.symbol}-{scoring_version}-{prediction_source}"
    )
    return AgentPrediction(
        prediction_id=prediction_id,
        trade_date=rating.facts.trade_date,
        symbol=rating.facts.symbol,
        name=rating.facts.name,
        score=rating.score,
        rating=rating.rating,
        confidence=rating.confidence,
        scoring_version=scoring_version,
        prediction_source=prediction_source,
        data_as_of=data_as_of,
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
        prediction_source=prediction.prediction_source,
        data_as_of=prediction.data_as_of,
        evaluation_label=label,
        outcome_ready=bool(outcome and outcome.next_day_ready),
        promoted_to_second_board=bool(outcome and outcome.promoted_to_second_board),
        next_high_pct=outcome.next_high_pct if outcome else None,
        next_close_pct=outcome.next_close_pct if outcome else None,
        next_open_to_high_pct=outcome.next_open_to_high_pct if outcome else None,
        next_open_to_low_pct=outcome.next_open_to_low_pct if outcome else None,
        next_open_to_close_pct=outcome.next_open_to_close_pct if outcome else None,
        three_day_high_pct=outcome.three_day_high_pct if outcome else None,
        three_day_close_pct=outcome.three_day_close_pct if outcome else None,
        three_day_open_to_close_pct=outcome.three_day_open_to_close_pct if outcome else None,
        max_drawdown_from_next_open_3d=(
            outcome.max_drawdown_from_next_open_3d if outcome else None
        ),
        lesson=lesson,
        scoring_suggestion=suggestion,
    )


def _evaluation_label(
    prediction: AgentPrediction,
    outcome: FirstBoardOutcome | None,
) -> str:
    """Assign an Evaluation Agent label from rating and outcome facts."""

    if not outcome or not outcome.next_day_ready:
        return "pending"

    high_rated = prediction.rating in {"A", "B"}
    entry_return = outcome.next_open_to_close_pct
    if entry_return is None:
        return "pending"
    if high_rated and entry_return > 0:
        return "success"
    if high_rated and entry_return == 0:
        return "partial"
    if high_rated:
        return "miss"
    if entry_return > 0:
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
            "次日开盘后的走势尚未缓存，暂时不能评价这条预测。",
            "优先补齐次日 K 线；三日走势作为独立的中期观察指标。",
        )
    if label == "success":
        return (
            "高评级样本从次日开盘到收盘取得正收益，方向得到验证。",
            "继续检查晋级率、盘中回撤和三日收益，避免只看单日结果。",
        )
    if label == "partial":
        return (
            "高评级样本次日开盘到收盘基本持平。",
            "保留为中性样本，不与冲高或晋级结果混合评价。",
        )
    if label == "miss":
        return (
            "高评级样本从次日开盘到收盘为负，属于可交易口径下的误判。",
            "比较市场环境、板块强度和个股结构因子，定位高分误判来源。",
        )
    if label == "false_negative":
        return (
            "低评级样本从次日开盘到收盘上涨，说明可能漏掉了有效信号。",
            "复查题材扩散、市场状态交叉项和走势结构，寻找漏判因子。",
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
        f"区间内保存 {len(items)} 条首板评分预测，其中 {len(ready)} 条已有次日介入结果。",
    ]
    if counts.get("miss"):
        notes.append(f"发现 {counts['miss']} 条高评级弱表现样本，建议优先复盘评分过度乐观来源。")
    if counts.get("false_negative"):
        notes.append(f"发现 {counts['false_negative']} 条低评级走强样本，可能存在漏判因子。")
    if counts.get("success"):
        notes.append(f"{counts['success']} 条高评级样本得到强表现验证，可作为正向案例沉淀。")
    if not ready:
        notes.append("目前次日走势覆盖不足，需要等待后续 K 线回填。")
    return notes


def _build_warnings(
    predictions: list[AgentPrediction],
    ready_count: int,
) -> list[str]:
    """Return data-quality warnings for the evaluation response."""

    warnings: list[str] = []
    if predictions and ready_count / len(predictions) < 0.5:
        warnings.append("次日介入结果覆盖低于 50%，当前评价仍需谨慎。")
    if not predictions:
        warnings.append("所选区间没有已保存预测；评估接口不会自动回补历史预测。")
    if any(item.prediction_source == "historical_backtest" for item in predictions):
        warnings.append("历史回测快照与实时预测已分开标记，统计时不可混为真实前向预测。")
    return warnings


def select_canonical_prediction_snapshots(
    predictions: list[AgentPrediction],
    *,
    preferred_scoring_version: str | None = None,
) -> list[AgentPrediction]:
    """Select one complete prediction batch per date.

    If an active live batch exists, no historical row from that date may leak
    into the displayed Top10. Retired auction-final experiments are ignored;
    dates without an active live batch use one coherent historical version.
    """

    by_date: dict[date, list[AgentPrediction]] = {}
    for prediction in predictions:
        by_date.setdefault(prediction.trade_date, []).append(prediction)

    selected: list[AgentPrediction] = []
    for trade_date in sorted(by_date):
        daily = by_date[trade_date]
        live = [
            item
            for item in daily
            if item.prediction_source == "live"
            and "auction-final" not in item.scoring_version
        ]
        if live:
            batch_key = min(
                (
                    item.created_at.isoformat(),
                    item.scoring_version,
                    item.data_as_of.isoformat(),
                )
                for item in live
            )
            selected.extend(
                item
                for item in live
                if (
                    item.created_at.isoformat(),
                    item.scoring_version,
                    item.data_as_of.isoformat(),
                )
                == batch_key
            )
            continue

        historical = [
            item for item in daily if item.prediction_source == "historical_backtest"
        ]
        if not historical:
            continue
        versions = {item.scoring_version for item in historical}
        if preferred_scoring_version in versions:
            selected_version = preferred_scoring_version
        else:
            selected_version = max(
                versions,
                key=lambda version: max(
                    item.created_at.isoformat()
                    for item in historical
                    if item.scoring_version == version
                ),
            )
        selected.extend(
            item for item in historical if item.scoring_version == selected_version
        )

    return sorted(selected, key=lambda item: (item.trade_date, -item.score, item.symbol))
