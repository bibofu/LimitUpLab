"""Build and persist the latest low-position discovery snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.first_board_discovery import refresh_first_board_discovery


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh the latest low-position research candidate pool.",
    )
    parser.add_argument("--recall-limit", type=int, default=60)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    response = refresh_first_board_discovery(
        recall_limit=args.recall_limit,
        top_k=args.top_k,
        max_workers=args.max_workers,
        force=args.force,
    )
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
