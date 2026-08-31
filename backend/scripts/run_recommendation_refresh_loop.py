"""Refresh recommendation quotes, news and financial reports every 30 minutes."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import configure_runtime_environment
from app.services.recommendation_intelligence import (
    DEFAULT_REFRESH_INTERVAL_MINUTES,
    refresh_recommendation_intelligence,
)


DEFAULT_LOCK_PATH = BACKEND_ROOT / "data" / "recommendation_refresh.lock"
DEFAULT_REPORT_PATH = BACKEND_ROOT / "data" / "recommendation_refresh_latest.json"


class RefreshLoopLock:
    """Prevent duplicate local or container refresh workers."""

    def __init__(self, path: Path, *, stale_after: timedelta):
        self.path = path
        self.stale_after = stale_after
        self._owned = False

    def __enter__(self) -> RefreshLoopLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            modified_at = datetime.fromtimestamp(
                self.path.stat().st_mtime,
                tz=timezone.utc,
            )
            if datetime.now(timezone.utc) - modified_at > self.stale_after:
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

    def touch(self) -> None:
        if self._owned:
            self.path.touch()

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        if self._owned:
            self.path.unlink(missing_ok=True)
            self._owned = False


def main() -> int:
    """Run one refresh or keep a single bounded worker alive."""

    configure_runtime_environment()
    parser = argparse.ArgumentParser(
        description="Refresh recommendation intelligence on a fixed interval.",
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

    with RefreshLoopLock(
        args.lock_path,
        stale_after=timedelta(minutes=interval * 3),
    ) as lock:
        while True:
            started = time.monotonic()
            try:
                _run_refresh(interval, args.report_path)
            except Exception as error:  # noqa: BLE001
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
            elapsed = time.monotonic() - started
            time.sleep(max(1, interval * 60 - elapsed))


def _run_refresh(interval: int, report_path: Path) -> None:
    response = refresh_recommendation_intelligence(interval_minutes=interval)
    payload = response.model_dump(mode="json")
    _write_report(report_path, payload)
    print(
        json.dumps(
            {
                "status": response.status,
                "refreshed_at": response.refreshed_at.isoformat(),
                "item_count": len(response.items),
                "warnings": response.warnings,
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
