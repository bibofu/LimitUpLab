"""Technical acceptance for an approved genuine-empty business contract."""

import json
from pathlib import Path
from time import perf_counter

from app.agent_eval.approval import business_contract
from app.agent_eval.core_batch import write_json
from app.agent_eval.event_extractor import BUSINESS_SYSTEM, extract_business_answer
from app.agent_eval.frozen_registry import FrozenAgentToolRegistry
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.models import BudgetSpec
from app.agent_eval.recorder import digest
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.tools import ToolGateway


def calibration_samples(day):
    """Language variants and counterexamples derived from the approved empty fact."""
    return [
        ("plain", f"{day}科创板收盘涨停0只，名单为空。", day, 0),
        ("spaced", f"数据日期：{day}。科创板没有收盘涨停股票。", day, 0),
        ("approx", f"{day}科创板收盘涨停约 0 只。", day, 0),
        ("distractor", f"{day}科创板收盘涨停0家；同日全市场收盘涨停40家。", day, 0),
        ("wrong_count", f"{day}科创板收盘涨停1只。", day, 1),
        ("wrong_date", "2026-09-10科创板收盘涨停0只。", "2026-09-10", 0),
    ]


def accept_empty(bundle: Path, approval_path: Path, destination: Path, provider):
    from app.agent_eval.worker import GuardedProvider
    from app.services.llm_provider import capture_llm_usage
    if destination.exists():
        raise FileExistsError(destination)
    suite = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
    if len(suite["cases"]) != 1:
        raise ValueError("empty acceptance requires a single-case bundle")
    entry = suite["cases"][0]
    case, world = load_case(bundle / entry["case"]), load_world(bundle / entry["world"])
    carry = json.loads(approval_path.read_text(encoding="utf-8"))
    case_hash, world_hash = digest(case.model_dump(mode="json")), digest(world.model_dump(mode="json"))
    if (carry.get("schema_version") != "business-approval-carry-forward-v1"
            or carry.get("case_id") != case.case_id
            or carry.get("contract_digest") != digest(business_contract(case))
            or carry.get("target", {}).get("case_digest") != case_hash
            or carry.get("target", {}).get("baseline_digest") != world_hash
            or carry.get("equivalence", {}).get("business_contract_unchanged") is not True):
        raise ValueError("business approval carry-forward is missing or stale")
    expected = case.assertions[0].expected
    if (case.status != "candidate" or case.mode != "offline" or len(case.assertions) != 1
            or expected.get("members") != [] or expected.get("matched_count") != 0
            or set(expected.get("row_selection", {})) != {"closed_limit", "market"}):
        raise ValueError("unsupported genuine-empty contract")
    day, market = expected["trade_date"], expected["row_selection"]["market"]
    registry, routes = FrozenAgentToolRegistry(world), []
    requests = [
        ("market_event_pool", {"event_type": "limit_up", "trade_date": day, "market": market, "result_mode": "list", "limit": 30}),
        ("market_event_pool", {"event_type": "limit_up", "trade_date": day, "market": market, "result_mode": "list", "limit": 100}),
        ("limit_up_events", {"trade_date": day, "market": market, "closed_only": True, "limit": 100}),
        ("limit_up_events", {"trade_date": day, "market": market, "event_status": "closed", "limit": 100}),
    ]
    for tool, args in requests:
        with registry.anchored():
            gateway = ToolGateway(registry, EvidenceStore())
            validated = gateway.validate({"name": tool, "args": args})
            _, payload, state = gateway.execute(tool, validated)
        rows = payload.get("events", payload.get("items", []))
        if state != "empty" or payload.get("matched_count") != 0 or rows or payload.get("source_errors"):
            raise ValueError("route does not prove a genuine empty result")
        routes.append({"tool": tool, "arguments": args, "passed": True})
    samples = calibration_samples(day)
    budget = BudgetSpec(max_agent_runs=1, max_model_calls=len(samples), max_input_tokens=200000,
                        max_output_tokens=40000, max_wall_time_seconds=240, max_estimated_cost_usd=None)
    destination.mkdir(parents=True)
    guarded = GuardedProvider(provider, destination, perf_counter() + 240, budget)
    results = []
    with capture_llm_usage() as usage:
        for name, answer, label_day, label_count in samples:
            try:
                extracted = extract_business_answer(guarded, answer)
                actual = {"trade_date": extracted.trade_date.isoformat() if extracted.trade_date else None,
                          "members": [member.model_dump() for member in extracted.members],
                          "matched_count": extracted.matched_count, "ambiguous": extracted.ambiguous}
                label = {"trade_date": label_day, "members": [], "matched_count": label_count, "ambiguous": False}
                results.append({"sample": name, "answer": answer, "label": label, "actual": actual,
                                "passed": actual == label, "extraction": extracted.model_dump(mode="json")})
            except Exception as error:
                results.append({"sample": name, "answer": answer, "passed": False,
                                "error_type": type(error).__name__})
    passed = all(item["passed"] for item in results)
    report = {"schema_version": "empty-technical-acceptance-v1", "case_id": case.case_id,
              "case_digest": case_hash, "baseline_digest": world_hash,
              "approval_carry_forward_digest": digest(carry), "routes": routes, "calibration": results,
              "label_origin": "deterministic language variants of the user-approved fact; not human answer annotations",
              "extractor_prompt_digest": digest(BUSINESS_SYSTEM), "model": getattr(provider, "model", None),
              "model_calls": guarded.calls, "total_tokens": usage.total_tokens if usage.token_usage_complete else None,
              "technical_acceptance": passed, "active_promotion": False, "release_eligible": False,
              "scope": "genuine-empty core requirement only; additional claims and full-answer quality are excluded"}
    write_json(destination / "acceptance.json", report)
    return {key: report[key] for key in ("case_id", "technical_acceptance", "model_calls", "total_tokens")}


def promote_empty(bundle: Path, approval_path: Path, acceptance_path: Path, destination: Path):
    """Activate only the scoped empty-result contract after replay and extractor calibration."""
    if destination.exists():
        raise FileExistsError(destination)
    suite = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
    if len(suite["cases"]) != 1:
        raise ValueError("empty promotion requires a single-case bundle")
    entry = suite["cases"][0]
    case, world = load_case(bundle / entry["case"]), load_world(bundle / entry["world"])
    carry = json.loads(approval_path.read_text(encoding="utf-8"))
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    case_hash, world_hash = digest(case.model_dump(mode="json")), digest(world.model_dump(mode="json"))
    if (case.status != "candidate" or acceptance.get("technical_acceptance") is not True
            or acceptance.get("case_digest") != case_hash or acceptance.get("baseline_digest") != world_hash
            or acceptance.get("approval_carry_forward_digest") != digest(carry)
            or acceptance.get("extractor_prompt_digest") != digest(BUSINESS_SYSTEM)):
        raise ValueError("empty technical acceptance is incomplete or stale")
    samples, results = calibration_samples(case.assertions[0].expected["trade_date"]), acceptance.get("calibration", [])
    if len(samples) != len(results):
        raise ValueError("empty calibration samples missing")
    for (name, answer, day, count), result in zip(samples, results):
        label = {"trade_date": day, "members": [], "matched_count": count, "ambiguous": False}
        if (result.get("sample") != name or result.get("answer") != answer or result.get("label") != label
                or result.get("actual") != label or result.get("passed") is not True):
            raise ValueError("empty calibration failed or labels changed")
    active = case.model_copy(update={"status": "active"})
    destination.mkdir(parents=True)
    write_json(destination / "case.json", active.model_dump(mode="json"))
    write_json(destination / "world.json", world.model_dump(mode="json"))
    write_json(destination / "business-approval-carry-forward.json", carry)
    write_json(destination / "technical-acceptance.json", acceptance)
    manifest = {"case_id": case.case_id, "case_version": case.case_version, "mode": case.mode,
                "status": "active", "scope": "genuine-empty core requirement",
                "approved_candidate_digest": case_hash, "active_case_digest": digest(active.model_dump(mode="json")),
                "baseline_digest": world_hash, "approval_carry_forward_digest": digest(carry),
                "acceptance_digest": digest(acceptance), "release_eligible": False,
                "answer_quality_approved": False,
                "limitations": ["additional business statements require separate review",
                                "full-answer semantic quality is not approved by extractor calibration",
                                "unrecorded valid offline routes yield unscorable fixture_failure"]}
    write_json(destination / "manifest.json", manifest)
    return manifest
