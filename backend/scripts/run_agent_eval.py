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
    eval_failure_report,
    eval_suite_report,
    load_eval_cases,
    run_agent_eval_suite,
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
        "--mode",
        choices=("offline", "live-llm"),
        default="offline",
        help="offline uses deterministic fallback; live-llm calls the configured LLM provider.",
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

    fixture_path = BACKEND_ROOT / "tests" / "fixtures" / "agent_eval_cases.json"
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
    suite = run_agent_eval_suite(
        cases=load_eval_cases(fixture_path),
        events=SAMPLE_EVENTS,
        llm_provider=provider,
        check_intent=args.mode == "offline",
        trials_per_case=trials,
        minimum_pass_rate=args.min_pass_rate if args.mode == "live-llm" else 1.0,
        require_llm_planner=args.mode == "live-llm",
        force_template_answer=not args.live_answer,
    )
    report = {
        "mode": args.mode,
        "answer_mode": "live-llm" if args.live_answer else "deterministic-template",
        **eval_suite_report(suite),
    }
    printed_report = (
        {key: value for key, value in report.items() if key != "results"}
        if args.summary_only
        else report
    )
    print(json.dumps(printed_report, ensure_ascii=False, indent=2))

    if args.mode == "live-llm":
        failure_path = Path(args.failure_output)
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(
            json.dumps(
                {
                    "mode": args.mode,
                    "answer_mode": (
                        "live-llm" if args.live_answer else "deterministic-template"
                    ),
                    **eval_failure_report(suite),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    if args.mode == "offline" and not suite.ok:
        raise SystemExit(1)
    if args.mode == "live-llm" and args.fail_on_failures and not suite.ok:
        raise SystemExit(1)
    if args.mode == "live-llm" and args.fail_on_unstable and suite.unstable_cases:
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
