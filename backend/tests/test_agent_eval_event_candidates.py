"""Local event materialization must preserve real production output and abstain."""

from datetime import datetime
import json
from pathlib import Path
import socket

import pytest

from app.agent_eval.event_candidates import prepare_event_candidates
from app.agent_eval.loader import load_case, load_world
from app.agent_eval.recorder import load_capture
from app.repositories.limit_up_repository import SQLiteLimitUpRepository
from app.services.sample_data import SAMPLE_EVENTS


ANCHOR = datetime.fromisoformat("2026-09-11T18:00:00+08:00")
BOOK = Path(__file__).resolve().parents[1] / "evals/blueprints/core40_live48.json"


@pytest.fixture
def database(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("local recording must not use network")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    path = tmp_path / "source.sqlite"
    events = [SAMPLE_EVENTS[0].model_copy(update={
        "symbol": f"{600000 + i:06}", "name": f"Test{i}", "trade_date": ANCHOR.date(),
        "board_height": 1, "closed_limit": True, "amount": float(i + 1) * 1000000,
    }) for i in range(25)]
    SQLiteLimitUpRepository(path).replace_events(events)
    return path


def test_two_candidates_record_full_lists_not_preview(database, tmp_path):
    before = database.read_bytes()
    destination = tmp_path / "batch"
    report = prepare_event_candidates(database, ANCHOR, BOOK, destination)
    assert report["candidate_count"] == 2
    assert not report["release_eligible"]
    for case_id, count in (("OFF-027", 10), ("OFF-038", 20)):
        folder = destination / case_id
        artifact = load_capture(folder / "capture.json")
        world = load_world(folder / "world.json")
        case = load_case(folder / "case.json")
        expected = case.assertions[0].expected["ordered_members"]
        assert [r["symbol"] for r in expected] == [f"{600000 + i:06}" for i in range(24, 24-count, -1)]
        assert len(artifact.body.recording.observation.payload["events"]) == count
        assert len(artifact.body.evidence_view["rows"]) == 8
        assert world.recordings[0] == artifact.body.recording
        assert case.status == "candidate"
        assert case.assertions[0].kind == "answer_matches_observation"
        review = json.loads((folder / "review.json").read_text(encoding="utf-8"))
        assert review["replay"]["passed"] and not review["release_eligible"]
        assert review["review_status"] == "unreviewed"
    assert database.read_bytes() == before
    with pytest.raises(FileExistsError):
        prepare_event_candidates(database, ANCHOR, BOOK, destination)


def test_missing_session_does_not_fabricate_assets(database, tmp_path):
    destination = tmp_path / "missing"
    with pytest.raises(ValueError, match="no local events"):
        prepare_event_candidates(database, ANCHOR.replace(day=12), BOOK, destination)
    assert not destination.exists()


def test_short_results_reject_whole_batch(database, tmp_path):
    # Keep the source genuine but insufficient; never pad expected members.
    import sqlite3
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM limit_up_events WHERE symbol != '600000'")
    destination = tmp_path / "short"
    with pytest.raises(ValueError, match="prerequisites"):
        prepare_event_candidates(database, ANCHOR, BOOK, destination)
    assert not destination.exists()
