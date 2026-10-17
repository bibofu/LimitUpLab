"""Process durable deterministic quality checks without calling a model."""

import argparse
import json
import logging
import signal
import sys
import threading
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import configure_runtime_environment
from app.services.quality_detection import process_pending, retention_days, sample_rate


def main(argv=None):
    configure_runtime_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    if not 1 <= args.interval <= 60:
        parser.error("--interval must be between 1 and 60 seconds")
    if not 1 <= args.limit <= 50:
        parser.error("--limit must be between 1 and 50")
    sample_rate()
    retention_days()
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    while not stop.is_set():
        try:
            result = process_pending(limit=args.limit, stop_event=stop)
            if args.once or result["processed"]:
                print(json.dumps(result), flush=True)
        except Exception as error:
            logging.error("Quality worker batch failed (%s)", type(error).__name__)
            if args.once:
                return 1
        if args.once:
            return 0 if not result["failed"] else 1
        stop.wait(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
