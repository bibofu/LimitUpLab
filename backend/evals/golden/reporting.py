"""Honest task/trial metrics and atomic, resumable local reports."""

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from evals.golden.dimensions import dimension_counts


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def record_trial(report, result):
    """One scored result per trial; retain replaced interrupted attempts separately."""
    key = (result["case_id"], result["trial"])
    for index, previous in enumerate(report["results"]):
        if (previous["case_id"], previous["trial"]) == key:
            if previous.get("completed", True):
                raise ValueError("Cannot replace a completed trial")
            report.setdefault("attempt_history", []).append(previous)
            report["results"][index] = result
            return
    report["results"].append(result)


def summarize(results, *, planned_trials, planned_cases=None, attempt_history=()):
    if len({(item["case_id"], item["trial"]) for item in results}) != len(results):
        raise ValueError("Duplicate trial results would distort the evaluation denominator")
    counts = Counter(item["verdict"] for item in results)
    by_case, by_category, by_family = defaultdict(list), defaultdict(list), defaultdict(list)
    for item in results:
        by_case[item["case_id"]].append(item)
        by_category[item["category"]].append(item)
        by_family[item.get("family", "unspecified")].append(item)
    def rate(items):
        return sum(item["verdict"] == "pass" for item in items) / len(items) if items else None
    complete_cases = [items for items in by_case.values() if len(items) == planned_trials
                      and all(item.get("completed", True) for item in items)]
    first_attempts = [item for item in results if item["trial"] == 1]
    durations = sorted(item["duration_seconds"] for item in results)
    attempts = [*attempt_history, *results]
    tokens = [turn.get("agent_usage", {}).get("total_tokens") for item in attempts for turn in item["turns"]]
    completed_trials = sum(item.get("completed", True) for item in results)
    return {
        "attempted_trials": len(results), "verdict_counts": dict(counts),
        "completed_trials": completed_trials, "recorded_attempts": len(attempts),
        "superseded_attempts": len(attempt_history),
        "planned_trials": planned_cases * planned_trials if planned_cases is not None else None,
        "execution_complete": completed_trials == planned_cases * planned_trials if planned_cases is not None else None,
        "first_attempt_pass_rate": rate(first_attempts), "trial_pass_rate": rate(results),
        "completed_cases": len(complete_cases),
        "fully_repeated_cases": len(complete_cases) if planned_trials > 1 else 0,
        "all_trials_passed_cases": sum(all(item["verdict"] == "pass" for item in items) for items in complete_cases),
        "by_category": {key: {"trials": len(items), "pass_rate": rate(items)} for key, items in by_category.items()},
        "by_family": {key: {"trials": len(items), "pass_rate": rate(items),
            "verdict_counts": dict(Counter(item["verdict"] for item in items))} for key, items in by_family.items()},
        "by_dimension": dimension_counts(results),
        "p50_seconds": durations[(len(durations) - 1) // 2] if durations else None,
        "p95_seconds": durations[max(0, (len(durations) * 95 + 99) // 100 - 1)] if durations else None,
        "agent_tokens": sum(tokens) if tokens and all(value is not None for value in tokens) else None,
        "note": "Rates use one latest result per trial; interrupted attempts remain in attempt_history and usage. Pass includes uncalibrated model judgements. Review and harness errors never count as passes.",
    }


def save_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report["summary"] = summarize(report["results"], planned_trials=report["manifest"]["trials"],
                                  planned_cases=len(report["manifest"]["case_ids"]),
                                  attempt_history=report.get("attempt_history", []))
    target = directory / "report.json"
    temporary = directory / "report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(target)
    summary = report["summary"]
    lines = ["# Agent golden evaluation", "", f"Mode: `{report['mode']}`. Synthetic market fixtures only.", "",
             "Model-judged results have not been calibrated by a human reviewer. This is not real-market accuracy.", "",
             f"Attempted trials: {summary['attempted_trials']}/{summary['planned_trials']}; verdicts: `{summary['verdict_counts']}`.",
             f"Completed trials: {summary['completed_trials']}; preserved interrupted attempts: {summary['superseded_attempts']}.",
             f"First-attempt pass rate: {summary['first_attempt_pass_rate']}; all-attempt pass rate: {summary['trial_pass_rate']}.",
             f"Cases completing all {report['manifest']['trials']} planned trial(s): {summary['completed_cases']}; passing every planned trial: {summary['all_trials_passed_cases']}.",
             "A single trial does not establish repeatability.", "",
             "| Case | Trial | Verdict | Seconds | Failed or unresolved checks |", "|---|---:|---|---:|---|"]
    for result in report["results"]:
        failed = [check["name"] for turn in result["turns"] for check in turn.get("checks", []) if check["passed"] is not True]
        if result.get("error"):
            failed.append(result["error"])
        lines.append(f"| {result['case_id']} | {result['trial']} | {result['verdict']} | {result['duration_seconds']} | {', '.join(failed).replace('|', '/')} |")
    lines.extend(["", "## Check dimensions", "", "Each evaluated turn contributes once per dimension. Missing dimensions are not evaluated; fixture gaps are not passes. Facts here cover structured values, while prose remains in semantic judging.", "",
                  "| Dimension | Turns | Pass | Fail | Review | Harness error |",
                  "|---|---:|---:|---:|---:|---:|"])
    for name, counts in summary["by_dimension"].items():
        lines.append(f"| {name} | {counts['turns']} | {counts['pass']} | {counts['fail']} | {counts['review']} | {counts['harness_error']} |")
    lines.extend(["", "## Verification boundary", "", "The runner uses production context preparation, chat entry, tool gateway, evidence store and SQLite journal. It does not test HTTP admission, browser rendering or external market providers.", "", "See report.json for every turn, response, evidence, stream revision, oracle, judgement, usage and memory state. Memory families are task scenarios, not isolated measures of memory accuracy."])
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
