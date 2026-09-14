"""JSON authoring and independent oracle tests; synthetic rows are not Golden facts."""

from datetime import date
import json
from pathlib import Path

import pytest

from app.agent_eval.dataset import build_dataset, expected_for, load_recipe, routes, run_dataset
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.selection import complete_day, select_rows
from app.agent_eval.business_facts import _truth
from app.agent_eval.core_batch import LocalResearchRegistry
from app.agent_eval.historical_live import HistoricalLiveRegistry
from app.agent_eval.frozen_registry import FrozenAgentToolRegistry
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.tools import ToolGateway
from app.repositories.limit_up_repository import SQLiteLimitUpRepository
from app.services.sample_data import SAMPLE_EVENTS


RECIPE = Path(__file__).resolve().parents[1] / "evals/suites/local30.json"


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
