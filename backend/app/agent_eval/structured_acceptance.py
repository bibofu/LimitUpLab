"""Shared technical acceptance for approved structured fact cases."""

import json
from pathlib import Path
from time import perf_counter

from app.agent_eval.batch_preflight import _baseline, _oracle, approval_index
from app.agent_eval.core_batch import write_json
from app.agent_eval.event_extractor import BUSINESS_SYSTEM, extract_business_answer
from app.agent_eval.extractor import SYSTEM as SUMMARY_SYSTEM, extract_answer
from app.agent_eval.facts import normalize_number
from app.agent_eval.frozen_registry import FrozenAgentToolRegistry
from app.agent_eval.historical_live import HistoricalLiveRegistry
from app.agent_eval.highest_acceptance import calibration_samples as highest_samples
from app.agent_eval.loader import load_case
from app.agent_eval.models import BudgetSpec
from app.agent_eval.recorder import digest
from app.agent_eval.selection import select_rows
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.tools import ToolGateway


def count_samples(day, count):
    """Normal forms plus wrong fact/date and multi-claim coverage probes."""
    return [
        ("plain", f"{day}收盘涨停{count}家。", [(day, "limit_up_count", count, "exact")]),
        ("spaced", f"数据日期：{day}。收盘涨停 {count} 只。", [(day, "limit_up_count", count, "exact")]),
        ("approx", f"{day}收盘涨停约{count}只。", [(day, "limit_up_count", count, "approximate")]),
        ("wrong_count", f"{day}收盘涨停{count + 1}家。", [(day, "limit_up_count", count + 1, "exact")]),
        ("wrong_date", f"2026-09-08收盘涨停{count}家。", [("2026-09-08", "limit_up_count", count, "exact")]),
        ("multi", f"{day}收盘涨停{count}家，未回封18家。",
         [(day, "limit_up_count", count, "exact"), (day, "unsealed_count", 18, "exact")]),
    ]


def _summary_actual(extraction):
    numeric_slots = sum(item.numeric for item in extraction.inventory)
    claims = []
    for claim in extraction.claims:
        family, value = normalize_number(claim.value_text, claim.unit)
        if family != "count":
            raise ValueError("count calibration extracted a non-count unit")
        claims.append((claim.trade_date.isoformat(), claim.metric, int(value), claim.certainty))
    return {"claims": sorted(claims), "numeric_slots": numeric_slots,
            "numeric_claims": len(extraction.claims)}


def _business_actual(extraction):
    return {"trade_date": extraction.trade_date.isoformat() if extraction.trade_date else None,
            "limit_up_count": extraction.limit_up_count,
            "members": [member.model_dump() for member in extraction.members],
            "ambiguous": extraction.ambiguous}


def _count_requests(case, day):
    return ([
        ("market_summary", {"include_limit_down": False}),
    ] if case.assertions[0].target == "answer" else [
        ("market_event_pool", {"event_type": "limit_up", "trade_date": day, "result_mode": "count"}),
        ("limit_up_events", {"trade_date": day, "event_status": "closed", "limit": 100}),
    ])


def _execute_count_routes(case, world, database, cache):
    key = (case.mode, digest(world.model_dump(mode="json")))
    if key not in cache:
        cache[key] = (HistoricalLiveRegistry(database, world) if case.mode == "live_historical"
                      else FrozenAgentToolRegistry(world))
    registry = cache[key]
    expected = case.assertions[0].expected
    day, count = expected["trade_date"], expected["limit_up_count"]
    requests = _count_requests(case, day)
    routes = []
    for tool, args in requests:
        with registry.anchored():
            gateway = ToolGateway(registry, EvidenceStore())
            validated = gateway.validate({"name": tool, "args": args})
            _, payload, state = gateway.execute(tool, validated)
        if tool == "market_summary":
            passed = state in {"ok", "partial"} and payload.get("trade_date") == day and payload.get("limit_up_count") == count
        elif tool == "market_event_pool":
            passed = state == "ok" and payload.get("trade_date") == day and payload.get("matched_count") == count
        else:
            passed = (state == "ok" and payload.get("trade_date") == day
                      and payload.get("matched_count") == count and payload.get("returned_count") == count)
        if not passed:
            raise ValueError(f"{case.case_id} count route changed the approved fact")
        routes.append({"tool": tool, "arguments": args, "passed": True})
    return routes


def accept_count_batch(bundle: Path, approval_paths: list[Path], preflight_path: Path,
                       destination: Path, provider, database: Path, case_ids: list[str]):
    """Accept count contracts together while calibrating each unique extractor/fact pair once."""
    from app.agent_eval.worker import GuardedProvider
    from app.services.llm_provider import capture_llm_usage
    if destination.exists():
        raise FileExistsError(destination)
    suite = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
    entries = {item["id"]: item for item in suite["cases"]}
    approvals = approval_index([json.loads(path.read_text(encoding="utf-8")) for path in approval_paths])
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight_cases = {item["case_id"]: item for item in preflight.get("cases", [])}
    if (preflight.get("suite_id") != suite["suite_id"] or preflight.get("suite_version") != suite["version"]):
        raise ValueError("preflight belongs to another suite")
    prepared, route_cache = [], {}
    for case_id in case_ids:
        if case_id not in entries or preflight_cases.get(case_id, {}).get("verdict") != "pass":
            raise ValueError("case absent or not preflighted")
        case, world = load_case(bundle / entries[case_id]["case"]), _baseline(bundle, entries[case_id])
        approved = approvals.get(case_id)
        binding = approved and approved["binding"]
        if (not binding or binding.get("case_digest") != digest(case.model_dump(mode="json"))
                or binding.get("baseline_digest") != digest(world.model_dump(mode="json"))
                or _oracle(case, world)["category"] != "count"):
            raise ValueError("count approval or oracle is stale")
        routes = _execute_count_routes(case, world, database, route_cache)
        expected = case.assertions[0].expected
        kind = "summary" if case.assertions[0].target == "answer" else "business"
        prepared.append((case, world, approved, routes, kind, expected["trade_date"], expected["limit_up_count"]))
    calibration_keys = list(dict.fromkeys((kind, day, count) for *_, kind, day, count in prepared))
    samples_total = sum(len(count_samples(day, count)) for _, day, count in calibration_keys)
    destination.mkdir(parents=True)
    budget = BudgetSpec(max_agent_runs=1, max_model_calls=samples_total, max_input_tokens=500000,
                        max_output_tokens=80000, max_wall_time_seconds=480, max_estimated_cost_usd=None)
    guarded = GuardedProvider(provider, destination, perf_counter() + 480, budget)
    calibration = {}
    with capture_llm_usage() as usage:
        for kind, day, count in calibration_keys:
            results = []
            for name, answer, label_claims in count_samples(day, count):
                try:
                    if kind == "summary":
                        extracted = extract_answer(guarded, answer)
                        actual = _summary_actual(extracted)
                        label = {"claims": sorted(label_claims), "numeric_slots": len(label_claims),
                                 "numeric_claims": len(label_claims)}
                    else:
                        extracted = extract_business_answer(guarded, answer)
                        actual = _business_actual(extracted)
                        main = next(claim for claim in label_claims if claim[1] == "limit_up_count")
                        label = {"trade_date": main[0], "limit_up_count": main[2],
                                 "members": [], "ambiguous": False}
                    results.append({"sample": name, "answer": answer, "label": label, "actual": actual,
                                    "passed": actual == label, "extraction": extracted.model_dump(mode="json")})
                except Exception as error:
                    results.append({"sample": name, "answer": answer, "passed": False,
                                    "error_type": type(error).__name__})
            calibration[f"{kind}:{day}:{count}"] = results
    cases = []
    for case, world, approved, routes, kind, day, count in prepared:
        key = f"{kind}:{day}:{count}"
        passed = all(item["passed"] for item in calibration[key])
        cases.append({"case_id": case.case_id, "case_version": case.case_version,
                      "case_digest": digest(case.model_dump(mode="json")),
                      "baseline_digest": digest(world.model_dump(mode="json")),
                      "approval_digest": approved["approval_digest"], "routes": routes,
                      "calibration_key": key, "technical_acceptance": passed})
    passed = all(item["technical_acceptance"] for item in cases)
    report = {"schema_version": "structured-count-acceptance-v1", "suite_id": suite["suite_id"],
              "suite_version": suite["version"], "cases": cases, "calibration": calibration,
              "extractor_prompt_digests": {"summary": digest(SUMMARY_SYSTEM), "business": digest(BUSINESS_SYSTEM)},
              "label_origin": "deterministic variants and counterexamples derived from user-approved facts",
              "model": getattr(provider, "model", None), "model_calls": guarded.calls,
              "total_tokens": usage.total_tokens if usage.token_usage_complete else None,
              "technical_acceptance": passed, "active_promotion": False, "release_eligible": False,
              "limitations": ["count core requirements only", "additional claims and full-answer quality are excluded"]}
    write_json(destination / "acceptance.json", report)
    return {key: report[key] for key in ("technical_acceptance", "model_calls", "total_tokens")}


def promote_count_batch(bundle: Path, approval_paths: list[Path], acceptance_path: Path, destination: Path):
    """Activate count core contracts; this is not approval of full generated answers."""
    if destination.exists():
        raise FileExistsError(destination)
    suite = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
    entries = {item["id"]: item for item in suite["cases"]}
    approvals = approval_index([json.loads(path.read_text(encoding="utf-8")) for path in approval_paths])
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    if (acceptance.get("schema_version") != "structured-count-acceptance-v1"
            or acceptance.get("suite_id") != suite["suite_id"] or acceptance.get("suite_version") != suite["version"]
            or acceptance.get("technical_acceptance") is not True
            or acceptance.get("extractor_prompt_digests") != {
                "summary": digest(SUMMARY_SYSTEM), "business": digest(BUSINESS_SYSTEM)}):
        raise ValueError("count technical acceptance is incomplete or stale")
    calibration = acceptance.get("calibration", {})
    for key, results in calibration.items():
        kind, day, count_text = key.split(":")
        samples = count_samples(day, int(count_text))
        if len(results) != len(samples):
            raise ValueError("count calibration samples missing")
        for result, (name, answer, claims) in zip(results, samples):
            if kind == "summary":
                label = {"claims": sorted(claims), "numeric_slots": len(claims), "numeric_claims": len(claims)}
            else:
                main = next(claim for claim in claims if claim[1] == "limit_up_count")
                label = {"trade_date": main[0], "limit_up_count": main[2], "members": [], "ambiguous": False}
            if (result.get("sample") != name or result.get("answer") != answer
                    or digest(result.get("label")) != digest(label) or digest(result.get("actual")) != digest(label)
                    or result.get("passed") is not True):
                raise ValueError("count calibration failed or labels changed")
    active_entries = []
    for accepted in acceptance.get("cases", []):
        case_id = accepted["case_id"]
        if case_id not in entries or accepted.get("technical_acceptance") is not True:
            raise ValueError("accepted count case is absent or failed")
        case, world = load_case(bundle / entries[case_id]["case"]), _baseline(bundle, entries[case_id])
        approved = approvals.get(case_id)
        if (not approved or accepted.get("case_digest") != digest(case.model_dump(mode="json"))
                or accepted.get("baseline_digest") != digest(world.model_dump(mode="json"))
                or accepted.get("approval_digest") != approved["approval_digest"]
                or accepted.get("calibration_key") not in calibration):
            raise ValueError("count case approval or asset binding is stale")
        expected = case.assertions[0].expected
        kind = "summary" if case.assertions[0].target == "answer" else "business"
        if accepted["calibration_key"] != f"{kind}:{expected['trade_date']}:{expected['limit_up_count']}":
            raise ValueError("count calibration belongs to another fact")
        expected_routes = [{"tool": tool, "arguments": args, "passed": True}
                           for tool, args in _count_requests(case, expected["trade_date"])]
        if accepted.get("routes") != expected_routes:
            raise ValueError("count route acceptance is missing or changed")
        active = case.model_copy(update={"status": "active"})
        folder = destination / case_id
        folder.mkdir(parents=True)
        write_json(folder / "case.json", active.model_dump(mode="json"))
        write_json(folder / ("world.json" if case.mode == "offline" else "baseline.json"), world.model_dump(mode="json"))
        active_entries.append({"case_id": case_id, "case_version": case.case_version, "mode": case.mode,
                               "scope": "trade date and closing limit-up count",
                               "active_case_digest": digest(active.model_dump(mode="json")),
                               "baseline_digest": accepted["baseline_digest"],
                               "approval_digest": accepted["approval_digest"],
                               "calibration_key": accepted["calibration_key"]})
    if not active_entries:
        raise ValueError("count acceptance contains no cases")
    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination / "technical-acceptance.json", acceptance)
    manifest = {"schema_version": "active-count-golden-batch-v1", "suite_id": suite["suite_id"],
                "suite_version": suite["version"], "status": "active", "scope": "count core requirements",
                "cases": active_entries, "acceptance_digest": digest(acceptance),
                "release_eligible": False, "answer_quality_approved": False,
                "limitations": ["additional statements and full-answer semantics require separate review",
                                "Historical Live remains bounded to the recorded local data baseline"]}
    write_json(destination / "manifest.json", manifest)
    return manifest


def _canonical_members(members):
    return [{"symbol": member["symbol"], "name": "".join(member["name"].split())} for member in members]


def _members_text(members):
    return "、".join(f"{member['name']}（{member['symbol']}）" for member in members)


def selection_samples(day, members):
    """Each unique approved list gets formatting, order, omission, duplicate and date probes."""
    members = _canonical_members(members)
    other_day = "2026-09-10" if day != "2026-09-10" else "2026-09-11"
    table = f"数据日期：{day}\n|代码|名称|\n|---|---|\n" + "\n".join(
        f"|{member['symbol']}|{member['name']}|" for member in members)
    variants = [
        ("plain", f"{day}名单：{_members_text(members)}。", day, members),
        ("table", table, day, members),
        ("reverse", f"{day}名单：{_members_text(list(reversed(members)))}。", day, list(reversed(members))),
        ("omission", f"{day}名单：{_members_text(members[:-1])}。", day, members[:-1]),
        ("duplicate", f"{day}名单：{_members_text(members + members[:1])}。", day, members + members[:1]),
        ("wrong_date", f"{other_day}名单：{_members_text(members)}。", other_day, members),
    ]
    return variants


def _selection_args(expected):
    selection, args = expected["row_selection"], {"trade_date": expected["trade_date"], "limit": 100}
    for key in ("market", "board_height", "min_board_height"):
        if key in selection:
            args[key] = selection[key]
    if "symbol" in selection:
        args["query"] = selection["symbol"]
    if selection.get("closed_limit") is False:
        args["event_status"] = "failed"
    elif selection.get("min_break_count", 0) > 0:
        args["event_status"] = "broken_intraday"
        if selection.get("closed_limit") is True:
            args["closed_only"] = True
    elif selection.get("closed_limit") is True:
        args.update(event_status="closed", closed_only=True)
    if "order_by" in selection:
        args.update(sort_by=selection["order_by"],
                    sort_order="desc" if selection.get("descending", True) else "asc")
    return args


def _execute_selection_route(case, world, database, cache):
    key = (case.mode, digest(world.model_dump(mode="json")))
    if key not in cache:
        cache[key] = (HistoricalLiveRegistry(database, world) if case.mode == "live_historical"
                      else FrozenAgentToolRegistry(world))
    registry, expected = cache[key], case.assertions[0].expected
    args = _selection_args(expected)
    with registry.anchored():
        gateway = ToolGateway(registry, EvidenceStore())
        validated = gateway.validate({"name": "limit_up_events", "args": args})
        _, payload, state = gateway.execute("limit_up_events", validated)
    if state not in {"ok", "empty"} or payload.get("source_errors"):
        raise ValueError(f"{case.case_id} selection route failed")
    if payload.get("matched_count") != payload.get("returned_count"):
        raise ValueError(f"{case.case_id} selection route is truncated")
    selected = select_rows(payload.get("events", []), expected["row_selection"])
    actual = _canonical_members(selected)
    declared = _canonical_members(expected["members"])
    if (actual != declared if expected.get("ordered") else set(map(digest, actual)) != set(map(digest, declared))):
        raise ValueError(f"{case.case_id} direct route changes approved members")
    return {"tool": "limit_up_events", "arguments": args, "passed": True,
            "source_count": len(payload.get("events", [])), "selected_count": len(selected)}


def accept_selection_batch(bundle: Path, approval_paths: list[Path], preflight_path: Path,
                           destination: Path, provider, database: Path, case_ids: list[str]):
    """Route-check every case and calibrate every unique approved member list once."""
    from app.agent_eval.worker import GuardedProvider
    from app.services.llm_provider import capture_llm_usage
    if destination.exists():
        raise FileExistsError(destination)
    suite = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
    entries = {item["id"]: item for item in suite["cases"]}
    approvals = approval_index([json.loads(path.read_text(encoding="utf-8")) for path in approval_paths])
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight_cases = {item["case_id"]: item for item in preflight.get("cases", [])}
    if (preflight.get("suite_id"), preflight.get("suite_version")) != (suite["suite_id"], suite["version"]):
        raise ValueError("preflight belongs to another suite")
    prepared, route_cache = [], {}
    for case_id in case_ids:
        if case_id not in entries or preflight_cases.get(case_id, {}).get("verdict") != "pass":
            raise ValueError("case absent or not preflighted")
        case, world = load_case(bundle / entries[case_id]["case"]), _baseline(bundle, entries[case_id])
        approved = approvals.get(case_id)
        binding = approved and approved["binding"]
        if (not binding or binding.get("case_digest") != digest(case.model_dump(mode="json"))
                or binding.get("baseline_digest") != digest(world.model_dump(mode="json"))
                or _oracle(case, world)["category"] != "selection"):
            raise ValueError("selection approval or oracle is stale")
        expected = case.assertions[0].expected
        route = _execute_selection_route(case, world, database, route_cache)
        calibration_key = digest({"trade_date": expected["trade_date"],
                                  "members": _canonical_members(expected["members"]),
                                  "ordered": expected.get("ordered", False)})
        prepared.append((case, world, approved, route, calibration_key, expected))
    unique = {}
    for *_, key, expected in prepared:
        unique.setdefault(key, expected)
    sample_total = sum(len(selection_samples(value["trade_date"], value["members"])) for value in unique.values())
    destination.mkdir(parents=True)
    budget = BudgetSpec(max_agent_runs=1, max_model_calls=sample_total, max_input_tokens=3000000,
                        max_output_tokens=500000, max_wall_time_seconds=900, max_estimated_cost_usd=None)
    guarded = GuardedProvider(provider, destination, perf_counter() + 900, budget)
    calibration = {}
    with capture_llm_usage() as usage:
        for key, expected in unique.items():
            results = []
            for name, answer, label_day, label_members in selection_samples(expected["trade_date"], expected["members"]):
                label = {"trade_date": label_day, "members": label_members, "ambiguous": False}
                try:
                    extracted = extract_business_answer(guarded, answer)
                    actual = {"trade_date": extracted.trade_date.isoformat() if extracted.trade_date else None,
                              "members": _canonical_members([member.model_dump() for member in extracted.members]),
                              "ambiguous": extracted.ambiguous}
                    results.append({"sample": name, "answer": answer, "label": label, "actual": actual,
                                    "passed": actual == label, "extraction": extracted.model_dump(mode="json")})
                except Exception as error:
                    results.append({"sample": name, "answer": answer, "label": label, "passed": False,
                                    "error_type": type(error).__name__})
            calibration[key] = results
    cases = []
    for case, world, approved, route, key, _ in prepared:
        cases.append({"case_id": case.case_id, "case_version": case.case_version,
                      "case_digest": digest(case.model_dump(mode="json")),
                      "baseline_digest": digest(world.model_dump(mode="json")),
                      "approval_digest": approved["approval_digest"], "route": route,
                      "calibration_key": key,
                      "technical_acceptance": all(item["passed"] for item in calibration[key])})
    report = {"schema_version": "structured-selection-acceptance-v1", "suite_id": suite["suite_id"],
              "suite_version": suite["version"], "cases": cases, "calibration": calibration,
              "extractor_prompt_digest": digest(BUSINESS_SYSTEM),
              "label_origin": "deterministic variants and counterexamples derived from user-approved member lists",
              "model": getattr(provider, "model", None), "model_calls": guarded.calls,
              "total_tokens": usage.total_tokens if usage.token_usage_complete else None,
              "technical_acceptance": all(item["technical_acceptance"] for item in cases),
              "active_promotion": False, "release_eligible": False,
              "limitations": ["member identity/date/order core requirements only", "additional claims are excluded"]}
    write_json(destination / "acceptance.json", report)
    return {key: report[key] for key in ("technical_acceptance", "model_calls", "total_tokens")}


def promote_selection_batch(bundle: Path, approval_paths: list[Path], acceptance_path: Path, destination: Path):
    """Activate only member identity/date/order contracts after batch calibration."""
    if destination.exists():
        raise FileExistsError(destination)
    suite = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
    entries = {item["id"]: item for item in suite["cases"]}
    approvals = approval_index([json.loads(path.read_text(encoding="utf-8")) for path in approval_paths])
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    if (acceptance.get("schema_version") != "structured-selection-acceptance-v1"
            or (acceptance.get("suite_id"), acceptance.get("suite_version")) != (suite["suite_id"], suite["version"])
            or acceptance.get("technical_acceptance") is not True
            or acceptance.get("extractor_prompt_digest") != digest(BUSINESS_SYSTEM)):
        raise ValueError("selection technical acceptance is incomplete or stale")
    expected_by_key, prepared = {}, []
    for accepted in acceptance.get("cases", []):
        case_id = accepted["case_id"]
        if case_id not in entries or accepted.get("technical_acceptance") is not True:
            raise ValueError("accepted selection case is absent or failed")
        case, world = load_case(bundle / entries[case_id]["case"]), _baseline(bundle, entries[case_id])
        approved, expected = approvals.get(case_id), case.assertions[0].expected
        key = digest({"trade_date": expected["trade_date"], "members": _canonical_members(expected["members"]),
                      "ordered": expected.get("ordered", False)})
        if (not approved or _oracle(case, world)["category"] != "selection"
                or accepted.get("case_digest") != digest(case.model_dump(mode="json"))
                or accepted.get("baseline_digest") != digest(world.model_dump(mode="json"))
                or accepted.get("approval_digest") != approved["approval_digest"]
                or accepted.get("calibration_key") != key):
            raise ValueError("selection case approval, oracle or asset binding is stale")
        route = accepted.get("route", {})
        if (route.get("tool") != "limit_up_events" or route.get("arguments") != _selection_args(expected)
                or route.get("passed") is not True or route.get("selected_count") != len(expected["members"])
                or type(route.get("source_count")) is not int or route["source_count"] < len(expected["members"])):
            raise ValueError("selection route acceptance is missing or changed")
        expected_by_key.setdefault(key, expected)
        prepared.append((case, world, approved, key))
    calibration = acceptance.get("calibration", {})
    if set(calibration) != set(expected_by_key):
        raise ValueError("selection calibration set does not match accepted contracts")
    for key, expected in expected_by_key.items():
        samples, results = selection_samples(expected["trade_date"], expected["members"]), calibration[key]
        if len(samples) != len(results):
            raise ValueError("selection calibration samples missing")
        for result, (name, answer, day, members) in zip(results, samples):
            label = {"trade_date": day, "members": members, "ambiguous": False}
            if (result.get("sample") != name or result.get("answer") != answer
                    or digest(result.get("label")) != digest(label) or digest(result.get("actual")) != digest(label)
                    or result.get("passed") is not True):
                raise ValueError("selection calibration failed or labels changed")
    active_entries = []
    for case, world, approved, key in prepared:
        active = case.model_copy(update={"status": "active"})
        folder = destination / case.case_id
        folder.mkdir(parents=True)
        write_json(folder / "case.json", active.model_dump(mode="json"))
        write_json(folder / ("world.json" if case.mode == "offline" else "baseline.json"), world.model_dump(mode="json"))
        active_entries.append({"case_id": case.case_id, "case_version": case.case_version, "mode": case.mode,
                               "scope": "member identity, data date, completeness and declared order",
                               "active_case_digest": digest(active.model_dump(mode="json")),
                               "baseline_digest": digest(world.model_dump(mode="json")),
                               "approval_digest": approved["approval_digest"], "calibration_key": key})
    if not active_entries:
        raise ValueError("selection acceptance contains no cases")
    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination / "technical-acceptance.json", acceptance)
    manifest = {"schema_version": "active-selection-golden-batch-v1", "suite_id": suite["suite_id"],
                "suite_version": suite["version"], "status": "active", "scope": "selection core requirements",
                "cases": active_entries, "acceptance_digest": digest(acceptance),
                "release_eligible": False, "answer_quality_approved": False,
                "limitations": ["additional statements and full-answer semantics require separate review",
                                "Historical Live remains bounded to the recorded local data baseline"]}
    write_json(destination / "manifest.json", manifest)
    return manifest


def _execute_highest_routes(case, world, database, cache):
    key = (case.mode, digest(world.model_dump(mode="json")))
    if key not in cache:
        cache[key] = (HistoricalLiveRegistry(database, world) if case.mode == "live_historical"
                      else FrozenAgentToolRegistry(world))
    registry, expected, routes = cache[key], case.assertions[0].expected, []
    identities = {digest(item) for item in _canonical_members(expected["members"])}
    for highest, limit in ((True, 30), (True, 100), (False, 100)):
        args = {"trade_date": expected["trade_date"], "limit": limit}
        if highest:
            args["highest_only"] = True
        with registry.anchored():
            gateway = ToolGateway(registry, EvidenceStore())
            validated = gateway.validate({"name": "limit_up_events", "args": args})
            _, payload, state = gateway.execute("limit_up_events", validated)
        rows = payload.get("events", [])
        if state != "ok" or payload.get("matched_count") != payload.get("returned_count") or not rows:
            raise ValueError(f"{case.case_id} highest route is incomplete")
        peak = max(row["board_height"] for row in rows)
        members = {digest(item) for item in _canonical_members([row for row in rows if row["board_height"] == peak])}
        if peak != expected["max_board_height"] or members != identities:
            raise ValueError(f"{case.case_id} highest route changes approved facts")
        routes.append({"tool": "limit_up_events", "arguments": args, "passed": True})
    return routes


def accept_highest_batch(bundle: Path, approval_paths: list[Path], preflight_path: Path,
                         destination: Path, provider, database: Path, case_ids: list[str]):
    from app.agent_eval.worker import GuardedProvider
    from app.services.llm_provider import capture_llm_usage
    if destination.exists():
        raise FileExistsError(destination)
    suite = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
    entries = {item["id"]: item for item in suite["cases"]}
    approvals = approval_index([json.loads(path.read_text(encoding="utf-8")) for path in approval_paths])
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight_cases = {item["case_id"]: item for item in preflight.get("cases", [])}
    prepared, route_cache = [], {}
    for case_id in case_ids:
        if case_id not in entries or preflight_cases.get(case_id, {}).get("verdict") != "pass":
            raise ValueError("highest case absent or not preflighted")
        case, world = load_case(bundle / entries[case_id]["case"]), _baseline(bundle, entries[case_id])
        approved = approvals.get(case_id)
        if (not approved or approved["binding"].get("case_digest") != digest(case.model_dump(mode="json"))
                or approved["binding"].get("baseline_digest") != digest(world.model_dump(mode="json"))
                or _oracle(case, world)["category"] != "highest"):
            raise ValueError("highest approval or oracle is stale")
        routes = _execute_highest_routes(case, world, database, route_cache)
        key = digest(case.assertions[0].expected)
        prepared.append((case, world, approved, routes, key, case.assertions[0].expected))
    unique = {}
    for *_, key, expected in prepared:
        unique.setdefault(key, expected)
    destination.mkdir(parents=True)
    calls = sum(len(highest_samples(expected)) for expected in unique.values())
    budget = BudgetSpec(max_agent_runs=1, max_model_calls=calls, max_input_tokens=500000,
                        max_output_tokens=80000, max_wall_time_seconds=480, max_estimated_cost_usd=None)
    guarded = GuardedProvider(provider, destination, perf_counter() + 480, budget)
    calibration = {}
    with capture_llm_usage() as usage:
        for key, expected in unique.items():
            results = []
            for name, answer, label in highest_samples(expected):
                normalized = {"trade_date": label["trade_date"], "max_board_height": label["max_board_height"],
                              "members": _canonical_members(label["members"]), "ambiguous": False}
                try:
                    extracted = extract_business_answer(guarded, answer)
                    actual = {"trade_date": extracted.trade_date.isoformat() if extracted.trade_date else None,
                              "max_board_height": extracted.max_board_height,
                              "members": _canonical_members([member.model_dump() for member in extracted.members]),
                              "ambiguous": extracted.ambiguous}
                    results.append({"sample": name, "answer": answer, "label": normalized, "actual": actual,
                                    "passed": actual == normalized, "extraction": extracted.model_dump(mode="json")})
                except Exception as error:
                    results.append({"sample": name, "answer": answer, "label": normalized, "passed": False,
                                    "error_type": type(error).__name__})
            calibration[key] = results
    cases = [{"case_id": case.case_id, "case_version": case.case_version,
              "case_digest": digest(case.model_dump(mode="json")),
              "baseline_digest": digest(world.model_dump(mode="json")),
              "approval_digest": approved["approval_digest"], "routes": routes, "calibration_key": key,
              "technical_acceptance": all(item["passed"] for item in calibration[key])}
             for case, world, approved, routes, key, _ in prepared]
    report = {"schema_version": "structured-highest-acceptance-v1", "suite_id": suite["suite_id"],
              "suite_version": suite["version"], "cases": cases, "calibration": calibration,
              "extractor_prompt_digest": digest(BUSINESS_SYSTEM),
              "label_origin": "deterministic variants and counterexamples derived from user-approved highest facts",
              "model": getattr(provider, "model", None), "model_calls": guarded.calls,
              "total_tokens": usage.total_tokens if usage.token_usage_complete else None,
              "technical_acceptance": all(item["technical_acceptance"] for item in cases),
              "active_promotion": False, "release_eligible": False,
              "limitations": ["highest height/member/date core requirements only", "additional claims are excluded"]}
    write_json(destination / "acceptance.json", report)
    return {key: report[key] for key in ("technical_acceptance", "model_calls", "total_tokens")}


def promote_highest_batch(bundle: Path, approval_paths: list[Path], acceptance_path: Path, destination: Path):
    if destination.exists():
        raise FileExistsError(destination)
    suite = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
    entries = {item["id"]: item for item in suite["cases"]}
    approvals = approval_index([json.loads(path.read_text(encoding="utf-8")) for path in approval_paths])
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    if (acceptance.get("schema_version") != "structured-highest-acceptance-v1"
            or (acceptance.get("suite_id"), acceptance.get("suite_version")) != (suite["suite_id"], suite["version"])
            or acceptance.get("technical_acceptance") is not True
            or acceptance.get("extractor_prompt_digest") != digest(BUSINESS_SYSTEM)):
        raise ValueError("highest technical acceptance is incomplete or stale")
    calibration, active_entries = acceptance.get("calibration", {}), []
    for accepted in acceptance.get("cases", []):
        case_id = accepted["case_id"]
        case, world = load_case(bundle / entries[case_id]["case"]), _baseline(bundle, entries[case_id])
        approved, expected = approvals.get(case_id), case.assertions[0].expected
        key = digest(expected)
        if (not approved or accepted.get("technical_acceptance") is not True
                or accepted.get("case_digest") != digest(case.model_dump(mode="json"))
                or accepted.get("baseline_digest") != digest(world.model_dump(mode="json"))
                or accepted.get("approval_digest") != approved["approval_digest"]
                or accepted.get("calibration_key") != key or key not in calibration):
            raise ValueError("highest case approval or binding is stale")
        expected_routes = []
        for highest, limit in ((True, 30), (True, 100), (False, 100)):
            args = {"trade_date": expected["trade_date"], "limit": limit}
            if highest:
                args["highest_only"] = True
            expected_routes.append({"tool": "limit_up_events", "arguments": args, "passed": True})
        if accepted.get("routes") != expected_routes:
            raise ValueError("highest route acceptance is missing or changed")
        samples, results = highest_samples(expected), calibration[key]
        if len(samples) != len(results):
            raise ValueError("highest calibration samples missing")
        for result, (name, answer, label) in zip(results, samples):
            normalized = {"trade_date": label["trade_date"], "max_board_height": label["max_board_height"],
                          "members": _canonical_members(label["members"]), "ambiguous": False}
            if (result.get("sample") != name or result.get("answer") != answer
                    or digest(result.get("label")) != digest(normalized)
                    or digest(result.get("actual")) != digest(normalized) or result.get("passed") is not True):
                raise ValueError("highest calibration failed or labels changed")
        active = case.model_copy(update={"status": "active"})
        folder = destination / case_id
        folder.mkdir(parents=True)
        write_json(folder / "case.json", active.model_dump(mode="json"))
        write_json(folder / ("world.json" if case.mode == "offline" else "baseline.json"), world.model_dump(mode="json"))
        active_entries.append({"case_id": case_id, "case_version": case.case_version, "mode": case.mode,
                               "scope": "highest height, tied members and data date",
                               "active_case_digest": digest(active.model_dump(mode="json")),
                               "baseline_digest": accepted["baseline_digest"],
                               "approval_digest": accepted["approval_digest"], "calibration_key": key})
    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination / "technical-acceptance.json", acceptance)
    manifest = {"schema_version": "active-highest-golden-batch-v1", "suite_id": suite["suite_id"],
                "suite_version": suite["version"], "status": "active", "scope": "highest core requirements",
                "cases": active_entries, "acceptance_digest": digest(acceptance),
                "release_eligible": False, "answer_quality_approved": False,
                "limitations": ["additional statements and full-answer semantics require separate review"]}
    write_json(destination / "manifest.json", manifest)
    return manifest
