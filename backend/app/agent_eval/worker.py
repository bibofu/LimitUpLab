"""One explicit real-model case in a disposable process; no production persistence."""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
from time import perf_counter
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi.encoders import jsonable_encoder
from langchain_core.messages import message_to_dict, messages_to_dict

from app.agent_eval.evaluators import evaluate_trajectory_terminal
from app.agent_eval.process_checks import evaluate_process
from app.agent_eval.extractor import SYSTEM as EXTRACTOR_SYSTEM, extract_answer
from app.agent_eval.facts import verify_summary_facts
from app.agent_eval.event_extractor import SYSTEM as EVENT_EXTRACTOR_SYSTEM, extract_event_answer
from app.agent_eval.event_facts import verify_event_facts
from app.agent_eval.business_facts import BusinessReport, verify_business_facts
from app.agent_eval.evaluators import finding
from app.agent_eval.event_extractor import BUSINESS_SYSTEM, extract_business_answer
from app.agent_eval.frozen_registry import FrozenAgentToolRegistry
from app.agent_eval.loader import load_suite, world_digest
from app.agent_eval.models import AssetRef, BudgetSpec, EvalResult, RunManifest
from app.agent_eval.recorder import digest
from app.agents.react_runtime import runtime
from app.agents.react_runtime.contracts import VERSION
from app.agents.react_runtime.evidence import EVIDENCE_VERSION
from app.agents.tools import TOOL_CONTRACT_VERSION
from app.config import configure_runtime_environment
from app.models import AgentChatRequest
from app.services.llm_provider import LLMProvider, capture_llm_usage, get_llm_provider, require_react_provider


def save(directory, name, value):
    with (directory / name).open("x", encoding="utf-8") as handle:
        json.dump(jsonable_encoder(value), handle, ensure_ascii=False, indent=2, allow_nan=False)


class GuardedProvider(LLMProvider):
    def __init__(self, provider, directory, deadline, budget):
        self.provider, self.directory, self.deadline, self.budget = provider, directory, deadline, budget
        self.calls = self.input_tokens = self.output_tokens = 0
        self.token_usage_complete = True
        self.budget_exhausted = False

    def generate_messages(self, messages, tools, *, timeout_seconds=30, max_tokens=4096):
        remaining = self.deadline - perf_counter()
        if (remaining <= 0 or self.calls >= self.budget.max_model_calls
                or self.input_tokens >= self.budget.max_input_tokens
                or self.output_tokens >= self.budget.max_output_tokens):
            self.budget_exhausted = True
            raise TimeoutError("evaluation execution budget exhausted")
        self.calls += 1
        name = f"call-{self.calls:02d}"
        save(self.directory, name + "-request.json", {"messages": messages_to_dict(messages), "tools": tools,
             "timeout_seconds": min(timeout_seconds, remaining), "max_tokens": max_tokens})
        try:
            result = self.provider.generate_messages(messages, tools,
                timeout_seconds=min(timeout_seconds, remaining),
                max_tokens=min(max_tokens, self.budget.max_output_tokens - self.output_tokens))
            save(self.directory, name + "-response.json", message_to_dict(result))
            usage = result.usage_metadata or {}
            if type(usage.get("input_tokens")) is int and type(usage.get("output_tokens")) is int:
                self.input_tokens += usage["input_tokens"]
                self.output_tokens += usage["output_tokens"]
            else:
                self.token_usage_complete = False
            return result
        except Exception as error:
            self.token_usage_complete = False
            save(self.directory, name + "-error.json", {"error_type": type(error).__name__})
            raise


def execute_case(case_path, world_path, directory, provider, *, wall_seconds=240, live_database=None, allow_judge=False):
    started = perf_counter()
    cases, worlds = load_suite([case_path], [world_path])
    case, world = cases[0], worlds[0]
    expected_mode = "live_historical" if live_database else "offline"
    if case.mode != expected_mode or len(case.conversation) != 1:
        raise ValueError("worker requires a single-turn case matching offline/live execution mode")
    if case.status not in {"candidate", "active"}:
        raise ValueError("case status is not runnable")
    require_react_provider(provider)
    if live_database:
        from app.agent_eval.historical_live import HistoricalLiveRegistry
        if "local_snapshot_live" in case.capabilities:
            from app.agent_eval.snapshot_live import SnapshotLiveRegistry
            registry = SnapshotLiveRegistry(live_database, world, directory / "live.sqlite")
        else:
            registry = HistoricalLiveRegistry(live_database, world)
        save(directory, "live-baseline-check.json", {"passed":True,
            "checked_recordings":registry.baseline_checks,"baseline_digest":world_digest(world),
            "tool_execution":"production methods over isolated local data",
            "ignored_drift_fields": ["post_limit_screen.generated_at", "post_limit_path.generated_at",
                                     "post_limit_statistics.generated_at"] if "local_snapshot_live" in case.capabilities else [],
            "scope": sorted(registry.enabled_tool_names) if "local_snapshot_live" in case.capabilities
                     else ["market_summary", "limit_up_events", "market_event_pool"]})
    else:
        registry = FrozenAgentToolRegistry(world)
    budget = BudgetSpec(max_agent_runs=1, max_model_calls=16, max_input_tokens=2000000,
                        max_output_tokens=100000, max_estimated_cost_usd=None,
                        max_wall_time_seconds=wall_seconds)
    request = AgentChatRequest(session_id=str(uuid4()), message_id=str(uuid4()),
                               message=case.conversation[0].content)
    event_case = any(a.target in {"answer.ordered_events", "answer.business_contract"}
                     for a in case.assertions if a.evaluator == "fact")
    extractor = extract_event_answer if event_case else extract_answer
    verifier = verify_event_facts if event_case else verify_summary_facts
    extractor_system = EVENT_EXTRACTOR_SYSTEM if event_case else EXTRACTOR_SYSTEM
    if any(a.target == "answer.business_contract" for a in case.assertions):
        extractor, verifier, extractor_system = extract_business_answer, verify_business_facts, BUSINESS_SYSTEM
    manifest = RunManifest(run_id=str(uuid4()), runtime_version=VERSION,
        tool_contract_version=TOOL_CONTRACT_VERSION, evidence_version=EVIDENCE_VERSION,
        evaluator_version="single-case-diagnostic-v1", model=getattr(provider, "model", "test-provider"),
        prompt_digest=digest({"agent": runtime.SYSTEM, "extractor": extractor_system}),
        cases=[AssetRef(id=case.case_id, version=case.case_version)],
        world_digests={} if live_database else {world.world_id: world_digest(world)}, budget=budget, worker_count=1)
    save(directory, "manifest.json", manifest.model_dump(mode="json"))
    save(directory, "request.json", request.model_dump(mode="json"))
    save(directory, "case.json", case.model_dump(mode="json"))
    save(directory, "baseline.json" if live_database else "world.json", world.model_dump(mode="json"))
    source_root = Path(__file__).resolve().parents[1]
    sources = {str(path.relative_to(source_root)): path.read_text(encoding="utf-8")
               for folder in [source_root / "agent_eval", source_root / "agents/react_runtime"]
               for path in sorted(folder.glob("*.py"))}
    client = getattr(getattr(provider, "chat_model", None), "root_client", None)
    provider_host = urlsplit(str(getattr(client, "base_url", ""))).hostname
    save(directory, "source.json", {"source_digest": digest(sources), "case_digest": digest(case.model_dump(mode="json")),
        "worker_pid": os.getpid(), "provider_host": provider_host,
        "extractor_prompt_digest": digest(extractor_system), "privacy_status": "unreviewed"})
    guarded = GuardedProvider(provider, directory, started + wall_seconds, budget)
    extraction_error = None
    review = None
    with capture_llm_usage() as usage:
        with registry.anchored():
            response = runtime.run(request, registry, guarded)
        save(directory, "response.json", response.model_dump(mode="json"))
        save(directory, "frozen-attempts.json", registry.attempts)
        trajectory = evaluate_trajectory_terminal(case, response, profile=world.profile)
        save(directory, "trajectory.json", trajectory.model_dump(mode="json"))
        process = evaluate_process(case, response)
        save(directory, "process.json", process.model_dump(mode="json"))
        extraction = None
        if any(a.target == "answer.tool_contract" for a in case.assertions):
            from app.agent_eval.trace_review import review_trace
            review = review_trace(case, response, provider=guarded if allow_judge else None)
            save(directory, "trace-review.json", review)
            facts = BusinessReport(case=manifest.cases[0], verdict=review["verdict"],
                findings=[finding("$tool_contract", "needs_review",
                    "See trace-review.json; semantic judgment is diagnostic, not Golden approval.")]
                    + [finding("$judge_" + key, value["verdict"], value.get("issue") or value.get("rationale", ""))
                       for key, value in review["dimensions"].items() if value["verdict"] != "not_run"])
        elif any(a.evaluator == "fact" for a in case.assertions):
            try:
                extraction = extractor(guarded, response.answer)
                save(directory, "extraction.json", extraction.model_dump(mode="json"))
            except Exception as error:
                extraction_error = type(error).__name__
                save(directory, "extraction-error.json", {"error_type": extraction_error})
            facts = verifier(case, world, response, extraction, diagnostic_unreviewed=True)
        else:
            facts = BusinessReport(case=manifest.cases[0], verdict="needs_review",
                findings=[finding("$answer_semantics", "needs_review", "semantic rubric requires review; numeric extraction not applicable")])
        save(directory, "facts.json", facts.model_dump(mode="json"))
    fact_ids = {a.id for a in case.assertions if a.evaluator == "fact"}
    trajectory_findings = [f for f in trajectory.findings if f.assertion_id not in fact_ids]
    findings = trajectory_findings + facts.findings
    trajectory_verdict = "fail" if any(f.verdict == "fail" for f in trajectory_findings) else (
        "needs_review" if any(f.verdict == "needs_review" for f in trajectory_findings) else "pass")
    cause = None
    if guarded.budget_exhausted:
        verdict, cause = "unscorable", "evaluator_failure"
    elif any(attempt["outcome"] == "rejected" for attempt in registry.attempts):
        verdict, cause = "unscorable", "fixture_failure"
    elif response.stop_reason in {"provider_error", "input_policy_error"}:
        verdict, cause = "unscorable", "provider_failure"
    elif any(f.verdict == "fail" for f in trajectory.findings):
        verdict, cause = "fail", "agent_failure"
    else:
        verdict = "needs_review"  # Uncalibrated extraction never establishes a release pass/fail.
        if extraction_error:
            cause = "evaluator_failure"
    result = EvalResult(case=manifest.cases[0], run_id=manifest.run_id, verdict=verdict, primary_cause=cause,
        findings=findings, model_calls=guarded.calls,
        total_tokens=usage.total_tokens if usage.token_usage_complete else None,
        elapsed_seconds=perf_counter() - started)
    save(directory, "result.json", result.model_dump(mode="json"))
    ledger = asdict(usage)
    ledger.update(token_usage_complete=usage.token_usage_complete, guarded_calls=guarded.calls,
                  estimated_cost_usd=None, pricing_status="not_configured")
    save(directory, "usage.json", ledger)
    summary = {"verdict": verdict, "primary_cause": cause, "release_eligible": False,
               "model": manifest.model, "model_calls": guarded.calls, "total_tokens": result.total_tokens,
               "trajectory": trajectory_verdict, "fact_diagnostic": facts.verdict,
               "process_diagnostic": process.verdict,
               "extraction_status": "uncalibrated", "case_status": case.status,
               "budget_exhausted": guarded.budget_exhausted,
               "trace_review": None if review is None else {"verdict": review["verdict"], "judge": review["judge"]},
               "agent_task_status": response.task_status, "elapsed_seconds": round(result.elapsed_seconds, 2)}
    save(directory, "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("case", "world", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--wall-seconds", type=int, default=240)
    parser.add_argument("--allow-llm", action="store_true", required=True)
    parser.add_argument("--live-database", type=Path)
    parser.add_argument("--allow-judge", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.wall_seconds <= 900:
        parser.error("wall-seconds must be between 1 and 900")
    try:
        configure_runtime_environment()
        # The child never initializes application repositories or the HTTP lease/journal path.
        original_connect = sqlite3.connect
        allowed_uri = args.live_database.resolve().as_uri() + "?mode=ro" if args.live_database else None
        live_copy = (args.output_dir / "live.sqlite").resolve()
        def forbid_database(*a, **k):
            if allowed_uri and a and a[0] == allowed_uri and k.get("uri") is True:
                return original_connect(*a, **k)
            if allowed_uri and a and (str(a[0]) == live_copy.as_uri() + "?mode=ro" and k.get("uri") is True
                    or not str(a[0]).startswith("file:") and Path(a[0]).resolve() == live_copy):
                return original_connect(*a, **k)
            raise RuntimeError("database access is forbidden in offline evaluation worker")
        sqlite3.connect = forbid_database
        from app.agents.react_runtime.lifecycle import CURRENT_CONTROL
        CURRENT_CONTROL.set(None)
        summary = execute_case(args.case, args.world, args.output_dir, get_llm_provider(),
                               wall_seconds=args.wall_seconds, live_database=args.live_database, allow_judge=args.allow_judge)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return {"pass": 0, "fail": 1, "needs_review": 2, "unscorable": 3}[summary["verdict"]]
    except Exception as error:
        cause = "provider_failure" if type(error).__name__ == "NativeFunctionCallingUnavailable" else "evaluator_failure"
        if type(error).__name__ == "HistoricalDataDrift":
            cause = "data_failure"
        save(args.output_dir, "worker-error.json", {"error_type": type(error).__name__, "verdict": "unscorable", "primary_cause": cause})
        print(json.dumps({"verdict": "unscorable", "error_type": type(error).__name__}), flush=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
