"""Exact trading-date completeness checks for immutable Top10 outcomes."""

from collections import defaultdict
from datetime import date

from app.models import (
    AgentPrediction,
    LimitUpEvent,
    OutcomeCompletenessDate,
    OutcomeCompletenessReport,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.evaluation_agent import select_canonical_prediction_snapshots


OUTCOME_COMPLETENESS_VERSION = "top10-outcome-completeness-v1"


def build_top10_outcome_completeness(
    *,
    events: list[LimitUpEvent],
    repository: SQLiteFirstBoardRepository,
    as_of_date: date | None = None,
    tracking_days: int = 6,
    top_per_day: int = 10,
) -> OutcomeCompletenessReport:
    """Validate D+1, D+3 and D+5 against exact local trading dates."""

    available_dates = sorted(
        {
            item.trade_date
            for item in events
            if as_of_date is None or item.trade_date <= as_of_date
        }
    )
    resolved_as_of = as_of_date or (available_dates[-1] if available_dates else None)
    if resolved_as_of is None or not available_dates:
        return _empty_report(resolved_as_of, "No local trading dates are available.")

    recent_dates = available_dates[-max(tracking_days, 0) :] if tracking_days else []
    if not recent_dates:
        return _empty_report(resolved_as_of, "Outcome tracking window is empty.")
    predictions = select_canonical_prediction_snapshots(
        repository.list_predictions_between(recent_dates[0], recent_dates[-1])
    )
    by_date: dict[date, list[AgentPrediction]] = defaultdict(list)
    for item in predictions:
        by_date[item.trade_date].append(item)
    if not by_date:
        return _empty_report(
            resolved_as_of,
            "No persisted predictions are available in the tracking window.",
        )

    date_positions = {item: index for index, item in enumerate(available_dates)}
    date_reports: list[OutcomeCompletenessDate] = []
    missing_cases: set[tuple[date, str]] = set()
    for trade_date in sorted(by_date):
        position = date_positions.get(trade_date)
        if position is None:
            continue
        candidates = sorted(
            by_date[trade_date],
            key=lambda item: (-item.score, item.symbol),
        )[: max(top_per_day, 0)]
        if not candidates:
            continue
        expected_dates = available_dates[position : position + 6]
        elapsed_post_days = max(len(expected_dates) - 1, 0)
        d1_mature = elapsed_post_days >= 1
        d3_mature = elapsed_post_days >= 3
        d5_mature = elapsed_post_days >= 5
        d1_ready: list[str] = []
        d3_ready: list[str] = []
        d5_ready: list[str] = []

        for candidate in candidates:
            bars = repository.list_post_bars(
                candidate.symbol,
                trade_date,
                limit=6,
            )
            bar_dates = {bar.trade_date for bar in bars}
            outcome = repository.get_outcome(candidate.symbol, trade_date)
            if (
                d1_mature
                and outcome is not None
                and outcome.next_day_ready
                and outcome.next_trade_date == expected_dates[1]
                and set(expected_dates[:2]).issubset(bar_dates)
            ):
                d1_ready.append(candidate.symbol)
            if (
                d3_mature
                and outcome is not None
                and outcome.three_day_ready
                and set(expected_dates[:4]).issubset(bar_dates)
            ):
                d3_ready.append(candidate.symbol)
            if d5_mature and set(expected_dates[:6]).issubset(bar_dates):
                d5_ready.append(candidate.symbol)

        symbols = [item.symbol for item in candidates]
        d1_missing = _missing_symbols(symbols, d1_ready) if d1_mature else []
        d3_missing = _missing_symbols(symbols, d3_ready) if d3_mature else []
        d5_missing = _missing_symbols(symbols, d5_ready) if d5_mature else []
        for symbol in {*d1_missing, *d3_missing, *d5_missing}:
            missing_cases.add((trade_date, symbol))
        matured = d1_mature or d3_mature or d5_mature
        status = (
            "partial"
            if d1_missing or d3_missing or d5_missing
            else "complete"
            if matured
            else "pending"
        )
        date_reports.append(
            OutcomeCompletenessDate(
                trade_date=trade_date,
                prediction_source=candidates[0].prediction_source,
                candidate_count=len(candidates),
                elapsed_post_trade_days=elapsed_post_days,
                d1_mature=d1_mature,
                d1_expected_count=len(candidates) if d1_mature else 0,
                d1_ready_count=len(d1_ready),
                d1_missing_symbols=d1_missing,
                d3_mature=d3_mature,
                d3_expected_count=len(candidates) if d3_mature else 0,
                d3_ready_count=len(d3_ready),
                d3_missing_symbols=d3_missing,
                d5_mature=d5_mature,
                d5_expected_count=len(candidates) if d5_mature else 0,
                d5_ready_count=len(d5_ready),
                d5_missing_symbols=d5_missing,
                status=status,
            )
        )

    return _build_report(
        as_of_date=resolved_as_of,
        date_reports=date_reports,
        missing_cases=missing_cases,
    )


def _build_report(
    *,
    as_of_date: date,
    date_reports: list[OutcomeCompletenessDate],
    missing_cases: set[tuple[date, str]],
) -> OutcomeCompletenessReport:
    expected_d1 = sum(item.d1_expected_count for item in date_reports)
    ready_d1 = sum(item.d1_ready_count for item in date_reports)
    expected_d3 = sum(item.d3_expected_count for item in date_reports)
    ready_d3 = sum(item.d3_ready_count for item in date_reports)
    expected_d5 = sum(item.d5_expected_count for item in date_reports)
    ready_d5 = sum(item.d5_ready_count for item in date_reports)
    has_gap = bool(missing_cases)
    has_mature = any(
        item.d1_mature or item.d3_mature or item.d5_mature for item in date_reports
    )
    warnings: list[str] = []
    for label, ready, expected in (
        ("D+1", ready_d1, expected_d1),
        ("D+3", ready_d3, expected_d3),
        ("D+5", ready_d5, expected_d5),
    ):
        if ready < expected:
            warnings.append(
                f"Tracked Top10 {label} coverage is incomplete: {ready}/{expected} ready."
            )
    return OutcomeCompletenessReport(
        as_of_date=as_of_date,
        status="partial" if has_gap else "healthy" if has_mature else "pending",
        prediction_trade_date_count=len(date_reports),
        tracked_prediction_count=sum(item.candidate_count for item in date_reports),
        d1_expected_count=expected_d1,
        d1_ready_count=ready_d1,
        d3_expected_count=expected_d3,
        d3_ready_count=ready_d3,
        d5_expected_count=expected_d5,
        d5_ready_count=ready_d5,
        missing_case_count=len(missing_cases),
        dates=date_reports,
        warnings=warnings,
        generated_by=OUTCOME_COMPLETENESS_VERSION,
    )


def _empty_report(as_of_date: date | None, warning: str) -> OutcomeCompletenessReport:
    return OutcomeCompletenessReport(
        as_of_date=as_of_date,
        status="missing",
        prediction_trade_date_count=0,
        tracked_prediction_count=0,
        d1_expected_count=0,
        d1_ready_count=0,
        d3_expected_count=0,
        d3_ready_count=0,
        d5_expected_count=0,
        d5_ready_count=0,
        missing_case_count=0,
        warnings=[warning],
        generated_by=OUTCOME_COMPLETENESS_VERSION,
    )


def _missing_symbols(expected: list[str], ready: list[str]) -> list[str]:
    ready_set = set(ready)
    return [symbol for symbol in expected if symbol not in ready_set]
