"""Build a replayable analysis artifact from one completed Live Eval report."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = BACKEND_ROOT / "tests" / "fixtures" / "agent_chat_live_eval_v1.json"
CATEGORIES = ("simple", "multi_tool", "replan", "multi_turn", "recovery", "boundary", "stress")
ROOT_CAUSES = (
    "Planner / intent",
    "Tool selection",
    "Tool argument",
    "Observation dependency",
    "Multi-turn context",
    "Recovery",
    "Answer completeness",
    "Grounding",
    "Efficiency / over-calling",
    "Eval / environment issue",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a completed Plan-and-Execute Live Eval run.")
    parser.add_argument("report", type=Path)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = _read_json(args.report)
    dataset = _read_json(args.dataset)
    cases = {case["case_id"]: case for case in dataset["cases"]}
    trials = [_enrich_trial(item, cases[item["case_id"]]) for item in report["results"]]
    output = args.output or args.report.with_name(
        f"live_baseline_plan_execute_{report['run_id'].removeprefix('live-')}.json"
    )
    artifact = {
        "artifact_type": "plan-and-execute-live-baseline",
        "source_report": str(args.report.resolve()),
        "source_run_id": report["run_id"],
        "repository_commit": _git_commit(),
        "execution_environment": {
            "agent_architecture": report["agent_architecture"],
            "observation_driven_replan_supported": report[
                "observation_driven_replan_supported"
            ],
            "dataset_version": report["dataset_version"],
            "environment_id": report["environment_id"],
            "planner_answer_models": sorted(
                {
                    str(item["token_usage"]["model"])
                    for item in trials
                    if item["token_usage"].get("model")
                }
            ),
            "planner_providers": sorted(
                {
                    str(planner["provider"])
                    for item in trials
                    for planner in _planner_outputs(item)
                    if planner.get("provider")
                }
            ),
            "judge_enabled": report["judge_enabled"],
            "judge_model": next(
                (
                    item["judge_result"].get("model")
                    for item in trials
                    if item.get("judge_result")
                ),
                None,
            ),
            "tool_environment": report["tool_environment"],
        },
        "source_report_metrics": report["metrics"],
        "overall_metrics": _aggregate(trials),
        "category_metrics": {
            category: _aggregate([item for item in trials if item["category"] == category])
            for category in CATEGORIES
        },
        "planner_metrics_by_category": {
            category: _planner_metrics(
                [item for item in trials if item["category"] == category]
            )
            for category in CATEGORIES
        },
        "dependency_metrics": _dependency_metrics(trials),
        "multi_turn_metrics": _tagged_category_metrics(trials, "multi_turn"),
        "recovery_metrics": _tagged_category_metrics(trials, "recovery"),
        "efficiency_metrics": _efficiency_metrics(trials),
        "per_case": _case_metrics(trials),
        "multi_tool_analysis": _multi_tool_analysis(trials),
        "observation_dependent_cases": _observation_cases(trials),
        "failure_reasons": _failure_clusters(trials),
        "judge_dimension_metrics": _judge_metrics(trials),
        "per_trial": trials,
    }
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"artifact_path": str(output.resolve()), **artifact["overall_metrics"]}, indent=2))


def _enrich_trial(trial: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    return {
        **trial,
        "question": case["turns"][-1]["user"],
        "conversation_turns": case["turns"],
        "case_tags": case.get("tags", []),
        "expected_contract": case["expected"],
        "failure_stage_labels": sorted({_failure_stage(reason) for reason in trial["failure_reasons"]}),
    }


def _planner_outputs(trial: dict[str, Any]) -> list[dict[str, Any]]:
    output = trial.get("planner_output")
    if isinstance(output, dict):
        return [output]
    if isinstance(output, list):
        return [item for item in output if isinstance(item, dict)]
    return []


def _aggregate(trials: list[dict[str, Any]]) -> dict[str, Any]:
    if not trials:
        return {"case_count": 0, "trial_count": 0}
    grouped = _group_by_case(trials)
    tool_calls = [item["tool_call_count"] for item in trials]
    llm_calls = [item["llm_call_count"] for item in trials]
    tokens = [item["token_usage"].get("total_tokens", 0) for item in trials]
    latency = [item["latency_ms"] for item in trials]
    fact_total = sum(item["required_fact_assertions"] for item in trials)
    fact_passed = sum(item["required_fact_assertions_passed"] for item in trials)
    success_counts = [sum(item["passed"] for item in items) for items in grouped.values()]
    return {
        "case_count": len(grouped),
        "trial_count": len(trials),
        "passed_trials": sum(item["passed"] for item in trials),
        "task_success_rate": _rate(sum(item["passed"] for item in trials), len(trials)),
        "overall_task_success_rate": _rate(
            sum(item["passed"] for item in trials), len(trials)
        ),
        "stable_case_rate": _rate(
            sum(all(item["passed"] for item in items) for items in grouped.values()),
            len(grouped),
        ),
        "at_least_2_of_3_case_rate": _rate(sum(count >= 2 for count in success_counts), len(grouped)),
        "exactly_1_of_3_case_rate": _rate(sum(count == 1 for count in success_counts), len(grouped)),
        "zero_of_3_case_rate": _rate(sum(count == 0 for count in success_counts), len(grouped)),
        **_planner_metrics(trials),
        "required_fact_coverage": _rate(fact_passed, fact_total),
        "required_fact_assertions": fact_total,
        "unsupported_claim_rate": None,
        "unsupported_claim_rate_reason": "no sentence-level claim ledger",
        "provider_failure_rate": _rate(
            sum(bool(item["token_usage"].get("failed_call_count")) for item in trials),
            len(trials),
        ),
        "avg_tool_calls": round(mean(tool_calls), 2),
        "p95_tool_calls": _percentile(tool_calls, 0.95),
        "avg_llm_calls": round(mean(llm_calls), 2),
        "avg_tokens": round(mean(tokens), 2),
        "p95_tokens": _percentile(tokens, 0.95),
        "avg_latency_ms": round(mean(latency), 2),
        "p50_latency_ms": _percentile(latency, 0.50),
        "p95_latency_ms": _percentile(latency, 0.95),
        "planner_tokens": None,
        "answer_tokens": None,
        "judge_tokens": None,
    }


def _planner_metrics(trials: list[dict[str, Any]]) -> dict[str, Any]:
    capability_total = sum(item["required_capability_count"] for item in trials)
    tool_total = sum(item["required_tool_count"] for item in trials)
    passed_trials = [item for item in trials if item["passed"]]
    repaired_passes = sum(item["backend_repair_needed"] for item in passed_trials)
    return {
        "raw_capability_recall": _rate(
            sum(item["raw_capability_hits"] for item in trials), capability_total
        ),
        "raw_required_tool_recall": _rate(
            sum(item["raw_required_tool_hits"] for item in trials), tool_total
        ),
        "effective_required_tool_recall": _rate(
            sum(item["effective_required_tool_hits"] for item in trials), tool_total
        ),
        "backend_repair_rate": _rate(
            sum(item["backend_repair_needed"] for item in trials), len(trials)
        ),
        "passed_trials_with_backend_repair": repaired_passes,
        "backend_repair_share_of_successes": _rate(
            repaired_passes, len(passed_trials)
        ),
        "backend_repair_operations": sum(item["backend_repair_count"] for item in trials),
    }


def _case_metrics(trials: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for case_id, items in _group_by_case(trials).items():
        passed = sum(item["passed"] for item in items)
        metrics[case_id] = {
            "category": items[0]["category"],
            "passed_trials": passed,
            "trial_count": len(items),
            "trial_success_rate": _rate(passed, len(items)),
            "stable": passed == len(items),
            "failure_stages": sorted(
                {stage for item in items for stage in item["failure_stage_labels"]}
            ),
            "backend_repair_trials": sum(item["backend_repair_needed"] for item in items),
            "backend_repair_operations": sum(item["backend_repair_count"] for item in items),
        }
    return metrics


def _dependency_metrics(trials: list[dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    dependency_types = {
        "LIVE-REPLAN-001": "top_n_symbols_to_downstream_tool",
        "LIVE-REPLAN-002": "previous_candidate_set_to_downstream_tool",
        "LIVE-REPLAN-003": "sector_result_to_sector_ranking",
        "LIVE-REPLAN-005": "previous_candidate_set_to_downstream_tool",
        "LIVE-REPLAN-006": "intersection_result_to_rating",
        "LIVE-REPLAN-007": "previous_candidate_set_to_downstream_tool",
        "LIVE-REPLAN-008": "sector_result_to_sector_ranking",
        "LIVE-STRESS-001": "mixed_dynamic_dependencies",
        "LIVE-STRESS-002": "intersection_result_to_downstream_tool",
    }
    for item in trials:
        expected = item["expected_contract"]
        reasons = item["failure_reasons"]
        for dependency in expected.get("tool_arg_dependencies", []):
            prefix = (
                f"argument dependency failed: {dependency['source_tool']}."
                f"{dependency['source_path']} -> {dependency['target_tool']}."
                f"{dependency['target_arg']}"
            )
            failure = next((reason for reason in reasons if reason.startswith(prefix)), None)
            checks.append(
                {
                    "case_id": item["case_id"],
                    "trial": item["trial"],
                    "dependency_type": dependency_types.get(item["case_id"], "tool_argument"),
                    "passed": failure is None,
                    "failure_reason": _dependency_failure_reason(failure),
                }
            )
        for dependency in expected.get("multi_source_tool_arg_dependencies", []):
            prefix = (
                f"multi-source dependency failed: {dependency['operation']} -> "
                f"{dependency['target_tool']}.{dependency['target_arg']}"
            )
            failure = next((reason for reason in reasons if reason.startswith(prefix)), None)
            checks.append(
                {
                    "case_id": item["case_id"],
                    "trial": item["trial"],
                    "dependency_type": dependency_types.get(item["case_id"], "multi_source"),
                    "passed": failure is None,
                    "failure_reason": _dependency_failure_reason(failure),
                }
            )
    passed = sum(item["passed"] for item in checks)
    by_type: dict[str, Any] = {}
    for dependency_type in sorted({item["dependency_type"] for item in checks}):
        selected = [item for item in checks if item["dependency_type"] == dependency_type]
        selected_passed = sum(item["passed"] for item in selected)
        by_type[dependency_type] = {
            "passed": selected_passed,
            "total": len(selected),
            "success_rate": _rate(selected_passed, len(selected)),
        }
    return {
        "tool_arg_dependency_success_rate": _rate(passed, len(checks)),
        "passed": passed,
        "total": len(checks),
        "by_type": by_type,
        "multi_turn_entity_to_tool_args": None,
        "multi_turn_entity_to_tool_args_reason": (
            "current Live golden has no per-argument assertion for multi-turn cases"
        ),
        "checks": checks,
    }


def _dependency_failure_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    if "missing sources" in reason:
        return "source observation missing"
    if "wrong candidate" in reason:
        return "wrong candidate set"
    return "target arg missing or wrong"


def _tagged_category_metrics(
    trials: list[dict[str, Any]], category: str
) -> dict[str, Any]:
    selected = [item for item in trials if item["category"] == category]
    result: dict[str, Any] = {"overall": _aggregate(selected), "by_scenario": {}}
    for tag in sorted({tag for item in selected for tag in item["case_tags"]}):
        tagged = [item for item in selected if tag in item["case_tags"]]
        result["by_scenario"][tag] = _aggregate(tagged)
    return result


def _efficiency_metrics(trials: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate = _aggregate(trials)
    return {
        key: aggregate[key]
        for key in (
            "avg_tool_calls",
            "p95_tool_calls",
            "avg_llm_calls",
            "avg_tokens",
            "p95_tokens",
            "avg_latency_ms",
            "p50_latency_ms",
            "p95_latency_ms",
            "judge_tokens",
        )
    }


def _multi_tool_analysis(trials: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for case_id, items in _group_by_case(
        [item for item in trials if item["category"] == "multi_tool"]
    ).items():
        labels = sorted(
            {
                _multi_tool_failure_label(reason)
                for item in items
                for reason in item["failure_reasons"]
            }
        )
        output[case_id] = {
            "passed_trials": sum(item["passed"] for item in items),
            "trial_count": len(items),
            "failure_classifications": labels,
            "pre_replan_foundation_issue": bool(labels),
            "failure_reasons_by_trial": [item["failure_reasons"] for item in items],
            "tool_calls_by_trial": [item["tool_calls"] for item in items],
            "tool_trace_by_trial": [
                [
                    {
                        "name": trace["name"],
                        "input": trace["input"],
                        "status": trace["status"],
                        "result_state": (
                            trace.get("result", {}).get("status")
                            if trace.get("result")
                            else None
                        ),
                    }
                    for trace in item["tool_trace"]
                ]
                for item in items
            ],
        }
    return output


def _multi_tool_failure_label(reason: str) -> str:
    if reason.startswith("missing capabilities"):
        return "planner intent error"
    if reason.startswith("missing required tools"):
        return "missing tool"
    if "forbidden tool" in reason or "unexpected tool" in reason:
        return "extra / unnecessary tool"
    if reason.startswith("argument dependency"):
        return "tool argument error"
    if reason.startswith("multi-source dependency"):
        return "cross-tool composition error"
    if reason.startswith("answer missing required term"):
        return "answer completeness error"
    if reason.startswith("answer fact missing"):
        return "grounding error"
    return "eval / environment issue"


def _observation_cases(trials: list[dict[str, Any]]) -> dict[str, Any]:
    relevant = [item for item in trials if item["category"] in {"replan", "stress"}]
    output: dict[str, Any] = {}
    for case_id, items in _group_by_case(relevant).items():
        passed = sum(item["passed"] for item in items)
        if passed and any(item["replan_count"] > 0 for item in items if item["passed"]):
            execution_label = "true observation-dependent success"
        elif passed and all(item["backend_repair_needed"] for item in items if item["passed"]):
            execution_label = "deterministic-policy-success"
        elif passed:
            execution_label = "preplanned-success"
        else:
            execution_label = "failure"
        output[case_id] = {
            "trial_results": ["PASS" if item["passed"] else "FAIL" for item in items],
            "passed_trials": passed,
            "execution_label": execution_label,
            "replan_observed": any(item["replan_count"] > 0 for item in items),
            "failure_stages_by_trial": [item["failure_stage_labels"] for item in items],
            "tool_calls_by_trial": [item["tool_calls"] for item in items],
            "conditional_assertions_by_trial": [item["conditional_assertions"] for item in items],
        }
    return output


def _failure_clusters(trials: list[dict[str, Any]]) -> dict[str, Any]:
    clusters: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"failure_assertion_count": 0, "trials": set(), "affected_cases": set(), "examples": []}
    )
    for item in trials:
        for reason in item["failure_reasons"]:
            for name in _root_causes(reason, item):
                cluster = clusters[name]
                cluster["failure_assertion_count"] += 1
                cluster["trials"].add((item["case_id"], item["trial"]))
                cluster["affected_cases"].add(item["case_id"])
                if reason not in cluster["examples"] and len(cluster["examples"]) < 3:
                    cluster["examples"].append(reason)
    return {
        name: {
            "failed_trial_count": len(value["trials"]),
            "failure_assertion_count": value["failure_assertion_count"],
            "affected_cases": sorted(value["affected_cases"]),
            "representative_examples": value["examples"],
        }
        for name, value in sorted(
            ((name, clusters[name]) for name in ROOT_CAUSES),
            key=lambda pair: len(pair[1]["trials"]),
            reverse=True,
        )
    }


def _judge_metrics(trials: list[dict[str, Any]]) -> dict[str, Any] | None:
    judged = [item for item in trials if item.get("judge_result")]
    if not judged:
        return None
    scores: dict[str, list[int]] = defaultdict(list)
    for item in judged:
        for dimension, score in item["judge_result"]["scores"].items():
            scores[dimension].append(score)
    return {
        "pass_rate": _rate(sum(item["judge_result"]["passed"] for item in judged), len(judged)),
        "average_score_by_dimension": {
            dimension: round(mean(values), 4) for dimension, values in scores.items()
        },
    }


def _failure_stage(reason: str) -> str:
    if reason.startswith("missing capabilities"):
        return "initial planning failure"
    if reason.startswith("missing required tools"):
        return "missing follow-up tool"
    if reason.startswith("multi-source dependency"):
        return "dynamic candidate discovery failure"
    if reason.startswith("argument dependency"):
        return "tool argument dependency failure"
    if reason.startswith("conditional tools"):
        return "conditional branch failure"
    if "result state expected" in reason:
        return "tool failure handling"
    if reason.startswith("answer fact missing"):
        return "grounding/fact coverage failure"
    if reason.startswith("answer missing required term"):
        return "answer composition failure"
    if "budget exceeded" in reason:
        return "budget failure"
    if "Judge" in reason:
        return "judge-only failure"
    return "other evaluator failure"


def _root_causes(reason: str, trial: dict[str, Any]) -> list[str]:
    stage = _failure_stage(reason)
    if stage == "initial planning failure":
        return ["Planner / intent"]
    if stage in {"missing follow-up tool", "conditional branch failure"}:
        return ["Tool selection"]
    if stage in {"dynamic candidate discovery failure", "tool argument dependency failure"}:
        return ["Tool argument", "Observation dependency"]
    if stage == "tool failure handling":
        return ["Recovery", "Tool argument"]
    if stage == "grounding/fact coverage failure":
        return ["Grounding"]
    if stage == "answer composition failure":
        return ["Answer completeness"]
    if stage == "budget failure":
        return ["Efficiency / over-calling"]
    if trial["category"] == "multi_turn":
        return ["Multi-turn context"]
    return ["Eval / environment issue"]


def _group_by_case(trials: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in trials:
        grouped[item["case_id"]].append(item)
    return dict(grouped)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _percentile(values: list[int], quantile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * quantile)]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object in {path}")
    return payload


def _git_commit() -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=BACKEND_ROOT.parent,
        capture_output=True,
        check=False,
        text=True,
    )
    return completed.stdout.strip() or None


if __name__ == "__main__":
    main()
