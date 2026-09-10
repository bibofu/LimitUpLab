"""Run and publish the seven-stage LimitUpLab Chat Eval V2."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.chat_eval_dataset import load_dataset_selection
from app.agents.chat_eval_gate import evaluate_release_gate
from app.agents.chat_eval_judge_calibration import evaluate_judge_calibration
from app.agents.chat_eval_runner_v2 import (
    EvalConfigurationError,
    run_chat_eval_suite,
    run_online_shadow_eval,
    write_completed_report,
)
from app.config import (
    detect_local_proxy,
    hydrate_windows_environment,
    replace_proxy_environment,
    env_bool,
)
from app.services.llm_provider import (
    DEFAULT_OPENAI_BASE_URL,
    DisabledLLMProvider,
    OpenAIChatCompletionsProvider,
    get_llm_provider,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Chat Eval V2.")
    parser.add_argument("--dataset", choices=("dev", "holdout", "all"), default="dev")
    parser.add_argument(
        "--mode", choices=("offline", "live", "online-shadow"), default="offline"
    )
    parser.add_argument("--trials", type=int)
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--sample-size", type=int)
    parser.add_argument("--seed", default="chat-eval-v2")
    parser.add_argument("--case-filter")
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "output" / "agent-eval",
    )
    args = parser.parse_args()

    trials = args.trials if args.trials is not None else (1 if args.mode == "offline" else 3)
    if trials < 1:
        parser.error("--trials must be positive")
    if args.mode == "online-shadow" and args.judge:
        parser.error("--judge is not supported for online-shadow")
    try:
        if args.mode == "online-shadow":
            if args.case_filter:
                parser.error("--case-filter is not supported for anonymized shadow runs")
            report = run_online_shadow_eval(
                sample_size=args.sample_size or 40,
                seed=args.seed,
            )
        else:
            cases = load_dataset_selection(args.dataset)
            if args.case_filter:
                needle = args.case_filter.lower()
                cases = [
                    case
                    for case in cases
                    if needle in case.case_id.lower()
                    or any(needle in tag.lower() for tag in case.tags)
                    or any(
                        needle in capability.lower()
                        for group in case.expected.allowed_capability_sets
                        for capability in group
                    )
                ]
            if not cases:
                parser.error("--case-filter matched no Chat Eval V2 cases")
            llm_provider = None
            if args.mode == "live":
                _prepare_live_llm_environment()
                llm_provider = get_llm_provider()
                if isinstance(llm_provider, DisabledLLMProvider):
                    raise EvalConfigurationError(
                        "live mode requires LIMITUPLAB_LLM_ENABLED and an API key"
                    )
            judge_provider = _judge_provider() if args.judge else None
            report = run_chat_eval_suite(
                cases,
                mode=args.mode,
                trials=trials,
                seed=args.seed,
                sample_size=args.sample_size,
                llm_provider=llm_provider,
                judge_provider=judge_provider,
            )
            if args.judge:
                report["judge_calibration"] = _load_judge_calibration()
        release_scope = (
            args.sample_size is None
            and args.case_filter is None
            and (
                (args.mode == "offline" and args.dataset == "dev")
                or (args.mode == "live" and args.dataset == "all")
            )
        )
        report["release_gate"] = evaluate_release_gate(
            report,
            scope_eligible=release_scope,
            mature=env_bool("LIMITUPLAB_EVAL_MATURE_GATE"),
            approved_baseline=_load_approved_baseline(),
        )
        report_path = write_completed_report(report, output_root=args.output_root)
    except (EvalConfigurationError, FileNotFoundError, ValueError) as error:
        print(
            json.dumps(
                {
                    "status": "configuration_error",
                    "mode": args.mode,
                    "error": str(error),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(2) from error

    printable = (
        {key: value for key, value in report.items() if key != "results"}
        if args.summary_only
        else report
    )
    printable["report_path"] = str(report_path)
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    if report["failed_cases"] or report["release_gate"].get("passed") is False:
        raise SystemExit(1)


def _prepare_live_llm_environment() -> None:
    """Make CLI model settings match the Windows development startup path."""

    hydrate_windows_environment(("DEEPSEEK_API_KEY", "OPENAI_API_KEY"))
    if os.getenv("DEEPSEEK_API_KEY", "").strip():
        os.environ.setdefault("LIMITUPLAB_LLM_ENABLED", "true")
        os.environ.setdefault("LIMITUPLAB_LLM_BASE_URL", "https://api.deepseek.com")
        os.environ.setdefault("LIMITUPLAB_LLM_MODEL", "deepseek-v4-flash")
    proxy = os.getenv("LIMITUPLAB_PROXY_URL", "").strip() or detect_local_proxy()
    replace_proxy_environment(proxy)


def _judge_provider() -> OpenAIChatCompletionsProvider:
    """Require an explicitly pinned, independently configurable Judge model."""

    hydrate_windows_environment(
        ("LIMITUPLAB_EVAL_JUDGE_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")
    )
    model = os.getenv("LIMITUPLAB_EVAL_JUDGE_MODEL", "").strip()
    api_key = (
        os.getenv("LIMITUPLAB_EVAL_JUDGE_API_KEY", "").strip()
        or os.getenv("DEEPSEEK_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    if not model or not api_key:
        raise EvalConfigurationError(
            "--judge requires LIMITUPLAB_EVAL_JUDGE_MODEL and a Judge/API key"
        )
    base_url = (
        os.getenv("LIMITUPLAB_EVAL_JUDGE_BASE_URL", "").strip()
        or os.getenv("LIMITUPLAB_LLM_BASE_URL", "").strip()
        or os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL).strip()
    )
    return OpenAIChatCompletionsProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        thinking_enabled=False,
        native_function_calling_enabled=False,
    )


def _load_approved_baseline() -> dict | None:
    raw_path = os.getenv("LIMITUPLAB_EVAL_BASELINE_PATH", "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise EvalConfigurationError(f"approved eval baseline not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("mode") != "live" or payload.get("status") != "completed":
        raise EvalConfigurationError("approved eval baseline must be a completed live report")
    return payload


def _load_judge_calibration() -> dict:
    raw_path = os.getenv("LIMITUPLAB_EVAL_JUDGE_CALIBRATION_PATH", "").strip()
    if not raw_path:
        return {"status": "missing", "passed": False}
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise EvalConfigurationError(f"Judge calibration not found: {path}")
    return evaluate_judge_calibration(path)


if __name__ == "__main__":
    main()
