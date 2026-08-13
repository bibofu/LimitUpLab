"""Run Agent chat regression suites."""

from __future__ import annotations

import argparse
import json
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
from app.services.llm_provider import get_llm_provider
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
    args = parser.parse_args()

    fixture_path = BACKEND_ROOT / "tests" / "fixtures" / "agent_eval_cases.json"
    provider = get_llm_provider() if args.mode == "live-llm" else None
    suite = run_agent_eval_suite(
        cases=load_eval_cases(fixture_path),
        events=SAMPLE_EVENTS,
        llm_provider=provider,
        check_intent=args.mode == "offline",
    )
    report = {"mode": args.mode, **eval_suite_report(suite)}
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.mode == "live-llm":
        failure_path = Path(args.failure_output)
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(
            json.dumps(
                {"mode": args.mode, **eval_failure_report(suite)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    if args.mode == "offline" and not suite.ok:
        raise SystemExit(1)
    if args.mode == "live-llm" and args.fail_on_failures and not suite.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
