"""Honest task/trial metrics and atomic, resumable local reports."""

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def summarize(results, *, planned_trials):
    counts = Counter(item["verdict"] for item in results)
    by_case, by_category = defaultdict(list), defaultdict(list)
    for item in results:
        by_case[item["case_id"]].append(item)
        by_category[item["category"]].append(item)
    def rate(items):
        return sum(item["verdict"] == "pass" for item in items) / len(items) if items else None
    complete_cases = [items for items in by_case.values() if len(items) == planned_trials]
    first_attempts = [item for item in results if item["trial"] == 1]
    durations = sorted(item["duration_seconds"] for item in results)
    tokens = [turn.get("agent_usage", {}).get("total_tokens") for item in results for turn in item["turns"]]
    return {
        "attempted_trials": len(results), "verdict_counts": dict(counts),
        "first_attempt_pass_rate": rate(first_attempts), "trial_pass_rate": rate(results),
        "fully_repeated_cases": len(complete_cases),
        "all_trials_passed_cases": sum(all(item["verdict"] == "pass" for item in items) for items in complete_cases),
        "by_category": {key: {"trials": len(items), "pass_rate": rate(items)} for key, items in by_category.items()},
        "p50_seconds": durations[(len(durations) - 1) // 2] if durations else None,
        "p95_seconds": durations[max(0, (len(durations) * 95 + 99) // 100 - 1)] if durations else None,
        "agent_tokens": sum(tokens) if tokens and all(value is not None for value in tokens) else None,
        "note": "Pass includes model-judged assertions where configured; judge is not human-calibrated. Review and harness errors never count as passes.",
    }


def save_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report["summary"] = summarize(report["results"], planned_trials=report["manifest"]["trials"])
    target = directory / "report.json"
    temporary = directory / "report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(target)
    summary = report["summary"]
    lines = ["# Agent golden evaluation", "", f"Mode: `{report['mode']}`. Synthetic market fixtures only.", "",
             "Model-judged results have not been calibrated by a human reviewer. This is not real-market accuracy.", "",
             f"Attempted trials: {summary['attempted_trials']}; verdicts: `{summary['verdict_counts']}`.",
             f"First-attempt pass rate: {summary['first_attempt_pass_rate']}; all-attempt pass rate: {summary['trial_pass_rate']}.",
             f"Complete repeated cases: {summary['fully_repeated_cases']}; all attempts passed: {summary['all_trials_passed_cases']}.", "",
             "| Case | Trial | Verdict | Seconds | Failed or unresolved checks |", "|---|---:|---|---:|---|"]
    for result in report["results"]:
        failed = [check["name"] for turn in result["turns"] for check in turn.get("checks", []) if check["passed"] is not True]
        if result.get("error"):
            failed.append(result["error"])
        lines.append(f"| {result['case_id']} | {result['trial']} | {result['verdict']} | {result['duration_seconds']} | {', '.join(failed).replace('|', '/')} |")
    lines.extend(["", "## Verification boundary", "", "The runner uses production context preparation, chat entry, tool gateway, evidence store and SQLite journal. It does not test HTTP admission, browser rendering or external market providers.", "", "See report.json for every turn, response, evidence, stream revision, oracle, judgement, usage and memory state."])
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
