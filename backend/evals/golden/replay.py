"""Judge-only replay of immutable recorded Agent turns; never a new Agent run."""

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from app.models import AgentChatResponse
from app.services.llm_provider import capture_llm_usage
from evals.golden.cases import load_cases
from evals.golden.comparison import _index
from evals.golden.contracts import Case, Check, Expectation, verdict
from evals.golden.grading import grade_turn, visible_drafts
from evals.golden.judge import DELIVERY_SYSTEM, JUDGE_VERSION, VISIBLE_SYSTEM, judge_turn
from evals.golden.manifest import (
    BACKEND, code_fingerprint, git_commit, library_versions, require_disabled_sdk_retries,
    run_identity, tree_fingerprint,
)
from evals.golden.protocol_metrics import protocol_metrics
from evals.golden.reporting import digest
from evals.golden.runner import Budget, BudgetedProvider, usage_payload, without_sdk_retries

REPLAY_VERSION = "golden-judge-replay-v1"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _fixed_input(turn):
    return {key: deepcopy(turn[key]) for key in (
        "user", "expected", "response", "events", "runtime_metadata",
    )} | {"drafts": visible_drafts(turn["events"])}


def _response(snapshot):
    response = AgentChatResponse.model_validate(deepcopy(snapshot))
    normalized = response.model_dump(mode="json")
    fields = ("answer", "tool_calls", "tool_results", "task_status", "stop_reason")
    _require(all(key in snapshot and digest(snapshot[key]) == digest(normalized[key]) for key in fields),
             "Response normalization changed fixed Judge/grading inputs")
    return response


def load_replay(source, *, selected_turns=None, cases=None):
    """Validate the entire source before selecting turns; no provider is initialized."""
    source = Path(source).resolve()
    raw = source.read_bytes()
    original = json.loads(raw)
    manifest = original["manifest"]
    ids, trials = manifest["case_ids"], manifest["trials"]
    _require(isinstance(ids, list) and ids and all(isinstance(key, str) and key for key in ids)
             and len(set(ids)) == len(ids), "Source case IDs must be nonempty and unique")
    _require(type(trials) is int and trials > 0, "Source trials must be a positive integer")
    indexed = _index(original)
    current = {case.id: case.model_dump(mode="json")
               for case in (load_cases() if cases is None else cases)}
    definitions, inputs = {}, []
    for (case_id, trial), result in indexed.items():
        definition = result["definition"]
        _require(Case.model_validate(definition).model_dump(mode="json") == definition,
                 "Invalid source case definition")
        _require(definition == current.get(case_id), "Case/oracle changed; judge replay requires fixed tasks")
        _require(case_id not in definitions or definitions[case_id] == definition, "Trial definitions differ")
        definitions[case_id] = deepcopy(definition)
        _require(result.get("planned_turns") == len(definition["turns"]), "Invalid planned source turns")
        trial_verdict = verdict([Check(name="turn", passed=True if t["verdict"] == "pass"
            else False if t["verdict"] == "fail" else None) for t in result["turns"]])
        _require(result["verdict"] == "harness_error" or result["verdict"] == trial_verdict,
                 "Source trial verdict disagrees with its turns")
        _require(not result.get("unsupported_tools") or result["verdict"] == "harness_error",
                 "Source fixture gaps must retain harness_error")
        for index, (turn, defined) in enumerate(zip(result["turns"], definition["turns"])):
            _require(turn["index"] == index and turn["user"] == defined["user"]
                     and turn["session"] == defined["session"] and turn["expected"] == defined["expect"],
                     "Stored turn differs from its case/oracle")
            response = _response(turn["response"])
            _require(response.session_id == f"golden-{case_id}-t{trial}-{defined['session']}",
                     "Source response session mismatch")
            _require(isinstance(turn["events"], list) and isinstance(turn["runtime_metadata"], list),
                     "Source events/runtime metadata missing")
            _require(turn["verdict"] == verdict([Check.model_validate(c) for c in turn["checks"]]),
                     "Source verdict disagrees with saved checks")
            fixed = _fixed_input(turn)
            inputs.append({"key": [case_id, trial, index], "input_sha256": digest(fixed),
                           "source_trial_verdict": result["verdict"],
                           "source_trial_turns": len(definition["turns"]),
                           "source_turn": deepcopy(turn)})
    _require(inputs, "Source contains no replayable turns")
    _require(digest([definitions[key] for key in manifest["case_ids"]]) == manifest["dataset_hash"],
             "Source dataset hash mismatch")
    if selected_turns is not None:
        selected = [tuple(key) for key in selected_turns]
        available = {tuple(item["key"]) for item in inputs}
        _require(selected and len(set(selected)) == len(selected) and set(selected) <= available,
                 "Selected turns must be unique existing case/trial/zero-based-turn keys")
        inputs = [item for item in inputs if tuple(item["key"]) in set(selected)]
    return {"source_path": str(source), "source_sha256": hashlib.sha256(raw).hexdigest(),
            "generation_manifest": deepcopy(manifest), "source_summary": deepcopy(original.get("summary")),
            "inputs": inputs, "fixed_inputs_sha256": digest([
                {"key": item["key"], "input_sha256": item["input_sha256"]} for item in inputs])}


def summarize_replay(report):
    completed = [item for item in report["results"] if item["completed"]]
    usage = [item["judge_usage"] for item in report["results"] if item.get("judge_usage")]
    totals = {key: sum(item.get(key) or 0 for item in usage) for key in (
        "call_count", "failed_call_count", "measured_call_count", "prompt_tokens", "completion_tokens",
    )}
    complete_usage = (bool(usage) and all(item.get("token_usage_complete") for item in usage)
                      and totals["call_count"] == report["logical_calls_used"])
    totals.update(total_tokens=sum(item["total_tokens"] for item in usage) if complete_usage else None,
                  token_usage_complete=complete_usage)
    by_trial = defaultdict(list)
    for item in completed:
        by_trial[tuple(item["key"][:2])].append(item)
    trial_verdicts = []
    originals = {tuple(item["key"]): item for item in report["inputs"]}
    for items in by_trial.values():
        if len(items) == originals[tuple(items[0]["key"])]["source_trial_turns"]:
            trial_verdicts.append("harness_error" if any(i["verdict"] == "harness_error" for i in items)
                else verdict([Check(name="turn", passed=True if i["verdict"] == "pass"
                              else False if i["verdict"] == "fail" else None) for i in items]))
    return {"planned_turns": len(report["inputs"]), "completed_turns": len(completed),
            "execution_complete": bool(report.get("integrity_verified"))
                and len(completed) == len(report["inputs"]) and not report.get("stop_reason"),
            "turn_verdict_counts": dict(Counter(item["verdict"] for item in completed)),
            "fully_scored_source_trials": len(trial_verdicts),
            "trial_verdict_counts": dict(Counter(trial_verdicts)),
            "new_agent_calls": 0, "new_judge_usage": totals,
            "transitions": dict(Counter(f"{i['original_verdict']}->{i['verdict']}" for i in completed)),
            **protocol_metrics(report["results"], errors_key="judge_errors", diagnostics_key="judge_diagnostics")}


def _replace_report(temporary, target):
    # Our writer is closed. Windows readers without FILE_SHARE_DELETE can still
    # briefly deny replacement; keep the old report intact and retry only rename.
    for attempt in range(6):
        try:
            temporary.replace(target)
            return
        except PermissionError as error:
            if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 5:
                raise
            time.sleep(0.02 * 2 ** attempt)


def _save(report, output):
    report["summary"] = summarize_replay(report)
    temporary = output / "report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _replace_report(temporary, output / "report.json")


def replay_report(source, output, provider, *, max_calls=120, selected_turns=None, cases=None):
    """Run fresh judgements on fixed inputs; historical usage never enters this ledger."""
    output = Path(output).resolve()
    _require(not output.exists(), "Use a new output directory; replay never overwrites reports")
    _require(type(max_calls) is int and max_calls > 0, "Model-call budget must be positive")
    loaded = load_replay(source, selected_turns=selected_turns, cases=cases)
    provider = without_sdk_retries(provider)
    configuration = require_disabled_sdk_retries(provider)
    report = {"mode": "judge-replay", "version": REPLAY_VERSION, **run_identity(),
        "source": {key: value for key, value in loaded.items() if key != "inputs"},
        "inputs": loaded["inputs"], "judge_manifest": {
            "judge_version": JUDGE_VERSION, "git_commit": git_commit(), "code_hash": code_fingerprint(),
            "evaluator_hash": tree_fingerprint(BACKEND / "evals"),
            "current_production_hash": tree_fingerprint(BACKEND / "app"),
            "judge_prompt_hash": digest({"final": DELIVERY_SYSTEM, "visible": VISIBLE_SYSTEM}),
            "provider": type(provider).__name__, "model": getattr(provider, "model", type(provider).__name__),
            "effective_model_configuration": configuration, "libraries": library_versions()},
        "max_logical_calls": max_calls, "logical_calls_used": 0, "results": [], "integrity_verified": False,
        "note": "Judge-only replay, no new Agent execution or human labels. Source judgements are historical "
                "observations, not ground truth. Evaluator changes are not matched-setting Agent comparisons."}
    output.mkdir(parents=True, exist_ok=False)
    _save(report, output)
    def reserve(used):
        report["logical_calls_used"] = used
        _save(report, output)
    budget = Budget(max_calls, on_take=reserve)
    measured = BudgetedProvider(provider, budget)
    try:
        for source_item in report["inputs"]:
            if budget.used >= max_calls:
                report["stop_reason"] = "model_call_budget"
                break
            turn = deepcopy(source_item["source_turn"])
            item = {"key": source_item["key"], "input_sha256": source_item["input_sha256"],
                    "original_verdict": turn["verdict"], "completed": False, "verdict": "review",
                    "judge_errors": {}, "judge_diagnostics": {}, "checks": []}
            report["results"].append(item)
            tracker = None
            try:
                response = _response(turn["response"])
                with capture_llm_usage() as tracker:
                    review = judge_turn(measured, user=turn["user"],
                        expectations=turn["expected"]["semantic_checks"],
                        response=response.model_copy(deep=True),
                        drafts=visible_drafts(deepcopy(turn["events"])),
                        runtime_metadata=deepcopy(turn["runtime_metadata"]))
                item.update(judgements=review.judgements, judge_errors=review.errors,
                            judge_diagnostics=review.diagnostics)
                checks = grade_turn(Expectation.model_validate(turn["expected"]),
                    response, events=turn["events"],
                    judgements=review.judgements)
                for dimension, name in (("factual", "visible_factual_grounding"),
                        ("source", "visible_source_attribution"), ("safety", "visible_answer_safety")):
                    finding = getattr(review, dimension)
                    item[dimension + "_judgement"] = finding
                    checks.append(Check(name=name, passed=finding["passed"] if finding else None,
                                        detail=finding["reason"] if finding else "Independent review pending"))
                item.update(checks=[c.model_dump(mode="json") for c in checks], verdict=verdict(checks), completed=True)
                if source_item["source_trial_verdict"] == "harness_error":
                    item["verdict"] = "harness_error"
            except KeyboardInterrupt:
                report["stop_reason"] = "interrupted"
                raise
            except Exception as error:
                item["judge_errors"]["replay"] = type(error).__name__
                item["verdict"] = "harness_error"
                report["stop_reason"] = "replay_error"
            finally:
                item["judge_usage"] = usage_payload(tracker) if tracker else None
                if budget.denied or budget.persistence_diagnostics is not None:
                    item.update(verdict="harness_error", completed=False)
                    report["stop_reason"] = ("budget_persistence_error" if budget.persistence_diagnostics
                                             is not None else "model_call_budget")
                    item["budget_persistence_diagnostics"] = budget.persistence_diagnostics
                _save(report, output)
            if report.get("stop_reason"):
                break
    except KeyboardInterrupt:
        pass  # Partial results and already reserved calls remain visible; never resume implicitly.
    integrity_error = None
    try:
        if hashlib.sha256(Path(source).read_bytes()).hexdigest() != loaded["source_sha256"]:
            integrity_error = "source_changed"
        elif (not all(digest(_fixed_input(i["source_turn"])) == i["input_sha256"] for i in report["inputs"])
              or digest([{"key": i["key"], "input_sha256": i["input_sha256"]} for i in report["inputs"]])
              != loaded["fixed_inputs_sha256"]):
            integrity_error = "input_changed"
    except Exception as error:
        integrity_error = "integrity_error"
        report["integrity_error_type"] = type(error).__name__
    if integrity_error:
        if report.get("stop_reason"):
            report["previous_stop_reason"] = report["stop_reason"]
        report["stop_reason"] = integrity_error
    else:
        report["integrity_verified"] = True
    _save(report, output)
    return report
