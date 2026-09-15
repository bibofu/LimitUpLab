"""Score a fresh Active Golden run with calibrated evaluators and explicit scope."""

import json
from pathlib import Path
from time import perf_counter

from app.agent_eval.business_facts import BusinessExtraction, verify_business_facts
from app.agent_eval.core_batch import write_json
from app.agent_eval.evaluators import evaluate_trajectory_terminal
from app.agent_eval.extractor import Extraction, SYSTEM as SUMMARY_SYSTEM
from app.agent_eval.event_extractor import BUSINESS_SYSTEM
from app.agent_eval.facts import verify_summary_facts
from app.agent_eval.full_answer_judge import JUDGE_SYSTEM as FULL_ANSWER_SYSTEM, judge_full_answer, _visible_evidence
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.models import BudgetSpec
from app.agent_eval.process_checks import evaluate_process
from app.agent_eval.recorder import digest
from app.agent_eval.semantic_acceptance import JUDGE_SYSTEM, judge_semantics
from app.models import AgentChatResponse


def _acceptance_index(paths):
    factual, semantic, full_answer = {}, None, None
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        schema = report.get("schema_version")
        if report.get("technical_acceptance") is not True:
            raise ValueError("technical acceptance is not passing")
        if schema == "semantic-terminal-acceptance-v1":
            if semantic is not None:
                raise ValueError("duplicate semantic acceptance")
            semantic = report
            continue
        if schema == "full-answer-judge-acceptance-v1":
            if full_answer is not None:
                raise ValueError("duplicate full-answer judge acceptance")
            full_answer = report
            continue
        if schema == "empty-technical-acceptance-v1":
            entries = [{"case_id": report["case_id"], "case_digest": report["case_digest"],
                        "baseline_digest": report["baseline_digest"], "technical_acceptance": True}]
            prompts = {"business": report["extractor_prompt_digest"]}
        elif schema in {"structured-count-acceptance-v1", "structured-selection-acceptance-v1",
                        "structured-highest-acceptance-v1"}:
            entries = report["cases"]
            prompts = report.get("extractor_prompt_digests") or {"business": report["extractor_prompt_digest"]}
        else:
            raise ValueError("unsupported technical acceptance schema")
        for entry in entries:
            if entry["case_id"] in factual or entry.get("technical_acceptance") is not True:
                raise ValueError("duplicate or failed factual acceptance")
            factual[entry["case_id"]] = {"entry": entry, "prompts": prompts, "acceptance_digest": digest(report)}
    return factual, semantic, full_answer


def _percentile(values, proportion):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int((len(ordered) - 1) * proportion + 0.999999)))]


def _accepted_candidate_digest(case):
    """Return the digest used before the reviewed case was promoted to active."""
    if case.status != "active":
        raise ValueError("formal scoring requires an Active Golden case")
    return digest(case.model_copy(update={"status": "candidate"}).model_dump(mode="json"))


def _full_answer_verdict(core, judgment):
    if core in {"fail", "unscorable"}:
        return core
    return judgment


def _verdict_counts(report, field):
    cases = report.get("cases", [])
    return {verdict: sum(item.get(field) == verdict for item in cases)
            for verdict in ("pass", "fail", "needs_review", "unscorable")}


def compare_formal_reports(previous, current):
    """Build a stable, machine-readable comparison across report schema versions."""
    metrics = ("core_contract_pass_rate", "terminal_accuracy", "factual_core_accuracy",
               "semantic_answer_accuracy", "infrastructure_success_rate", "full_answer_pass_rate")
    previous_full = _verdict_counts(previous, "full_answer_verdict")
    current_full = _verdict_counts(current, "full_answer_verdict")
    previous_metrics = {name: previous.get(name) for name in metrics}
    if previous_metrics["full_answer_pass_rate"] is None and previous.get("case_count"):
        previous_metrics["full_answer_pass_rate"] = previous_full["pass"] / previous["case_count"]
    current_metrics = {name: current.get(name) for name in metrics}
    deltas = {name: (current_metrics[name] - previous_metrics[name]
                     if current_metrics[name] is not None and previous_metrics[name] is not None else None)
              for name in metrics}
    before = {item["case_id"]: item for item in previous.get("cases", [])}
    changes = []
    for item in current.get("cases", []):
        old = before.get(item["case_id"])
        if old is None:
            changes.append({"case_id": item["case_id"], "change": "added"})
            continue
        fields = {}
        for field in ("core_contract_verdict", "terminal_verdict", "full_answer_verdict"):
            if old.get(field) != item.get(field):
                fields[field] = {"before": old.get(field), "after": item.get(field)}
        if fields:
            changes.append({"case_id": item["case_id"], "change": "changed", "fields": fields})
    return {
        "schema_version": "formal-agent-baseline-diff-v1",
        "previous_schema_version": previous.get("schema_version"),
        "current_schema_version": current.get("schema_version"),
        "suite_id": current.get("suite_id"),
        "case_count": current.get("case_count"),
        "metrics": {name: {"before": previous_metrics[name], "after": current_metrics[name],
                           "delta": deltas[name]} for name in metrics},
        "core_counts": {"before": previous.get("counts"), "after": current.get("counts")},
        "full_answer_counts": {"before": previous_full, "after": current_full},
        "case_changes": changes,
    }


def _write_diff_markdown(path, comparison):
    lines = ["# Local30 正式基线差异", "", "| 指标 | 旧基线 | 新基线 | 变化 |",
             "| --- | ---: | ---: | ---: |"]
    for name, values in comparison["metrics"].items():
        before, after, delta = values["before"], values["after"], values["delta"]
        fmt = lambda value: "—" if value is None else f"{value:.2%}"
        lines.append(f"| {name} | {fmt(before)} | {fmt(after)} | {fmt(delta)} |")
    lines.extend(["", "## Case 变化", ""])
    if comparison["case_changes"]:
        for item in comparison["case_changes"]:
            details = ", ".join(f"{key}: {value['before']} → {value['after']}"
                                for key, value in item.get("fields", {}).items())
            lines.append(f"- {item['case_id']}: {details or item['change']}")
    else:
        lines.append("- 无终态或裁决变化。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def score_formal_run(bundle: Path, run_root: Path, acceptance_paths: list[Path],
                     destination: Path, provider, previous_report: Path | None = None):
    from app.agent_eval.worker import GuardedProvider
    from app.services.llm_provider import capture_llm_usage
    if destination.exists():
        raise FileExistsError(destination)
    suite = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
    batch = json.loads((run_root / "batch-results.json").read_text(encoding="utf-8"))
    factual_acceptance, semantic_acceptance, full_answer_acceptance = _acceptance_index(acceptance_paths)
    if len(batch.get("cases", [])) != len(suite["cases"]):
        raise ValueError("formal run is incomplete")
    batch_by_id = {item["id"]: item for item in batch["cases"]}
    semantic_cases = [entry for entry in suite["cases"] if entry["id"] not in factual_acceptance]
    if semantic_cases and (semantic_acceptance is None
            or semantic_acceptance.get("judge_prompt_digest") != digest(JUDGE_SYSTEM)):
        raise ValueError("semantic judge acceptance is missing or stale")
    if (factual_acceptance and (full_answer_acceptance is None
            or full_answer_acceptance.get("technical_acceptance") is not True
            or full_answer_acceptance.get("judge_prompt_digest") != digest(FULL_ANSWER_SYSTEM))):
        raise ValueError("full-answer judge acceptance is missing or stale")
    destination.mkdir(parents=True)
    budget = BudgetSpec(max_agent_runs=1, max_model_calls=len(suite["cases"]), max_input_tokens=3000000,
                        max_output_tokens=120000, max_wall_time_seconds=600, max_estimated_cost_usd=None)
    guarded = GuardedProvider(provider, destination / "judge-calls", perf_counter() + 240, budget)
    (destination / "judge-calls").mkdir()
    results = []
    with capture_llm_usage() as judge_usage:
        for entry in suite["cases"]:
            case_id, run = entry["id"], run_root / entry["id"]
            case, world = load_case(bundle / entry["case"]), load_world(bundle / entry["baseline"])
            if (digest(case.model_dump(mode="json")) != entry["case_digest"]
                    or digest(world.model_dump(mode="json")) != entry["baseline_digest"]):
                raise ValueError("Golden suite binding changed")
            run_case = load_case(run / "case.json")
            run_world = load_world(run / ("world.json" if case.mode == "offline" else "baseline.json"))
            if (run_case.model_dump(mode="json") != case.model_dump(mode="json")
                    or digest(run_world.model_dump(mode="json")) != entry["baseline_digest"]):
                raise ValueError("run does not belong to the Active Golden asset")
            response = AgentChatResponse.model_validate_json((run / "response.json").read_text(encoding="utf-8"))
            trajectory, process = evaluate_trajectory_terminal(case, response, profile=case.profile), evaluate_process(case, response)
            supervisor = batch_by_id[case_id]
            accepted = factual_acceptance.get(case_id)
            semantic, full_judgment = None, None
            if accepted:
                binding = accepted["entry"]
                if (binding["case_digest"] != _accepted_candidate_digest(case)
                        or binding["baseline_digest"] != entry["baseline_digest"]):
                    raise ValueError("factual acceptance is stale")
                business = any(item.target == "answer.business_contract" for item in case.assertions)
                prompt = BUSINESS_SYSTEM if business else SUMMARY_SYSTEM
                prompt_key = "business" if business else "summary"
                if accepted["prompts"].get(prompt_key) != digest(prompt):
                    raise ValueError("run extractor is no longer calibrated")
                extraction_type = BusinessExtraction if business else Extraction
                extraction = extraction_type.model_validate_json((run / "extraction.json").read_text(encoding="utf-8"))
                verifier = verify_business_facts if business else verify_summary_facts
                facts = verifier(case, world, response, extraction, diagnostic_unreviewed=True)
                fact_verdict = next((item.verdict for item in facts.findings if item.assertion_id == "facts"), "needs_review")
                additional = [item.model_dump(mode="json") for item in facts.findings
                              if item.verdict == "needs_review" and item.assertion_id not in {"$calibration", "facts"}]
                full_judgment = judge_full_answer(
                    guarded, case.conversation[-1].content, response.answer, _visible_evidence(response),
                ).model_dump(mode="json")
            else:
                facts, fact_verdict, additional = None, None, []
                accepted_case = next((item for item in semantic_acceptance["cases"] if item["case_id"] == case_id), None)
                if (not accepted_case or accepted_case["case_digest"] != _accepted_candidate_digest(case)
                        or accepted_case["baseline_digest"] != entry["baseline_digest"]
                        or accepted_case.get("technical_acceptance") is not True):
                    raise ValueError("semantic case acceptance is stale")
                rubric = next(item.expected for item in case.assertions if item.evaluator == "safety")
                judgment = judge_semantics(guarded, case.conversation[-1].content, rubric, response.answer)
                semantic = judgment.model_dump(mode="json")
                full_judgment = semantic
            delegated = {item.id for item in case.assertions if item.evaluator in {"fact", "safety"}}
            deterministic = [item for item in trajectory.findings if item.assertion_id not in delegated]
            terminal = next(item.verdict for item in trajectory.findings if item.assertion_id == "$terminal")
            if supervisor["verdict"] == "unscorable":
                core, cause = "unscorable", supervisor.get("primary_cause") or "evaluator_failure"
            elif any(item.verdict == "fail" for item in deterministic) or process.verdict == "fail":
                core, cause = "fail", "agent_failure"
            elif accepted and fact_verdict == "fail":
                core, cause = "fail", "agent_failure"
            elif not accepted and semantic["verdict"] == "fail":
                core, cause = "fail", "agent_failure"
            elif (accepted and fact_verdict != "pass") or (not accepted and semantic["verdict"] != "pass"):
                core, cause = "needs_review", "evaluator_failure"
            else:
                core, cause = "pass", None
            full_answer = _full_answer_verdict(core, full_judgment["verdict"])
            results.append({"case_id": case_id, "mode": case.mode, "core_contract_verdict": core,
                            "full_answer_verdict": full_answer, "failure_cause": cause,
                            "full_answer_failure_cause": "agent_failure" if full_answer == "fail" else None,
                            "actual_terminal": response.task_status, "terminal_verdict": terminal,
                            "fact_verdict": fact_verdict, "semantic_judgment": semantic,
                            "full_answer_judgment": full_judgment,
                            "process_verdict": process.verdict,
                            "provisional_extraction_diagnostics": additional,
                            "open_review_items": ([{"source": "full_answer_judge", "judgment": full_judgment}]
                                                  if full_answer == "needs_review" else []),
                            "agent_model_calls": supervisor.get("model_calls"), "agent_tokens": supervisor.get("total_tokens"),
                            "elapsed_seconds": supervisor.get("elapsed_seconds")})
    counts = {key: sum(item["core_contract_verdict"] == key for item in results)
              for key in ("pass", "fail", "needs_review", "unscorable")}
    factual = [item for item in results if item["fact_verdict"] is not None]
    semantic = [item for item in results if item["semantic_judgment"] is not None]
    elapsed = [item["elapsed_seconds"] for item in results]
    report = {"schema_version": "formal-agent-baseline-v3", "suite_id": suite["suite_id"],
              "run_root": str(run_root.resolve()), "model": batch["cases"][0].get("model"),
              "case_count": len(results), "counts": counts,
              "core_contract_pass_rate": counts["pass"] / len(results),
              "terminal_accuracy": sum(item["terminal_verdict"] == "pass" for item in results) / len(results),
              "factual_core_accuracy": sum(item["fact_verdict"] == "pass" for item in factual) / len(factual),
              "semantic_answer_accuracy": sum(item["semantic_judgment"]["verdict"] == "pass" for item in semantic) / len(semantic),
              "infrastructure_success_rate": sum(item["core_contract_verdict"] != "unscorable" for item in results) / len(results),
              "full_answer_pass_rate": sum(item["full_answer_verdict"] == "pass" for item in results) / len(results),
              "full_answer_failures": sum(item["full_answer_verdict"] == "fail" for item in results),
              "full_answer_review_pending": sum(item["full_answer_verdict"] == "needs_review" for item in results),
              "agent_model_calls": sum(item.get("model_calls") or 0 for item in batch["cases"]),
              "agent_tokens": batch["total_tokens"], "judge_model_calls": guarded.calls,
              "judge_tokens": judge_usage.total_tokens if judge_usage.token_usage_complete else None,
              "latency_seconds": {"p50": _percentile(elapsed, .5), "p95": _percentile(elapsed, .95)},
              "cases": results, "release_eligible": False,
              "notes": ["core pass is scoped to the activated deterministic contract",
                        "full-answer factual judgments use a separately calibrated judge and cannot override core failures"]}
    write_json(destination / "report.json", report)
    runtime_versions = sorted({AgentChatResponse.model_validate_json(
        (run_root / entry["id"] / "response.json").read_text(encoding="utf-8")).generated_by
        for entry in suite["cases"]})
    manifest = {
        "schema_version": "formal-agent-run-manifest-v1",
        "suite_id": suite["suite_id"],
        "model": report["model"],
        "case_count": len(results),
        "runtime_versions": runtime_versions,
        "judge_prompt_digests": {"semantic": digest(JUDGE_SYSTEM), "full_answer": digest(FULL_ANSWER_SYSTEM)},
        "inputs": {
            "suite": {"path": str(bundle.resolve()), "digest": digest(suite)},
            "run": {"path": str(run_root.resolve()), "batch_digest": digest(batch)},
            "acceptances": [{"path": str(path.resolve()),
                             "digest": digest(json.loads(path.read_text(encoding="utf-8")))}
                            for path in acceptance_paths],
        },
        "artifacts": {"report.json": digest(report)},
    }
    write_json(destination / "manifest.json", manifest)
    if previous_report is not None:
        previous = json.loads(previous_report.read_text(encoding="utf-8"))
        comparison = compare_formal_reports(previous, report)
        comparison["previous_report"] = str(previous_report.resolve())
        comparison["current_report"] = str((destination / "report.json").resolve())
        write_json(destination / "diff.json", comparison)
        _write_diff_markdown(destination / "DIFF.md", comparison)
    lines = ["# 当前Agent正式Golden基线", "", f"核心合同：{counts['pass']}/{len(results)}通过，"
             f"{counts['fail']}失败，{counts['needs_review']}待复核，{counts['unscorable']}不可评分。", "",
             "| Case | Mode | Core | Terminal | Facts/Judge | Full answer | Cause |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for item in results:
        semantic_verdict = item["semantic_judgment"]["verdict"] if item["semantic_judgment"] else item["fact_verdict"]
        lines.append(f"| {item['case_id']} | {item['mode']} | {item['core_contract_verdict']} | "
                     f"{item['terminal_verdict']} | {semantic_verdict} | {item['full_answer_verdict']} | {item['failure_cause'] or '—'} |")
    (destination / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
