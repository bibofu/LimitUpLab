"""Versioned prediction time eligibility; no database or network side effects."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo


CN_TZ = ZoneInfo("Asia/Shanghai")
TIME_CONTRACT_VERSION = "prediction-time-v1"


@dataclass(frozen=True)
class TimeVerdict:
    cohort: str
    research_eligible: bool
    strict_forward_eligible: bool
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


def assess_prediction_time(prediction) -> TimeVerdict:
    """Legacy close batches remain research samples, never current finals."""
    if prediction.prediction_source == "historical_backtest":
        return TimeVerdict("historical_backtest", True, False)
    if "auction-final" in prediction.scoring_version:
        return TimeVerdict("retired_auction", False, False, ("retired_experiment",))
    created = prediction.created_at
    if created.tzinfo is None:
        return TimeVerdict("invalid_time", False, False, ("timezone_missing",))
    created = created.astimezone(CN_TZ)
    metadata = prediction.prediction_provenance
    if metadata:
        errors = provenance_errors(
            base_date=prediction.trade_date, data_as_of=prediction.data_as_of,
            created_at=created, provenance=metadata,
        )
        if errors:
            return TimeVerdict("invalid_time", False, False, tuple(errors))
        stage = metadata["stage"]
        return TimeVerdict(stage, True, stage == "premarket_final")
    if created.date() == prediction.trade_date and created.time() >= time(15):
        if prediction.data_as_of != prediction.trade_date:
            return TimeVerdict("invalid_time", False, False, ("legacy_data_date_mismatch",))
        reasons = ["legacy_input_timestamps_unverified"]
        if created.time() < time(15, 30):
            reasons.append("before_data_readiness_gate")
        return TimeVerdict("legacy_close", True, False, tuple(reasons))
    return TimeVerdict("invalid_time", False, False, ("legacy_final_time_unverified",))


def provenance_errors(*, base_date: date, data_as_of: date, created_at: datetime,
                      provenance: dict) -> list[str]:
    """Validate explicit stages and information cutoffs, including timezone."""
    if created_at.tzinfo is None:
        return ["timezone_missing"]
    created = created_at.astimezone(CN_TZ)
    if provenance.get("version") != TIME_CONTRACT_VERSION:
        return ["time_contract_version_missing"]
    stage = provenance.get("stage")
    if stage == "close_baseline":
        return [] if (
            created.date() == base_date and created.time() >= time(15, 30)
            and data_as_of == base_date
        ) else ["close_baseline_outside_same_day_window"]
    if stage != "premarket_final":
        return ["unknown_prediction_stage"]
    try:
        target = date.fromisoformat(provenance["target_trade_date"])
        cutoff = datetime.fromisoformat(provenance["information_cutoff_at"])
    except (KeyError, TypeError, ValueError):
        return ["final_time_metadata_missing"]
    if cutoff.tzinfo is None:
        return ["cutoff_timezone_missing"]
    cutoff = cutoff.astimezone(CN_TZ)
    errors = []
    if not (target > base_date and created.date() == target and data_as_of == target
            and time(9) <= created.time() < time(9, 30)):
        errors.append("final_outside_preopen_window")
    if not (datetime.combine(base_date, time(15), CN_TZ) <= cutoff <= created
            and cutoff < datetime.combine(target, time(9, 30), CN_TZ)):
        errors.append("information_cutoff_outside_window")
    if provenance.get("calendar_verified") is not True:
        errors.append("target_calendar_unverified")
    return errors


def close_provenance() -> dict:
    return {"version": TIME_CONTRACT_VERSION, "stage": "close_baseline"}


def time_cohort_counts(predictions) -> dict[str, int]:
    from collections import Counter
    return dict(Counter(assess_prediction_time(item).cohort for item in predictions))
