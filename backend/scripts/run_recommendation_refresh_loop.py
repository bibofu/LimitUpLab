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
    finalize_recommendation_intelligence,
    refresh_recommendation_intelligence,
    should_finalize_recommendation_intelligence,
)


DEFAULT_LOCK_PATH = BACKEND_ROOT / "data" / "recommendation_refresh.lock"
DEFAULT_REPORT_PATH = BACKEND_ROOT / "data" / "recommendation_refresh_latest.json"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
PREMARKET_REFRESH_TIME = datetime_time(8, 0)
MARKET_OPEN_TIME = datetime_time(9, 30)
WORKER_STALE_AFTER = timedelta(hours=30)


class RefreshLoopLock:
    """Prevent duplicate local or container refresh workers."""

    # Initialize RefreshLoopLock with the supplied dependencies and per-instance state.
    def __init__(self, path: Path, *, stale_after: timedelta):
        self.path = path
        self.stale_after = stale_after
        self._owned = False

    # Acquire ownership of the process lock before allowing the guarded loop to run.
    def __enter__(self) -> RefreshLoopLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            lock_pid = _read_lock_pid(self.path)
            modified_at = datetime.fromtimestamp(self.path.stat().st_mtime, tz=timezone.utc)
            if (
                (lock_pid is not None and not _process_exists(lock_pid))
                or datetime.now(timezone.utc) - modified_at > self.stale_after
            ):
                self.path.unlink(missing_ok=True)
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as error:
            raise RuntimeError(
                f"Recommendation refresh loop is already running: {self.path}"
            ) from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            )
        self._owned = True
        return self

    # Refresh the modification time of the lock owned by this loop as a liveness signal.
    def touch(self) -> None:
        if self._owned:
            self.path.touch()

    # Remove the lock only if this instance acquired it, including when the guarded work raised an
    # exception.
    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        if self._owned:
            self.path.unlink(missing_ok=True)
            self._owned = False


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

    if args.once:
        _run_refresh(interval, args.report_path)
        return 0

    with RefreshLoopLock(args.lock_path, stale_after=WORKER_STALE_AFTER) as lock:
        run_immediately = _inside_premarket_catch_up_window()
        while True:
            if not run_immediately:
                time.sleep(_seconds_until_next_premarket_refresh())
            error = _run_refresh_with_retries(interval, args.report_path)
            if error is not None:
                _write_report(
                    args.report_path,
                    {
                        "status": "error",
                        "refreshed_at": datetime.now(timezone.utc).isoformat(),
                        "error": str(error),
                    },
                )
                print(f"Recommendation refresh failed: {error}", flush=True)
            lock.touch()
            run_immediately = False


# Refresh candidate intelligence, finalize it when eligible and persist the iteration report.
def _run_refresh(interval: int, report_path: Path) -> None:
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


def _run_refresh_with_retries(
    interval: int,
    report_path: Path,
    *,
    attempts: int = 3,
) -> Exception | None:
    """Retry transient provider failures so the 08:00 finalization is not skipped."""

    for attempt in range(1, max(attempts, 1) + 1):
        try:
            _run_refresh(interval, report_path)
            return None
        except Exception as error:  # noqa: BLE001
            if attempt >= max(attempts, 1):
                return error
            time.sleep(5)
    return None


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


def _inside_premarket_catch_up_window(now: datetime | None = None) -> bool:
    """Catch up after a worker restart, but never collect evidence after market open."""

    current = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    return PREMARKET_REFRESH_TIME <= current.time() < MARKET_OPEN_TIME


# Read the process identity stored in a refresh-loop lock; invalid lock content returns None.
# A None result represents the unavailable or inapplicable branch; callers must check it before
# using the value.
def _read_lock_pid(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(payload.get("pid"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


# Check whether the recorded process ID is still present before reclaiming its lock.
def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


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
