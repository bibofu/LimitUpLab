"""Date-aware falsification diagnostic for the 14 scoring factors.

The scoring optimizer waits for >=60 outcome-ready trade dates before letting a
challenger promote. That discipline is correct, but it also delays the feedback
loop by months. This module compresses the "is there *any* usable signal yet"
question into a fast diagnostic over every candidate that already has a
next-day outcome. Statistical inference is performed at the trade-date level:
daily cross-sectional ICs use a sign-flip test, extreme-group spreads preserve
ties, and the joint Lasso is evaluated by leave-one-date-out prediction with
date-block bootstrap stability.

The point is falsification, not validation: failing to find signal here does
not prove the factors are useless. It is date-aware negative evidence under one
outcome definition and should prevent premature re-tuning while more complete
outcomes accumulate.
"""

from __future__ import annotations

from collections import defaultdict
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


FACTOR_SIGNAL_DIAGNOSTIC_VERSION = "factor-signal-diagnostic-v2-date-blocked"
SINGLE_FACTOR_MIN_SAMPLE = 3
SINGLE_FACTOR_MIN_TRADE_DATES = 5
MULTIVARIATE_MIN_TRADE_DATES = 8
VERDICT_MIN_TRADE_DATES = 10
BONFERRONI_FAMILY_SIZE = len(FACTOR_NAMES)
BOOTSTRAP_DEFAULT_ITERATIONS = 200
PERMUTATION_DEFAULT_ITERATIONS = 4096
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
    if trade_dates and len(trade_dates) < 30:
        warnings.append(
            f"仅有 {len(trade_dates)} 个 outcome-ready 交易日，"
            "日期级统计功效仍然有限。"
        )

    bonferroni_alpha = round(0.05 / BONFERRONI_FAMILY_SIZE, 5)
    factor_rows, strongest_factor_key = _build_factor_rows(
        samples,
        permutation_iterations=PERMUTATION_DEFAULT_ITERATIONS,
        random_seed=random_seed,
    )
    lasso_summary = _build_lasso_summary(
        samples=samples,
        factor_keys=list(FACTOR_NAMES.keys()),
        lasso_alpha_fraction=lasso_alpha_fraction,
        bootstrap_iterations=bootstrap_iterations,
        random_seed=random_seed,
        warnings=warnings,
    )
    verdict_status, verdict = _build_verdict(
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
        verdict_status=verdict_status,
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
    sample_trade_dates = sorted({sample.trade_date for sample in samples})
    return samples, sample_trade_dates


def _build_factor_rows(
    samples: list[_CandidateSample],
    *,
    permutation_iterations: int,
    random_seed: int,
) -> tuple[list[FactorSignalDiagnosticRow], str | None]:
    """Compute equal-date-weighted IC and tie-safe extreme-group spreads."""

    rows: list[FactorSignalDiagnosticRow] = []
    bonferroni_alpha = 0.05 / BONFERRONI_FAMILY_SIZE
    strongest_key: str | None = None
    strongest_abs_rho = -1.0
    for factor_index, (factor_key, factor_name) in enumerate(FACTOR_NAMES.items()):
        pairs = [
            (sample.factor_scores[factor_key], sample.outcome)
            for sample in samples
            if factor_key in sample.factor_scores
        ]
        n = len(pairs)
        daily_ics = _daily_factor_ics(samples, factor_key)
        trade_date_count = len(daily_ics)
        mean_ic = (
            float(np.mean([value for _trade_date, value in daily_ics]))
            if daily_ics
            else None
        )
        median_ic = (
            float(np.median([value for _trade_date, value in daily_ics]))
            if daily_ics
            else None
        )
        positive_rate = (
            float(np.mean([value > 0 for _trade_date, value in daily_ics]))
            if daily_ics
            else None
        )
        p_value = (
            _sign_flip_p_value(
                np.array([value for _trade_date, value in daily_ics], dtype=float),
                iterations=permutation_iterations,
                random_seed=random_seed + factor_index,
            )
            if trade_date_count >= SINGLE_FACTOR_MIN_TRADE_DATES
            else None
        )
        significant = bool(p_value is not None and p_value < bonferroni_alpha)
        (
            spread_date_count,
            top_count,
            bottom_count,
            top_mean,
            bottom_mean,
            spread,
        ) = _date_aware_tercile_spread(samples, factor_key)
        if mean_ic is None:
            direction = "inconclusive"
        elif mean_ic > 1e-9:
            direction = "positive"
        elif mean_ic < -1e-9:
            direction = "negative"
        else:
            direction = "inconclusive"
        rows.append(
            FactorSignalDiagnosticRow(
                factor_key=factor_key,
                factor_name=factor_name,
                sample_size=n,
                trade_date_count=trade_date_count,
                mean_daily_ic=None if mean_ic is None else round(mean_ic, 4),
                median_daily_ic=None if median_ic is None else round(median_ic, 4),
                daily_ic_positive_rate=(
                    None if positive_rate is None else round(positive_rate, 4)
                ),
                p_value=None if p_value is None else round(p_value, 4),
                significant_after_bonferroni=significant,
                tercile_trade_date_count=spread_date_count,
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
        if mean_ic is not None and abs(mean_ic) > strongest_abs_rho:
            strongest_abs_rho = abs(mean_ic)
            strongest_key = factor_key
    return rows, strongest_key


def _samples_by_date(
    samples: list[_CandidateSample],
) -> dict[date, list[_CandidateSample]]:
    """Group samples without treating same-day candidates as independent dates."""

    grouped: dict[date, list[_CandidateSample]] = defaultdict(list)
    for sample in samples:
        grouped[sample.trade_date].append(sample)
    return dict(sorted(grouped.items()))


def _daily_factor_ics(
    samples: list[_CandidateSample],
    factor_key: str,
) -> list[tuple[date, float]]:
    """Return one cross-sectional Spearman IC per eligible trade date."""

    daily_ics: list[tuple[date, float]] = []
    for trade_date, date_samples in _samples_by_date(samples).items():
        pairs = [
            (sample.factor_scores[factor_key], sample.outcome)
            for sample in date_samples
            if factor_key in sample.factor_scores
        ]
        if len(pairs) < SINGLE_FACTOR_MIN_SAMPLE:
            continue
        factor_values = np.array([pair[0] for pair in pairs], dtype=float)
        outcomes = np.array([pair[1] for pair in pairs], dtype=float)
        rho = _spearman_rho(factor_values, outcomes)
        if rho is not None:
            daily_ics.append((trade_date, rho))
    return daily_ics


def _sign_flip_p_value(
    values: np.ndarray,
    *,
    iterations: int,
    random_seed: int,
) -> float | None:
    """Two-sided randomization p-value for a date-level mean statistic."""

    if values.size < 2 or iterations <= 0:
        return None
    observed = abs(float(values.mean()))
    rng = np.random.default_rng(random_seed)
    exceedances = 0
    for _ in range(iterations):
        signs = rng.choice(np.array([-1.0, 1.0]), size=values.size)
        if abs(float(np.mean(values * signs))) >= observed - 1e-12:
            exceedances += 1
    return (exceedances + 1) / (iterations + 1)


def _build_lasso_summary(
    *,
    samples: list[_CandidateSample],
    factor_keys: list[str],
    lasso_alpha_fraction: float,
    bootstrap_iterations: int,
    random_seed: int,
    warnings: list[str],
) -> FactorSignalLassoSummary:
    """Fit a joint Lasso and evaluate it by leave-one-date-out prediction."""

    sample_size = len(samples)
    trade_dates = sorted({sample.trade_date for sample in samples})
    if len(trade_dates) < MULTIVARIATE_MIN_TRADE_DATES:
        warnings.append("有效交易日不足以做日期阻断的联合 Lasso/OLS 分析。")
        return FactorSignalLassoSummary(
            sample_size=sample_size,
            lasso_alpha=0.0,
            alpha_max=0.0,
            retained_factor_count=0,
            retained_factor_keys=[],
            bootstrap_iterations=0,
            note=(
                f"有效交易日 {len(trade_dates)} < {MULTIVARIATE_MIN_TRADE_DATES}，"
                "跳过日期阻断的联合分析。"
            ),
        )

    X, y, date_labels = _date_centered_design_matrix(samples, factor_keys)
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
    (
        blocked_oos_r2,
        blocked_oos_date_count,
        blocked_oos_mean_ic,
        blocked_oos_ic_p_value,
    ) = _blocked_lodo_lasso(
        X=X,
        y=y,
        date_labels=date_labels,
        alpha_fraction=lasso_alpha_fraction,
        permutation_iterations=PERMUTATION_DEFAULT_ITERATIONS,
        random_seed=random_seed + 1000,
    )
    retention_rates = _bootstrap_retention(
        X=X,
        y=y,
        date_labels=date_labels,
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
    joint_signal_detected = bool(
        blocked_oos_r2 is not None
        and blocked_oos_r2 > 0.0
        and blocked_oos_mean_ic is not None
        and blocked_oos_mean_ic > 0.0
        and blocked_oos_ic_p_value is not None
        and blocked_oos_ic_p_value < 0.05
    )
    note = (
        f"Lasso 在 alpha={lasso_alpha:.4g}（={lasso_alpha_fraction:.3g}*alpha_max）下保留 "
        f"{len(retained)}/{len(factor_keys)} 个因子；"
        f"样本内 OLS 调整 R^2={_fmt_float(ols_adjusted_r2)}；"
        f"按交易日留一 OOS R^2={_fmt_float(blocked_oos_r2)}，"
        f"平均日 IC={_fmt_float(blocked_oos_mean_ic)}；"
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
        blocked_oos_r2=(
            None if blocked_oos_r2 is None else round(blocked_oos_r2, 4)
        ),
        blocked_oos_trade_date_count=blocked_oos_date_count,
        blocked_oos_mean_daily_ic=(
            None if blocked_oos_mean_ic is None else round(blocked_oos_mean_ic, 4)
        ),
        blocked_oos_ic_p_value=(
            None
            if blocked_oos_ic_p_value is None
            else round(blocked_oos_ic_p_value, 4)
        ),
        joint_signal_detected=joint_signal_detected,
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


def _date_centered_design_matrix(
    samples: list[_CandidateSample],
    factor_keys: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Normalize features and outcomes within each trade date."""

    X, y = _design_matrix(samples, factor_keys)
    labels = np.array([sample.trade_date.toordinal() for sample in samples], dtype=int)
    X_centered = np.zeros_like(X, dtype=float)
    y_centered = np.zeros_like(y, dtype=float)
    for label in np.unique(labels):
        mask = labels == label
        date_X = X[mask]
        date_y = y[mask]
        for column_index in range(X.shape[1]):
            column = date_X[:, column_index]
            std = float(column.std())
            if std > 0.0:
                X_centered[mask, column_index] = (column - column.mean()) / std
        y_centered[mask] = date_y - date_y.mean()
    return X_centered, y_centered, labels


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
    beta, _residuals, rank, _sv = np.linalg.lstsq(
        X_std, y_centered, rcond=None
    )
    residual = y_centered - X_std @ beta
    ss_res = float(np.sum(residual ** 2))
    r2 = 1.0 - ss_res / ss_tot
    degrees = n - rank - 1
    if degrees > 0:
        adjusted = 1.0 - (1.0 - r2) * (n - 1) / degrees
    else:
        adjusted = None
    return r2, adjusted


def _blocked_lodo_lasso(
    *,
    X: np.ndarray,
    y: np.ndarray,
    date_labels: np.ndarray,
    alpha_fraction: float,
    permutation_iterations: int,
    random_seed: int,
) -> tuple[float | None, int, float | None, float | None]:
    """Evaluate Lasso with every trade date held out exactly once."""

    unique_dates = np.unique(date_labels)
    predictions = np.full(y.shape, np.nan, dtype=float)
    daily_ics: list[float] = []
    covered_dates = 0
    for holdout_date in unique_dates:
        train_mask = date_labels != holdout_date
        test_mask = date_labels == holdout_date
        if int(np.sum(train_mask)) < 3 or int(np.sum(test_mask)) < 2:
            continue
        X_train, X_test = _standardize_train_test(X[train_mask], X[test_mask])
        y_train = y[train_mask]
        y_train_centered = y_train - y_train.mean()
        alpha_max = float(
            np.max(np.abs(X_train.T @ y_train_centered) / y_train.size)
        )
        if alpha_max <= 0.0:
            predictions[test_mask] = 0.0
        else:
            beta = _fit_lasso(
                X_train,
                y_train_centered,
                max(alpha_fraction * alpha_max, 1e-9),
            )
            predictions[test_mask] = X_test @ beta
        covered_dates += 1
        rho = _spearman_rho(predictions[test_mask], y[test_mask])
        if rho is not None:
            daily_ics.append(rho)

    covered = np.isfinite(predictions)
    if not np.any(covered):
        return None, 0, None, None
    denominator = float(np.sum(y[covered] ** 2))
    if denominator <= 0.0:
        oos_r2 = None
    else:
        residual = y[covered] - predictions[covered]
        oos_r2 = 1.0 - float(np.sum(residual ** 2)) / denominator
    mean_daily_ic = float(np.mean(daily_ics)) if daily_ics else None
    ic_p_value = (
        _sign_flip_p_value(
            np.array(daily_ics, dtype=float),
            iterations=permutation_iterations,
            random_seed=random_seed,
        )
        if len(daily_ics) >= SINGLE_FACTOR_MIN_TRADE_DATES
        else None
    )
    return oos_r2, covered_dates, mean_daily_ic, ic_p_value


def _standardize_train_test(
    X_train: np.ndarray,
    X_test: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Fit standardization on training rows and apply it to held-out rows."""

    train = np.zeros_like(X_train, dtype=float)
    test = np.zeros_like(X_test, dtype=float)
    for column_index in range(X_train.shape[1]):
        column = X_train[:, column_index]
        mean = float(column.mean())
        std = float(column.std())
        if std > 0.0:
            train[:, column_index] = (column - mean) / std
            test[:, column_index] = (X_test[:, column_index] - mean) / std
    return train, test


def _bootstrap_retention(
    *,
    X: np.ndarray,
    y: np.ndarray,
    date_labels: np.ndarray,
    factor_count: int,
    alpha_fraction: float,
    iterations: int,
    random_seed: int,
) -> dict[int, float]:
    """Refit Lasso on resampled trade-date blocks and count retention."""

    rng = np.random.default_rng(random_seed)
    unique_dates = np.unique(date_labels)
    retention_counts = {j: 0 for j in range(factor_count)}
    completed_iterations = 0
    for _ in range(iterations):
        sampled_dates = rng.choice(unique_dates, size=unique_dates.size, replace=True)
        index_blocks = [np.flatnonzero(date_labels == label) for label in sampled_dates]
        if not index_blocks:
            continue
        idx = np.concatenate(index_blocks)
        X_b = X[idx]
        y_b = y[idx]
        n = X_b.shape[0]
        X_b_std, y_b_centered = _standardize(X_b, y_b)
        ss_tot = float(np.sum(y_b_centered ** 2))
        if ss_tot <= 0.0:
            continue
        alpha_max_b = float(np.max(np.abs(X_b_std.T @ y_b_centered) / n))
        if alpha_max_b <= 0.0:
            continue
        alpha_b = max(alpha_fraction * alpha_max_b, 1e-9)
        beta_b = _fit_lasso(X_b_std, y_b_centered, alpha_b, max_iter=200)
        completed_iterations += 1
        for j in range(factor_count):
            if abs(beta_b[j]) > 1e-8:
                retention_counts[j] += 1
    denominator = max(completed_iterations, 1)
    return {j: count / denominator for j, count in retention_counts.items()}


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
    """Tie-safe top/bottom quantile spread for one trade date."""

    n = factor_values.size
    if n < 3 or np.unique(factor_values).size < 2:
        return 0, 0, None, None, None
    lower = float(np.quantile(factor_values, 1.0 / 3.0, method="lower"))
    upper = float(np.quantile(factor_values, 2.0 / 3.0, method="higher"))
    if lower >= upper:
        lower = float(np.min(factor_values))
        upper = float(np.max(factor_values))
    bottom = outcomes[factor_values <= lower]
    top = outcomes[factor_values >= upper]
    if bottom.size == 0 or top.size == 0:
        return 0, 0, None, None, None
    top_mean = float(top.mean())
    bottom_mean = float(bottom.mean())
    return (
        int(top.size),
        int(bottom.size),
        top_mean,
        bottom_mean,
        top_mean - bottom_mean,
    )


def _date_aware_tercile_spread(
    samples: list[_CandidateSample],
    factor_key: str,
) -> tuple[int, int, int, float | None, float | None, float | None]:
    """Average tie-safe extreme-group spreads with equal weight per date."""

    top_count = 0
    bottom_count = 0
    daily_top_means: list[float] = []
    daily_bottom_means: list[float] = []
    daily_spreads: list[float] = []
    for date_samples in _samples_by_date(samples).values():
        pairs = [
            (sample.factor_scores[factor_key], sample.outcome)
            for sample in date_samples
            if factor_key in sample.factor_scores
        ]
        if len(pairs) < SINGLE_FACTOR_MIN_SAMPLE:
            continue
        factor_values = np.array([pair[0] for pair in pairs], dtype=float)
        outcomes = np.array([pair[1] for pair in pairs], dtype=float)
        date_top_count, date_bottom_count, top_mean, bottom_mean, spread = (
            _tercile_spread(factor_values, outcomes)
        )
        if spread is None or top_mean is None or bottom_mean is None:
            continue
        top_count += date_top_count
        bottom_count += date_bottom_count
        daily_top_means.append(top_mean)
        daily_bottom_means.append(bottom_mean)
        daily_spreads.append(spread)
    date_count = len(daily_spreads)
    if not daily_spreads:
        return 0, 0, 0, None, None, None
    return (
        date_count,
        top_count,
        bottom_count,
        float(np.mean(daily_top_means)),
        float(np.mean(daily_bottom_means)),
        float(np.mean(daily_spreads)),
    )


def _build_verdict(
    *,
    sample_size: int,
    factor_rows: list[FactorSignalDiagnosticRow],
    lasso_summary: FactorSignalLassoSummary,
    bonferroni_alpha: float,
    trade_date_count: int,
) -> tuple[str, str]:
    """Assemble a plain-text honest verdict from the diagnostic evidence."""

    if trade_date_count < VERDICT_MIN_TRADE_DATES:
        return "insufficient_sample", (
            f"共有 {sample_size} 个候选样本，但仅覆盖 {trade_date_count} 个"
            f" outcome-ready 交易日（<{VERDICT_MIN_TRADE_DATES}），"
            "日期级统计功效不足，暂不下结论，"
            "请继续积累结果完整的次日 Outcome。"
        )

    significant = [
        row for row in factor_rows if row.significant_after_bonferroni
    ]
    tested_factor_count = sum(row.p_value is not None for row in factor_rows)
    strongest = max(
        (row for row in factor_rows if row.mean_daily_ic is not None),
        key=lambda row: abs(row.mean_daily_ic or 0.0),
        default=None,
    )
    parts: list[str] = []
    if significant:
        names = "、".join(row.factor_name for row in significant)
        parts.append(
            f"在 {sample_size} 个候选、{trade_date_count} 个 outcome-ready 交易日上，"
            f"{len(significant)} 个因子的平均日横截面 IC 通过日期级符号翻转检验"
            f"及 Bonferroni 校正（alpha={bonferroni_alpha:.4f}）：{names}。"
        )
    else:
        parts.append(
            f"在 {sample_size} 个候选、{trade_date_count} 个 outcome-ready 交易日上，"
            f"{tested_factor_count} 个具备足够日期内变异的因子均未通过日期级符号翻转检验"
            f"及 Bonferroni 校正（alpha={bonferroni_alpha:.4f}）。"
        )
    if strongest is not None:
        parts.append(
            f"单因子中最强的是「{strongest.factor_name}」"
            f"（平均日 IC={strongest.mean_daily_ic:+.2f}，"
            f"有效日期={strongest.trade_date_count}，"
            f"未校正 p={_fmt_float(strongest.p_value)}）。"
        )
    if lasso_summary.blocked_oos_r2 is not None:
        parts.append(
            f"联合 Lasso 按交易日留一验证的 OOS R^2="
            f"{lasso_summary.blocked_oos_r2:.3f}，平均日 IC="
            f"{_fmt_float(lasso_summary.blocked_oos_mean_daily_ic)}。"
        )
    if (
        lasso_summary.bootstrap_max_retention_rate is not None
        and lasso_summary.bootstrap_max_retention_rate < 0.7
    ):
        parts.append("bootstrap 重抽样下联合选择不稳定，当前不能据此调整权重。")
    if significant or lasso_summary.joint_signal_detected:
        parts.append(
            "提示：发现需要继续验证的候选信号，但当前日期较少，"
            "不得据此自动调整生产权重。"
        )
        return "signal_requires_validation", " ".join(parts)
    else:
        parts.append(
            "结论：当前日期阻断诊断未发现可复现的单因子或联合因子信号；"
            "这是低功效下的暂时性否定证据，不等于已经证明因子是噪声。"
        )
        return "no_robust_signal", " ".join(parts)


def _caveats() -> list[str]:
    """Fixed honesty caveats that always accompany the verdict."""

    return [
        "单因子采用逐日横截面 IC；联合模型采用按交易日留一验证，股票数量不作为独立时间样本数。",
        "日期阻断降低了同日相关性造成的虚假显著，但连续交易日仍可能共享市场状态。",
        "Outcome 仅取次日开盘到收盘单一口径，其他口径结论可能不同。",
        "因子分来自手写点规则，缺失 enrichment 时按中性兜底截断为常数，会人为压低相关。",
        "有效交易日较少时统计功效不足，未发现信号不等于信号不存在或已被证明为噪声。",
    ]
