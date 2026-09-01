"""Finalize both recommendation strategies at 09:25:10 Asia/Shanghai."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import configure_runtime_environment
from app.repositories import SQLiteAuctionFinalRepository
from app.services.auction_final_recommendations import (
    AuctionFinalizationError,
    finalize_auction_recommendations,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
FINALIZE_AT = clock_time(9, 25, 10)
FINAL_RETRY_DEADLINE = clock_time(9, 27, 0)
DEFAULT_LOCK_PATH = BACKEND_ROOT / "data" / "auction_final.lock"
DEFAULT_REPORT_PATH = BACKEND_ROOT / "data" / "auction_final_latest.json"


class WorkerLock:
    """Prevent duplicate auction-final workers from racing one snapshot."""

    def __init__(self, path: Path):
        self.path = path
        self.owned = False

    def __enter__(self) -> "WorkerLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            age_seconds = time.time() - self.path.stat().st_mtime
            if age_seconds > 180:
                self.path.unlink(missing_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise RuntimeError(f"Auction-final worker is already running: {self.path}") from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        self.owned = True
        return self

    def touch(self) -> None:
        if self.owned:
            self.path.touch()

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        if self.owned:
            self.path.unlink(missing_ok=True)


def main() -> int:
    """Run once for diagnostics or continuously at the fixed auction boundary."""

    configure_runtime_environment()
    parser = argparse.ArgumentParser(
        description="Finalize pre-market candidates at 09:25:10 Asia/Shanghai.",
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--trade-date", type=date.fromisoformat)
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()

    if args.once:
        _run_once(args.report_path, args.trade_date)
        return 0

    with WorkerLock(args.lock_path) as lock:
        now = datetime.now(SHANGHAI_TZ)
        catch_up_date = _startup_catch_up_date(
            now,
            snapshot_exists=SQLiteAuctionFinalRepository().get(now.date()) is not None,
        )
        if catch_up_date is not None:
            try:
                _run_once(args.report_path, catch_up_date)
            except AuctionFinalizationError as error:
                _write_report(
                    args.report_path,
                    {
                        "status": "error",
                        "trade_date": catch_up_date.isoformat(),
                        "attempted_at": now.isoformat(),
                        "error": str(error),
                    },
                )
                print(f"Auction finalization catch-up failed: {error}", flush=True)
        while True:
            lock.touch()
            now = datetime.now(SHANGHAI_TZ)
            target = _next_target(now)
            time.sleep(max(0.1, min((target - now).total_seconds(), 60)))
            now = datetime.now(SHANGHAI_TZ)
            if now < target:
                continue
            if SQLiteAuctionFinalRepository().get(now.date()) is not None:
                time.sleep(60)
                continue
            try:
                _run_once(args.report_path, now.date())
            except AuctionFinalizationError as error:
                _write_report(
                    args.report_path,
                    {
                        "status": "waiting" if now.time() < FINAL_RETRY_DEADLINE else "error",
                        "trade_date": now.date().isoformat(),
                        "attempted_at": now.isoformat(),
                        "error": str(error),
                    },
                )
                print(f"Auction finalization deferred: {error}", flush=True)
                time.sleep(5 if now.time() < FINAL_RETRY_DEADLINE else 60)


def _startup_catch_up_date(
    now: datetime,
    *,
    snapshot_exists: bool,
) -> date | None:
    """Return today's session when a late-started worker missed 09:25."""

    if snapshot_exists or now.weekday() >= 5 or now.time() < FINALIZE_AT:
        return None
    return now.date()


def _next_target(now: datetime) -> datetime:
    """Return today's exact boundary or the next weekday boundary."""

    candidate = datetime.combine(now.date(), FINALIZE_AT, tzinfo=SHANGHAI_TZ)
    if now.weekday() < 5 and now <= candidate:
        return candidate
    if now.weekday() < 5 and now.time() <= FINAL_RETRY_DEADLINE:
        return now
    next_date = now.date() + timedelta(days=1)
    while next_date.weekday() >= 5:
        next_date += timedelta(days=1)
    return datetime.combine(next_date, FINALIZE_AT, tzinfo=SHANGHAI_TZ)


def _run_once(report_path: Path, trade_date: date | None) -> None:
    response = finalize_auction_recommendations(trade_date=trade_date)
    payload = response.model_dump(mode="json")
    _write_report(report_path, payload)
    print(
        json.dumps(
            {
                "status": response.status,
                "trade_date": response.trade_date.isoformat(),
                "finalized_at": response.finalized_at.isoformat(),
                "candidate_count": len(response.candidates),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


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
