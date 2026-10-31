"""Aggregate review signals and process sampled answers into human cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import configure_runtime_environment, review_settings
from app.services.review_jobs import ReviewJobService
from app.services.worker_lifecycle import WorkerLifecycle


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process one batch and exit")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args(argv)
    configure_runtime_environment()
    settings = review_settings()
    if not settings.enabled:
        print(json.dumps({"enabled": False, "processed_jobs": 0}, ensure_ascii=False))
        return 0
    with WorkerLifecycle("review-worker") as lifecycle:
        lease = lifecycle.claim()
        if lease is None:
            print(json.dumps({"skipped": "already_running"}, ensure_ascii=False))
            return 0
        service = ReviewJobService()
        total = {"sampled_runs": 0, "processed_jobs": 0, "aggregated_signals": 0}
        lifecycle.ready()
        while not lifecycle.stop_requested and not lease.lost.is_set():
            result = service.run_once(
                limit=args.limit, should_stop=lambda: lifecycle.stop_requested or lease.lost.is_set(),
            )
            for key in total:
                total[key] += int(result.get(key) or 0)
            print(json.dumps({**result, "total": total}, ensure_ascii=False), flush=True)
            if args.once:
                return 0
            if not result["processed_jobs"] and not result["aggregated_signals"]:
                lifecycle.wait(max(1.0, args.interval))
        return 0 if lifecycle.stop_requested else 1


if __name__ == "__main__":
    raise SystemExit(main())
