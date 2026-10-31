"""Process committed conversation turns into unconfirmed memory candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import configure_runtime_environment
from app.services.llm_provider import get_llm_provider
from app.services.memory_extraction import MemoryExtractionWorker, auto_learn_mode
from app.services.worker_lifecycle import WorkerLifecycle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process one batch and exit")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--interval", type=float, default=60.0)
    args = parser.parse_args(argv)
    configure_runtime_environment()
    mode = auto_learn_mode()
    if mode == "off":
        print(json.dumps({"mode": mode, "processed": 0}, ensure_ascii=False))
        return 0
    with WorkerLifecycle("memory-extraction") as lifecycle:
        lease = lifecycle.claim()
        if lease is None:
            print(json.dumps({"mode": mode, "skipped": "already_running"}, ensure_ascii=False))
            return 0
        provider = get_llm_provider()
        worker = MemoryExtractionWorker(provider=provider)
        total = 0
        lifecycle.ready()
        while not lifecycle.stop_requested and not lease.lost.is_set():
            processed = worker.run_once(
                limit=args.limit, should_stop=lambda: lifecycle.stop_requested or lease.lost.is_set(),
            )
            total += processed
            print(json.dumps({"mode": mode, "processed": processed, "total": total},
                             ensure_ascii=False), flush=True)
            if args.once:
                return 0
            if processed == 0:
                lifecycle.wait(max(1.0, args.interval))
        return 0 if lifecycle.stop_requested else 1


if __name__ == "__main__":
    raise SystemExit(main())
