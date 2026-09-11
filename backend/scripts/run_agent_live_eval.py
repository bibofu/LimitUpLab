"""CLI for the real-LLM Live Behavioral Eval suite."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.chat_live_eval_runner import load_live_eval_dataset, run_live_eval_suite, write_live_eval_report
from app.config import configure_runtime_environment
from app.services.llm_provider import DisabledLLMProvider, OpenAIChatCompletionsProvider, get_llm_provider


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LimitUpLab Live Behavioral Eval.")
    parser.add_argument("--category")
    parser.add_argument("--case-id")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--model")
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    configure_runtime_environment()
    if args.model:
        os.environ["LIMITUPLAB_LLM_MODEL"] = args.model
    provider = get_llm_provider()
    if isinstance(provider, DisabledLLMProvider):
        parser.error("Live Eval requires the project's configured LLM and API key")
    dataset = load_live_eval_dataset()
    cases = [
        case for case in dataset.cases
        if (not args.category or case.category == args.category)
        and (not args.case_id or case.case_id == args.case_id)
    ]
    if not cases:
        parser.error("no cases matched")
    judge_provider = _judge_provider() if args.judge else None
    report = run_live_eval_suite(
        cases, llm_provider=provider, judge_provider=judge_provider, trials=args.trials
    )
    path = write_live_eval_report(report, args.output) if args.output else write_live_eval_report(report)
    print(json.dumps({**report["metrics"], "report_path": str(path)}, ensure_ascii=False, indent=2))
    if report["metrics"]["task_success_rate"] != 1:
        raise SystemExit(1)


def _judge_provider() -> OpenAIChatCompletionsProvider:
    model = os.getenv("LIMITUPLAB_EVAL_JUDGE_MODEL", "").strip()
    key = os.getenv("LIMITUPLAB_EVAL_JUDGE_API_KEY", "").strip()
    if not model or not key:
        raise SystemExit("--judge requires LIMITUPLAB_EVAL_JUDGE_MODEL and LIMITUPLAB_EVAL_JUDGE_API_KEY")
    return OpenAIChatCompletionsProvider(
        api_key=key,
        model=model,
        base_url=os.getenv("LIMITUPLAB_EVAL_JUDGE_BASE_URL", "https://api.openai.com/v1"),
        thinking_enabled=False,
        native_function_calling_enabled=False,
    )


if __name__ == "__main__":
    main()
