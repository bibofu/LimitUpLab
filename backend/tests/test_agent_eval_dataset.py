"""JSON authoring and independent oracle tests; synthetic rows are not Golden facts."""

from datetime import date
import json
from pathlib import Path

import pytest

from app.agent_eval.dataset import build_dataset, expected_for, load_recipe, routes, run_dataset, recheck_dataset
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.selection import complete_day, select_rows
from app.agent_eval.business_facts import _truth
from app.agent_eval.business_facts import BusinessExtraction, identity, verify_business_facts
from app.agent_eval.process_checks import independent_select
from app.agent_eval.recorder import digest
from app.agent_eval.core_batch import LocalResearchRegistry
from app.agent_eval.approval import business_contract
from app.agent_eval.batch_preflight import preflight_batch
from app.agent_eval.historical_live import HistoricalLiveRegistry
from app.agent_eval.frozen_registry import FrozenAgentToolRegistry
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.tools import ToolGateway
from app.repositories.limit_up_repository import SQLiteLimitUpRepository
from app.services.sample_data import SAMPLE_EVENTS


RECIPE = Path(__file__).resolve().parents[1] / "evals/suites/local30.json"


def test_replacement_empty_prerequisite_is_independent_of_recording():
    item = load_recipe(RECIPE.with_name("off033-replacement.json"))["cases"][0]
    expected = expected_for(item, [])
    assert expected["matched_count"] == 0 and expected["members"] == []
    row = {"trade_date": "2026-09-11", "symbol": "688001", "name": "合成", "closed_limit": True}
    with pytest.raises(ValueError, match="empty prerequisite"):
        expected_for(item, [row])


def test_business_contract_excludes_asset_identity_but_not_reviewed_content(dataset):
    _, target, _ = dataset
    case = load_case(target / "OFF-010" / "case.json")
    revised = case.model_copy(deep=True)
    revised.case_version += 1
    revised.world.version += 1
    assert business_contract(case) == business_contract(revised)
    revised.conversation[0].content += "改变问题"
    assert business_contract(case) != business_contract(revised)


def test_batch_preflight_checks_real_asset_bindings_and_oracle(dataset, tmp_path):
    _, target, _ = dataset
    case = load_case(target / "OFF-001" / "case.json")
    world = load_world(target / "world.json")
    approval = {"reviewer": "user_in_current_conversation", "origin": "contract_test",
        "approved_items": ["question_and_business_scope", "expected_business_facts", "scoring_rules"],
        "cases": [{"case_id": case.case_id, "case_version": case.case_version,
                   "case_digest": digest(case.model_dump(mode="json")),
                   "baseline_digest": digest(world.model_dump(mode="json"))}]}
    from app.agent_eval.core_batch import write_json
    approval_path = tmp_path / "approval.json"
    write_json(approval_path, approval)
    report = preflight_batch(target, [approval_path], tmp_path / "preflight", case_ids=["OFF-001"])
    assert report["passed"] and report["counts"] == {"count": 1} and report["model_calls"] == 0
    approval["cases"][0]["case_digest"] = "stale"
    stale = tmp_path / "stale.json"
    write_json(stale, approval)
    blocked = preflight_batch(target, [stale], tmp_path / "blocked", case_ids=["OFF-001"])
    assert not blocked["passed"] and blocked["counts"] == {"blocked": 1}


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    folder = tmp_path_factory.mktemp("local30")
    database = folder / "source.sqlite"
    events = []
    for day in (8, 10, 11):
        for index in range(48):
            symbol = str(600000 + index) if index < 40 else str(300000 + index) if index < 44 else str(688000 + index)
            if index == 0:
                symbol = "002790"
            events.append(SAMPLE_EVENTS[0].model_copy(update={
                "trade_date": date(2026, 9, day), "symbol": symbol, "name": f"合成{index}",
                "concept": "合成", "amount": float(1000 + index),
                "board_height": 4 if index < 3 else 1,
                "closed_limit": index % 5 != 4, "break_count": index % 3}))
    SQLiteLimitUpRepository(database).replace_events(events)
    before = database.read_bytes()
    target = folder / "bundle"
    report = build_dataset(RECIPE, database, target)
    assert before == database.read_bytes()
    return database, target, report


def test_recipe_has_requested_counts_and_distinct_offline_contracts():
    book = load_recipe(RECIPE)
    assert sum(c["id"].startswith("OFF-") for c in book["cases"]) == 20
    assert sum(c["id"].startswith("LH-") for c in book["cases"]) == 10
    contracts = [json.dumps([c["kind"], c.get("day"), c.get("selection"), c.get("anchor_datetime")], sort_keys=True)
                 for c in book["cases"] if c["id"].startswith("OFF-")]
    assert len(set(contracts)) == 20


@pytest.mark.parametrize("selection", [
    {"typo": 1}, {"market": "all"}, {"closed_limit": 1}, {"take": 3},
    {"order_by": "unknown"}, {"min_break_count": -1}, {"take": True, "order_by": "amount"},
    {"descending": "yes", "order_by": "amount"},
])
def test_unknown_or_ambiguous_oracle_semantics_rejected(selection):
    with pytest.raises(ValueError):
        select_rows([], selection)


def test_independent_selection_keeps_reclosure_and_ties():
    rows = [{"symbol": symbol, "name": symbol, "closed_limit": closed, "break_count": breaks,
             "board_height": 1, "amount": amount} for symbol, closed, breaks, amount in
            [("600002", True, 1, 20), ("600001", True, 1, 20), ("600003", False, 2, 30),
             ("300001", True, 0, 40), ("689001", True, 1, 10)]]
    assert [r["symbol"] for r in select_rows(rows, {"closed_limit": True, "market": "main_board", "min_break_count": 1,
            "order_by": "amount", "take": 2})] == ["600001", "600002"]
    assert len(select_rows(rows, {"min_break_count": 1})) == 4
    assert len(select_rows(rows, {"market": "star_market"})) == 1
    assert len(select_rows(rows, {"max_break_count": 0})) == 1


def test_built_cases_oracles_and_review_are_explicit(dataset):
    database, target, report = dataset
    assert report["offline"] == 20 and report["historical_live"] == 10
    assert report["active_golden"] == 0 and not report["release_eligible"]
    manifest = json.loads((target / "suite.json").read_text(encoding="utf-8"))
    world = load_world(target / "world.json")
    assert len(complete_day(world, "2026-09-11")) == 48
    for entry in manifest["cases"]:
        case = load_case(target / entry["case"])
        assert case.status == "candidate" and entry["oracle_crosscheck"]
        assert entry["review_status"] == "pending_human_review"
        assert (case.world is None) == (case.mode == "live_historical")
        for assertion in case.assertions:
            if isinstance(assertion.expected, dict) and "row_selection" in assertion.expected:
                _, members = _truth(assertion.expected, world, {})
                assert [(r["symbol"], r["name"]) for r in members] == [(r["symbol"], r["name"]) for r in assertion.expected["members"]]
    assert "审核：口径" in (target / "REVIEW.md").read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        build_dataset(RECIPE, database, target)


def test_complete_partition_cannot_silently_truncate(dataset):
    _, target, _ = dataset
    world = load_world(target / "world.json")
    world.recordings = [r for r in world.recordings if r.observation.payload.get("event_status") != "failed"]
    with pytest.raises(ValueError, match="both complete"):
        complete_day(world, "2026-09-11")


def test_local_event_pool_uses_typed_production_path_and_blocks_remote(dataset):
    database, target, _ = dataset
    before = database.read_bytes()
    registry = HistoricalLiveRegistry(database, load_world(target / "world.json"))
    gateway = ToolGateway(registry, EvidenceStore())
    with registry.anchored():
        args = gateway.validate({"name": "market_event_pool", "args": {
            "event_type": "limit_up", "trade_date": "2026-09-11", "result_mode": "count"}})
        _, payload, state = gateway.execute("market_event_pool", args)
    assert state == "ok" and payload["matched_count"] > 0 and payload["items"] == []
    assert not hasattr(registry, "execute_frozen_calls")
    with pytest.raises(ValueError, match="remote"):
        registry.market_event_pool(event_type="limit_down")
    assert before == database.read_bytes()


def test_batch_runs_all_cases_without_retry_and_hash_checks(dataset, tmp_path, monkeypatch):
    database, target, _ = dataset
    calls = []
    def fake(case, baseline, destination, **kwargs):
        calls.append((case, kwargs["live_database"]))
        return {"verdict": "unscorable", "total_tokens": None}
    monkeypatch.setattr("app.agent_eval.runner.run_offline", fake)
    result = run_dataset(target, database, tmp_path / "runs")
    assert len(calls) == 30 and sum(db is not None for _, db in calls) == 10
    assert len(result["cases"]) == 30 and not result["token_usage_complete"]
    with pytest.raises(ValueError, match="unknown"):
        run_dataset(target, database, tmp_path / "unknown", case_ids=["NOT-A-CASE"])


def test_name_whitespace_is_not_fuzzy_entity_matching():
    assert identity("002161", "远 望\t谷") == identity("002161", "远望谷")
    assert identity("002161", "远望谷") != identity("002160", "远望谷")
    assert identity("002161", "远望股") != identity("002161", "远望谷")
    assert identity("002161", None) != identity("002161", "远望谷")


def test_independent_compute_predicates_and_counts():
    rows = [{"symbol": "600001", "closed_limit": True, "break_count": 0, "amount": 30},
            {"symbol": "600002", "closed_limit": True, "break_count": 1, "amount": 10},
            {"symbol": "600003", "closed_limit": False, "break_count": 1, "amount": 20}]
    args = {"operation": "select", "filters": [{"field": "closed_limit", "operator": "eq", "value": True},
        {"field": "break_count", "operator": "ge", "value": 0}], "sort_by": "amount", "limit": 1}
    selected, count = independent_select(rows, args)
    assert count == 2 and selected == [rows[0]]
    for damage in ({"field": "closed_limit", "operator": "eq", "value": 1},
                   {"field": "symbol", "operator": "in", "value": ["600001"]}):
        with pytest.raises(ValueError):
            independent_select(rows, {"operation": "select", "filters": [damage]})


def test_pool_metadata_count_and_computed_rows_are_valid_evidence(dataset, monkeypatch, tmp_path):
    from langchain_core.messages import AIMessage, ToolMessage
    from app.agents.react_runtime import runtime
    from app.agents.react_runtime.compliance import ComplianceReview
    from app.services.prompt_security import PromptInjectionAssessment
    from app.models import AgentChatRequest
    from uuid import uuid4
    monkeypatch.setattr(runtime, "review_answer", lambda *a, **k: ComplianceReview(decision="allow", violations=[], reason="synthetic"))
    monkeypatch.setattr(runtime, "review_input", lambda *a, **k: PromptInjectionAssessment(decision="allow", signals=[], reason="synthetic"))
    _, target, _ = dataset
    world = load_world(target / "world.json")
    answer = "合成契约回答，非真实市场结论。"
    for key in ("OFF-031", "OFF-043"):
        case = load_case(target / key / "case.json")
        expected = case.assertions[0].expected
        class Provider:
            count = 0
            def generate_messages(self, messages, tools, **kwargs):
                self.count += 1
                name, args = "market_event_pool", {"trade_date": "2026-09-10", "event_type": "limit_up", "result_mode": "count"}
                if key == "OFF-043":
                    name, args = "limit_up_events", {"trade_date": "2026-09-11", "event_status": "closed", "limit": 100}
                if self.count == 1:
                    return AIMessage(content="", tool_calls=[{"id": "query", "name": name, "args": args}])
                if key == "OFF-043" and self.count in (2, 3):
                    views = [json.loads(m.content) for m in messages if isinstance(m, ToolMessage)]
                    eid = next(v["evidence_id"] for v in reversed(views) if v.get("evidence_id"))
                    name = "compute_result" if self.count == 2 else "read_evidence"
                    args = {"evidence_id": eid, "limit": 100 if self.count == 2 else 30}
                    if self.count == 2:
                        args.update(operation="select", filters=[{"field": "break_count", "operator": "ge", "value": 1}])
                    return AIMessage(content="", tool_calls=[{"id": name, "name": name, "args": args}])
                return AIMessage(content=answer)
        registry = FrozenAgentToolRegistry(world)
        with registry.anchored():
            response = runtime.run(AgentChatRequest(session_id=str(uuid4()), message_id=str(uuid4()), message=case.conversation[0].content), registry, Provider())
        extraction = BusinessExtraction(answer_digest=digest(answer), extractor_version="synthetic", origin="contract_test",
            quote=answer, start=0, trade_date=expected["trade_date"], members=expected.get("members", []),
            inventory_complete=True, inventory_reviewers=["synthetic"], extraction_reviewers=["synthetic"],
            ambiguous=False, limit_up_count=expected.get("limit_up_count"))
        report = verify_business_facts(case, world, response, extraction)
        assert report.verdict == "pass", (key, report.model_dump(), response.answer)
        if key == "OFF-031":
            from app.agent_eval.core_batch import write_json
            mini, runs = tmp_path / "mini", tmp_path / "runs"
            (mini / key).mkdir(parents=True)
            source = runs / key
            source.mkdir(parents=True)
            original = json.loads((target / "suite.json").read_text(encoding="utf-8"))
            original["cases"] = [e for e in original["cases"] if e["id"] == key]
            write_json(mini / "suite.json", original)
            write_json(mini / key / "case.json", case.model_dump(mode="json"))
            write_json(mini / "world.json", world.model_dump(mode="json"))
            for name, value in (("case", case), ("world", world), ("response", response), ("extraction", extraction)):
                write_json(source / (name + ".json"), value.model_dump(mode="json"))
            write_json(source / "source.json", {"case_digest": digest(case.model_dump(mode="json"))})
            write_json(source / "supervisor.json", {"verdict": "unscorable", "primary_cause": "fixture_failure"})
            checked = recheck_dataset(mini, [runs], tmp_path / "rechecked")
            assert checked["fresh_model_calls"] == 0 and checked["core_fact_passes"] == 1
            assert checked["execution_unscorable"] == [key]  # Never hide the original failure.
            case.conversation[0].content += " 不同的问题"
            (source / "case.json").write_text(case.model_dump_json(), encoding="utf-8")
            with pytest.raises(ValueError, match="fresh run"):
                recheck_dataset(mini, [runs], tmp_path / "mismatch")
        if key == "OFF-043":
            extraction.members[0].symbol = "999999"
            assert verify_business_facts(case, world, response, extraction).verdict == "fail"
