"""Refresh and finalize recommendation intelligence at 08:00 Asia/Shanghai."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import configure_runtime_environment
from app.services.recommendation_intelligence import (
    DEFAULT_REFRESH_INTERVAL_MINUTES,
    MARKET_OPEN_TIME,
    PARTIAL_REFRESH_RETRY_DELAY,
    finalize_recommendation_intelligence,
    refresh_recommendation_intelligence,
    should_finalize_recommendation_intelligence,
)
from app.models import RecommendationIntelligenceResponse
from app.services.worker_lifecycle import WorkerLifecycle


DEFAULT_LOCK_PATH = BACKEND_ROOT / "data" / "recommendation_refresh.lock"
DEFAULT_REPORT_PATH = BACKEND_ROOT / "data" / "recommendation_refresh_latest.json"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
PREMARKET_REFRESH_TIME = datetime_time(8, 0)
WORKER_STALE_AFTER = timedelta(hours=30)
MAX_RECOVERY_ROUNDS = 3


class RefreshLoopLock:
    """Coordinate every refresh worker through a PostgreSQL lease."""

    def __init__(self, path: Path, *, stale_after: timedelta):
        # Keep historical CLI arguments accepted for installed task definitions.
        from app.storage.job_context import SharedJobLock
        self._shared = SharedJobLock("recommendation-refresh")

    def __enter__(self) -> RefreshLoopLock:
        self._shared.__enter__()
        return self

    def touch(self) -> None:
        self._shared.touch()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._shared.__exit__(exc_type, exc_value, traceback)


def main() -> int:
    """Run one refresh or keep a single bounded worker alive."""

    configure_runtime_environment()
    parser = argparse.ArgumentParser(
        description="Refresh recommendation intelligence at 08:00 Asia/Shanghai.",
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--interval-minutes",
        type=int,
        default=int(
            os.getenv(
                "LIMITUPLAB_RECOMMENDATION_REFRESH_MINUTES",
                str(DEFAULT_REFRESH_INTERVAL_MINUTES),
            )
        ),
    )
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()
    interval = max(5, min(args.interval_minutes, 1440))

    with WorkerLifecycle("recommendation-refresh") as lifecycle:
        with RefreshLoopLock(args.lock_path, stale_after=WORKER_STALE_AFTER) as lock:
            lifecycle.ready(lock._shared.lease)
            return _run_loop(args, interval, lock, lifecycle)


def _run_loop(args, interval, lock, lifecycle) -> int:
    if args.once:
        if not lifecycle.stop_requested:
            _run_refresh(interval, args.report_path)
        return 0

    # Reconcile persisted state before the first scheduled wait.
    next_wait = 0.0
    recovery_rounds = 0
    while not lifecycle.stop_requested:
        if next_wait and lifecycle.wait(next_wait):
            break
        lock.touch()
        result = _run_refresh_with_retries(interval, args.report_path, lifecycle=lifecycle)
        if isinstance(result, Exception):
            _write_report(
                args.report_path,
                {"status": "error", "refreshed_at": datetime.now(timezone.utc).isoformat(),
                 "error": str(result)},
            )
            print(f"Recommendation refresh failed: {result}", flush=True)
        recovery_rounds = recovery_rounds + 1 if _needs_recovery(result) else 0
        next_wait = _next_refresh_wait(result, recovery_rounds)
        if recovery_rounds > MAX_RECOVERY_ROUNDS:
            recovery_rounds = 0
    return 0


# Refresh candidate intelligence, finalize it when eligible and persist the iteration report.
def _run_refresh(interval: int, report_path: Path) -> RecommendationIntelligenceResponse:
    response = refresh_recommendation_intelligence(interval_minutes=interval)
    if should_finalize_recommendation_intelligence(response):
        response = finalize_recommendation_intelligence(response)
    payload = response.model_dump(mode="json")
    _write_report(report_path, payload)
    print(
        json.dumps(
            {
                "status": response.status,
                "refreshed_at": response.refreshed_at.isoformat(),
                "item_count": len(response.items),
                "stage": response.stage,
                "target_trade_date": (
                    response.target_trade_date.isoformat()
                    if response.target_trade_date
                    else None
                ),
                "warnings": response.warnings,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return response


def _run_refresh_with_retries(
    interval: int,
    report_path: Path,
    *,
    attempts: int = 3,
    lifecycle: WorkerLifecycle | None = None,
) -> RecommendationIntelligenceResponse | Exception | None:
    """Retry transient provider failures so the 08:00 finalization is not skipped."""

    for attempt in range(1, max(attempts, 1) + 1):
        if lifecycle is not None and lifecycle.stop_requested:
            return None
        try:
            return _run_refresh(interval, report_path)
        except Exception as error:  # noqa: BLE001
            if attempt >= max(attempts, 1) or (lifecycle is not None and lifecycle.stop_requested):
                return error
            if lifecycle is not None:
                lifecycle.wait(5)
            else:
                time.sleep(5)
    return None


def _needs_recovery(result, now: datetime | None = None) -> bool:
    if isinstance(result, Exception):
        return True
    if result is None or result.stage != "draft" or result.status != "partial":
        return False
    current = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    return result.target_trade_date is None or current < datetime.combine(
        result.target_trade_date, MARKET_OPEN_TIME, tzinfo=SHANGHAI_TZ,
    )


def _next_refresh_wait(result, recovery_rounds: int, now: datetime | None = None) -> float:
    """Bound recovery traffic while never delaying the regular 08:00 run."""
    scheduled = _seconds_until_next_premarket_refresh(now)
    if _needs_recovery(result, now) and 0 < recovery_rounds <= MAX_RECOVERY_ROUNDS:
        backoff = PARTIAL_REFRESH_RETRY_DELAY.total_seconds() * 2 ** (recovery_rounds - 1)
        return min(scheduled, backoff)
    return scheduled


def _seconds_until_next_premarket_refresh(now: datetime | None = None) -> float:
    """Return the delay until the next 08:00 Asia/Shanghai refresh."""

    current = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    scheduled = datetime.combine(
        current.date(),
        PREMARKET_REFRESH_TIME,
        tzinfo=SHANGHAI_TZ,
    )
    if current >= scheduled:
        scheduled += timedelta(days=1)
    return max(1.0, (scheduled - current).total_seconds())


# Persist the refresh result through a temporary file so readers see a complete report.
def _write_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
