"""Current provider changes never prove a previous-trading-day comparison."""

import json
import subprocess
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.agents import tools
from app.collectors import first_board_enrichment_collector as enrichment
from app.collectors.hithink_finance_collector import (
    HithinkFinanceCollector,
    HithinkHotStockFact,
    HithinkHotStockSnapshot,
)


@pytest.mark.parametrize("source", ["hithink-finance", "eastmoney"])
@pytest.mark.parametrize("source_change", [None, 0, 5, -3])
def test_current_ranking_keeps_source_change_without_inventing_previous_day(
    monkeypatch, source, source_change,
):
    snapshot = HithinkHotStockSnapshot(
        captured_at=datetime.now(timezone.utc), period="day", source=source,
        items=[HithinkHotStockFact(
            symbol="000001", thscode="000001.SZ", name="测试股票", rank=1,
            heat=100, rank_change=source_change, rank_trend=None,
        )],
    )
    collector = Mock()
    collector.collect_hot_stocks.return_value = snapshot
    monkeypatch.setattr(tools, "collect_eastmoney_hot_stock_ranking", Mock(return_value=snapshot))
    registry = tools.AgentToolRegistry(
        [], first_board_repository=object(), hithink_collector=collector,
    )

    result = registry.hot_stock_ranking(
        limit=1, source="eastmoney" if source == "eastmoney" else "tonghuashun",
    )

    row = result.output["items"][0]
    assert row["rank"] == 1
    assert row["heat"] == 100
    assert row["rank_change"] == source_change
    assert row["previous_trade_day_rank"] is None
    assert row["previous_trade_day_rank_change"] is None
    assert result.output["rank_change_reference"] == "provider_defined"
    assert result.output["rank_change_comparison_date"] is None
    assert result.output["previous_trade_day"] is None
    assert "数据未成功获取" in result.output["field_availability"]["previous_trade_day_rank_change"]
    # The requested current ranking is complete; an optional unqueried
    # comparison does not downgrade unrelated current-rank answers.
    assert result.output["complete"] is True
    assert not result.output.get("data_missing")


@pytest.mark.parametrize("source_change", [None, 0, 5, -3])
def test_ths_normalization_distinguishes_missing_and_real_zero(source_change):
    source_row = {
        "thscode": "000001.SZ", "ticker": "000001", "name": "测试股票",
        "rank": 1, "heat": 100,
    }
    if source_change is not None:
        source_row["rank_change"] = source_change

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"ok": True, "data": {"item": [source_row]}}), "",
        )

    snapshot = HithinkFinanceCollector(executable="hithink-finance", runner=runner).collect_hot_stocks(limit=1)

    assert snapshot.items[0].rank_change == source_change
    assert snapshot.items[0].previous_trade_day_rank is None
    assert snapshot.items[0].previous_trade_day_rank_change is None


@pytest.mark.parametrize("previous_rank,change,trend", [
    (None, None, None),
    (0, None, None),
    (2, 0, "flat"),
    (7, 5, "up"),
    (1, -1, "down"),
])
def test_eastmoney_unknown_baseline_does_not_become_flat(
    monkeypatch, previous_rank, change, trend,
):
    row = {"sc": "SZ000001", "rk": 2}
    if previous_rank is not None:
        row["hisRc"] = previous_rank
    monkeypatch.setattr(enrichment, "_fetch_eastmoney_popularity_rows", lambda: (
        datetime.now(timezone.utc), [row],
    ))

    snapshot = enrichment.collect_eastmoney_hot_stock_ranking(
        limit=1, name_resolver=lambda: {"000001": "测试股票"},
    )

    assert snapshot.items[0].rank == 2
    assert snapshot.items[0].rank_change == change
    assert snapshot.items[0].rank_trend == trend
