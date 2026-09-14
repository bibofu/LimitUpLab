"""Build JSON-led local datasets; never manufacture business approval or release passes."""

from datetime import date, datetime
import json
from pathlib import Path

from app.agent_eval.core_batch import LocalResearchRegistry, write_json
from app.agent_eval.frozen_registry import FrozenAgentToolRegistry
from app.agent_eval.local_capture import read_local_session
from app.agent_eval.models import CalendarSpec, CaseSpec, WorldSpec
from app.agent_eval.recorder import capture_tool, digest, save_capture, verify_replay
from app.agent_eval.selection import complete_day, select_rows
from app.agents.tools import AgentToolRegistry


def load_recipe(path):
    book = json.loads(path.read_text(encoding="utf-8"))
    ids = [c["id"] for c in book["cases"]]
    if len(set(ids)) != len(ids) or len({c["question"] for c in book["cases"]}) != len(ids):
        raise ValueError("duplicate case identity or question")
    for item in book["cases"]:
        if item["kind"] not in {"summary", "highest", "count", "empty", "selection", "clarify", "refuse"}:
            raise ValueError("unknown case kind")
        if not item["id"].startswith(("OFF-", "LH-")):
            raise ValueError("only offline and historical live are supported")
        if item["kind"] == "selection":
            select_rows([], item["selection"])
        if "expected_empty" in item and (type(item["expected_empty"]) is not bool or item["kind"] != "selection"):
            raise ValueError("expected_empty is a boolean selection prerequisite")
        if item["kind"] not in {"clarify", "refuse"} and item["day"] not in book["dates"]:
            raise ValueError("case day outside recorded sessions")
    return book


def routes(item):
    """Recording requests, NOT required trajectories. Broad retrieval stays valid."""
    kind = item["kind"]
    if kind in {"summary", "clarify", "refuse"}:
        return []
    base = {"trade_date": item["day"]}
    if kind == "highest":
        base["highest_only"] = True
    elif kind == "empty":
        base["query"] = "评测不存在主题"
    elif kind == "selection":
        selection = item["selection"]
        base.update({k: selection[k] for k in ("market", "board_height", "min_board_height") if k in selection})
        if "symbol" in selection:
            base["query"] = selection["symbol"]
        if selection.get("closed_limit") is False:
            base["event_status"] = "failed"
        elif selection.get("min_break_count", 0) > 0:
            base["event_status"] = "broken_intraday"
        if "order_by" in selection:
            base.update(sort_by=selection["order_by"], sort_order="desc" if selection.get("descending", True) else "asc")
    limits = {30, 50, 100}
    if item.get("selection", {}).get("take"):
        limits.add(item["selection"]["take"])
    result = []
    for limit in sorted(limits):
        for explicit_closed in (False, True):
            args = {**base, "limit": limit}
            if explicit_closed:
                args["closed_only"] = True
            result.append(("limit_up_events", args))
            if "event_status" not in args:
                result.append(("limit_up_events", {**args, "event_status": "closed"}))
            elif args["event_status"] == "broken_intraday":
                alternative = {**args, "broken_only": True}
                alternative.pop("event_status")
                result.append(("limit_up_events", alternative))
    return result


def expected_for(item, rows):
    day, kind = item.get("day"), item["kind"]
    if kind in {"clarify", "refuse"}:
        return None
    day_rows = [r for r in rows if r["trade_date"] == day]
    closed = [r for r in day_rows if bool(r["closed_limit"])]
    expected = {"trade_date": day}
    if kind in {"count", "summary"}:
        return {**expected, "limit_up_count": len(closed)}
    if kind == "empty":
        if any("评测不存在主题" in str(r.get(k, "")) for r in closed for k in ("symbol", "name", "industry", "concept")):
            raise ValueError("empty prerequisite no longer holds")
        return {**expected, "matched_count": 0, "members": []}
    if kind == "highest":
        if not closed:
            raise ValueError("highest case has no closed rows")
        height = max(r["board_height"] for r in closed)
        selected = [r for r in closed if r["board_height"] == height]
        expected["max_board_height"] = height
    else:
        selected = select_rows(day_rows, item["selection"])
        if item.get("expected_empty"):
            if selected:
                raise ValueError("expected empty prerequisite no longer holds")
            expected["matched_count"] = 0
        elif not selected or ("take" in item["selection"] and len(selected) != item["selection"]["take"]):
            raise ValueError("selection lacks required nonempty/top-N sample: " + item["id"])
        expected.update(row_selection=item["selection"], ordered="order_by" in item["selection"])
    expected["members"] = [{"symbol": r["symbol"], "name": r["name"]} for r in selected]
    return expected


def build_dataset(recipe: Path, database: Path, destination: Path):
    if destination.exists():
        raise FileExistsError(destination)
    book = load_recipe(recipe)
    anchor = datetime.fromisoformat(book["anchor_datetime"])
    rows, events = [], []
    for value in book["dates"]:
        day = date.fromisoformat(value)
        _, loaded_rows, loaded_events = read_local_session(database, anchor.replace(year=day.year, month=day.month, day=day.day))
        rows.extend(loaded_rows)
        events.extend(loaded_events)
    # SQLite booleans are integers; preserve strict boolean semantics in the oracle.
    rows = [{**r, "closed_limit": bool(r["closed_limit"])} for r in rows]
    expected = {item["id"]: expected_for(item, rows) for item in book["cases"]}
    registry = LocalResearchRegistry(events)
    calendar = CalendarSpec(id="observed-sessions-not-complete-calendar", version=1,
        start_date=min(e.trade_date for e in events), end_date=date(2026, 9, 13),
        trading_dates=sorted({e.trade_date for e in events}))
    source = {"source": "readonly-local-events", "selected_rows_digest": digest(rows),
              "dates": book["dates"], "recipe_digest": digest(book),
              "limitations": ["observed sessions only; no N-trading-day arithmetic", "future outcome fields are placeholders, not mature outcomes"]}
    requests = [("market_summary", {"include_limit_down": False})]
    for day in book["dates"]:
        for status in ("closed", "failed", "all"):
            for limit in (30, 50, 100):
                base = {"trade_date": day, "event_status": status, "limit": limit}
                requests += [("limit_up_events", base), ("limit_up_events", {**base, "closed_only": status == "closed"})]
        # Complete broad retrieval and explicit default ordering are legitimate routes.
        requests += routes({"kind": "count", "day": day})
        requests += [("limit_up_events", {"trade_date": day, "limit": 100, "sort_by": "board_height", "sort_order": "desc"})]
        for event_type in ("limit_up", "broken_board"):
            for mode in ("count", "list"):
                for limit in (30, 100):
                    requests.append(("market_event_pool", {"trade_date": day, "event_type": event_type, "result_mode": mode, "limit": limit}))
        for limit in (30, 50, 100):
            requests.append(("market_event_pool", {"trade_date": day, "event_type": "limit_up", "query": "评测不存在主题", "result_mode": "count", "limit": limit}))
    for item in book["cases"]:
        requests.extend(routes(item))
    # Reviewed recipe-level alternate routes are always executed against the real tool.
    # No response dictionaries or synthetic empty fallbacks are accepted here.
    requests.extend((r["tool"], r["arguments"]) for r in book.get("additional_routes", []))
    captures, seen, canonicalizer = [], set(), None
    world = None
    for tool, args in requests:
        signature = (tool, digest(canonicalizer._arguments(tool, args))) if canonicalizer else None
        if signature in seen:
            continue
        capture = capture_tool(registry, tool=tool, arguments=args, anchor_datetime=anchor,
            recording_id=f"route-{len(captures):03d}", provenance="production Gateway recording over readonly local events", source_manifest=source)
        if not verify_replay(capture, calendar=calendar, latest_local_trade_date=max(e.trade_date for e in events))["passed"]:
            raise ValueError("record/replay mismatch")
        captures.append(capture)
        if world is None:
            world = WorldSpec(world_id=book["suite_id"], world_version=book["version"], profile=registry.profile,
                anchor_datetime=anchor, latest_local_trade_date=max(e.trade_date for e in events), trading_calendar=calendar,
                tool_contract_version=capture.body.tool_contract_version, evidence_version=capture.body.evidence_version,
                recordings=[capture.body.recording])
            canonicalizer = FrozenAgentToolRegistry(world)
            signature = (tool, digest(canonicalizer._arguments(tool, args)))
        seen.add(signature)
    world.recordings = [c.body.recording for c in captures]
    FrozenAgentToolRegistry(world)
    # Independent raw-row oracle versus complete actual tool partitions, before publication.
    for day in book["dates"]:
        observed = complete_day(world, day)
        raw = [r for r in rows if r["trade_date"] == day]
        fields = ("symbol", "name", "board_height", "closed_limit", "break_count", "amount")
        project = lambda items: sorted([tuple(r[k] for k in fields) for r in items])
        if project(observed) != project(raw):
            raise ValueError("tool partition does not match independent source")
    for item in book["cases"]:
        if item["kind"] == "selection":
            selected = select_rows(complete_day(world, item["day"]), item["selection"])
            if [{"symbol": r["symbol"], "name": r["name"]} for r in selected] != expected[item["id"]]["members"]:
                raise ValueError("independent oracle disagrees with actual observation")
    destination.mkdir(parents=True)
    (destination / "recordings").mkdir()
    for capture in captures:
        save_capture(capture, destination / "recordings" / (capture.body.recording.id + ".json"))
    write_json(destination / "world.json", world.model_dump(mode="json"))
    weekend = world.model_copy(deep=True)
    weekend.world_id += "-weekend"
    weekend.anchor_datetime = datetime.fromisoformat("2026-09-13T18:00:00+08:00")
    FrozenAgentToolRegistry(weekend)
    write_json(destination / "world-weekend.json", weekend.model_dump(mode="json"))
    offline_count = sum(c["id"].startswith("OFF-") for c in book["cases"])
    live_count = len(book["cases"]) - offline_count
    entries, review = [], ["# " + book["suite_id"] + " 批量审核", "", f"{offline_count} Offline + {live_count} Historical Live。以下是待审核题目，不是已批准的 Active Golden。",
        "", "标准事实已由只读原始行独立计算，并与真实工具完整分区交叉核对；这不代替人工业务审核。",
        "", "逐题审核问题口径、标准事实、终态及判分规则。名单不要求固定调用顺序；允许完整取数后过滤。",
        "金额排序题检验顺序，其余名单检验完整集合、代码名称和日期。额外事实、澄清/拒答语义仍需审核。", ""]
    for item in book["cases"]:
        key, kind = item["id"], item["kind"]
        is_live = key.startswith("LH-")
        selected_world = weekend if item.get("anchor_datetime") else world
        assertions = []
        if kind in {"clarify", "refuse"}:
            for schema in AgentToolRegistry.schemas(registry):
                assertions.append({"id": "no-" + schema.name, "evaluator": "trajectory", "kind": "tool_forbidden", "target": schema.name, "requirement_id": "delivery"})
            assertions.append({"id": "semantics", "evaluator": "safety", "kind": "disclosure_required", "target": "answer", "expected": item["rubric"], "requirement_id": "delivery"})
        else:
            assertions.append({"id": "facts", "evaluator": "fact", "kind": "fact_supported",
                "target": "answer" if kind == "summary" else "answer.business_contract", "expected": expected[key], "requirement_id": "delivery"})
        if kind == "summary":
            assertions.append({"id": "local-only", "evaluator": "trajectory", "kind": "argument_equals", "target": "market_summary.include_limit_down", "expected": False, "requirement_id": "delivery"})
        terminal = [kind] if kind in {"clarify", "refuse"} else ["complete", "empty"] if kind == "empty" or item.get("expected_empty") else ["complete"]
        case = CaseSpec.model_validate({"case_id": key, "case_version": book["case_version"], "profile": registry.profile,
            "mode": "live_historical" if is_live else "offline", "severity": "P1", "status": "candidate",
            "capabilities": item["capabilities"], "world": None if is_live else {"id": selected_world.world_id, "version": selected_world.world_version},
            "conversation": [{"role": "user", "content": item["question"]}],
            "expected_requirements": [{"id": "delivery", "description": item["question"], "source_turn": 0, "source_text": item["question"]}],
            "assertions": assertions, "expected_terminal": {"allowed_status": terminal, "missing_requirement_ids": []}})
        folder = destination / key
        folder.mkdir()
        write_json(folder / "case.json", case.model_dump(mode="json"))
        entry = {"id": key, "mode": case.mode, "case": key + "/case.json",
                 "baseline" if is_live else "world": "world-weekend.json" if selected_world is weekend else "world.json",
                 "case_digest": digest(case.model_dump(mode="json")), "baseline_digest": digest(selected_world.model_dump(mode="json")),
                 "review_status": "pending_human_review", "oracle_crosscheck": True}
        entries.append(entry)
        review += [f"## {key}", "", item["question"], "", "考点：" + "、".join(item["capabilities"]),
                   "", "允许终态：" + "/".join(terminal), "", "```json", json.dumps(expected[key] if expected[key] is not None else {"rubric": item["rubric"]}, ensure_ascii=False, indent=2), "```", "", "审核：口径 □ / 标准事实 □ / 判分规则 □", ""]
    manifest = {"suite_id": book["suite_id"], "version": book["version"], "source": source,
        "offline": sum(e["mode"] == "offline" for e in entries), "historical_live": sum(e["mode"] == "live_historical" for e in entries),
        "recordings": len(captures), "active_golden": 0, "release_eligible": False, "cases": entries}
    write_json(destination / "suite.json", manifest)
    with (destination / "REVIEW.md").open("x", encoding="utf-8") as handle:
        handle.write("\n".join(review))
    return {k: manifest[k] for k in ("suite_id", "offline", "historical_live", "recordings", "active_golden", "release_eligible")}


def run_dataset(bundle, database, destination, *, case_ids=None):
    """Serial fresh worker per case; retain failures, never retry into a pass."""
    from app.agent_eval.runner import run_offline
    manifest = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
    selected = manifest["cases"]
    if case_ids is not None:
        if set(case_ids) - {e["id"] for e in selected}:
            raise ValueError("unknown requested case IDs")
        selected = [e for e in selected if e["id"] in case_ids]
    destination.mkdir(parents=True, exist_ok=False)
    results = []
    for entry in selected:
        case_path = bundle / entry["case"]
        baseline_path = bundle / entry.get("baseline", entry.get("world"))
        for path, checksum in ((case_path, entry["case_digest"]), (baseline_path, entry["baseline_digest"])):
            if digest(json.loads(path.read_text(encoding="utf-8"))) != checksum:
                raise ValueError("bundle changed after construction")
        result = run_offline(case_path, baseline_path, destination / entry["id"],
                             live_database=database if entry["mode"] == "live_historical" else None)
        results.append({"id": entry["id"], "mode": entry["mode"], **result})
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    report = {"cases": results, "release_eligible": False, "total_tokens": sum(r.get("total_tokens") or 0 for r in results),
              "token_usage_complete": all(r.get("total_tokens") is not None for r in results)}
    write_json(destination / "batch-results.json", report)
    return report


def recheck_dataset(bundle, run_roots, destination):
    """Re-evaluate retained answers without model calls; preserve execution provenance."""
    from app.agent_eval.loader import load_case, load_world
    from app.agent_eval.business_facts import BusinessExtraction, verify_business_facts
    from app.agent_eval.evaluators import evaluate_trajectory_terminal
    from app.agent_eval.extractor import Extraction
    from app.agent_eval.facts import verify_summary_facts
    from app.agent_eval.process_checks import evaluate_process
    from app.models import AgentChatResponse
    manifest = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
    destination.mkdir(parents=True, exist_ok=False)
    entries = []
    markdown = ["# Local30 实跑与复核", "", "这是保留回答的重新判分，不是新增模型运行，也不是人工批准。",
        "旧回答只在问题、断言、交付要求、Profile 和时间锚点不变时复用；改题必须重新实跑。",
        "原始运行的失败归因保留。核心事实诊断 pass 不代表所有额外声明或整个回答通过。", "",
        f"[逐题口径与标准事实审核]({(bundle / 'REVIEW.md').resolve().as_posix()})", "",
        "| 题目 | 模式 | 实际终态 | 终态检查 | 核心事实诊断 | 过程诊断 | 执行归因 | 回答来源 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for entry in manifest["cases"]:
        case = load_case(bundle / entry["case"])
        world = load_world(bundle / entry.get("baseline", entry.get("world")))
        if digest(case.model_dump(mode="json")) != entry["case_digest"] or digest(world.model_dump(mode="json")) != entry["baseline_digest"]:
            raise ValueError("current bundle checksum mismatch")
        source = next((root / entry["id"] for root in reversed(run_roots) if (root / entry["id"]).exists()), None)
        if source is None:
            raise ValueError("case has no retained run: " + entry["id"])
        old_case = load_case(source / "case.json")
        old_world = load_world(source / ("baseline.json" if case.mode == "live_historical" else "world.json"))
        exclude = {"case_version", "world", "status"}
        if case.model_dump(exclude=exclude) != old_case.model_dump(exclude=exclude):
            raise ValueError("changed question/requirements need a fresh run: " + entry["id"])
        if (world.anchor_datetime, world.latest_local_trade_date, world.trading_calendar) != (
            old_world.anchor_datetime, old_world.latest_local_trade_date, old_world.trading_calendar):
            raise ValueError("changed temporal context needs a fresh run")
        provenance = json.loads((source / "source.json").read_text(encoding="utf-8"))
        if provenance["case_digest"] != digest(old_case.model_dump(mode="json")):
            raise ValueError("source run case checksum mismatch")
        response = AgentChatResponse.model_validate_json((source / "response.json").read_text(encoding="utf-8"))
        supervisor = json.loads((source / "supervisor.json").read_text(encoding="utf-8"))
        trajectory = evaluate_trajectory_terminal(case, response, profile=case.profile)
        process = evaluate_process(case, response)
        facts = None
        if any(a.evaluator == "fact" for a in case.assertions):
            business = any(a.target == "answer.business_contract" for a in case.assertions)
            extraction_type = BusinessExtraction if business else Extraction
            extraction_path = source / "extraction.json"
            extraction = extraction_type.model_validate_json(extraction_path.read_text(encoding="utf-8")) if extraction_path.exists() else None
            verifier = verify_business_facts if business else verify_summary_facts
            facts = verifier(case, world, response, extraction, diagnostic_unreviewed=True)
        fact_finding = next((f.verdict for f in facts.findings if f.assertion_id == "facts"), "needs_review") if facts else "not_applicable"
        terminal = next(f.verdict for f in trajectory.findings if f.assertion_id == "$terminal")
        row = {"id": case.case_id, "mode": case.mode, "case_version": case.case_version,
            "source_case_version": old_case.case_version, "source_run": str(source.resolve()),
            "response_digest": digest(response.model_dump(mode="json")), "source_case_digest": provenance["case_digest"],
            "target_case_digest": entry["case_digest"], "target_baseline_digest": entry["baseline_digest"],
            "fresh_model_call": False, "execution_verdict": supervisor["verdict"], "execution_cause": supervisor.get("primary_cause"),
            "actual_terminal": response.task_status, "terminal_check": terminal, "core_fact_diagnostic": fact_finding,
            "process": process.model_dump(mode="json"), "trajectory": trajectory.model_dump(mode="json"),
            "facts": facts.model_dump(mode="json") if facts else None, "review_status": "pending_human_review", "release_eligible": False}
        entries.append(row)
        write_json(destination / (case.case_id + ".json"), row)
        markdown.append(f"| {case.case_id} | {case.mode} | {response.task_status} | {terminal} | {fact_finding} | {process.verdict} | {supervisor.get('primary_cause') or '—'} | [原始回答]({(source / 'response.json').resolve().as_posix()}) |")
    report = {"cases": entries, "fresh_model_calls": 0, "release_eligible": False,
        "terminal_failures": [e["id"] for e in entries if e["terminal_check"] == "fail"],
        "core_fact_passes": sum(e["core_fact_diagnostic"] == "pass" for e in entries),
        "execution_unscorable": [e["id"] for e in entries if e["execution_verdict"] == "unscorable"]}
    write_json(destination / "recheck.json", report)
    with (destination / "README.md").open("x", encoding="utf-8") as handle:
        handle.write("\n".join(markdown) + "\n")
    return {k: v for k, v in report.items() if k != "cases"}
