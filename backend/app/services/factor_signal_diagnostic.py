"""Independent in-sample falsification diagnostic for the 14 scoring factors.

The scoring optimizer waits for >=60 outcome-ready trade dates before letting a
challenger promote. That discipline is correct, but it also delays the feedback
loop by months. This module compresses the "is there *any* usable signal yet"
question into a single honest in-sample pass over every candidate that already
has a next-day outcome: per-factor Spearman rank correlation with a Bonferroni
gate, a tercile spread, and a joint Lasso whose selection stability is checked
by bootstrap resampling.

The point is falsification, not validation: failing to find signal here does
not prove the factors are useless, but finding none is strong evidence that the
current sample carries no exploitable cross-sectional signal and that the v3
governance should keep waiting rather than re-tune weights early.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np

from app.models import (
    FactorSignalDiagnosticResponse,
    FactorSignalDiagnosticRow,
    FactorSignalLassoSummary,
    LimitUpEvent,
    ScoringPolicy,
)
from app.repositories import SQLiteFirstBoardRepository, SQLiteScoringPolicyRepository
from app.services.scoring_policy import FACTOR_KEYS_BY_NAME, FACTOR_NAMES


FACTOR_SIGNAL_DIAGNOSTIC_VERSION = "factor-signal-diagnostic-v1"
SINGLE_FACTOR_MIN_SAMPLE = 3
MULTIVARIATE_MIN_SAMPLE = 15
VERDICT_MIN_SAMPLE = 10
BONFERRONI_FAMILY_SIZE = len(FACTOR_NAMES)
BOOTSTRAP_DEFAULT_ITERATIONS = 200
LASSO_ALPHA_FRACTION = 0.1

# Outcome measure -> (field on FirstBoardOutcome, readiness flag attribute).
_OUTCOME_MEASURES: dict[str, tuple[str, str]] = {
    "next_open_to_close_pct": ("next_open_to_close_pct", "next_day_ready"),
    "three_day_open_to_close_pct": ("three_day_open_to_close_pct", "three_day_ready"),
}


@dataclass(frozen=True)
class _CandidateSample:
    """One rated first-board candidate joined with a ready post-board outcome."""

    trade_date: date
    symbol: str
    factor_scores: dict[str, float]
    outcome: float


def build_factor_signal_diagnostic(
    *,
    events: list[LimitUpEvent],
    start_date: date,
    end_date: date,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    policy_repository: SQLiteScoringPolicyRepository | None = None,
    scoring_policy: ScoringPolicy | None = None,
    outcome_measure: str = "next_open_to_close_pct",
    lasso_alpha_fraction: float = LASSO_ALPHA_FRACTION,
    bootstrap_iterations: int = BOOTSTRAP_DEFAULT_ITERATIONS,
    random_seed: int = 13,
) -> FactorSignalDiagnosticResponse:
    """Falsify the 14 first-board scoring factors against a post-board outcome."""

    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    if outcome_measure not in _OUTCOME_MEASURES:
        raise ValueError(
            f"Unsupported outcome_measure: {outcome_measure}. "
            f"Expected one of {sorted(_OUTCOME_MEASURES)}"
        )

    repository = first_board_repository or SQLiteFirstBoardRepository()
    registry = policy_repository or SQLiteScoringPolicyRepository(
        repository.database_path
    )
    active_policy = scoring_policy or registry.ensure_default_policy()

    outcome_field, readiness_flag = _OUTCOME_MEASURES[outcome_measure]
    samples, trade_dates = _collect_samples(
        events=events,
        start_date=start_date,
        end_date=end_date,
        repository=repository,
        scoring_policy=active_policy,
        outcome_field=outcome_field,
        readiness_flag=readiness_flag,
    )
    sample_size = len(samples)
    warnings: list[str] = []
    if trade_dates and len(trade_dates) <= 5:
        warnings.append(
            f"样本集中在 {len(trade_dates)} 个连续交易日，状态集中偏差较大。"
        )

    bonferroni_alpha = round(0.05 / BONFERRONI_FAMILY_SIZE, 5)
    factor_rows, strongest_factor_key = _build_factor_rows(samples)
    lasso_summary = _build_lasso_summary(
        samples=samples,
        factor_keys=list(FACTOR_NAMES.keys()),
        lasso_alpha_fraction=lasso_alpha_fraction,
        bootstrap_iterations=bootstrap_iterations,
        random_seed=random_seed,
        warnings=warnings,
    )
    verdict = _build_verdict(
        sample_size=sample_size,
        factor_rows=factor_rows,
        lasso_summary=lasso_summary,
        bonferroni_alpha=bonferroni_alpha,
        trade_date_count=len(trade_dates),
    )
    caveats = _caveats()

    return FactorSignalDiagnosticResponse(
        start_date=start_date,
        end_date=end_date,
        scoring_version=active_policy.version,
        outcome_measure=outcome_measure,
        trade_date_count=len(trade_dates),
        sample_size=sample_size,
        bonferroni_alpha=bonferroni_alpha,
        factors=factor_rows,
        lasso=lasso_summary,
        strongest_factor_key=strongest_factor_key,
        verdict=verdict,
        caveats=caveats,
        warnings=warnings,
        generated_by=FACTOR_SIGNAL_DIAGNOSTIC_VERSION,
    )


def _collect_samples(
    *,
    events: list[LimitUpEvent],
    start_date: date,
    end_date: date,
    repository: SQLiteFirstBoardRepository,
    scoring_policy: ScoringPolicy,
    outcome_field: str,
    readiness_flag: str,
) -> tuple[list[_CandidateSample], list[date]]:
    """Re-rate candidates per date and join ready post-board outcomes."""

    from app.agents.first_board import build_first_board_ratings

    trade_dates = sorted(
        {
            event.trade_date
            for event in events
            if start_date <= event.trade_date <= end_date
        }
    )
    outcome_lookup = {
        (outcome.base_trade_date, outcome.symbol): outcome
        for outcome in repository.list_outcomes_between(start_date, end_date)
    }
    samples: list[_CandidateSample] = []
    for trade_date in trade_dates:
        ratings = build_first_board_ratings(
            events=events,
            trade_date=trade_date,
            first_board_repository=repository,
            scoring_policy=scoring_policy,
        )
        for candidate in ratings.candidates:
            outcome = outcome_lookup.get((trade_date, candidate.facts.symbol))
            if outcome is None or not getattr(outcome, readiness_flag):
                continue
            value = getattr(outcome, outcome_field)
            if value is None:
                continue
            factor_scores = {
                FACTOR_KEYS_BY_NAME[item.name]: float(item.score)
                for item in candidate.score_breakdown
                if item.name in FACTOR_KEYS_BY_NAME
            }
            samples.append(
                _CandidateSample(
                    trade_date=trade_date,
                    symbol=candidate.facts.symbol,
                    factor_scores=factor_scores,
                    outcome=float(value),
                )
            )
    return samples, trade_dates


def _build_factor_rows(
    samples: list[_CandidateSample],
) -> tuple[list[FactorSignalDiagnosticRow], str | None]:
    """Compute per-factor Spearman correlation, p-value and tercile spread."""

    rows: list[FactorSignalDiagnosticRow] = []
    bonferroni_alpha = 0.05 / BONFERRONI_FAMILY_SIZE
    strongest_key: str | None = None
    strongest_abs_rho = -1.0
    for factor_key, factor_name in FACTOR_NAMES.items():
        pairs = [
            (sample.factor_scores[factor_key], sample.outcome)
            for sample in samples
            if factor_key in sample.factor_scores
        ]
        n = len(pairs)
        if n < SINGLE_FACTOR_MIN_SAMPLE:
            rows.append(
                FactorSignalDiagnosticRow(
                    factor_key=factor_key,
                    factor_name=factor_name,
                    sample_size=n,
                    spearman_rho=None,
                    p_value=None,
                    significant_after_bonferroni=False,
                    top_tercile_count=0,
                    bottom_tercile_count=0,
                    direction="inconclusive",
                )
            )
            continue
        factor_values = np.array([pair[0] for pair in pairs], dtype=float)
        outcomes = np.array([pair[1] for pair in pairs], dtype=float)
        rho = _spearman_rho(factor_values, outcomes)
        p_value = _spearman_p_value(rho, n)
        significant = bool(p_value is not None and p_value < bonferroni_alpha)
        top_count, bottom_count, top_mean, bottom_mean, spread = _tercile_spread(
            factor_values, outcomes
        )
        if rho is None:
            direction = "inconclusive"
        elif rho > 1e-9:
            direction = "positive"
        elif rho < -1e-9:
            direction = "negative"
        else:
            direction = "inconclusive"
        rows.append(
            FactorSignalDiagnosticRow(
                factor_key=factor_key,
                factor_name=factor_name,
                sample_size=n,
                spearman_rho=None if rho is None else round(rho, 4),
                p_value=None if p_value is None else round(p_value, 4),
                significant_after_bonferroni=significant,
                top_tercile_count=top_count,
                bottom_tercile_count=bottom_count,
                top_tercile_mean_outcome=(
                    None if top_mean is None else round(top_mean, 4)
                ),
                bottom_tercile_mean_outcome=(
                    None if bottom_mean is None else round(bottom_mean, 4)
                ),
                tercile_spread_pct=None if spread is None else round(spread, 4),
                direction=direction,
            )
        )
        if rho is not None and abs(rho) > strongest_abs_rho:
            strongest_abs_rho = abs(rho)
            strongest_key = factor_key
    return rows, strongest_key


def _build_lasso_summary(
    *,
    samples: list[_CandidateSample],
    factor_keys: list[str],
    lasso_alpha_fraction: float,
    bootstrap_iterations: int,
    random_seed: int,
    warnings: list[str],
) -> FactorSignalLassoSummary:
    """Fit a joint Lasso on standardized factor scores vs the outcome."""

    sample_size = len(samples)
    if sample_size < MULTIVARIATE_MIN_SAMPLE:
        warnings.append("样本量不足以做多变量联合 Lasso/OLS 分析。")
        return FactorSignalLassoSummary(
            sample_size=sample_size,
            lasso_alpha=0.0,
            alpha_max=0.0,
            retained_factor_count=0,
            retained_factor_keys=[],
            bootstrap_iterations=0,
            note=(
                f"样本量 n={sample_size} < {MULTIVARIATE_MIN_SAMPLE}，"
                "跳过联合 Lasso/OLS 分析。"
            ),
        )

    X, y = _design_matrix(samples, factor_keys)
    X_std, y_centered = _standardize(X, y)
    alpha_max = float(np.max(np.abs(X_std.T @ y_centered) / sample_size))
    if alpha_max <= 0.0:
        warnings.append("alpha_max 为 0（因子与 outcome 正交或 outcome 无变异），联合分析无意义。")
        return FactorSignalLassoSummary(
            sample_size=sample_size,
            lasso_alpha=0.0,
            alpha_max=0.0,
            retained_factor_count=0,
            retained_factor_keys=[],
            bootstrap_iterations=0,
            note="alpha_max 为 0，联合分析无意义。",
        )
    lasso_alpha = max(lasso_alpha_fraction * alpha_max, 1e-9)
    beta = _fit_lasso(X_std, y_centered, lasso_alpha)
    coefficients = {
        factor_keys[j]: float(beta[j]) for j in range(len(factor_keys))
    }
    retained = [
        factor_keys[j] for j in range(len(factor_keys)) if abs(beta[j]) > 1e-8
    ]
    ols_r2, ols_adjusted_r2 = _ols_r2(X_std, y_centered)
    retention_rates = _bootstrap_retention(
        X=X,
        y=y,
        factor_count=len(factor_keys),
        alpha_fraction=lasso_alpha_fraction,
        iterations=bootstrap_iterations,
        random_seed=random_seed,
    )
    max_retention = max(retention_rates.values()) if retention_rates else None
    if (max_retention or 0.0) < 0.7:
        warnings.append(
            "bootstrap 重抽样下任一因子的 Lasso 保留率均未稳定超过 70%，当前联合选择不稳定。"
        )
    note = (
        f"Lasso 在 alpha={lasso_alpha:.4g}（=0.1*alpha_max）下保留 "
        f"{len(retained)}/{len(factor_keys)} 个因子；"
        f"OLS 调整 R^2={_fmt_float(ols_adjusted_r2)}；"
        f"bootstrap 保留率最高为 {_fmt_float(max_retention)}（0.7 以上视为稳定）。"
    )
    return FactorSignalLassoSummary(
        sample_size=sample_size,
        lasso_alpha=round(lasso_alpha, 6),
        alpha_max=round(alpha_max, 6),
        retained_factor_count=len(retained),
        retained_factor_keys=retained,
        ols_r2=None if ols_r2 is None else round(ols_r2, 4),
        ols_adjusted_r2=(
            None if ols_adjusted_r2 is None else round(ols_adjusted_r2, 4)
        ),
        bootstrap_iterations=bootstrap_iterations,
        bootstrap_max_retention_rate=(
            None if max_retention is None else round(max_retention, 4)
        ),
        coefficients=coefficients,
        bootstrap_retention_rates={
            factor_keys[j]: round(retention_rates[j], 4)
            for j in range(len(factor_keys))
        },
        note=note,
    )


def _design_matrix(
    samples: list[_CandidateSample], factor_keys: list[str]
) -> tuple[np.ndarray, np.ndarray]:
    """Assemble the (n, p) factor matrix and (n,) outcome vector."""

    X = np.zeros((len(samples), len(factor_keys)), dtype=float)
    y = np.zeros(len(samples), dtype=float)
    key_index = {key: j for j, key in enumerate(factor_keys)}
    for i, sample in enumerate(samples):
        for key, value in sample.factor_scores.items():
            if key in key_index:
                X[i, key_index[key]] = value
        y[i] = sample.outcome
    return X, y


def _standardize(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Center y and z-score each X column; constant columns become zeros."""

    X_std = np.empty_like(X, dtype=float)
    for j in range(X.shape[1]):
        column = X[:, j]
        std = float(column.std())
        if std <= 0.0:
            X_std[:, j] = 0.0
        else:
            X_std[:, j] = (column - column.mean()) / std
    y_centered = y - y.mean()
    return X_std, y_centered


def _fit_lasso(
    X: np.ndarray,
    y: np.ndarray,
    alpha: float,
    max_iter: int = 1000,
    tol: float = 1e-6,
) -> np.ndarray:
    """Coordinate-descent Lasso on standardized features and centered target."""

    n, p = X.shape
    beta = np.zeros(p, dtype=float)
    for _ in range(max_iter):
        beta_prev = beta.copy()
        for j in range(p):
            residual = y - X @ beta + X[:, j] * beta[j]
            rho = float(X[:, j] @ residual) / n
            beta[j] = _soft_threshold(rho, alpha)
        if np.max(np.abs(beta - beta_prev)) < tol:
            break
    return beta


def _soft_threshold(rho: float, lam: float) -> float:
    """Soft-thresholding operator for L1 coordinate updates."""

    if rho > lam:
        return rho - lam
    if rho < -lam:
        return rho + lam
    return 0.0


def _fmt_float(value: float | None) -> str:
    """Format an optional float for the verdict note, tolerating None."""

    if value is None:
        return "N/A"
    return f"{value:.3f}"


def _ols_r2(
    X_std: np.ndarray, y_centered: np.ndarray
) -> tuple[float | None, float | None]:
    """Return OLS R^2 and adjusted R^2 on the standardized design."""

    n, p = X_std.shape
    ss_tot = float(np.sum(y_centered ** 2))
    if ss_tot <= 0.0:
        return None, None
    beta, _residuals, _rank, _sv = np.linalg.lstsq(
        X_std, y_centered, rcond=None
    )
    residual = y_centered - X_std @ beta
    ss_res = float(np.sum(residual ** 2))
    r2 = 1.0 - ss_res / ss_tot
    degrees = n - p - 1
    if degrees > 0:
        adjusted = 1.0 - (1.0 - r2) * (n - 1) / degrees
    else:
        adjusted = None
    return r2, adjusted


def _bootstrap_retention(
    *,
    X: np.ndarray,
    y: np.ndarray,
    factor_count: int,
    alpha_fraction: float,
    iterations: int,
    random_seed: int,
) -> dict[int, float]:
    """Refit Lasso on bootstrap resamples and count per-factor retention."""

    rng = np.random.default_rng(random_seed)
    n = X.shape[0]
    retention_counts = {j: 0 for j in range(factor_count)}
    for _ in range(iterations):
        idx = rng.integers(0, n, n)
        X_b = X[idx]
        y_b = y[idx]
        X_b_std, y_b_centered = _standardize(X_b, y_b)
        ss_tot = float(np.sum(y_b_centered ** 2))
        if ss_tot <= 0.0:
            continue
        alpha_max_b = float(np.max(np.abs(X_b_std.T @ y_b_centered) / n))
        if alpha_max_b <= 0.0:
            continue
        alpha_b = max(alpha_fraction * alpha_max_b, 1e-9)
        beta_b = _fit_lasso(X_b_std, y_b_centered, alpha_b, max_iter=200)
        for j in range(factor_count):
            if abs(beta_b[j]) > 1e-8:
                retention_counts[j] += 1
    return {j: count / iterations for j, count in retention_counts.items()}


def _spearman_rho(x: np.ndarray, y: np.ndarray) -> float | None:
    """Pearson correlation of average ranks; None if degenerate or too small."""

    if x.size < SINGLE_FACTOR_MIN_SAMPLE or y.size < SINGLE_FACTOR_MIN_SAMPLE:
        return None
    rank_x = _average_ranks(x)
    rank_y = _average_ranks(y)
    std_x = float(rank_x.std())
    std_y = float(rank_y.std())
    if std_x <= 0.0 or std_y <= 0.0:
        return None
    return float(np.corrcoef(rank_x, rank_y)[0, 1])


def _spearman_p_value(rho: float | None, n: int) -> float | None:
    """Two-sided p-value via the t-approximation (no scipy dependency)."""

    if rho is None or n <= 2:
        return None
    denom = 1.0 - rho * rho
    if denom <= 0.0:
        return 0.0
    t_stat = rho * math.sqrt((n - 2) / denom)
    p = 1.0 - math.erf(abs(t_stat) / math.sqrt(2.0))
    return max(0.0, min(1.0, p))


def _average_ranks(values: np.ndarray) -> np.ndarray:
    """Ranks with average ties, matching scipy.stats.rankdata default."""

    order = np.argsort(values, kind="mergesort")
    ranks = np.empty_like(values, dtype=float)
    sorted_values = values[order]
    n = values.size
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_values[j + 1] == sorted_values[i]:
            j += 1
        average_rank = (i + j) / 2.0 + 1.0
        ranks[order[i : j + 1]] = average_rank
        i = j + 1
    return ranks


def _tercile_spread(
    factor_values: np.ndarray, outcomes: np.ndarray
) -> tuple[int, int, float | None, float | None, float | None]:
    """Mean outcome of the top vs bottom tercile by factor score."""

    n = factor_values.size
    tercile = n // 3
    if tercile < 1:
        return 0, 0, None, None, None
    order = np.argsort(factor_values, kind="mergesort")
    bottom = outcomes[order[:tercile]]
    top = outcomes[order[-tercile:]]
    top_mean = float(top.mean())
    bottom_mean = float(bottom.mean())
    return tercile, tercile, top_mean, bottom_mean, top_mean - bottom_mean


def _build_verdict(
    *,
    sample_size: int,
    factor_rows: list[FactorSignalDiagnosticRow],
    lasso_summary: FactorSignalLassoSummary,
    bonferroni_alpha: float,
    trade_date_count: int,
) -> str:
    """Assemble a plain-text honest verdict from the diagnostic evidence."""

    if sample_size < VERDICT_MIN_SAMPLE:
        return (
            f"样本量 n={sample_size}（跨 {trade_date_count} 个交易日）过小，"
            f"统计功效不足（<{VERDICT_MIN_SAMPLE}），暂不下结论，"
            "请继续积累结果完整的次日 Outcome。"
        )

    significant = [
        row for row in factor_rows if row.significant_after_bonferroni
    ]
    strongest = max(
        (row for row in factor_rows if row.spearman_rho is not None),
        key=lambda row: abs(row.spearman_rho or 0.0),
        default=None,
    )
    parts: list[str] = []
    if significant:
        names = "、".join(row.factor_name for row in significant)
        parts.append(
            f"在 n={sample_size}（跨 {trade_date_count} 个交易日）有次日 Outcome 的首板候选上，"
            f"{len(significant)} 个因子通过 Bonferroni 校正（alpha={bonferroni_alpha:.4f}）：{names}。"
        )
    else:
        parts.append(
            f"在 n={sample_size}（跨 {trade_date_count} 个交易日）有次日 Outcome 的首板候选上，"
            f"14 个因子均未通过 Bonferroni 校正（alpha={bonferroni_alpha:.4f}）。"
        )
    if strongest is not None:
        parts.append(
            f"单因子中最强的是「{strongest.factor_name}」"
            f"（Spearman rho={strongest.spearman_rho:+.2f}，未校正 p={strongest.p_value:.3f}），"
            "该量级在纯噪声下也常见。"
        )
    if lasso_summary.ols_adjusted_r2 is not None:
        parts.append(
            f"联合 OLS 调整 R^2={lasso_summary.ols_adjusted_r2:.3f}，"
            f"Lasso 保留 {lasso_summary.retained_factor_count}/14 个因子。"
        )
    if (
        lasso_summary.bootstrap_max_retention_rate is not None
        and lasso_summary.bootstrap_max_retention_rate < 0.7
    ):
        parts.append("bootstrap 重抽样下联合选择不稳定，当前不能据此调整权重。")
    if significant:
        parts.append(
            "提示：出现统计显著的因子，但仍需样本外验证后再考虑权重调整，避免从噪声中学习。"
        )
    else:
        parts.append(
            "结论：现有样本下未发现可利用的横截面单因子或联合因子信号，"
            "与 v3 尚未战胜最早封板基线一致；建议继续积累结果完整交易日，而非提前调整因子权重。"
        )
    return " ".join(parts)


def _caveats() -> list[str]:
    """Fixed honesty caveats that always accompany the verdict."""

    return [
        "本诊断为样本内证伪，非样本外验证；未发现信号不能反过来当作因子有效的证据。",
        "样本来自连续相邻交易日，可能共享同一市场状态，存在状态集中偏差。",
        "Outcome 仅取次日开盘到收盘单一口径，其他口径结论可能不同。",
        "因子分来自手写点规则，缺失 enrichment 时按中性兜底截断为常数，会人为压低相关。",
        "n 较小时统计功效不足，未发现信号不等于信号不存在。",
    ]
