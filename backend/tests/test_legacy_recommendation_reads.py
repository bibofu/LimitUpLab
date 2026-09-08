"""Older production snapshots can contain retired discovery items; never rewrite them on read."""
import json
import sqlite3

import pytest
from pydantic import ValidationError

from app.database import connect, initialize_database
from app.models import RecommendationIntelligenceResponse
from app.repositories.recommendation_intelligence_repository import (
    RETIRED_DISCOVERY_WARNING,
    SQLiteRecommendationIntelligenceRepository,
    _load_persisted_response,
)


def legacy_payload(*, discovery_only=False):
    item = {"strategy": "relay", "base_trade_date": "2026-09-07", "symbol": "600001",
            "name": "测试", "rank": 1, "base_score": 80, "refreshed_at": "2026-09-07T16:00:00+00:00"}
    return {"refresh_id": "legacy", "refreshed_at": "2026-09-07T16:00:00+00:00",
            "interval_minutes": 30, "status": "complete", "items": [
                {**item, "strategy": "discovery"}, *([] if discovery_only else [item]),
            ]}


@pytest.mark.parametrize("table", ["recommendation_intelligence_current", "recommendation_intelligence_snapshots", "recommendation_prediction_finals"])
def test_legacy_reads_preserve_original_json_and_expose_only_relay(tmp_path, table):
    path = tmp_path / "legacy.sqlite"
    raw = json.dumps(legacy_payload())
    with connect(path) as connection:
        initialize_database(connection)
        if table.endswith("_current"):
            connection.execute(f"INSERT INTO {table} (slot,refresh_id,refreshed_at,status,response_json) VALUES (1,'legacy','2026-09-07','complete',?)", (raw,))
        elif table.endswith("_snapshots"):
            connection.execute(f"INSERT INTO {table} (refresh_id,refreshed_at,status,response_json) VALUES ('legacy','2026-09-07','complete',?)", (raw,))
        else:
            connection.execute(f"INSERT INTO {table} (target_trade_date,finalized_at,response_json) VALUES ('2026-09-08','2026-09-08',?)", (raw,))
    repository = SQLiteRecommendationIntelligenceRepository(path)
    response = repository.get_latest_displayable()
    assert [item.strategy for item in response.items] == ["relay"]
    assert RETIRED_DISCOVERY_WARNING in response.warnings
    if table.endswith("_finals"):
        assert repository.get_final("2026-09-08") == response
    with sqlite3.connect(path) as connection:
        assert connection.execute(f"SELECT response_json FROM {table}").fetchone()[0] == raw


def test_refresh_archives_original_mixed_snapshot(tmp_path):
    path = tmp_path / "legacy.sqlite"
    raw = json.dumps(legacy_payload(discovery_only=True))
    with connect(path) as connection:
        initialize_database(connection)
        connection.execute("INSERT INTO recommendation_intelligence_current (slot,refresh_id,refreshed_at,status,response_json) VALUES (1,'legacy','2026-09-07','complete',?)", (raw,))
    repository = SQLiteRecommendationIntelligenceRepository(path)
    assert repository.get_latest_displayable().items == []
    payload = legacy_payload()
    payload.update(refresh_id="new", items=[payload["items"][1]])
    repository.save(RecommendationIntelligenceResponse.model_validate(payload))
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT response_json FROM recommendation_intelligence_snapshots WHERE refresh_id='legacy'").fetchone()[0] == raw
    assert repository.get_latest().refresh_id == "new"


def test_unknown_strategy_is_not_silently_hidden():
    payload = legacy_payload()
    payload["items"][0]["strategy"] = "unknown-strategy"
    with pytest.raises(ValidationError):
        _load_persisted_response(json.dumps(payload))


def test_public_model_still_rejects_retired_strategy():
    with pytest.raises(ValidationError):
        RecommendationIntelligenceResponse.model_validate(legacy_payload())
