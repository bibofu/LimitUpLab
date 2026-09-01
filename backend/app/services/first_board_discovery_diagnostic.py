"""Forward-only checks retained for the historical first-board discovery experiment."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import mean, median

import numpy as np

from app.models import (
    FirstBoardDiscoveryCandidate,
    FirstBoardDiscoveryDiagnosticResponse,
    FirstBoardDiscoveryFactorDiagnosticRow,
    FirstBoardDiscoveryOutcomeDate,
    LimitUpEvent,
)
from app.repositories import (
    SQLiteAuctionFinalRepository,
    SQLiteFirstBoardDiscoveryRepository,
)
from app.services.factor_signal_diagnostic import (
    DateBlockedFactorSample,
    build_date_blocked_lasso_summary,
    sign_flip_p_value,
    spearman_rho,
)
from app.services.first_board_discovery import LEGACY_FIRST_BOARD_DISCOVERY_VERSION


DISCOVERY_DIAGNOSTIC_VERSION = "first-board-discovery-diagnostic-v1-date-blocked"
MIN_FACTOR_TRADE_DATES = 5
MIN_VERDICT_TRADE_DATES = 10
PERMUTATION_ITERATIONS = 4096
DISCOVERY_FACTOR_NAMES = {
    "total_score": "总分",
    "theme_strength": "题材强度",
    "news_catalyst": "新闻催化",
    "popularity": "市场关注度",
    "momentum": "短期动量",
    "volume_expansion": "量能扩张",
    "position_structure": "位置结构",
    "data_quality": "数据完整性",
}
_FACTOR_KEYS_BY_NAME = {
    value: key
    for key, value in DISCOVERY_FACTOR_NAMES.items()
    if key != "total_score"
}


def build_first_board_discovery_diagnostic(
    *,
    events: list[LimitUpEvent],
    start_date: date,
    end_date: date,
    discovery_repository: SQLiteFirstBoardDiscoveryRepository | None = None,
    auction_repository: SQLiteAuctionFinalRepository | None = None,
    strategy_version: str = LEGACY_FIRST_BOARD_DISCOVERY_VERSION,
    top_k: int = 10,
    bootstrap_iterations: int = 200,
    random_seed: int = 29,
) -> FirstBoardDiscoveryDiagnosticResponse:
    """Evaluate only snapshots that existed before their target session."""

    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    bounded_top_k = max(1, min(top_k, 30))
    discovery_repo = discovery_repository or SQLiteFirstBoardDiscoveryRepository()
    auction_repo = auction_repository or SQLiteAuctionFinalRepository(
        discovery_repo.database_path
    )
    snapshots = [
        snapshot
        for snapshot in discovery_repo.list_by_target_date(start_date, end_date)
        if snapshot.generated_by == strategy_version
    ]
    snapshots_by_target = {
        snapshot.target_trade_date: snapshot
        for snapshot in snapshots
        if (
            snapshot.target_trade_date is not None
            and snapshot.data_as_of < snapshot.target_trade_date
            and snapshot.snapshot_created_at.date() <= snapshot.data_as_of
        )
    }
    official_by_date = {
        snapshot.trade_date: snapshot
        for snapshot in auction_repo.list_between(start_date, end_date)
    }
    event_dates = {
        event.trade_date
        for event in events
        if start_date <= event.trade_date <= end_date
    }
    first_board_symbols = defaultdict(set)
    for event in events:
        if (
            start_date <= event.trade_date <= end_date
            and event.closed_limit
            and event.board_height == 1
        ):
            first_board_symbols[event.trade_date].add(event.symbol)

    factor_samples: list[DateBlockedFactorSample] = []
    date_rows: list[FirstBoardDiscoveryOutcomeDate] = []
    base_top_hits = 0
    base_top_count = 0
    pool_hits = 0
    pool_count = 0
    official_hits = 0
    official_count = 0
    official_pool_hits = 0
    official_pool_count = 0

    for target_date, snapshot in sorted(snapshots_by_target.items()):
        if target_date not in event_dates:
            continue
        candidates = sorted(
            snapshot.candidates,
            key=lambda item: (-item.score, item.facts.symbol),
        )
        successful = first_board_symbols[target_date]
        top_candidates = candidates[:bounded_top_k]
        date_pool_hits = sum(
            candidate.facts.symbol in successful for candidate in candidates
        )
        date_top_hits = sum(
            candidate.facts.symbol in successful for candidate in top_candidates
        )
        base_top_count += len(top_candidates)
        base_top_hits += date_top_hits
        pool_count += len(candidates)
        pool_hits += date_pool_hits

        for candidate in candidates:
            factor_samples.append(
                DateBlockedFactorSample(
                    trade_date=target_date,
                    symbol=candidate.facts.symbol,
                    factor_scores=_factor_scores(candidate),
                    outcome=float(candidate.facts.symbol in successful),
                )
            )

        official_snapshot = official_by_date.get(target_date)
        official_candidates = (
            sorted(
                [
                    candidate
                    for candidate in official_snapshot.candidates
                    if candidate.strategy == "discovery"
                ],
                key=lambda item: item.final_rank,
            )[:bounded_top_k]
            if official_snapshot is not None
            else []
        )
        date_official_hits = sum(
            candidate.symbol in successful for candidate in official_candidates
        )
        if official_candidates:
            official_count += len(official_candidates)
            official_hits += date_official_hits
            official_pool_count += len(candidates)
            official_pool_hits += date_pool_hits

        pool_rate = _rate(date_pool_hits, len(candidates))
        base_rate = _rate(date_top_hits, len(top_candidates))
        official_rate = _rate(date_official_hits, len(official_candidates))
        date_rows.append(
            FirstBoardDiscoveryOutcomeDate(
                data_as_of=snapshot.data_as_of,
                target_trade_date=target_date,
                candidate_count=len(candidates),
                top_k=bounded_top_k,
                base_top_count=len(top_candidates),
                base_top_hit_count=date_top_hits,
                base_top_hit_rate=base_rate,
                pool_hit_count=date_pool_hits,
                pool_hit_rate=pool_rate,
                base_top_lift=_difference(base_rate, pool_rate),
                official_top_count=len(official_candidates),
                official_top_hit_count=date_official_hits,
                official_top_hit_rate=official_rate,
                official_top_lift=_difference(official_rate, pool_rate),
                official_snapshot_available=bool(official_candidates),
                successful_symbols=sorted(
                    candidate.facts.symbol
                    for candidate in candidates
                    if candidate.facts.symbol in successful
                ),
            )
        )

    warnings: list[str] = []
    ready_date_count = len(date_rows)
    if ready_date_count < MIN_VERDICT_TRADE_DATES:
        warnings.append(
            f"只有 {ready_date_count} 个前向快照具备目标日结果，"
            "当前统计只用于建立基线，不调整生产权重。"
        )
    missing_outcomes = len(snapshots_by_target) - ready_date_count
    if missing_outcomes > 0:
        warnings.append(f"另有 {missing_outcomes} 个快照尚未到达或缺少目标交易日数据。")

    factor_rows, strongest_factor_key = _factor_rows(
        factor_samples,
        random_seed=random_seed,
    )
    lasso = build_date_blocked_lasso_summary(
        samples=factor_samples,
        factor_keys=[
            key for key in DISCOVERY_FACTOR_NAMES if key != "total_score"
        ],
        lasso_alpha_fraction=0.1,
        bootstrap_iterations=bootstrap_iterations,
        random_seed=random_seed,
        warnings=warnings,
    )
    verdict_status, verdict = _verdict(
        trade_date_count=ready_date_count,
        factor_rows=factor_rows,
        lasso_signal=lasso.joint_signal_detected,
    )
    base_rate = _rate(base_top_hits, base_top_count)
    pool_rate = _rate(pool_hits, pool_count)
    official_rate = _rate(official_hits, official_count)
    official_pool_rate = _rate(official_pool_hits, official_pool_count)

    return FirstBoardDiscoveryDiagnosticResponse(
        start_date=start_date,
        end_date=end_date,
        strategy_version=strategy_version,
        snapshot_count=len(snapshots_by_target),
        outcome_ready_trade_date_count=ready_date_count,
        sample_size=len(factor_samples),
        top_k=bounded_top_k,
        base_top_sample_size=base_top_count,
        base_top_hit_count=base_top_hits,
        base_top_hit_rate=base_rate,
        pool_hit_count=pool_hits,
        pool_hit_rate=pool_rate,
        base_top_lift=_difference(base_rate, pool_rate),
        official_top_sample_size=official_count,
        official_top_hit_count=official_hits,
        official_top_hit_rate=official_rate,
        official_top_lift=_difference(official_rate, official_pool_rate),
        mean_daily_base_top_lift=_average(
            [item.base_top_lift for item in date_rows if item.base_top_lift is not None]
        ),
        mean_daily_official_top_lift=_average(
            [
                item.official_top_lift
                for item in date_rows
                if item.official_top_lift is not None
            ]
        ),
        bonferroni_alpha=round(0.05 / len(DISCOVERY_FACTOR_NAMES), 5),
        factors=factor_rows,
        lasso=lasso,
        strongest_factor_key=strongest_factor_key,
        verdict_status=verdict_status,
        verdict=verdict,
        dates=date_rows,
        caveats=[
            "只评估真实持久化的前向快照，不为历史日期事后重建候选。",
            "Outcome 为目标交易日是否收盘首板，不代表后续收益或可交易性。",
            "单因子按交易日计算横截面 IC；联合模型按交易日留一，股票数不冒充独立日期数。",
            "集合竞价正式 Top10 仅从该功能启用后的真实终值快照开始评价。",
            "目标日涨停池必须已经完成收盘导入；盘中部分数据不得作为 Outcome。",
        ],
        warnings=warnings,
        generated_by=DISCOVERY_DIAGNOSTIC_VERSION,
    )


def _factor_scores(candidate: FirstBoardDiscoveryCandidate) -> dict[str, float]:
    scores = {"total_score": candidate.score / 100.0}
    for item in candidate.score_breakdown:
        key = _FACTOR_KEYS_BY_NAME.get(item.name)
        if key is not None and item.max_score > 0:
            scores[key] = item.score / item.max_score
    return scores


def _factor_rows(
    samples: list[DateBlockedFactorSample],
    *,
    random_seed: int,
) -> tuple[list[FirstBoardDiscoveryFactorDiagnosticRow], str | None]:
    grouped = defaultdict(list)
    for sample in samples:
        grouped[sample.trade_date].append(sample)
    alpha = 0.05 / len(DISCOVERY_FACTOR_NAMES)
    rows: list[FirstBoardDiscoveryFactorDiagnosticRow] = []
    for index, (factor_key, factor_name) in enumerate(DISCOVERY_FACTOR_NAMES.items()):
        daily_ics: list[float] = []
        sample_size = 0
        for date_samples in grouped.values():
            pairs = [
                (item.factor_scores[factor_key], item.outcome)
                for item in date_samples
                if factor_key in item.factor_scores
            ]
            sample_size += len(pairs)
            if len(pairs) < 3:
                continue
            factor_values = np.array([item[0] for item in pairs], dtype=float)
            outcomes = np.array([item[1] for item in pairs], dtype=float)
            rho = spearman_rho(factor_values, outcomes)
            if rho is not None:
                daily_ics.append(rho)
        p_value = (
            sign_flip_p_value(
                np.array(daily_ics, dtype=float),
                iterations=PERMUTATION_ITERATIONS,
                random_seed=random_seed + index,
            )
            if len(daily_ics) >= MIN_FACTOR_TRADE_DATES
            else None
        )
        mean_ic = mean(daily_ics) if daily_ics else None
        if mean_ic is None or abs(mean_ic) <= 1e-9:
            direction = "inconclusive"
        else:
            direction = "positive" if mean_ic > 0 else "negative"
        rows.append(
            FirstBoardDiscoveryFactorDiagnosticRow(
                factor_key=factor_key,
                factor_name=factor_name,
                sample_size=sample_size,
                trade_date_count=len(daily_ics),
                mean_daily_ic=None if mean_ic is None else round(mean_ic, 4),
                median_daily_ic=(
                    None if not daily_ics else round(median(daily_ics), 4)
                ),
                daily_ic_positive_rate=(
                    None
                    if not daily_ics
                    else round(sum(value > 0 for value in daily_ics) / len(daily_ics), 4)
                ),
                p_value=None if p_value is None else round(p_value, 4),
                significant_after_bonferroni=bool(
                    p_value is not None and p_value < alpha
                ),
                direction=direction,
            )
        )
    strongest = max(
        (item for item in rows if item.mean_daily_ic is not None),
        key=lambda item: abs(item.mean_daily_ic or 0.0),
        default=None,
    )
    return rows, strongest.factor_key if strongest is not None else None


def _verdict(
    *,
    trade_date_count: int,
    factor_rows: list[FirstBoardDiscoveryFactorDiagnosticRow],
    lasso_signal: bool,
) -> tuple[str, str]:
    if trade_date_count < MIN_VERDICT_TRADE_DATES:
        return (
            "insufficient_sample",
            f"目前只有 {trade_date_count} 个结果完整的前向交易日，"
            f"少于 {MIN_VERDICT_TRADE_DATES} 日，暂不判断首板挖掘规则是否有效。",
        )
    significant = [item for item in factor_rows if item.significant_after_bonferroni]
    if significant or lasso_signal:
        names = "、".join(item.factor_name for item in significant) or "联合因子"
        return (
            "signal_requires_validation",
            f"发现需要继续前向验证的候选信号：{names}；"
            "当前结果不得直接用于调整生产权重。",
        )
    return (
        "no_robust_signal",
        "日期阻断诊断尚未发现可复现的首板挖掘信号；"
        "这是当前样本下的暂时性否定证据，不等于已经证明规则无效。",
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 4)


def _average(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None
