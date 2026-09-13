"""Explicit local recording CLI. No LLM or production HTTP requests."""

import argparse
from datetime import datetime
import json
from pathlib import Path

from app.agent_eval.local_capture import record_local_summary
from app.agent_eval.recorder import load_capture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record-local-summary")
    record.add_argument("--database", required=True, type=Path)
    record.add_argument("--anchor", required=True, type=datetime.fromisoformat)
    record.add_argument("--output", required=True, type=Path)
    validate = commands.add_parser("validate-capture")
    validate.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "record-local-summary":
        result = record_local_summary(args.database, args.anchor, args.output)
    else:
        artifact = load_capture(args.path)
        result = {"checksum_valid": True, "structure_digests_valid": True,
                  "privacy_status": artifact.body.privacy_status}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
