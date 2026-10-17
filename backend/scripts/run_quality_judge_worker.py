"""Run explicitly queued administrator semantic reviews; never sample or backfill."""

import argparse
import json
import logging
from pathlib import Path
import signal
import sys
import threading

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import configure_runtime_environment
from app.services.quality_judge_worker import process_judge_jobs


def main(argv=None):
    configure_runtime_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)
    if not 1 <= args.interval <= 60 or not 1 <= args.limit <= 20:
        parser.error("interval must be 1..60 and limit must be 1..20")
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    while not stop.is_set():
        try:
            result = process_judge_jobs(limit=args.limit, stop_event=stop)
            if args.once or result["processed"]:
                print(json.dumps(result), flush=True)
        except Exception as error:
            logging.error("Judge worker batch failed (%s)", type(error).__name__)
            if args.once:
                return 1
        if args.once:
            return 0 if not result["failed"] else 1
        stop.wait(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
