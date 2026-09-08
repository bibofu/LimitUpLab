"""Run the trustworthy automated after-close prediction and review loop."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time as time_module
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.collectors import collect_a_share_trade_dates, parse_akshare_trade_date
from app.config import (
    configure_runtime_environment,
    detect_local_proxy,
    replace_proxy_environment,
)
from app.models import DailyPipelineRun, DailyReviewSnapshot
from app.repositories import (
    SQLiteDailyPipelineRepository,
    SQLiteFirstBoardRepository,
    SQLiteLimitUpRepository,
    SQLiteReviewSnapshotRepository,
)
from app.services.daily_review import build_daily_review_snapshot
from app.services.system_health import expected_local_data_date
from scripts.update_daily_data import DailyUpdateReport, run_daily_update


CN_TZ = ZoneInfo("Asia/Shanghai")
AFTER_CLOSE_TIME = time(15, 30)
DEFAULT_LOCK_PATH = BACKEND_ROOT / "data" / "daily_close_loop.lock"
DEFAULT_REPORT_PATH = BACKEND_ROOT / "data" / "daily_close_loop_latest.json"
DEFAULT_ALERT_PATH = BACKEND_ROOT / "data" / "daily_close_loop_alert.json"

CalendarCollector = Callable[[date, date], list[date]]
UpdateRunner = Callable[..., DailyUpdateReport]
SleepFunction = Callable[[float], None]
ReviewSnapshotBuilder = Callable[..., DailyReviewSnapshot]


class DailyCloseLoopAlreadyRunning(RuntimeError):
    """Raised when another daily close-loop process owns the lock."""


@dataclass(frozen=True)
class TargetTradeDate:
    """Resolved target date plus calendar diagnostics."""

    trade_date: date
    calendar_source: str
    warning: str | None = None
    skip_reason: str | None = None


@dataclass(frozen=True)
class DailyCloseLoopExecution:
    """CLI-friendly result for one close-loop invocation."""

    status: str
    exit_code: int
    run: DailyPipelineRun | None
    message: str


class DailyCloseLoopLock:
    """Cross-process file lock with bounded stale-lock recovery."""

    def __init__(self, path: Path, *, stale_after: timedelta = timedelta(hours=6)):
        self.path = path
        self.stale_after = stale_after
        self._owned = False

    def __enter__(self) -> DailyCloseLoopLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_lock()
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        )
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as error:
            raise DailyCloseLoopAlreadyRunning(
                f"Daily close loop is already running; lock={self.path}"
            ) from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        self._owned = True
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        if self._owned:
            self.path.unlink(missing_ok=True)
            self._owned = False

    def _remove_stale_lock(self) -> None:
        if not self.path.exists():
            return
        modified_at = datetime.fromtimestamp(
            self.path.stat().st_mtime,
            tz=timezone.utc,
        )
        if datetime.now(timezone.utc) - modified_at > self.stale_after:
            self.path.unlink(missing_ok=True)


def main() -> None:
    """Parse CLI options and execute one scheduled or manual close loop."""

    _prepare_network_environment()
    parser = argparse.ArgumentParser(
        description="Run the automated LimitUpLab after-close prediction loop.",
    )
    parser.add_argument("--date", help="Optional target trading date in YYYYMMDD format.")
    parser.add_argument(
        "--trigger",
        choices=("scheduled", "manual", "startup"),
        default="manual",
    )
    parser.add_argument("--force", action="store_true", help="Rerun even if already successful.")
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument("--skip-enrichment", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--retry-delay-seconds", type=float, default=20)
    parser.add_argument("--history-days", type=int, default=60)
    parser.add_argument("--top-targets", type=int, default=10)
    parser.add_argument("--max-tracked-kline-fetches", type=int, default=80)
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--alert-path", type=Path, default=DEFAULT_ALERT_PATH)
    args = parser.parse_args()

    execution = execute_daily_close_loop(
        requested_date=parse_akshare_trade_date(args.date) if args.date else None,
        trigger=args.trigger,
        force=args.force,
        skip_import=args.skip_import,
        refresh_enrichment=not args.skip_enrichment,
        max_attempts=args.max_attempts,
        retry_delay_seconds=args.retry_delay_seconds,
        history_days=args.history_days,
        top_targets=args.top_targets,
        max_tracked_kline_fetches=args.max_tracked_kline_fetches,
        lock_path=args.lock_path,
        report_path=args.report_path,
        alert_path=args.alert_path,
    )
    print(
        json.dumps(
            {
                "status": execution.status,
                "message": execution.message,
                "run": execution.run.model_dump(mode="json") if execution.run else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(execution.exit_code)


def execute_daily_close_loop(
    *,
    requested_date: date | None = None,
    trigger: str = "manual",
    force: bool = False,
    skip_import: bool = False,
    refresh_enrichment: bool = True,
    max_attempts: int = 3,
    retry_delay_seconds: float = 20,
    history_days: int = 60,
    top_targets: int = 10,
    max_tracked_kline_fetches: int = 80,
    now: datetime | None = None,
    lock_path: Path = DEFAULT_LOCK_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
    alert_path: Path = DEFAULT_ALERT_PATH,
    limit_up_repository: SQLiteLimitUpRepository | None = None,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    run_repository: SQLiteDailyPipelineRepository | None = None,
    calendar_collector: CalendarCollector = collect_a_share_trade_dates,
    update_runner: UpdateRunner = run_daily_update,
    review_snapshot_builder: ReviewSnapshotBuilder = build_daily_review_snapshot,
    sleep_fn: SleepFunction = time_module.sleep,
) -> DailyCloseLoopExecution:
    """Resolve, lock, retry and audit one complete after-close pipeline run."""

    current = now or datetime.now(CN_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CN_TZ)
    run_repo = run_repository or SQLiteDailyPipelineRepository()

    try:
        with DailyCloseLoopLock(lock_path):
            target = resolve_target_trade_date(
                now=current,
                requested_date=requested_date,
                force=force,
                calendar_collector=calendar_collector,
            )
            if target.skip_reason:
                return _persist_terminal_run(
                    repository=run_repo,
                    trade_date=target.trade_date,
                    trigger=trigger,
                    status="skipped",
                    attempt_count=0,
                    report={
                        "calendar_source": target.calendar_source,
                        "warning": target.warning,
                        "reason": target.skip_reason,
                    },
                    error_message=None,
                    report_path=report_path,
                    alert_path=alert_path,
                    message=target.skip_reason,
                )

            previous = run_repo.latest_for_date(target.trade_date)
            if previous and previous.status == "success" and not force:
                return DailyCloseLoopExecution(
                    status="skipped",
                    exit_code=0,
                    run=previous,
                    message=(
                        f"{target.trade_date.isoformat()} already completed successfully "
                        f"in run {previous.run_id}."
                    ),
                )

            started_at = datetime.now(timezone.utc)
            run = DailyPipelineRun(
                run_id=f"daily_{uuid4().hex}",
                trade_date=target.trade_date,
                trigger=trigger,
                status="running",
                attempt_count=0,
                report=None,
                started_at=started_at,
            )
            run_repo.save_run(run)

            limit_repo = limit_up_repository or SQLiteLimitUpRepository(seed_if_empty=False)
            first_repo = first_board_repository or SQLiteFirstBoardRepository()
            live_eligible = (
                target.trade_date == current.date()
                and current.time() >= AFTER_CLOSE_TIME
            )
            last_report: DailyUpdateReport | None = None
            last_error: str | None = None
            incomplete_reasons: list[str] = []
            attempts = max(1, max_attempts)

            for attempt in range(1, attempts + 1):
                run = run.model_copy(update={"attempt_count": attempt})
                run_repo.save_run(run)
                try:
                    last_report = update_runner(
                        trade_date=target.trade_date,
                        history_days=history_days,
                        top_targets=top_targets,
                        max_tracked_kline_fetches=max_tracked_kline_fetches,
                        skip_import=skip_import,
                        refresh_enrichment=refresh_enrichment,
                        replace_date=not skip_import,
                        persist_live_prediction=live_eligible,
                        limit_up_repository=limit_repo,
                        first_board_repository=first_repo,
                    )
                    if target.warning:
                        last_report.warnings.insert(0, target.warning)
                    incomplete_reasons = _incomplete_reasons(
                        last_report,
                        live_eligible=live_eligible,
                    )
                    last_error = None
                    if not incomplete_reasons:
                        break
                except Exception as error:  # noqa: BLE001
                    last_error = str(error)
                    incomplete_reasons = []

                if attempt < attempts:
                    sleep_fn(max(0, retry_delay_seconds) * (2 ** (attempt - 1)))

            report_payload = asdict(last_report) if last_report else None
            review_snapshot_payload: dict[str, str] | None = None
            if last_report is not None and last_error is None:
                try:
                    review_snapshot = review_snapshot_builder(
                        events=limit_repo.list_events(),
                        as_of_date=target.trade_date,
                        first_board_repository=first_repo,
                        snapshot_repository=SQLiteReviewSnapshotRepository(
                            first_repo.database_path
                        ),
                    )
                    review_snapshot_payload = {
                        "as_of_date": review_snapshot.as_of_date.isoformat(),
                        "start_date": review_snapshot.start_date.isoformat(),
                        "generated_by": review_snapshot.generated_by,
                    }
                except Exception as error:  # noqa: BLE001
                    incomplete_reasons.append(
                        f"daily review snapshot failed: {error}"
                    )
            if last_error:
                status = "error"
                message = f"Daily close loop failed after {run.attempt_count} attempts: {last_error}"
                exit_code = 1
            elif incomplete_reasons:
                status = "partial"
                last_error = "; ".join(incomplete_reasons)
                message = f"Daily close loop completed partially: {last_error}"
                exit_code = 2
            else:
                status = "success"
                message = (
                    f"Daily close loop completed for {target.trade_date.isoformat()} "
                    f"after {run.attempt_count} attempt(s)."
                )
                exit_code = 0

            final_report = {
                "calendar_source": target.calendar_source,
                "live_prediction_eligible": live_eligible,
                "pipeline": report_payload,
                "review_snapshot": review_snapshot_payload,
                "incomplete_reasons": incomplete_reasons,
            }
            finished_run = run.model_copy(
                update={
                    "status": status,
                    "report": final_report,
                    "error_message": last_error,
                    "finished_at": datetime.now(timezone.utc),
                }
            )
            run_repo.save_run(finished_run)
            _write_execution_files(
                run=finished_run,
                report_path=report_path,
                alert_path=alert_path,
            )
            return DailyCloseLoopExecution(
                status=status,
                exit_code=exit_code,
                run=finished_run,
                message=message,
            )
    except DailyCloseLoopAlreadyRunning as error:
        return DailyCloseLoopExecution(
            status="skipped",
            exit_code=0,
            run=None,
            message=str(error),
        )


def resolve_target_trade_date(
    *,
    now: datetime,
    requested_date: date | None,
    force: bool,
    calendar_collector: CalendarCollector = collect_a_share_trade_dates,
) -> TargetTradeDate:
    """Resolve the latest closed A-share trading date using a real calendar."""

    cutoff = now.date() if now.time() >= AFTER_CLOSE_TIME else now.date() - timedelta(days=1)
    if requested_date and requested_date > cutoff and not force:
        return TargetTradeDate(
            trade_date=requested_date,
            calendar_source="time-gate",
            skip_reason=(
                f"{requested_date.isoformat()} has not reached the after-close data window."
            ),
        )

    window_end = max(cutoff, requested_date or cutoff)
    window_start = window_end - timedelta(days=45)
    try:
        trade_dates = calendar_collector(window_start, window_end)
        if not trade_dates:
            raise RuntimeError("calendar result was empty")
        if requested_date:
            if requested_date not in trade_dates and not force:
                return TargetTradeDate(
                    trade_date=requested_date,
                    calendar_source="akshare.tool_trade_date_hist_sina",
                    skip_reason=f"{requested_date.isoformat()} is not an A-share trading day.",
                )
            return TargetTradeDate(
                trade_date=requested_date,
                calendar_source="akshare.tool_trade_date_hist_sina",
            )
        return TargetTradeDate(
            trade_date=max(item for item in trade_dates if item <= cutoff),
            calendar_source="akshare.tool_trade_date_hist_sina",
        )
    except Exception as error:  # noqa: BLE001
        fallback, _reason = expected_local_data_date(now)
        fallback_date = requested_date or fallback or cutoff
        return TargetTradeDate(
            trade_date=fallback_date,
            calendar_source="weekday-fallback",
            warning=f"Trading calendar unavailable; used weekday fallback: {error}",
        )


def _incomplete_reasons(
    report: DailyUpdateReport,
    *,
    live_eligible: bool,
) -> list[str]:
    reasons: list[str] = []
    health = report.health
    if not health.get("raw_events_ready"):
        reasons.append("raw limit-up events are missing")
    if health.get("raw_events_ready") and not health.get("first_board_features_ready"):
        reasons.append("first-board features are missing")
    live_snapshot_ready = (
        report.live_prediction_snapshot_ready
        or report.persisted_live_predictions > 0
    )
    if live_eligible and report.target_candidates_checked > 0 and not live_snapshot_ready:
        reasons.append("live Top10 prediction snapshot was not persisted")
    if report.tracked_cache_missing > 0:
        reasons.append(
            f"{report.tracked_cache_missing} tracked Top10 candidates still lack available bars"
        )
    if (
        report.tracked_next_day_outcomes_ready
        < report.tracked_next_day_outcomes_expected
    ):
        reasons.append(
            "tracked Top10 D+1 outcomes are incomplete "
            f"({report.tracked_next_day_outcomes_ready}/"
            f"{report.tracked_next_day_outcomes_expected})"
        )
    if (
        report.tracked_three_day_outcomes_ready
        < report.tracked_three_day_outcomes_expected
    ):
        reasons.append(
            "tracked Top10 D+3 outcomes are incomplete "
            f"({report.tracked_three_day_outcomes_ready}/"
            f"{report.tracked_three_day_outcomes_expected})"
        )
    if report.tracked_five_day_paths_ready < report.tracked_five_day_paths_expected:
        reasons.append(
            "tracked Top10 D+5 paths are incomplete "
            f"({report.tracked_five_day_paths_ready}/"
            f"{report.tracked_five_day_paths_expected})"
        )
    if health.get("status") == "missing" and not reasons:
        reasons.append("agent data health is missing")
    return reasons


def _persist_terminal_run(
    *,
    repository: SQLiteDailyPipelineRepository,
    trade_date: date,
    trigger: str,
    status: str,
    attempt_count: int,
    report: dict[str, object],
    error_message: str | None,
    report_path: Path,
    alert_path: Path,
    message: str,
) -> DailyCloseLoopExecution:
    now = datetime.now(timezone.utc)
    run = DailyPipelineRun(
        run_id=f"daily_{uuid4().hex}",
        trade_date=trade_date,
        trigger=trigger,
        status=status,
        attempt_count=attempt_count,
        report=report,
        error_message=error_message,
        started_at=now,
        finished_at=now,
    )
    repository.save_run(run)
    _write_execution_files(run=run, report_path=report_path, alert_path=alert_path)
    return DailyCloseLoopExecution(
        status=status,
        exit_code=0 if status == "skipped" else 1,
        run=run,
        message=message,
    )


def _write_execution_files(
    *,
    run: DailyPipelineRun,
    report_path: Path,
    alert_path: Path,
) -> None:
    payload = run.model_dump(mode="json")
    _atomic_write_json(report_path, payload)
    if run.status in {"partial", "error"}:
        _atomic_write_json(alert_path, payload)
    else:
        alert_path.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _prepare_network_environment() -> None:
    """Replace inherited proxy state with an explicit or reachable proxy."""

    configure_runtime_environment()
    proxy = os.getenv("LIMITUPLAB_PROXY_URL", "").strip() or detect_local_proxy()
    replace_proxy_environment(proxy)


if __name__ == "__main__":
    main()
