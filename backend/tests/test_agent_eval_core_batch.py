"""Batch assets and bounded Live exercise production methods on readonly source rows."""

from datetime import date
from pathlib import Path
import sqlite3

import pytest

from app.agent_eval.core_batch import prepare_core_batch
from app.agent_eval.historical_live import HistoricalDataDrift, HistoricalLiveRegistry
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.frozen_registry import FrozenAgentToolRegistry
from app.repositories.limit_up_repository import SQLiteLimitUpRepository
from app.services.sample_data import SAMPLE_EVENTS

BOOK = Path(__file__).resolve().parents[1] / "evals/blueprints/core40_live48.json"


@pytest.fixture
def batch(tmp_path):
    database = tmp_path / "source.sqlite"
    events = []
    for day, count in ((8,7),(10,2),(11,3)):
        for i in range(count):
            star = day == 8 and i >= 3
            events.append(SAMPLE_EVENTS[0].model_copy(update={
                "trade_date":date(2026,9,day), "symbol":f"{688000+i if star else 600000+i}",
                "name":f"样本{i}", "concept":"样本", "board_height":1 if star else 4,
                "closed_limit":not star or i == 3, "break_count":1 if star else 0}))
    SQLiteLimitUpRepository(database).replace_events(events)
    before = database.read_bytes()
    folder = tmp_path / "assets"
    report = prepare_core_batch(database, BOOK, folder)
    assert database.read_bytes() == before
    return database, folder, report


def test_five_offline_and_one_live_boundaries(batch):
    database, folder, report = batch
    assert report["offline_candidates"] == 5 and report["historical_live_candidates"] == 1
    assert report["recordings"] == 29 and not report["release_eligible"]
    for key in ("OFF-010","OFF-028","OFF-031","OFF-032","OFF-033"):
        case = load_case(folder / key / "case.json")
        world = load_world(folder / key / "world.json")
        FrozenAgentToolRegistry(world)  # Detect duplicate effective signatures and invalid fixtures.
        assert case.status == "candidate" and case.world.id == world.world_id
        assert world.anchor_datetime.day == (13 if key == "OFF-032" else 11)
    empty = load_case(folder / "OFF-033/case.json")
    assert empty.assertions[0].expected["matched_count"] == 0
    live = load_case(folder / "LH-004/case.json")
    assert live.mode == "live_historical" and live.world is None
    with pytest.raises(FileExistsError):
        prepare_core_batch(database, BOOK, folder)


def test_live_real_tool_accepts_unrecorded_arguments_and_does_not_write(batch):
    database, folder, _ = batch
    before = database.read_bytes()
    baseline = load_world(folder / "LH-004/baseline.json")
    registry = HistoricalLiveRegistry(database, baseline)
    assert not hasattr(registry, "execute_frozen_calls")
    assert len(registry.baseline_checks) == 29
    # limit=2 is NOT in the baseline: real method execution must still work.
    result = registry.limit_up_events(trade_date=date(2026,9,11), limit=2)
    assert len(result.output) == 2
    assert database.read_bytes() == before


def test_baseline_drift_stops_before_any_provider_call(batch):
    database, folder, _ = batch
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE limit_up_events SET amount=amount+1")
    with pytest.raises(HistoricalDataDrift):
        HistoricalLiveRegistry(database, load_world(folder / "LH-004/baseline.json"))
