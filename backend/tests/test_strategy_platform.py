from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.database import connect, initialize_database
from app.agents.chat import answer_first_board_chat
from app.agents.tools import AgentToolRegistry
from app.main import app
from app.routers import strategies as strategy_routes
from app.models import AgentChatRequest
from app.repositories import SQLiteFirstBoardRepository, SQLiteStrategyRepository
from app.services.llm_provider import DisabledLLMProvider
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


def test_strategy_route_contract_handles_empty_history_stock_and_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = SQLiteStrategyRepository(tmp_path / "routes.sqlite")
    empty = _snapshot().model_copy(update={
        "run_id": "relay:test:empty",
        "status": "empty",
        "candidate_count": 0,
        "payload": {"candidates": [], "warnings": []},
    })
    repo.save_if_absent(empty)
    monkeypatch.setattr(strategy_routes, "SQLiteStrategyRepository", lambda: repo)
    monkeypatch.setattr(
        strategy_routes,
        "strategy_stock_path",
        lambda strategy_id, symbol, data_as_of: {"status": "empty", "path": []},
    )
    latest = strategy_routes.get_latest_strategy("relay_one_to_two")
    history = strategy_routes.get_strategy_history("relay_one_to_two", limit=30)
    stock = strategy_routes.get_strategy_stock("relay_one_to_two", "000001")
    assert latest.status == "empty"
    assert history.runs[0].run_id == empty.run_id
    assert stock.candidate is None
    assert stock.path["status"] == "empty"
    with pytest.raises(HTTPException) as caught:
        strategy_routes.get_latest_strategy("unknown")
    assert caught.value.status_code == 404


def test_agent_strategy_catalog_is_registry_grounded_and_non_predictive(tmp_path: Path) -> None:
    response = answer_first_board_chat(
        AgentChatRequest(session_id="strategy-catalog", message="目前有哪些涨停后策略，各自研究什么，成熟度如何？"),
        events=[],
        repository=SQLiteFirstBoardRepository(tmp_path / "agent.sqlite"),
        llm_provider=DisabledLLMProvider(),
    )
    assert response.intent == "strategy_catalog"
    assert response.tool_calls[0] == "strategy_catalog"
    assert all(name in response.answer for name in ("一进二接力", "高位回撤", "横盘缩量", "断板修复", "二进三"))
    assert "前向验证" in response.answer
    assert "探索研究" in response.answer
    assert "预测概率" in response.answer
    assert "低位挖掘" not in response.answer


def test_agent_reads_registered_latest_run_with_version_cutoff_and_anchor(tmp_path: Path) -> None:
    database = tmp_path / "agent-latest.sqlite"
    SQLiteStrategyRepository(database).save_if_absent(_snapshot())
    response = answer_first_board_chat(
        AgentChatRequest(session_id="strategy-latest", message="一进二接力策略最新排名和候选"),
        events=[],
        repository=SQLiteFirstBoardRepository(database),
        llm_provider=DisabledLLMProvider(),
    )
    assert response.intent == "strategy_latest"
    assert "版本 test" in response.answer
    assert "数据截止 2026-09-01" in response.answer
    assert "涨停锚点 2026-09-01" in response.answer
    assert "前向验证" in response.answer
    assert "研究排名" in response.answer


def test_strategy_comparison_refuses_ranking_when_samples_are_not_comparable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_statistics(strategy_id: str, *, data_as_of, days: int):
        return {
            "status": "ready",
            "candidate_count": 4,
            "run_count": 2,
            "signal_dates": ["2026-09-01", "2026-09-02"],
            "sample_quality": "insufficient",
        }

    monkeypatch.setattr("app.agents.tools.build_strategy_statistics", fake_statistics)
    registry = AgentToolRegistry(
        events=[],
        first_board_repository=SQLiteFirstBoardRepository(tmp_path / "compare.sqlite"),
    )
    result = registry.strategy_statistics(["一进二", "高位回撤"])
    assert result.output["comparison_allowed"] is False
    assert len(result.output["entries"]) == 2
    assert "拒绝给出策略优劣结论" in result.output["comparison_warning"]
    response = answer_first_board_chat(
        AgentChatRequest(session_id="strategy-compare", message="比较一进二策略和高位回撤策略哪个更好，看历史样本"),
        events=[],
        repository=registry.first_board_repository,
        llm_provider=DisabledLLMProvider(),
    )
    assert response.intent == "strategy_statistics"
    assert "样本 4" in response.answer
    assert "完整度" in response.answer
    assert "拒绝给出策略优劣结论" in response.answer
