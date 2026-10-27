"""Owner-scoped operator review for held long-term memory candidates.

This is intentionally a backend tool, not a user-facing memory control. It
prints candidate evidence and requires explicit confirm/reject commands.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import configure_runtime_environment
from app.repositories.long_term_memory_repository import (
    LongTermMemoryRepository,
    MemoryConflict,
    MemoryNotFound,
)


def _item(candidate: dict) -> dict:
    return {
        "memory_id": candidate.get("memory_id"),
        "memory_type": candidate.get("memory_type"),
        "key": candidate.get("key"),
        "value": candidate.get("value"),
        "scope_type": candidate.get("scope_type"),
        "scope": candidate.get("scope"),
        "status": candidate.get("status"),
        "candidate_action": candidate.get("candidate_action"),
        "supersedes_memory_id": candidate.get("supersedes_memory_id"),
        "supersedes_revision": candidate.get("supersedes_revision"),
        "source_quote": candidate.get("source_quote"),
        "revision": candidate.get("revision"),
        "candidate_expires_at": candidate.get("candidate_expires_at"),
    }


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, help="owner id (visitor_...)")
    sub = parser.add_subparsers(dest="command")
    listing = sub.add_parser("list", help="list pending candidates")
    listing.add_argument("--limit", type=int, default=100)
    confirm = sub.add_parser("confirm", help="activate one candidate")
    confirm.add_argument("--id", required=True)
    confirm.add_argument("--operation-id")
    reject = sub.add_parser("reject", help="reject one candidate")
    reject.add_argument("--id", required=True)
    reject.add_argument("--revision", required=True, type=int)
    reject.add_argument("--operation-id")
    args = parser.parse_args(argv)
    command = args.command or "list"
    repository = LongTermMemoryRepository()
    try:
        if command == "list":
            items = repository.list_candidates(
                args.owner, limit=max(1, min(args.limit, 200)),
            )
            _print({"owner": args.owner, "count": len(items),
                    "items": [_item(item) for item in items]})
            return 0
        if command == "confirm":
            result = repository.confirm_candidate(
                args.owner, args.id,
                args.operation_id or f"operator-confirm-{args.id}",
            )
            _print({"owner": args.owner, "current": _item(result.current.model_dump(mode="json"))})
            return 0
        result = repository.reject_candidate(
            args.owner, args.id, args.revision,
            args.operation_id or f"operator-reject-{args.id}-{args.revision}",
        )
        _print({"owner": args.owner, "current": _item(result.current.model_dump(mode="json"))})
        return 0
    except (MemoryConflict, MemoryNotFound, ValueError) as error:
        print(json.dumps({"error": type(error).__name__, "message": str(error)},
                         ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    configure_runtime_environment()
    raise SystemExit(main())
