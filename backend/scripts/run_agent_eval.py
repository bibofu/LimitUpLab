"""Run Agent chat regression suites."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.eval_runner import (
    conversation_eval_suite_report,
    eval_failure_report,
    eval_suite_report,
    load_conversation_eval_scenarios,
    load_eval_cases,
    planner_eval_suite_report,
    run_agent_conversation_planner_eval_suite,
    run_agent_eval_suite,
    run_agent_planner_eval_suite,
)
from app.agents.query_contract_eval import (
    load_query_contract_eval_cases,
    query_contract_eval_report,
    run_query_contract_eval_suite,
)
from app.config import (
    detect_local_proxy,
    hydrate_windows_environment,
    replace_proxy_environment,
)
from app.services.llm_provider import DisabledLLMProvider, get_llm_provider
from app.services.sample_data import SAMPLE_EVENTS


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Agent chat eval cases.")
    parser.add_argument(
        "--suite",
        choices=("core", "paraphrase", "conversation"),
        default="core",
        help="core runs stable regressions; paraphrase measures live semantic routing.",
    )
    parser.add_argument(
        "--mode",
        choices=("offline", "live-llm"),
        default="offline",
        help="offline uses deterministic fallback; live-llm calls the configured LLM provider.",
    )
    parser.add_argument(
        "--case-filter",
        help="Run only case or scenario IDs containing this text.",
    )
    parser.add_argument(
        "--failure-output",
        default=str(BACKEND_ROOT / "data" / "agent_eval_failures.json"),
        help="Where to write failed and backend-repaired live eval samples.",
    )
    parser.add_argument(
        "--fail-on-failures",
        action="store_true",
        help="Exit non-zero when live-llm cases fail. Offline mode always fails on regressions.",
    )
    parser.add_argument(
        "--trials",
        type=int,
        help="Trials per case. Defaults to 1 offline and 3 for live-llm.",
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=2 / 3,
        help="Minimum trial pass rate for a live case (default: two of three).",
    )
    parser.add_argument(
        "--live-answer",
        action="store_true",
        help="Also use the real LLM for final answers; default isolates Planner stability.",
    )
    parser.add_argument(
        "--fail-on-unstable",
        action="store_true",
        help="Exit non-zero when any case varies across repeated live trials.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print aggregate metrics only; the live failure artifact keeps case details.",
    )
    args = parser.parse_args()

    if args.suite in {"paraphrase", "conversation"} and args.mode != "live-llm":
        parser.error(f"--suite {args.suite} requires --mode live-llm")
    fixture_names = {
        "core": "agent_eval_cases.json",
        "paraphrase": "agent_paraphrase_eval_cases.json",
        "conversation": "agent_conversation_eval_scenarios.json",
    }
    fixture_name = fixture_names[args.suite]
    fixture_path = BACKEND_ROOT / "tests" / "fixtures" / fixture_name
    contract_fixture_path = (
        BACKEND_ROOT / "tests" / "fixtures" / "query_contract_v2_cases.json"
    )
    trials = args.trials if args.trials is not None else (3 if args.mode == "live-llm" else 1)
    if trials <= 0:
        parser.error("--trials must be greater than zero")
    if not 0 < args.min_pass_rate <= 1:
        parser.error("--min-pass-rate must be within (0, 1]")

    provider = None
    if args.mode == "live-llm":
        _prepare_live_llm_environment()
        provider = get_llm_provider()
        if isinstance(provider, DisabledLLMProvider):
            print(
                json.dumps(
                    {
                        "mode": args.mode,
                        "status": "configuration_error",
                        "error": (
                            "live-llm requires an enabled LLM and a configured API key; "
                            "no real model request was sent"
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            raise SystemExit(2)
    contract_suite = run_query_contract_eval_suite(
        load_query_contract_eval_cases(contract_fixture_path)
    )
    contract_report = query_contract_eval_report(contract_suite)
    if args.suite == "paraphrase":
        assert provider is not None
        planner_cases = load_eval_cases(fixture_path)
        if args.case_filter:
            planner_cases = [
                item for item in planner_cases if args.case_filter in item.case_id
            ]
        if not planner_cases:
            parser.error("--case-filter matched no paraphrase cases")
        planner_suite = run_agent_planner_eval_suite(
            cases=planner_cases,
            events=SAMPLE_EVENTS,
            llm_provider=provider,
            trials_per_case=trials,
            minimum_pass_rate=args.min_pass_rate,
        )
        report = {
            "mode": args.mode,
            "suite": args.suite,
            "answer_mode": "planner-only",
            **planner_eval_suite_report(planner_suite),
            "query_contract": contract_report,
        }
        suite_ok = planner_suite.ok
        unstable_cases = planner_suite.unstable_cases
    elif args.suite == "conversation":
        assert provider is not None
        conversation_scenarios = load_conversation_eval_scenarios(fixture_path)
        if args.case_filter:
            conversation_scenarios = [
                item
                for item in conversation_scenarios
                if args.case_filter in item.scenario_id
            ]
        if not conversation_scenarios:
            parser.error("--case-filter matched no conversation scenarios")
        conversation_suite = run_agent_conversation_planner_eval_suite(
            scenarios=conversation_scenarios,
            events=SAMPLE_EVENTS,
            llm_provider=provider,
            trials_per_scenario=trials,
            minimum_pass_rate=args.min_pass_rate,
        )
        report = {
            "mode": args.mode,
            "suite": args.suite,
            "answer_mode": "planner-only-with-history",
            **conversation_eval_suite_report(conversation_suite),
            "query_contract": contract_report,
        }
        suite_ok = conversation_suite.ok
        unstable_cases = conversation_suite.unstable_scenarios
    else:
        core_cases = load_eval_cases(fixture_path)
        if args.case_filter:
            core_cases = [
                item for item in core_cases if args.case_filter in item.case_id
            ]
        if not core_cases:
            parser.error("--case-filter matched no core cases")
        suite = run_agent_eval_suite(
            cases=core_cases,
            events=SAMPLE_EVENTS,
            llm_provider=provider,
            check_intent=args.mode == "offline",
            trials_per_case=trials,
            minimum_pass_rate=(
                args.min_pass_rate if args.mode == "live-llm" else 1.0
            ),
            require_llm_planner=args.mode == "live-llm",
            force_template_answer=not args.live_answer,
        )
        report = {
            "mode": args.mode,
            "suite": args.suite,
            "answer_mode": (
                "live-llm" if args.live_answer else "deterministic-template"
            ),
            **eval_suite_report(suite),
            "query_contract": contract_report,
        }
        suite_ok = suite.ok
        unstable_cases = suite.unstable_cases
    if args.summary_only:
        printed_report = {key: value for key, value in report.items() if key != "results"}
        printed_report["query_contract"] = {
            key: value
            for key, value in contract_report.items()
            if key != "results"
        }
    else:
        printed_report = report
    print(json.dumps(printed_report, ensure_ascii=False, indent=2))

    if args.mode == "live-llm":
        failure_path = Path(args.failure_output)
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_payload = (
            report
            if args.suite in {"paraphrase", "conversation"}
            else {
                "mode": args.mode,
                "answer_mode": (
                    "live-llm" if args.live_answer else "deterministic-template"
                ),
                **eval_failure_report(suite),
                "query_contract": contract_report,
            }
        )
        failure_path.write_text(
            json.dumps(failure_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if args.mode == "offline" and (not suite_ok or not contract_suite.ok):
        raise SystemExit(1)
    if args.mode == "live-llm" and args.fail_on_failures and not suite_ok:
        raise SystemExit(1)
    if args.mode == "live-llm" and args.fail_on_unstable and unstable_cases:
        raise SystemExit(1)


def _prepare_live_llm_environment() -> None:
    """Make CLI LLM settings match the Windows development startup path."""

    hydrate_windows_environment(("DEEPSEEK_API_KEY", "OPENAI_API_KEY"))
    if os.getenv("DEEPSEEK_API_KEY", "").strip():
        os.environ.setdefault("LIMITUPLAB_LLM_ENABLED", "true")
        os.environ.setdefault("LIMITUPLAB_LLM_BASE_URL", "https://api.deepseek.com")
        os.environ.setdefault("LIMITUPLAB_LLM_MODEL", "deepseek-v4-flash")
    proxy = os.getenv("LIMITUPLAB_PROXY_URL", "").strip() or detect_local_proxy()
    replace_proxy_environment(proxy)


if __name__ == "__main__":
    main()
