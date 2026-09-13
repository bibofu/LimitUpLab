"""Explicit local recording CLI. No LLM or production HTTP requests."""

import argparse
from datetime import datetime
import json
from pathlib import Path

from app.agent_eval.local_capture import record_local_summary
from app.agent_eval.recorder import load_capture
from app.agent_eval.candidates import save_summary_candidate
from app.agent_eval.evaluators import evaluate_trajectory_terminal
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.facts import Extraction, verify_summary_facts
from app.models import AgentChatResponse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record-local-summary")
    record.add_argument("--database", required=True, type=Path)
    record.add_argument("--anchor", required=True, type=datetime.fromisoformat)
    record.add_argument("--output", required=True, type=Path)
    validate = commands.add_parser("validate-capture")
    validate.add_argument("path", type=Path)
    candidate = commands.add_parser("prepare-summary-candidate")
    candidate.add_argument("capture", type=Path)
    candidate.add_argument("--output-dir", required=True, type=Path)
    check = commands.add_parser("check-response")
    check.add_argument("--case", required=True, type=Path)
    check.add_argument("--response", required=True, type=Path)
    check.add_argument("--profile", required=True, choices=["v1_close_review", "extended"])
    fact = commands.add_parser("check-summary-facts")
    fact.add_argument("--case", required=True, type=Path)
    fact.add_argument("--world", required=True, type=Path)
    fact.add_argument("--response", required=True, type=Path)
    fact.add_argument("--extraction", type=Path)
    args = parser.parse_args()
    exit_code = 0
    if args.command == "record-local-summary":
        result = record_local_summary(args.database, args.anchor, args.output)
    elif args.command == "validate-capture":
        artifact = load_capture(args.path)
        result = {"checksum_valid": True, "structure_digests_valid": True,
                  "privacy_status": artifact.body.privacy_status}
    elif args.command == "prepare-summary-candidate":
        result = save_summary_candidate(load_capture(args.capture), args.output_dir)
    elif args.command == "check-response":
        response = AgentChatResponse.model_validate_json(args.response.read_text(encoding="utf-8"))
        result = evaluate_trajectory_terminal(load_case(args.case), response, profile=args.profile).model_dump(mode="json")
        exit_code = {"pass": 0, "fail": 1, "needs_review": 2}[result["verdict"]]
    else:
        response = AgentChatResponse.model_validate_json(args.response.read_text(encoding="utf-8"))
        extraction = Extraction.model_validate_json(args.extraction.read_text(encoding="utf-8")) if args.extraction else None
        result = verify_summary_facts(load_case(args.case), load_world(args.world), response, extraction).model_dump(mode="json")
        exit_code = {"pass": 0, "fail": 1, "needs_review": 2}[result["verdict"]]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
