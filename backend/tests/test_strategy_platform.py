from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.database import connect, initialize_database
from app.main import app
from app.repositories import SQLiteStrategyRepository
from app.services.strategy_catalog import STRATEGY_IDS, list_strategy_definitions
from app.strategy_models import StrategyRunSnapshot


def _snapshot(*, score: int = 80, anchor_date: str = "2026-09-01") -> StrategyRunSnapshot:
    return StrategyRunSnapshot(
        run_id="relay:test:2026-09-01",
        strategy_id="relay_one_to_two",
        strategy_version="test",
        signal_date=date(2026, 9, 1),
        data_as_of=date(2026, 9, 1),
        generated_at=datetime(2026, 9, 1, 8, tzinfo=timezone.utc),
        input_fingerprint=f"fingerprint-{score}",
        status="ready",
        maturity="forward_validation",
        output_type="ranked_research",
        candidate_count=1,
        payload={
            "candidates": [{
                "symbol": "000001", "name": "测试", "anchor_date": anchor_date,
                "rank": 1, "score": score,
            }]
        },
    )


def test_strategy_catalog_has_fixed_order_and_output_contracts() -> None:
    definitions = list_strategy_definitions()
    assert tuple(item.strategy_id for item in definitions) == STRATEGY_IDS
    assert definitions[0].output_type == "ranked_research"
    assert definitions[0].maturity == "forward_validation"
    assert all(item.output_type == "observation_pool" for item in definitions[1:])
    assert all(item.maturity == "exploratory" for item in definitions[1:])


def test_strategy_snapshot_is_immutable_and_requires_non_future_anchor(tmp_path: Path) -> None:
    repo = SQLiteStrategyRepository(tmp_path / "strategy.sqlite")
    first = repo.save_if_absent(_snapshot(score=80))
    replay = repo.save_if_absent(_snapshot(score=99))
    assert first.payload["candidates"][0]["score"] == 80
    assert replay.payload["candidates"][0]["score"] == 80
    with pytest.raises(ValueError, match="non-future"):
        repo.save_if_absent(_snapshot(anchor_date="2026-09-02"))


def test_outcomes_use_exact_market_dates_and_leave_missing_d5_pending(tmp_path: Path) -> None:
    database = tmp_path / "strategy.sqlite"
    repo = SQLiteStrategyRepository(database)
    run = repo.save_if_absent(_snapshot())
    connection = connect(database)
    try:
        initialize_database(connection)
        for index, day in enumerate(("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04")):
            connection.execute(
                """
                INSERT INTO stock_daily_bars
                (symbol,trade_date,open,high,low,close,volume,amount,change_pct,source,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                ("000001", day, 10 + index, 11 + index, 9 + index, 10.5 + index,
                 1000, 10000, 1, "test", datetime.now(timezone.utc).isoformat()),
            )
        connection.commit()
    finally:
        connection.close()

    assert repo.backfill_outcomes() == 1
    connection = connect(database)
    try:
        row = connection.execute(
            "SELECT * FROM strategy_outcomes WHERE run_id=?", (run.run_id,)
        ).fetchone()
        assert row["d1_ready"] == 1
        assert row["d3_ready"] == 1
        assert row["d5_ready"] == 0
        assert row["d5_open_to_close_pct"] is None
    finally:
        connection.close()


def test_public_strategy_routes_replace_legacy_consolidation_route() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/strategies" in paths
    assert "/api/strategies/{strategy_id}/latest" in paths
    assert "/api/strategies/{strategy_id}/history" in paths
    assert "/api/strategies/{strategy_id}/stocks/{symbol}" in paths
    assert "/api/strategies/{strategy_id}/statistics" in paths
    assert "/api/strategies/consolidation" not in paths
    assert "/api/agents/first-board-discovery" not in paths
