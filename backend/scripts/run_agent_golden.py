"""Validate or run the frozen-world golden suite; live mode calls the configured LLM."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from evals.golden.cases import load_cases
from evals.golden.contracts import SUITE_VERSION
from evals.golden.reporting import digest, record_trial, save_report


def code_fingerprint():
    files = [*sorted((BACKEND / "app").rglob("*.py")), *sorted((BACKEND / "evals").rglob("*.py")),
             Path(__file__), BACKEND / "requirements.txt"]
    return digest({path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files})


def tree_fingerprint(directory):
    return digest({path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sorted(directory.rglob("*.py"))})


def provider_configuration(provider):
    """Hash effective settings; never serialize keys, raw endpoints or URL credentials."""
    model = getattr(provider, "chat_model", None)
    settings = {name: getattr(model, name, None) for name in (
        "openai_api_base", "request_timeout", "temperature", "max_tokens", "top_p",
        "frequency_penalty", "presence_penalty", "seed", "reasoning_effort",
        "extra_body", "model_kwargs", "max_retries", "use_responses_api",
    )}
    settings["provider_options"] = {name: getattr(provider, name, None) for name in (
        "base_url", "thinking_enabled", "timeout_seconds", "planner_max_tokens",
        "max_tokens", "answer_max_tokens", "max_attempts", "retry_delay_seconds",
        "native_function_calling_enabled",
    )}
    root = getattr(model, "root_client", None)
    settings.update(endpoint=str(getattr(root, "base_url", "")),
                    sdk_timeout=str(getattr(root, "timeout", "")),
                    sdk_retries=getattr(root, "max_retries", None),
                    planner_max_tokens=getattr(provider, "planner_max_tokens", None),
                    native_function_calling_enabled=getattr(provider, "native_function_calling_enabled", None))
    return {"settings_sha256": digest(settings), "sdk_retries": 0,
            "budget_unit": "logical provider calls; SDK retries disabled; reservations persisted before calls"}


def validate_cases(cases):
    ids = [case.id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate golden case IDs")
    families = {}
    for case in cases:
        if case.family in families and families[case.family] != case.split:
            raise ValueError(f"Family crosses dataset splits: {case.family}")
        families[case.family] = case.split
        for turn in case.turns:
            if turn.expect.rows is None and not turn.expect.semantic_checks:
                raise ValueError(f"No answer oracle: {case.id}")
    return {"suite": SUITE_VERSION, "cases": len(cases), "categories": dict(Counter(c.category for c in cases)),
            "splits": dict(Counter(c.split for c in cases)), "smoke_cases": sum(c.smoke for c in cases),
            "turns": sum(len(c.turns) for c in cases), "case_ids": ids,
            "scope": "Schema/oracle inventory validation only; no model calls or measured model accuracy."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("validate", "live"), default="validate")
    parser.add_argument("--case", action="append", default=[], help="Exact case ID; may be repeated")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--split", choices=("all", "development", "holdout"), default="all")
    parser.add_argument("--category", choices=("single", "multi", "robustness"))
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--max-model-calls", type=int, default=600, help="Shared bound for Agent, memory and judge calls")
    parser.add_argument("--judge", choices=("none", "model"), default="none",
                        help="model uses a separate call to the configured model; not human-calibrated")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resume", action="store_true", help="Skip completed trials; interrupted trials restart in a fresh DB")
    args = parser.parse_args(argv)
    if args.trials < 1 or args.max_model_calls < 1:
        parser.error("trials and max-model-calls must be positive")
    if args.resume and args.output is None:
        parser.error("resume requires --output")
    all_cases = load_cases()
    validate_cases(all_cases)
    unknown = set(args.case) - {case.id for case in all_cases}
    if unknown:
        parser.error("Unknown case IDs: " + ", ".join(sorted(unknown)))
    cases = [case for case in all_cases if (not args.case or case.id in args.case)
             and (not args.smoke or case.smoke) and (args.split == "all" or case.split == args.split)
             and (not args.category or case.category == args.category)]
    if not cases:
        parser.error("No cases match the selection")
    if args.mode == "validate":
        print(json.dumps(validate_cases(cases), ensure_ascii=False, indent=2))
        return 0

    from app.config import configure_runtime_environment
    from app.services.llm_provider import DisabledLLMProvider, get_llm_provider
    from evals.golden.runner import Budget, BudgetedProvider, run_case, without_sdk_retries

    configure_runtime_environment()
    provider = get_llm_provider()
    if isinstance(provider, DisabledLLMProvider):
        parser.error("No enabled model provider; configure backend/.env or process environment. No credentials are printed.")
    provider = without_sdk_retries(provider)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output = (args.output or ROOT / "output/golden" / run_id).resolve()
    try:
        commit = subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", "rev-parse", "HEAD"],
                                         cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    manifest = {"suite_version": SUITE_VERSION, "dataset_hash": digest([c.model_dump() for c in cases]),
        "code_hash": code_fingerprint(), "git_commit": commit, "trials": args.trials,
        "evaluator_hash": tree_fingerprint(BACKEND / "evals"),
        "production_hash": tree_fingerprint(BACKEND / "app"),
        "case_ids": [c.id for c in cases], "model": getattr(provider, "model", type(provider).__name__),
        "provider": type(provider).__name__, "judge": args.judge, "judge_human_calibrated": False,
        "effective_model_configuration": provider_configuration(provider),
        "profile": "extended", "react_deadline_seconds": os.getenv("LIMITUPLAB_REACT_DEADLINE_SECONDS", "120"),
        "libraries": {name: version(name) for name in ("langchain-core", "langchain-openai", "langgraph", "pydantic")}}
    report_path = output / "report.json"
    if args.resume:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        # A new unrelated commit may coexist with identical source/data. Actual hashes govern resumption.
        comparable = lambda value: {key: item for key, item in value.items() if key != "git_commit"}
        if comparable(report["manifest"]) != comparable(manifest):
            parser.error("Resume manifest changed (data, code, model, judge or trials). Use a new output directory.")
    else:
        if report_path.exists():
            parser.error("Output already contains a report; use --resume or another directory")
        report = {"mode": "live-model-frozen-tools", "created_at": datetime.now(timezone.utc).isoformat(),
                  "manifest": manifest, "results": [], "attempt_history": [], "model_calls_used": 0}
    def reserve_call(used):
        report["model_calls_used"] = used
        save_report(report, output)
    budget = Budget(args.max_model_calls, report.get("model_calls_used", 0), on_take=reserve_call)
    measured = BudgetedProvider(provider, budget)
    completed = {(result["case_id"], result["trial"]) for result in report["results"]
                 if result.get("completed", True)}
    save_report(report, output)
    print(f"Report: {output / 'report.md'}", flush=True)
    try:
        for trial in range(1, args.trials + 1):
            for case in cases:
                if (case.id, trial) in completed:
                    continue
                if budget.used >= budget.maximum:
                    report["stop_reason"] = "model_call_budget"
                    return 2
                print(f"[start] {case.id} trial={trial}", flush=True)
                result = run_case(case, trial=trial, directory=output, provider=measured,
                                  judge_provider=measured if args.judge == "model" else None)
                record_trial(report, result)
                report["model_calls_used"] = budget.used
                save_report(report, output)
                print(f"[{result['verdict']}] {case.id} trial={trial} {result['duration_seconds']}s calls={budget.used}", flush=True)
                if result.get("stop_reason") == "model_call_budget":
                    report["stop_reason"] = "model_call_budget"
                    return 2
        report.pop("stop_reason", None)
    except KeyboardInterrupt:
        report["stop_reason"] = "interrupted"
        return 2
    finally:
        report["model_calls_used"] = budget.used
        save_report(report, output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if all(result["verdict"] == "pass" for result in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
