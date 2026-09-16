"""Evaluation CLI; real LLM execution requires explicit run-offline --allow-llm."""

import argparse
from datetime import date, datetime
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
    promotion = commands.add_parser("record-local-promotion")
    promotion.add_argument("--database", required=True, type=Path)
    promotion.add_argument("--start-date", required=True, type=date.fromisoformat)
    promotion.add_argument("--anchor", required=True, type=datetime.fromisoformat)
    promotion.add_argument("--output", required=True, type=Path)
    promotion.add_argument("--days", type=int, default=5)
    coverage = commands.add_parser("preflight-tool-coverage")
    coverage.add_argument("--registry", type=Path)
    coverage.add_argument("--output", type=Path)
    dataset = commands.add_parser("build-dataset")
    for name in ("recipe", "database", "output-dir"):
        dataset.add_argument("--" + name, required=True, type=Path)
    dataset_run = commands.add_parser("run-dataset")
    for name in ("bundle", "database", "output-dir"):
        dataset_run.add_argument("--" + name, required=True, type=Path)
    dataset_run.add_argument("--allow-llm", required=True, action="store_true")
    dataset_run.add_argument("--case-ids", nargs="+")
    recheck = commands.add_parser("recheck-dataset")
    for name in ("bundle", "output-dir"):
        recheck.add_argument("--" + name, required=True, type=Path)
    recheck.add_argument("--runs", required=True, nargs="+", type=Path)
    carry = commands.add_parser("carry-business-approval")
    for name in ("source-bundle", "target-bundle", "approval", "output"):
        carry.add_argument("--" + name, required=True, type=Path)
    carry.add_argument("--case-id", required=True)
    preflight = commands.add_parser("preflight-batch")
    preflight.add_argument("--bundle", required=True, type=Path)
    preflight.add_argument("--approvals", required=True, nargs="+", type=Path)
    preflight.add_argument("--output-dir", required=True, type=Path)
    preflight.add_argument("--case-ids", nargs="+")
    count_accept = commands.add_parser("accept-count-batch")
    count_accept.add_argument("--bundle", required=True, type=Path)
    count_accept.add_argument("--approvals", required=True, nargs="+", type=Path)
    count_accept.add_argument("--preflight", required=True, type=Path)
    count_accept.add_argument("--database", required=True, type=Path)
    count_accept.add_argument("--output-dir", required=True, type=Path)
    count_accept.add_argument("--case-ids", required=True, nargs="+")
    count_accept.add_argument("--allow-llm", required=True, action="store_true")
    count_promote = commands.add_parser("promote-count-batch")
    count_promote.add_argument("--bundle", required=True, type=Path)
    count_promote.add_argument("--approvals", required=True, nargs="+", type=Path)
    count_promote.add_argument("--acceptance", required=True, type=Path)
    count_promote.add_argument("--output-dir", required=True, type=Path)
    selection_accept = commands.add_parser("accept-selection-batch")
    selection_accept.add_argument("--bundle", required=True, type=Path)
    selection_accept.add_argument("--approvals", required=True, nargs="+", type=Path)
    selection_accept.add_argument("--preflight", required=True, type=Path)
    selection_accept.add_argument("--database", required=True, type=Path)
    selection_accept.add_argument("--output-dir", required=True, type=Path)
    selection_accept.add_argument("--case-ids", required=True, nargs="+")
    selection_accept.add_argument("--allow-llm", required=True, action="store_true")
    selection_promote = commands.add_parser("promote-selection-batch")
    selection_promote.add_argument("--bundle", required=True, type=Path)
    selection_promote.add_argument("--approvals", required=True, nargs="+", type=Path)
    selection_promote.add_argument("--acceptance", required=True, type=Path)
    selection_promote.add_argument("--output-dir", required=True, type=Path)
    highest_accept = commands.add_parser("accept-highest-batch")
    highest_accept.add_argument("--bundle", required=True, type=Path)
    highest_accept.add_argument("--approvals", required=True, nargs="+", type=Path)
    highest_accept.add_argument("--preflight", required=True, type=Path)
    highest_accept.add_argument("--database", required=True, type=Path)
    highest_accept.add_argument("--output-dir", required=True, type=Path)
    highest_accept.add_argument("--case-ids", required=True, nargs="+")
    highest_accept.add_argument("--allow-llm", required=True, action="store_true")
    highest_promote = commands.add_parser("promote-highest-batch")
    highest_promote.add_argument("--bundle", required=True, type=Path)
    highest_promote.add_argument("--approvals", required=True, nargs="+", type=Path)
    highest_promote.add_argument("--acceptance", required=True, type=Path)
    highest_promote.add_argument("--output-dir", required=True, type=Path)
    semantic_accept = commands.add_parser("accept-semantic-batch")
    semantic_accept.add_argument("--bundle", required=True, type=Path)
    semantic_accept.add_argument("--approvals", required=True, nargs="+", type=Path)
    semantic_accept.add_argument("--preflight", required=True, type=Path)
    semantic_accept.add_argument("--output-dir", required=True, type=Path)
    semantic_accept.add_argument("--case-ids", required=True, nargs="+")
    semantic_accept.add_argument("--allow-llm", required=True, action="store_true")
    semantic_promote = commands.add_parser("promote-semantic-batch")
    semantic_promote.add_argument("--bundle", required=True, type=Path)
    semantic_promote.add_argument("--approvals", required=True, nargs="+", type=Path)
    semantic_promote.add_argument("--acceptance", required=True, type=Path)
    semantic_promote.add_argument("--output-dir", required=True, type=Path)
    full_answer_accept = commands.add_parser("accept-full-answer-judge")
    full_answer_accept.add_argument("--output-dir", required=True, type=Path)
    full_answer_accept.add_argument("--allow-llm", required=True, action="store_true")
    assemble = commands.add_parser("assemble-golden-suite")
    assemble.add_argument("--sources", required=True, nargs="+", type=Path)
    assemble.add_argument("--expected-bundles", required=True, nargs="+", type=Path)
    assemble.add_argument("--output-dir", required=True, type=Path)
    migrate = commands.add_parser("migrate-additive-world")
    for name in ("source", "target-bundle", "acceptance", "output-dir", "acceptance-output-dir"):
        migrate.add_argument("--" + name, required=True, type=Path)
    migrate.add_argument("--case-id", required=True)
    migrate.add_argument("--add-route", required=True, nargs="+", type=json.loads)
    formal = commands.add_parser("score-formal-run")
    formal.add_argument("--bundle", required=True, type=Path)
    formal.add_argument("--run-root", required=True, type=Path)
    formal.add_argument("--acceptances", required=True, nargs="+", type=Path)
    formal.add_argument("--output-dir", required=True, type=Path)
    formal.add_argument("--previous-report", type=Path)
    formal.add_argument("--allow-llm", required=True, action="store_true")
    verify_formal = commands.add_parser("verify-formal-manifest")
    verify_formal.add_argument("--manifest", required=True, type=Path)
    verify_formal.add_argument("--artifacts-only", action="store_true")
    stability = commands.add_parser("build-stability-panel")
    stability.add_argument("--formal-dirs", required=True, nargs="+", type=Path)
    stability.add_argument("--output-dir", required=True, type=Path)
    stability.add_argument("--required-runs", type=int, default=3)
    verify_stability = commands.add_parser("verify-stability-manifest")
    verify_stability.add_argument("--manifest", required=True, type=Path)
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
    expansion = commands.add_parser("prepare-expansion")
    for name in ("database","book","baseline","output-dir"):
        expansion.add_argument("--"+name,required=True,type=Path)
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
    accept_empty_parser = commands.add_parser("accept-empty")
    for name in ("bundle", "approval", "output-dir"):
        accept_empty_parser.add_argument("--" + name, required=True, type=Path)
    accept_empty_parser.add_argument("--allow-llm", action="store_true", required=True)
    promote_empty_parser = commands.add_parser("promote-empty")
    for name in ("bundle", "approval", "acceptance", "output-dir"):
        promote_empty_parser.add_argument("--" + name, required=True, type=Path)
    promote = commands.add_parser("promote-highest")
    for name in ("review-dir","approval","acceptance","output-dir"):
        promote.add_argument("--"+name,required=True,type=Path)
    blueprint.add_argument("--book", required=True, type=Path)
    blueprint.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    exit_code = 0
    if args.command == "record-local-promotion":
        from app.agent_eval.local_promotion import record_local_promotion
        result = record_local_promotion(args.database, args.start_date, args.anchor, args.output, days=args.days)
        exit_code = 0 if result["data_readiness"] == "available" else 2
    elif args.command == "preflight-tool-coverage":
        from app.agent_eval.tool_coverage import DEFAULT_REGISTRY, preflight_tool_coverage, write_coverage_report
        result = preflight_tool_coverage(args.registry or DEFAULT_REGISTRY)
        if args.output:
            write_coverage_report(args.output, result)
        exit_code = 0 if result["registration_valid"] else 2
    elif args.command == "build-dataset":
        from app.agent_eval.dataset import build_dataset
        result = build_dataset(args.recipe, args.database, args.output_dir)
    elif args.command == "run-dataset":
        from app.agent_eval.dataset import run_dataset
        result = run_dataset(args.bundle, args.database, args.output_dir, case_ids=args.case_ids)
        exit_code = 2 if any(r["verdict"] != "pass" for r in result["cases"]) else 0
    elif args.command == "recheck-dataset":
        from app.agent_eval.dataset import recheck_dataset
        result = recheck_dataset(args.bundle, args.runs, args.output_dir)
        exit_code = 2
    elif args.command == "carry-business-approval":
        from app.agent_eval.approval import carry_business_approval
        result = carry_business_approval(args.source_bundle, args.target_bundle, args.approval,
                                         args.case_id, args.output)
    elif args.command == "preflight-batch":
        from app.agent_eval.batch_preflight import preflight_batch
        result = preflight_batch(args.bundle, args.approvals, args.output_dir, case_ids=args.case_ids)
    elif args.command == "accept-count-batch":
        from app.agent_eval.structured_acceptance import accept_count_batch
        from app.config import configure_runtime_environment
        from app.services.llm_provider import get_llm_provider
        configure_runtime_environment()
        result = accept_count_batch(args.bundle, args.approvals, args.preflight, args.output_dir,
                                    get_llm_provider(), args.database, args.case_ids)
    elif args.command == "promote-count-batch":
        from app.agent_eval.structured_acceptance import promote_count_batch
        result = promote_count_batch(args.bundle, args.approvals, args.acceptance, args.output_dir)
    elif args.command == "accept-selection-batch":
        from app.agent_eval.structured_acceptance import accept_selection_batch
        from app.config import configure_runtime_environment
        from app.services.llm_provider import get_llm_provider
        configure_runtime_environment()
        result = accept_selection_batch(args.bundle, args.approvals, args.preflight, args.output_dir,
                                        get_llm_provider(), args.database, args.case_ids)
    elif args.command == "promote-selection-batch":
        from app.agent_eval.structured_acceptance import promote_selection_batch
        result = promote_selection_batch(args.bundle, args.approvals, args.acceptance, args.output_dir)
    elif args.command == "accept-highest-batch":
        from app.agent_eval.structured_acceptance import accept_highest_batch
        from app.config import configure_runtime_environment
        from app.services.llm_provider import get_llm_provider
        configure_runtime_environment()
        result = accept_highest_batch(args.bundle, args.approvals, args.preflight, args.output_dir,
                                      get_llm_provider(), args.database, args.case_ids)
    elif args.command == "promote-highest-batch":
        from app.agent_eval.structured_acceptance import promote_highest_batch
        result = promote_highest_batch(args.bundle, args.approvals, args.acceptance, args.output_dir)
    elif args.command == "accept-semantic-batch":
        from app.agent_eval.semantic_acceptance import accept_semantic_batch
        from app.config import configure_runtime_environment
        from app.services.llm_provider import get_llm_provider
        configure_runtime_environment()
        result = accept_semantic_batch(args.bundle, args.approvals, args.preflight, args.output_dir,
                                       get_llm_provider(), args.case_ids)
    elif args.command == "promote-semantic-batch":
        from app.agent_eval.semantic_acceptance import promote_semantic_batch
        result = promote_semantic_batch(args.bundle, args.approvals, args.acceptance, args.output_dir)
    elif args.command == "accept-full-answer-judge":
        from app.agent_eval.full_answer_judge import accept_full_answer_judge
        from app.config import configure_runtime_environment
        from app.services.llm_provider import get_llm_provider
        configure_runtime_environment()
        result = accept_full_answer_judge(args.output_dir, get_llm_provider())
    elif args.command == "assemble-golden-suite":
        from app.agent_eval.golden_suite import assemble_golden_suite
        result = assemble_golden_suite(args.sources, args.expected_bundles, args.output_dir)
    elif args.command == "migrate-additive-world":
        from app.agent_eval.golden_suite import migrate_additive_world
        result = migrate_additive_world(args.source, args.target_bundle, args.acceptance,
                                        args.case_id, args.output_dir, args.acceptance_output_dir,
                                        args.add_route)
    elif args.command == "score-formal-run":
        from app.agent_eval.formal_report import score_formal_run
        from app.config import configure_runtime_environment
        from app.services.llm_provider import get_llm_provider
        configure_runtime_environment()
        result = score_formal_run(args.bundle, args.run_root, args.acceptances, args.output_dir,
                                  get_llm_provider(), previous_report=args.previous_report)
    elif args.command == "verify-formal-manifest":
        from app.agent_eval.stability import verify_formal_manifest
        result = verify_formal_manifest(args.manifest, verify_inputs=not args.artifacts_only)
    elif args.command == "build-stability-panel":
        from app.agent_eval.stability import build_stability_panel
        result = build_stability_panel(args.formal_dirs, args.output_dir, required_runs=args.required_runs)
        exit_code = 0 if result["stability_eligible"] else 2
    elif args.command == "verify-stability-manifest":
        from app.agent_eval.stability import verify_stability_manifest
        result = verify_stability_manifest(args.manifest)
    elif args.command == "promote-highest":
        from app.agent_eval.highest_acceptance import promote_highest
        result = promote_highest(args.review_dir,args.approval,args.acceptance,args.output_dir)
    elif args.command == "accept-highest":
        from app.agent_eval.highest_acceptance import accept_highest
        from app.config import configure_runtime_environment
        from app.services.llm_provider import get_llm_provider
        configure_runtime_environment()
        result = accept_highest(args.review_dir,args.approval,args.output_dir,get_llm_provider(),database=args.database)
    elif args.command == "accept-empty":
        from app.agent_eval.empty_acceptance import accept_empty
        from app.config import configure_runtime_environment
        from app.services.llm_provider import get_llm_provider
        configure_runtime_environment()
        result = accept_empty(args.bundle, args.approval, args.output_dir, get_llm_provider())
    elif args.command == "promote-empty":
        from app.agent_eval.empty_acceptance import promote_empty
        result = promote_empty(args.bundle, args.approval, args.acceptance, args.output_dir)
    elif args.command == "prepare-golden-review":
        from app.agent_eval.golden_review import review_business_run
        from app.config import configure_runtime_environment
        from app.services.llm_provider import get_llm_provider
        configure_runtime_environment()
        result = review_business_run(args.run_dir,args.output_dir,get_llm_provider())
    elif args.command == "prepare-expansion":
        from app.agent_eval.core_batch import prepare_expansion
        result = prepare_expansion(args.database,args.book,args.baseline,args.output_dir)
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
