"""Evaluation CLI; real LLM execution requires explicit run-offline --allow-llm."""

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
    events = commands.add_parser("prepare-local-event-candidates")
    events.add_argument("--database", required=True, type=Path)
    events.add_argument("--anchor", required=True, type=datetime.fromisoformat)
    events.add_argument("--book", required=True, type=Path)
    events.add_argument("--output-dir", required=True, type=Path)
    validate.add_argument("path", type=Path)
    candidate = commands.add_parser("prepare-summary-candidate")
    candidate.add_argument("capture", type=Path)
    candidate.add_argument("--output-dir", required=True, type=Path)
    check = commands.add_parser("check-response")
    check.add_argument("--case", required=True, type=Path)
    check.add_argument("--response", required=True, type=Path)
    check.add_argument("--profile", required=True, choices=["v1_close_review", "extended"])
    process = commands.add_parser("check-process")
    process.add_argument("--case", required=True, type=Path)
    process.add_argument("--response", required=True, type=Path)
    process.add_argument("--output", type=Path)
    fact = commands.add_parser("check-summary-facts")
    fact.add_argument("--case", required=True, type=Path)
    fact.add_argument("--world", required=True, type=Path)
    fact.add_argument("--response", required=True, type=Path)
    fact.add_argument("--extraction", type=Path)
    event_fact = commands.add_parser("check-event-facts")
    for name in ("case", "world", "response"):
        event_fact.add_argument("--" + name, required=True, type=Path)
    event_fact.add_argument("--extraction", type=Path)
    run = commands.add_parser("run-offline")
    for name in ("case", "world", "output-dir"):
        run.add_argument("--" + name, required=True, type=Path)
    run.add_argument("--allow-llm", action="store_true", required=True)
    run.add_argument("--wall-seconds", type=int, default=240)
    blueprint = commands.add_parser("blueprint-coverage")
    batch = commands.add_parser("prepare-core-batch")
    for name in ("database", "book", "output-dir"):
        batch.add_argument("--" + name, required=True, type=Path)
    live = commands.add_parser("run-live-historical")
    for name in ("database", "case", "baseline", "output-dir"):
        live.add_argument("--" + name, required=True, type=Path)
    live.add_argument("--allow-llm", action="store_true", required=True)
    live.add_argument("--wall-seconds", type=int, default=240)
    review = commands.add_parser("prepare-golden-review")
    review.add_argument("--run-dir",required=True,type=Path)
    review.add_argument("--output-dir",required=True,type=Path)
    review.add_argument("--allow-llm",action="store_true",required=True)
    accept = commands.add_parser("accept-highest")
    for name in ("review-dir","approval","output-dir"):
        accept.add_argument("--"+name,required=True,type=Path)
    accept.add_argument("--database",type=Path)
    accept.add_argument("--allow-llm",action="store_true",required=True)
    promote = commands.add_parser("promote-highest")
    for name in ("review-dir","approval","acceptance","output-dir"):
        promote.add_argument("--"+name,required=True,type=Path)
    blueprint.add_argument("--book", required=True, type=Path)
    blueprint.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    exit_code = 0
    if args.command == "promote-highest":
        from app.agent_eval.highest_acceptance import promote_highest
        result = promote_highest(args.review_dir,args.approval,args.acceptance,args.output_dir)
    elif args.command == "accept-highest":
        from app.agent_eval.highest_acceptance import accept_highest
        from app.config import configure_runtime_environment
        from app.services.llm_provider import get_llm_provider
        configure_runtime_environment()
        result = accept_highest(args.review_dir,args.approval,args.output_dir,get_llm_provider(),database=args.database)
    elif args.command == "prepare-golden-review":
        from app.agent_eval.golden_review import review_business_run
        from app.config import configure_runtime_environment
        from app.services.llm_provider import get_llm_provider
        configure_runtime_environment()
        result = review_business_run(args.run_dir,args.output_dir,get_llm_provider())
    elif args.command == "prepare-core-batch":
        from app.agent_eval.core_batch import prepare_core_batch
        result = prepare_core_batch(args.database, args.book, args.output_dir)
    elif args.command == "run-live-historical":
        from app.agent_eval.runner import run_offline
        result = run_offline(args.case, args.baseline, args.output_dir,
                            wall_seconds=args.wall_seconds, live_database=args.database)
        exit_code = {"pass":0,"fail":1,"needs_review":2,"unscorable":3}[result["verdict"]]
    elif args.command == "record-local-summary":
        result = record_local_summary(args.database, args.anchor, args.output)
    elif args.command == "prepare-local-event-candidates":
        from app.agent_eval.event_candidates import prepare_event_candidates
        result = prepare_event_candidates(args.database, args.anchor, args.book, args.output_dir)
    elif args.command == "validate-capture":
        artifact = load_capture(args.path)
        result = {"checksum_valid": True, "structure_digests_valid": True,
                  "privacy_status": artifact.body.privacy_status}
    elif args.command == "prepare-summary-candidate":
        result = save_summary_candidate(load_capture(args.capture), args.output_dir)
    elif args.command == "check-process":
        from app.agent_eval.process_checks import evaluate_process
        response = AgentChatResponse.model_validate_json(args.response.read_text(encoding="utf-8"))
        result = evaluate_process(load_case(args.case), response).model_dump(mode="json")
        if args.output:
            with args.output.open("x", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
        exit_code = {"pass": 0, "fail": 1, "needs_review": 2}[result["verdict"]]
    elif args.command == "check-response":
        response = AgentChatResponse.model_validate_json(args.response.read_text(encoding="utf-8"))
        result = evaluate_trajectory_terminal(load_case(args.case), response, profile=args.profile).model_dump(mode="json")
        exit_code = {"pass": 0, "fail": 1, "needs_review": 2}[result["verdict"]]
    elif args.command == "check-summary-facts":
        response = AgentChatResponse.model_validate_json(args.response.read_text(encoding="utf-8"))
        extraction = Extraction.model_validate_json(args.extraction.read_text(encoding="utf-8")) if args.extraction else None
        result = verify_summary_facts(load_case(args.case), load_world(args.world), response, extraction).model_dump(mode="json")
        exit_code = {"pass": 0, "fail": 1, "needs_review": 2}[result["verdict"]]
    elif args.command == "check-event-facts":
        from app.agent_eval.event_facts import ListExtraction, verify_event_facts
        response = AgentChatResponse.model_validate_json(args.response.read_text(encoding="utf-8"))
        extraction = ListExtraction.model_validate_json(args.extraction.read_text(encoding="utf-8")) if args.extraction else None
        result = verify_event_facts(load_case(args.case), load_world(args.world), response, extraction).model_dump(mode="json")
        exit_code = {"pass": 0, "fail": 1, "needs_review": 2}[result["verdict"]]
    elif args.command == "run-offline":
        from app.agent_eval.runner import run_offline
        result = run_offline(args.case, args.world, args.output_dir, wall_seconds=args.wall_seconds)
        exit_code = {"pass": 0, "fail": 1, "needs_review": 2, "unscorable": 3}[result["verdict"]]
    else:
        from app.agent_eval.blueprints import coverage, load_blueprints, save_blueprint_report
        book = load_blueprints(args.book)
        result = save_blueprint_report(book, args.output_dir) if args.output_dir else coverage(book)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
