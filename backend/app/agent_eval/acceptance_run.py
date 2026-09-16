"""Run explicitly selected candidate cases once; retain failures, never promote."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from time import perf_counter

from app.agent_eval.loader import load_case, load_world
from app.agent_eval.recorder import digest
from app.agent_eval.runner import run_offline


def summarize_runs(runs, supplements, output):
    from app.agent_eval.core_batch import write_json
    rows = []
    for folder in sorted(runs.iterdir()):
        if not (folder / "summary.json").is_file():
            continue
        read = lambda name: json.loads((folder / name).read_text(encoding="utf-8"))
        summary, judge = read("summary.json"), read("trace-review.json")
        response = read("response.json")
        attempts = read("frozen-attempts.json")
        row = {"case": folder.name, "worker_verdict": summary["verdict"], "cause": summary["primary_cause"],
            "recorded_tokens": summary["total_tokens"], "calls": summary["model_calls"],
            "initial_judge": judge["judge"]["status"], "initial_dimensions": judge["dimensions"],
            "terminal": response["task_status"], "fixture_rejections": [a for a in attempts if a["outcome"] == "rejected"],
            "artifact_digests": {name: digest(read(name)) for name in (
                "case.json", "response.json", "result.json", "usage.json", "trace-review.json", "manifest.json")}}
        supplement = supplements / folder.name
        if (supplement / "review.json").is_file():
            extra = json.loads((supplement / "review.json").read_text(encoding="utf-8"))
            binding = json.loads((supplement / "binding.json").read_text(encoding="utf-8"))
            if binding["response_digest"] != row["artifact_digests"]["response.json"] or binding["case_digest"] != row["artifact_digests"]["case.json"]:
                raise ValueError("supplement does not match original case/response")
            row["supplement"] = {"binding": binding, "judge": extra["judge"], "dimensions": extra["dimensions"],
                                 "review_digest": digest(extra)}
        rows.append(row)
    report = {"schema_version": "basic70-acceptance-audit-v1", "cases": rows,
        "agent_runs": len(rows), "initial_worker_verdicts": dict(Counter(r["worker_verdict"] for r in rows)),
        "initial_judge_statuses": dict(Counter(r["initial_judge"] for r in rows)),
        "initial_recorded_tokens": sum(r["recorded_tokens"] or 0 for r in rows),
        "supplement_recorded_tokens": sum(r.get("supplement", {}).get("binding", {}).get("total_tokens", 0) for r in rows),
        "initial_calls": sum(r["calls"] for r in rows),
        "supplement_calls": sum(r.get("supplement", {}).get("binding", {}).get("calls", 0) for r in rows),
        "new_active_golden": 0, "release_eligible": False,
        "limitations": ["Counts are raw diagnostic results, not acceptance or model pass rate.",
                        "Recorded tokens may exclude usage unreported by failed provider calls.",
                        "Same-model Judge, single run, and Codex review are not independent human approval."]}
    write_json(output, report)
    return report


def review_saved(run, output, *, max_input_chars=40000):
    """Explicit single supplemental judgment; original execution is immutable."""
    from app.agent_eval.models import BudgetSpec
    from app.agent_eval.trace_review import review_trace
    from app.agent_eval.worker import GuardedProvider, save
    from app.config import configure_runtime_environment
    from app.models import AgentChatResponse
    from app.services.llm_provider import get_llm_provider, capture_llm_usage

    case = load_case(run / "case.json")
    response = AgentChatResponse.model_validate_json((run / "response.json").read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=False)
    configure_runtime_environment()
    provider = get_llm_provider()
    budget = BudgetSpec(max_agent_runs=1, max_model_calls=1, max_input_tokens=100000,
                        max_output_tokens=2500, max_estimated_cost_usd=None, max_wall_time_seconds=60)
    guarded = GuardedProvider(provider, output, perf_counter() + 60, budget)
    with capture_llm_usage() as usage:
        review = review_trace(case, response, provider=guarded, max_input_chars=max_input_chars)
    save(output, "review.json", review)
    save(output, "binding.json", {"original_run": str(run.resolve()), "case_digest": digest(case.model_dump(mode="json")),
        "response_digest": digest(response.model_dump(mode="json")), "model": getattr(provider, "model", None),
        "budget": budget.model_dump(mode="json"), "calls": guarded.calls, "total_tokens": usage.total_tokens,
        "usage_complete": usage.token_usage_complete and guarded.token_usage_complete,
        "agent_reruns": 0, "release_eligible": False})
    print(json.dumps({"case": case.case_id, "judge": review["judge"], "dimensions": review["dimensions"]}, ensure_ascii=False), flush=True)
    return review


def select_entries(suite, ids):
    book = json.loads(suite.read_text(encoding="utf-8"))
    selected = [e for e in book["cases"] if e["id"] in ids]
    if len(selected) != len(ids) or len(set(ids)) != len(ids):
        raise ValueError("case selection must exist and be unique")
    for entry in selected:
        if entry["status"] != "candidate":
            raise ValueError("acceptance run only selects candidates")
        key = "world" if entry.get("world") else "baseline"
        case = load_case(suite.parent / entry["case"])
        world = load_world(suite.parent / entry[key])
        if digest(case.model_dump(mode="json")) != entry["case_digest"] or digest(world.model_dump(mode="json")) != entry[key + "_digest"]:
            raise ValueError("suite asset digest mismatch")
    return selected


def run_selected(suite, output, ids, *, workers=2, wall_seconds=90):
    if not 1 <= workers <= 2 or not 1 <= wall_seconds <= 120 or not 1 <= len(ids) <= 40:
        raise ValueError("bounded run requires 1..40 cases, 1..2 workers, 1..120 seconds/case")
    entries = select_entries(suite, ids)
    output.mkdir(parents=True, exist_ok=True)
    if any((output / e["id"]).exists() for e in entries):
        raise FileExistsError("existing case run; do not silently retry or overwrite")

    def run(entry):
        key = "world" if entry.get("world") else "baseline"
        result = run_offline(suite.parent / entry["case"], suite.parent / entry[key], output / entry["id"],
            wall_seconds=wall_seconds, live_database=Path(entry["live_database"]) if entry.get("live_database") else None,
            allow_judge=True)
        review_path = output / entry["id"] / "trace-review.json"
        review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
        summary = {"case": entry["id"], "verdict": result["verdict"], "cause": result.get("primary_cause"),
            "tokens": result.get("total_tokens"), "calls": result.get("model_calls"),
            "judge_status": review.get("judge", {}).get("status"),
            "dimensions": {k: v["verdict"] for k, v in review.get("dimensions", {}).items()}}
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return summary

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(run, entries))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path)
    parser.add_argument("--review-run", type=Path)
    parser.add_argument("--summarize-runs", type=Path)
    parser.add_argument("--supplements", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--case-ids", nargs="+")
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--wall-seconds", type=int, default=90)
    args = parser.parse_args()
    if args.summarize_runs and args.supplements:
        report = summarize_runs(args.summarize_runs, args.supplements, args.output_dir)
        print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False))
    elif not args.allow_llm:
        parser.error("real calls require --allow-llm")
    elif args.review_run:
        review_saved(args.review_run, args.output_dir)
    elif args.suite and args.case_ids:
        run_selected(args.suite.resolve(), args.output_dir, args.case_ids, workers=args.workers, wall_seconds=args.wall_seconds)
    else:
        parser.error("provide --review-run, or both --suite and --case-ids")
