"""Evaluate deterministic Agent runtime contracts in a disposable PostgreSQL schema."""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.runtime_evaluation import evaluate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, help="Optional new JSON file; existing files are never overwritten")
    args = parser.parse_args(argv)
    raw = os.getenv("LIMITUPLAB_EVALUATION_DATABASE_URL", "").strip()
    if not raw:
        parser.error("Set LIMITUPLAB_EVALUATION_DATABASE_URL to a dedicated PostgreSQL evaluation database")
    if args.output and args.output.exists():
        parser.error("Output already exists; choose a new filename")
    try:
        report = evaluate(raw, trials=args.trials)
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            with args.output.open("x", encoding="utf-8") as output:
                output.write(encoded + "\n")
        else:
            print(encoded)
        return 0 if all(row["contract_pass"] and row["delivery_success"] for row in report["samples"]) else 1
    except Exception as error:
        print(f"Runtime evaluation failed ({type(error).__name__}); no credentials or private data printed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
