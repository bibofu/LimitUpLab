"""Prediction coverage and benchmark audit for first-board scoring policies."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, time
from statistics import mean

from app.models import (
    AgentPrediction,
    FirstBoardOutcome,
    LimitUpEvent,
    PredictionBenchmarkMetrics,
    PredictionDateCoverage,
    PredictionQualityAuditResponse,
    PredictionQualityCohort,
    PredictionQualityPolicyStatus,
)
from app.repositories import SQLiteFirstBoardRepository, SQLiteScoringPolicyRepository
from app.services.evaluation_agent import select_canonical_prediction_snapshots


PREDICTION_QUALITY_AUDIT_VERSION = "prediction-quality-audit-v1"
V3_REQUIRED_TRADE_DATES = 60
LARGE_LOSS_THRESHOLD_PCT = -3.0


def build_prediction_quality_audit(
    *,
    events: list[LimitUpEvent],
    start_date: date,
    end_date: date,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    policy_repository: SQLiteScoringPolicyRepository | None = None,
    scoring_version: str | None = None,
    top_k: int = 10,
) -> PredictionQualityAuditResponse:
    """Audit prediction cohorts, outcome maturity and deterministic baselines."""

    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    repository = first_board_repository or SQLiteFirstBoardRepository()
    registry = policy_repository or SQLiteScoringPolicyRepository(
        repository.database_path
    )
    champion = registry.ensure_default_policy()
    audited_version = scoring_version or champion.version
    bounded_top_k = max(1, min(top_k, 30))

    available_dates = sorted({item.trade_date for item in events})
    if not available_dates:
        raise ValueError("No local limit-up events available.")
    raw_predictions = repository.list_predictions_between(start_date, end_date)
    audited_rows = [
        item for item in raw_predictions if item.scoring_version == audited_version
    ]
    canonical = _canonical_predictions(audited_rows)
    outcomes = {
        (item.base_trade_date, item.symbol): item
        for item in repository.list_outcomes_between(start_date, end_date)
    }
    event_index = {
        (item.trade_date, item.symbol): item
        for item in events
        if start_date <= item.trade_date <= end_date
    }

    date_coverage, complete_dates = _build_date_coverage(
        canonical=canonical,
        outcomes=outcomes,
        available_dates=available_dates,
        top_k=bounded_top_k,
    )
    benchmarks = _build_benchmarks(
        canonical=canonical,
        outcomes=outcomes,
        events=event_index,
        complete_dates=complete_dates,
        top_k=bounded_top_k,
    )
    outcome_ready_dates = {
        item.base_trade_date for item in outcomes.values() if item.next_day_ready
    }
    latest_run = registry.get_latest_optimization_run()
    policy_status = PredictionQualityPolicyStatus(
        champion_version=champion.version,
        latest_challenger_version=(
            latest_run.challenger_policy.version if latest_run else None
        ),
        latest_optimizer_version=latest_run.generated_by if latest_run else None,
        promotion_eligible=(
            latest_run.comparison.promotion_eligible if latest_run else None
        ),
        outcome_ready_trade_dates=len(outcome_ready_dates),
        required_trade_dates=V3_REQUIRED_TRADE_DATES,
        readiness_rate=round(
            min(len(outcome_ready_dates) / V3_REQUIRED_TRADE_DATES, 1.0),
            4,
        ),
        gate_reasons=(
            latest_run.comparison.gate_reasons
            if latest_run
            else ["尚未运行评分策略优化。"]
        ),
    )

    top_total = sum(item.top_count for item in date_coverage if item.next_day_mature)
    next_ready = sum(
        item.next_day_ready_count for item in date_coverage if item.next_day_mature
    )
    three_total = sum(item.top_count for item in date_coverage if item.three_day_mature)
    three_ready = sum(
        item.three_day_ready_count for item in date_coverage if item.three_day_mature
    )
    unique_raw_stock_dates = {
        (item.trade_date, item.symbol) for item in raw_predictions
    }
    data_as_of_violations = sum(
        item.data_as_of > item.trade_date for item in raw_predictions
    )
    cohorts = [
        *_cohort_summaries(raw_predictions, outcomes, dimension="prediction_source"),
        *_cohort_summaries(raw_predictions, outcomes, dimension="scoring_version"),
    ]
    findings = _audit_findings(
        raw_count=len(raw_predictions),
        unique_count=len(unique_raw_stock_dates),
        audited_version=audited_version,
        audited_count=len(canonical),
        mature_count=sum(item.next_day_mature for item in date_coverage),
        complete_count=len(complete_dates),
        next_coverage=_safe_ratio(next_ready, top_total),
        benchmarks=benchmarks,
        data_as_of_violations=data_as_of_violations,
    )
    recommendations = _audit_recommendations(
        outcome_ready_dates=len(outcome_ready_dates),
        next_coverage=_safe_ratio(next_ready, top_total),
        complete_dates=len(complete_dates),
        benchmarks=benchmarks,
    )
    warnings = [
        "historical_backtest 是按历史数据重算的研究样本，不能等同于当日真实 live 预测。",
        "基线只使用本地已有 Outcome 的候选，缓存不完整时可能存在样本选择偏差。",
        "审计指标用于比较评分方法，不代表未来收益或交易建议。",
    ]

    return PredictionQualityAuditResponse(
        start_date=start_date,
        end_date=end_date,
        latest_trade_date=available_dates[-1],
        audited_scoring_version=audited_version,
        top_k=bounded_top_k,
        raw_prediction_rows=len(raw_predictions),
        audited_prediction_rows=len(audited_rows),
        canonical_prediction_count=len(canonical),
        cross_cohort_duplicate_rows=len(raw_predictions) - len(unique_raw_stock_dates),
        data_as_of_violation_count=data_as_of_violations,
        prediction_trade_date_count=len({item.trade_date for item in canonical}),
        next_day_mature_trade_date_count=sum(
            item.next_day_mature for item in date_coverage
        ),
        complete_next_day_trade_date_count=len(complete_dates),
        next_day_outcome_coverage_rate=_safe_ratio(next_ready, top_total),
        three_day_outcome_coverage_rate=_safe_ratio(three_ready, three_total),
        cohorts=cohorts,
        date_coverage=date_coverage,
        benchmarks=benchmarks,
        policy_status=policy_status,
        findings=findings,
        recommendations=recommendations,
        warnings=warnings,
        generated_by=PREDICTION_QUALITY_AUDIT_VERSION,
    )


def _canonical_predictions(
    predictions: list[AgentPrediction],
) -> list[AgentPrediction]:
    """Use the same coherent daily snapshot as Evaluation and Review."""

    return select_canonical_prediction_snapshots(predictions)


def _build_date_coverage(
    *,
    canonical: list[AgentPrediction],
    outcomes: dict[tuple[date, str], FirstBoardOutcome],
    available_dates: list[date],
    top_k: int,
) -> tuple[list[PredictionDateCoverage], set[date]]:
    by_date: dict[date, list[AgentPrediction]] = defaultdict(list)
    for item in canonical:
        by_date[item.trade_date].append(item)
    date_positions = {item: index for index, item in enumerate(available_dates)}
    coverage: list[PredictionDateCoverage] = []
    complete_dates: set[date] = set()
    for trade_date in sorted(by_date):
        candidates = sorted(
            by_date[trade_date], key=lambda item: (-item.score, item.symbol)
        )
        top = candidates[:top_k]
        ready = [
            outcomes.get((trade_date, item.symbol)) for item in top
        ]
        next_ready = sum(bool(item and item.next_day_ready) for item in ready)
        three_ready = sum(bool(item and item.three_day_ready) for item in ready)
        position = date_positions.get(trade_date, -1)
        later_count = (
            len(available_dates) - position - 1 if position >= 0 else 0
        )
        next_mature = later_count >= 1
        three_mature = later_count >= 3
        if not next_mature:
            status = "not_mature"
        elif top and next_ready == len(top):
            status = "complete"
            complete_dates.add(trade_date)
        elif next_ready:
            status = "partial"
        else:
            status = "pending"
        coverage.append(
            PredictionDateCoverage(
                trade_date=trade_date,
                candidate_count=len(candidates),
                top_count=len(top),
                next_day_ready_count=next_ready,
                three_day_ready_count=three_ready,
                next_day_coverage_rate=_safe_ratio(next_ready, len(top)),
                three_day_coverage_rate=_safe_ratio(three_ready, len(top)),
                next_day_mature=next_mature,
                three_day_mature=three_mature,
                status=status,
            )
        )
    return coverage, complete_dates


def _build_benchmarks(
    *,
    canonical: list[AgentPrediction],
    outcomes: dict[tuple[date, str], FirstBoardOutcome],
    events: dict[tuple[date, str], LimitUpEvent],
    complete_dates: set[date],
    top_k: int,
) -> list[PredictionBenchmarkMetrics]:
    by_date: dict[date, list[AgentPrediction]] = defaultdict(list)
    for item in canonical:
        if item.trade_date in complete_dates:
            by_date[item.trade_date].append(item)

    model_daily: dict[date, list[FirstBoardOutcome]] = {}
    early_daily: dict[date, list[FirstBoardOutcome]] = {}
    random_daily: dict[date, list[FirstBoardOutcome]] = {}
    pool_daily: dict[date, list[FirstBoardOutcome]] = {}
    for trade_date, predictions in by_date.items():
        ranked = sorted(predictions, key=lambda item: (-item.score, item.symbol))
        model_daily[trade_date] = _prediction_outcomes(
            ranked[:top_k], outcomes
        )
        ready_predictions = [
            item
            for item in predictions
            if (outcome := outcomes.get((trade_date, item.symbol)))
            and outcome.next_day_ready
        ]
        pool_daily[trade_date] = _prediction_outcomes(ready_predictions, outcomes)
        early_ranked = sorted(
            ready_predictions,
            key=lambda item: (
                events.get((trade_date, item.symbol)).first_limit_time
                if events.get((trade_date, item.symbol))
                else time.max,
                events.get((trade_date, item.symbol)).break_count
                if events.get((trade_date, item.symbol))
                else 999,
                item.symbol,
            ),
        )
        early_daily[trade_date] = _prediction_outcomes(
            early_ranked[:top_k], outcomes
        )
        random_ranked = sorted(
            ready_predictions,
            key=lambda item: hashlib.sha256(
                f"{trade_date.isoformat()}:{item.symbol}:limituplab-v1".encode("utf-8")
            ).hexdigest(),
        )
        random_daily[trade_date] = _prediction_outcomes(
            random_ranked[:top_k], outcomes
        )

    pool = _benchmark_metrics(
        benchmark="outcome_ready_pool",
        label="Outcome 可用候选池",
        daily=pool_daily,
    )
    pool_avg = pool.avg_next_open_to_close_pct
    return [
        _with_pool_excess(
            _benchmark_metrics(
                benchmark="audited_policy_top_k",
                label="当前评分 Top10",
                daily=model_daily,
            ),
            pool_avg,
        ),
        _with_pool_excess(
            _benchmark_metrics(
                benchmark="early_seal_top_k",
                label="最早封板 Top10",
                daily=early_daily,
            ),
            pool_avg,
        ),
        _with_pool_excess(
            _benchmark_metrics(
                benchmark="deterministic_random_top_k",
                label="确定性随机 Top10",
                daily=random_daily,
            ),
            pool_avg,
        ),
        _with_pool_excess(pool, pool_avg),
    ]


def _prediction_outcomes(
    predictions: list[AgentPrediction],
    outcomes: dict[tuple[date, str], FirstBoardOutcome],
) -> list[FirstBoardOutcome]:
    return [
        outcome
        for item in predictions
        if (outcome := outcomes.get((item.trade_date, item.symbol)))
        and outcome.next_day_ready
    ]


def _benchmark_metrics(
    *,
    benchmark: str,
    label: str,
    daily: dict[date, list[FirstBoardOutcome]],
) -> PredictionBenchmarkMetrics:
    samples = [item for items in daily.values() for item in items]
    next_returns = [item.next_open_to_close_pct for item in samples]
    three_returns = [item.three_day_open_to_close_pct for item in samples]
    drawdowns = [item.max_drawdown_from_next_open_3d for item in samples]
    present_next = [float(item) for item in next_returns if item is not None]
    return PredictionBenchmarkMetrics(
        benchmark=benchmark,
        label=label,
        trade_date_count=sum(bool(items) for items in daily.values()),
        sample_size=len(samples),
        avg_next_open_to_close_pct=_average(next_returns),
        positive_rate=(
            round(sum(item > 0 for item in present_next) / len(present_next), 4)
            if present_next
            else None
        ),
        promoted_to_second_board_rate=(
            round(
                sum(item.promoted_to_second_board for item in samples) / len(samples),
                4,
            )
            if samples
            else None
        ),
        large_loss_rate=(
            round(
                sum(item <= LARGE_LOSS_THRESHOLD_PCT for item in present_next)
                / len(present_next),
                4,
            )
            if present_next
            else None
        ),
        avg_three_day_open_to_close_pct=_average(three_returns),
        avg_max_drawdown_from_next_open_3d=_average(drawdowns),
    )


def _with_pool_excess(
    metrics: PredictionBenchmarkMetrics,
    pool_avg: float | None,
) -> PredictionBenchmarkMetrics:
    current = metrics.avg_next_open_to_close_pct
    excess = (
        round(current - pool_avg, 4)
        if current is not None and pool_avg is not None
        else None
    )
    return metrics.model_copy(update={"excess_vs_ready_pool_pct": excess})


def _cohort_summaries(
    predictions: list[AgentPrediction],
    outcomes: dict[tuple[date, str], FirstBoardOutcome],
    *,
    dimension: str,
) -> list[PredictionQualityCohort]:
    groups: dict[str, list[AgentPrediction]] = defaultdict(list)
    for item in predictions:
        value = (
            item.prediction_source
            if dimension == "prediction_source"
            else item.scoring_version
        )
        groups[value].append(item)
    summaries: list[PredictionQualityCohort] = []
    for value, items in sorted(groups.items(), key=lambda pair: pair[0]):
        unique = {(item.trade_date, item.symbol) for item in items}
        ready = sum(
            bool((outcome := outcomes.get(key)) and outcome.next_day_ready)
            for key in unique
        )
        summaries.append(
            PredictionQualityCohort(
                dimension=dimension,  # type: ignore[arg-type]
                value=value,
                row_count=len(items),
                unique_stock_date_count=len(unique),
                trade_date_count=len({item[0] for item in unique}),
                next_day_ready_count=ready,
                next_day_coverage_rate=_safe_ratio(ready, len(unique)),
            )
        )
    return summaries


def _audit_findings(
    *,
    raw_count: int,
    unique_count: int,
    audited_version: str,
    audited_count: int,
    mature_count: int,
    complete_count: int,
    next_coverage: float,
    benchmarks: list[PredictionBenchmarkMetrics],
    data_as_of_violations: int,
) -> list[str]:
    findings = [
        f"原始预测表有 {raw_count} 行，跨版本和来源去重后为 {unique_count} 个股票-日期样本。",
        f"本次只审计 {audited_version}，得到 {audited_count} 个规范化预测样本。",
        f"{mature_count} 个交易日已具备次日成熟条件，其中 {complete_count} 日的 Top 样本结果完整。",
        f"成熟 Top 样本次日 Outcome 覆盖率为 {next_coverage:.1%}。",
    ]
    model = next(
        (item for item in benchmarks if item.benchmark == "audited_policy_top_k"),
        None,
    )
    early = next(
        (item for item in benchmarks if item.benchmark == "early_seal_top_k"),
        None,
    )
    if model and early and model.avg_next_open_to_close_pct is not None:
        findings.append(
            "当前评分 Top10 次日开盘到收盘均值为 "
            f"{model.avg_next_open_to_close_pct:+.2f}%，最早封板基线为 "
            f"{(early.avg_next_open_to_close_pct or 0):+.2f}%。"
        )
    if data_as_of_violations:
        findings.append(
            f"发现 {data_as_of_violations} 行 data_as_of 晚于预测日，需要排查潜在时间穿越。"
        )
    else:
        findings.append("未发现 data_as_of 晚于预测日的显式时间穿越记录。")
    return findings


def _audit_recommendations(
    *,
    outcome_ready_dates: int,
    next_coverage: float,
    complete_dates: int,
    benchmarks: list[PredictionBenchmarkMetrics],
) -> list[str]:
    recommendations: list[str] = []
    if outcome_ready_dates < V3_REQUIRED_TRADE_DATES:
        recommendations.append(
            f"继续积累结果完整交易日：当前 {outcome_ready_dates}，v3 晋级门槛为 {V3_REQUIRED_TRADE_DATES}。"
        )
    if next_coverage < 0.95:
        recommendations.append(
            "优先补齐成熟 Top10 的次日 Outcome 缓存，再讨论评分优劣。"
        )
    if complete_dates < 12:
        recommendations.append(
            "完整 Top10 日期过少，暂不对短期命中率做显著性结论。"
        )
    model = next(
        (item for item in benchmarks if item.benchmark == "audited_policy_top_k"),
        None,
    )
    early = next(
        (item for item in benchmarks if item.benchmark == "early_seal_top_k"),
        None,
    )
    if (
        model
        and early
        and model.avg_next_open_to_close_pct is not None
        and early.avg_next_open_to_close_pct is not None
        and model.avg_next_open_to_close_pct <= early.avg_next_open_to_close_pct
    ):
        recommendations.append(
            "当前评分尚未稳定战胜最早封板基线，v3 应降低无效复杂度并强化下行风险目标。"
        )
    recommendations.append(
        "live 与 historical_backtest 必须分开汇报，模型版本之间不得直接累加样本数。"
    )
    return recommendations


def _average(values: list[float | None]) -> float | None:
    present = [float(item) for item in values if item is not None]
    return round(mean(present), 4) if present else None


def _safe_ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
