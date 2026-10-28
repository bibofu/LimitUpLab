"""Aggregate review signals and process sampled answers into human cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import configure_runtime_environment, review_settings
from app.services.review_jobs import ReviewJobService
from app.services.runtime_coordination import LeaseStore


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
    lease = LeaseStore().claim("job:review-worker", ttl=300)
    if lease is None:
        print(json.dumps({"skipped": "already_running"}, ensure_ascii=False))
        return 0
    lease.start_heartbeat()
    service = ReviewJobService()
    total = {"sampled_runs": 0, "processed_jobs": 0, "aggregated_signals": 0}
    try:
        while not lease.lost.is_set():
            result = service.run_once(limit=args.limit)
            for key in total:
                total[key] += int(result.get(key) or 0)
            print(json.dumps({**result, "total": total}, ensure_ascii=False), flush=True)
            if args.once:
                return 0
            if not result["processed_jobs"] and not result["aggregated_signals"]:
                sleep(max(1.0, args.interval))
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        lease.release()


if __name__ == "__main__":
    raise SystemExit(main())
