"""Paired report comparison; refuse to attribute evaluator changes to the Agent."""

from collections import Counter

from evals.golden.dimensions import dimension_counts
from evals.golden.reporting import digest

MATCHED_SETTINGS = ("suite_version", "dataset_hash", "evaluator_hash", "trials",
                    "case_ids", "model", "provider", "judge", "judge_human_calibrated",
                    "effective_model_configuration", "profile", "react_deadline_seconds", "libraries")


def _index(report):
    if report.get("mode") != "live-model-frozen-tools":
        raise ValueError("Comparison requires live-model frozen-tool reports")
    manifest = report["manifest"]
    expected = {(case_id, trial) for case_id in manifest["case_ids"]
                for trial in range(1, manifest["trials"] + 1)}
    indexed = {}
    for result in report["results"]:
        key = (result["case_id"], result["trial"])
        if key in indexed:
            raise ValueError("Duplicate trial records")
        if not result.get("completed") or len(result["turns"]) != len(result["definition"]["turns"]):
            raise ValueError("Interrupted or incomplete trial cannot enter a paired comparison")
        indexed[key] = result
    if set(indexed) != expected:
        raise ValueError("Reports must contain all and only the planned trials")
    return indexed


def compare_reports(before, after):
    for field in MATCHED_SETTINGS:
        if field not in before["manifest"] or field not in after["manifest"]:
            raise ValueError(f"Missing reproducibility field: {field}")
        if before["manifest"][field] != after["manifest"][field]:
            raise ValueError(f"Incomparable reports: {field} changed")
    old, new = _index(before), _index(after)
    changes, regressions, improvements = [], [], []
    for key in sorted(old):
        left, right = old[key], new[key]
        if digest(left["definition"]) != digest(right["definition"]):
            raise ValueError(f"Case oracle changed: {key[0]}")
        if left["verdict"] != right["verdict"]:
            change = {"case_id": key[0], "trial": key[1], "before": left["verdict"], "after": right["verdict"]}
            changes.append(change)
            if left["verdict"] == "pass":
                regressions.append(change)
            elif right["verdict"] == "pass":
                improvements.append(change)
    def metrics(report):
        results = report["results"]
        return {"trials": len(results), "verdict_counts": dict(Counter(item["verdict"] for item in results)),
                "pass_rate": sum(item["verdict"] == "pass" for item in results) / len(results),
                "dimensions": dimension_counts(results)}
    return {"matched_settings": True, "before_commit": before["manifest"].get("git_commit"),
            "after_commit": after["manifest"].get("git_commit"),
            "before": metrics(before), "after": metrics(after), "changes": changes,
            "regressions": regressions, "improvements": improvements,
            "note": "Paired observed outcomes, not a causal or statistical guarantee. Model judging remains provisional; inspect per-turn checks and repeated trials."}
