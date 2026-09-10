"""Run the single versioned Agent Golden dataset."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.golden_dataset import GOLDEN_DATASET_PATH, load_golden_cases
from app.agents.golden_eval import golden_suite_report, run_golden_eval_suite
from app.config import detect_local_proxy, hydrate_windows_environment, replace_proxy_environment
from app.services.llm_provider import DisabledLLMProvider, get_llm_provider
from app.services.sample_data import SAMPLE_EVENTS


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Agent Golden eval cases.")
    parser.add_argument("--suite", choices=("golden",), default="golden")
    parser.add_argument("--mode", choices=("offline", "live-llm"), default="offline")
    parser.add_argument("--case-filter", help="Match a case ID or category.")
    parser.add_argument(
        "--failure-output",
        default=str(BACKEND_ROOT / "data" / "agent_eval_failures.json"),
        help="Write the four-layer failure report to this path.",
    )
    parser.add_argument(
        "--fail-on-failures", action="store_true",
        help="Fail on live-model case failures; offline failures always exit non-zero.",
    )
    parser.add_argument(
        "--live-answer", action="store_true",
        help="Use the real model for answers too; requires --mode live-llm.",
    )
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    if args.live_answer and args.mode != "live-llm":
        parser.error("--live-answer requires --mode live-llm")

    version, cases = load_golden_cases(GOLDEN_DATASET_PATH)
    if args.case_filter:
        cases = [case for case in cases if args.case_filter in case.case_id
                 or args.case_filter in case.category]
    if not cases:
        parser.error("--case-filter matched no golden cases")

    provider = None
    if args.mode == "live-llm":
        _prepare_live_llm_environment()
        provider = get_llm_provider()
        if isinstance(provider, DisabledLLMProvider):
            print(json.dumps({
                "mode": args.mode, "status": "configuration_error",
                "error": "live-llm requires an enabled LLM and API key; no model request was sent",
            }, ensure_ascii=False, indent=2))
            raise SystemExit(2)
    suite = run_golden_eval_suite(
        cases=cases, events=SAMPLE_EVENTS, dataset_version=version,
        llm_provider=provider, force_template_answer=not args.live_answer,
    )
    metadata = {
        "mode": args.mode, "suite": "golden",
        "answer_mode": "live-llm" if args.live_answer else "deterministic-template",
    }
    report = {**metadata, **golden_suite_report(suite)}
    printed = {key: value for key, value in report.items() if key != "results"} if args.summary_only else report
    print(json.dumps(printed, ensure_ascii=False, indent=2))
    failure_path = Path(args.failure_output)
    failure_path.parent.mkdir(parents=True, exist_ok=True)
    failure_path.write_text(json.dumps(
        {**metadata, **golden_suite_report(suite, failures_only=True)},
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")
    if not suite.ok and (args.mode == "offline" or args.fail_on_failures):
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
