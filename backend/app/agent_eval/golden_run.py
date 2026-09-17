"""One Active-suite entry point; shared worker, explicit diagnostic grading."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from app.agent_eval.core_batch import write_json
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.recorder import digest
from app.agent_eval.runner import run_offline


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def plan_suite(suite, ids=None, live_database=None):
    suite = Path(suite).resolve()
    book = read_json(suite)
    entries = book["cases"]
    if book.get("status") != "active" or len({e["id"] for e in entries}) != len(entries):
        raise ValueError("requires an Active suite with unique IDs")
    selected = entries if ids is None else [e for e in entries if e["id"] in ids]
    if not selected or (ids is not None and (len(ids) != len(set(ids)) or len(selected) != len(ids))):
        raise ValueError("selection must be nonempty, unique and present")
    plans = []
    for e in selected:
        case_path = (suite.parent / e["case"]).resolve()
        field = "world" if e.get("world") else "baseline"
        world_path = (suite.parent / e[field]).resolve()
        case, world = load_case(case_path), load_world(world_path)
        if (case.status != "active" or case.case_id != e["id"] or case.mode != e["mode"]
                or digest(case.model_dump(mode="json")) != e["case_digest"]
                or digest(world.model_dump(mode="json")) != e[field + "_digest"]):
            raise ValueError("stale Active asset: " + e["id"])
        if case.mode not in {"offline", "live_historical"}:
            raise ValueError("unsupported execution mode")
        database = e.get("live_database") or live_database
        if case.mode == "live_historical" and (not database or not Path(database).is_file()):
            raise ValueError("Live database missing for " + case.case_id + "; supply --live-database")
        plans.append({"id": case.case_id, "mode": case.mode, "case": str(case_path),
                      "baseline": str(world_path), "case_digest": e["case_digest"],
                      "baseline_digest": e[field + "_digest"],
                      "live_database": str(Path(database).resolve()) if database and case.mode != "offline" else None,
                      "contract_targets": [a.target for a in case.assertions]})
    return book, plans


def classify(summary, review):
    """Never turn skipped/broken judging or unreviewed extraction into a pass."""
    if summary.get("timed_out") or summary.get("verdict") == "unscorable" or summary.get("primary_cause") in {
        "evaluator_failure", "provider_failure", "fixture_failure", "data_failure", "tool_failure"}:
        return "unscorable", summary.get("primary_cause") or "evaluator_failure"
    if summary.get("verdict") == "fail":
        return "fail", summary.get("primary_cause") or "agent_failure"
    if summary.get("trajectory") == "fail" or summary.get("process_diagnostic") == "fail":
        return "fail", "deterministic_contract_failure"
    if not review:
        return "needs_review", "judge_not_requested_or_missing"
    if any(f.get("verdict") == "fail" for part in review.get("deterministic", {}).values()
           for f in part.get("findings", [])):
        return "fail", "deterministic_contract_failure"
    status = review.get("judge", {}).get("status")
    if status in {"judge_error", "input_budget_exceeded", "invalid_trace"}:
        return "unscorable", "judge_failure"
    if status != "completed":
        return "needs_review", "judge_" + str(status)
    dimensions = review.get("dimensions", {})
    values = [dimensions.get(k, {}).get("verdict") for k in
              ("task_completion", "grounding", "boundary_safety")]
    if "fail" in values:
        return "fail", "judge_reported_answer_failure"
    if values == ["pass"] * 3:
        return "pass", None
    return "needs_review", "ambiguous_judgment"


def run_golden(suite, output, *, ids=None, live_database=None, workers=2, wall_seconds=90,
               allow_llm=False, allow_judge=False, dry_run=False, executor=None):
    if not 1 <= workers <= 2 or not 1 <= wall_seconds <= 900:
        raise ValueError("workers must be 1..2 and wall_seconds 1..900")
    if not dry_run and not allow_llm:
        raise ValueError("execution requires explicit --allow-llm")
    book, plans = plan_suite(suite, ids, live_database)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "plan.json", {"suite_digest": digest(book), "cases": plans,
        "allow_judge": allow_judge, "dry_run": dry_run, "workers": workers,
        "wall_seconds_per_case": wall_seconds})
    def execute(plan):
        folder = output / plan["id"]
        try:
            summary = (executor or run_offline)(Path(plan["case"]), Path(plan["baseline"]), folder,
                wall_seconds=wall_seconds,
                live_database=Path(plan["live_database"]) if plan["live_database"] else None,
                allow_judge=allow_judge)
            review_path = folder / "trace-review.json"
            review = read_json(review_path) if review_path.exists() else None
            verdict, cause = classify(summary, review)
            row = {**plan, "verdict": verdict, "cause": cause, "worker_summary": summary,
                   "judge": review.get("judge") if review else None,
                   "dimensions": review.get("dimensions") if review else None,
                   "artifact_directory": str(folder),
                   "artifact_digests": {p.name: digest(read_json(p)) for p in folder.glob("*.json")
                                        if p.name in {"summary.json", "response.json", "trace-review.json", "result.json"}}}
        except Exception as error:
            row = {**plan, "verdict": "unscorable", "cause": "evaluator_failure",
                   "error_type": type(error).__name__, "artifact_directory": str(folder)}
        write_json(output / (plan["id"] + "-result.json"), row)
        return row
    if dry_run:
        rows = [{**p, "verdict": "not_run", "cause": None} for p in plans]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(execute, plans))
    counts = dict(Counter(r["verdict"] for r in rows))
    scored = counts.get("pass", 0) + counts.get("fail", 0)
    usages = [r.get("worker_summary", {}).get("total_tokens") for r in rows]
    report = {"schema_version": "golden-unified-run-v1", "suite_id": book["suite_id"],
        "suite_digest": digest(book), "case_count": len(rows), "dry_run": dry_run,
        "execution_kind": "preflight" if dry_run else "injected_test_executor" if executor else "real_worker",
        "counts": counts, "scored_count": scored,
        "diagnostic_pass_rate": counts.get("pass", 0) / scored if scored else None,
        "scoring_coverage": scored / len(rows),
        "recorded_tokens": sum(v for v in usages if isinstance(v, int)),
        "token_usage_complete": not dry_run and all(isinstance(v, int) for v in usages),
        "cases": rows, "release_eligible": False,
        "limitations": ["Pass/fail is an explicit diagnostic judgment, not calibrated release certification.",
                        "Unscorable and needs_review are excluded from pass-rate denominator; coverage is reported separately.",
                        "Preflight validates assets and database existence; worker validates Live baseline drift before model calls."]}
    write_json(output / "report.json", report)
    lines = ["# Golden统一运行报告", "", "诊断结果，不代表发布准入或稳定性通过。", "",
             f"题数：{len(rows)}；结果：{counts}；有效判分覆盖率：{report['scoring_coverage']:.1%}", "",
             "| Case | 模式 | 结果 | 原因 |", "|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['mode']} | {r['verdict']} | {r.get('cause') or '-'} |" for r in rows]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
