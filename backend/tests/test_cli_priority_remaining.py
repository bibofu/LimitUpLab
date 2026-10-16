"""Provider precedence and fallback boundaries without remote data access."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app.agents import tools
from app.collectors import first_board_enrichment_collector as enrichment
from app.collectors.hithink_finance_collector import (
    HithinkDragonTigerSnapshot,
    HithinkFinanceError,
    HithinkHotStockFact,
    HithinkHotStockSnapshot,
)
from app.services import dragon_tiger_review as review


DAY = date(2026, 10, 15)


def ranking(count=30, source="hithink-finance"):
    return HithinkHotStockSnapshot(
        captured_at=datetime.now(timezone.utc), period="day", source=source,
        items=[HithinkHotStockFact(
            symbol=f"{index:06}", thscode=f"{index:06}.SZ", name=f"股票{index}",
            rank=index, heat=None, rank_change=None, rank_trend=None,
        ) for index in range(1, count + 1)],
    )


def registry(collector):
    return tools.AgentToolRegistry(
        [], first_board_repository=object(), hithink_collector=collector,
    )


def test_auto_top100_prefers_ths_and_keeps_its_short_ranking(monkeypatch):
    collector = Mock()
    collector.collect_hot_stocks.return_value = ranking()
    fallback = Mock(side_effect=AssertionError("must not mix rankings"))
    monkeypatch.setattr(tools, "collect_eastmoney_hot_stock_ranking", fallback)

    result = registry(collector).hot_stock_ranking(limit=100)

    collector.collect_hot_stocks.assert_called_once_with(period="day", limit=100)
    assert result.output["source"] == "hithink-finance"
    assert result.output["count"] == 30
    assert result.output["requested_count"] == 100
    assert result.output["complete"] is False
    fallback.assert_not_called()


@pytest.mark.parametrize("empty", [False, True])
def test_auto_day_falls_back_as_one_complete_provider_ranking(monkeypatch, empty):
    collector = Mock()
    if empty:
        collector.collect_hot_stocks.return_value = ranking(0)
    else:
        collector.collect_hot_stocks.side_effect = HithinkFinanceError("offline")
    fallback = Mock(return_value=ranking(100, source="eastmoney"))
    monkeypatch.setattr(tools, "collect_eastmoney_hot_stock_ranking", fallback)

    result = registry(collector).hot_stock_ranking(limit=100)

    assert result.output["source"] == "eastmoney"
    assert result.output["complete"] is True
    assert result.output["count"] == 100
    fallback.assert_called_once()


@pytest.mark.parametrize("source,period", [("tonghuashun", "day"), ("auto", "hour")])
def test_explicit_ths_or_hour_ranking_never_silently_changes_provider(
    monkeypatch, source, period,
):
    collector = Mock()
    collector.collect_hot_stocks.side_effect = HithinkFinanceError("offline")
    fallback = Mock()
    monkeypatch.setattr(tools, "collect_eastmoney_hot_stock_ranking", fallback)
    with pytest.raises(HithinkFinanceError, match="offline"):
        registry(collector).hot_stock_ranking(source=source, period=period)
    fallback.assert_not_called()


def test_explicit_eastmoney_skips_ths(monkeypatch):
    collector = Mock()
    monkeypatch.setattr(tools, "collect_eastmoney_hot_stock_ranking", Mock(
        return_value=ranking(10, source="eastmoney"),
    ))
    result = registry(collector).hot_stock_ranking(source="eastmoney", limit=10)
    assert result.output["source"] == "eastmoney"
    collector.collect_hot_stocks.assert_not_called()


def test_eastmoney_fallback_survives_unavailable_ths_name_directory(monkeypatch):
    monkeypatch.setattr(enrichment, "_fetch_eastmoney_popularity_rows", lambda: (
        datetime.now(timezone.utc), [{"sc": "SZ000001", "rk": 1}],
    ))
    result = enrichment.collect_eastmoney_hot_stock_ranking(
        name_resolver=Mock(side_effect=HithinkFinanceError("CLI unavailable")),
    )
    assert result.items[0].symbol == "000001"
    assert result.items[0].name == "000001"
    assert result.source == "eastmoney"


def fallback_frame():
    return pd.DataFrame([
        {"代码": symbol, "名称": name, "涨跌幅": 9.5,
         "龙虎榜成交额": 400, "龙虎榜买入额": 300, "龙虎榜卖出额": 100,
         "龙虎榜净买额": 200, "上榜原因": "上榜原因"}
        for symbol, name in [("000001", "测试甲"), ("000002", "测试乙")]
    ])


def unavailable_collector():
    collector = Mock()
    collector.collect_dragon_tiger.side_effect = HithinkFinanceError("CLI unavailable")
    return collector


def test_dragon_tiger_dated_all_fallback_preserves_counts_and_missing_fields(monkeypatch):
    fallback = Mock(return_value=fallback_frame())
    monkeypatch.setattr(enrichment, "_fetch_eastmoney_dragon_tiger_frame", fallback)
    result = enrichment.collect_preferred_dragon_tiger_snapshot(
        trade_date=DAY, query="测试", limit=1, collector=unavailable_collector(),
    )
    fallback.assert_called_once_with(DAY)
    assert result.source == "eastmoney"
    assert result.trade_date == DAY
    assert result.stock_count == result.matched_count == 2
    assert len(result.items) == 1
    assert result.source_truncated is True
    assert result.items[0].name == "测试甲"
    assert result.items[0].net_buy_amount == 200
    assert result.items[0].organization_net_buy_amount is None
    assert result.items[0].hot_money_net_buy_amount is None
    assert result.items[0].range_days is None


@pytest.mark.parametrize("day,board", [(None, "all"), (DAY, "org"), (DAY, "hot_money")])
def test_dragon_tiger_does_not_substitute_non_equivalent_queries(monkeypatch, day, board):
    fallback = Mock()
    monkeypatch.setattr(enrichment, "_fetch_eastmoney_dragon_tiger_frame", fallback)
    with pytest.raises(HithinkFinanceError, match="CLI unavailable"):
        enrichment.collect_preferred_dragon_tiger_snapshot(
            trade_date=day, board_type=board, collector=unavailable_collector(),
        )
    fallback.assert_not_called()


def test_successful_empty_ths_dragon_tiger_is_not_an_outage(monkeypatch):
    snapshot = HithinkDragonTigerSnapshot(DAY, "all", 0, [])
    collector = Mock()
    collector.collect_dragon_tiger.return_value = snapshot
    fallback = Mock()
    monkeypatch.setattr(enrichment, "_fetch_eastmoney_dragon_tiger_frame", fallback)
    assert enrichment.collect_preferred_dragon_tiger_snapshot(
        trade_date=DAY, collector=collector,
    ) is snapshot
    fallback.assert_not_called()


def test_wrong_date_ths_dragon_tiger_uses_requested_date_fallback(monkeypatch):
    collector = Mock()
    collector.collect_dragon_tiger.return_value = HithinkDragonTigerSnapshot(
        date(2026, 10, 14), "all", 0, [],
    )
    fallback = Mock(return_value=fallback_frame())
    monkeypatch.setattr(enrichment, "_fetch_eastmoney_dragon_tiger_frame", fallback)
    result = enrichment.collect_preferred_dragon_tiger_snapshot(
        trade_date=DAY, collector=collector,
    )
    assert result.trade_date == DAY
    assert result.source == "eastmoney"
    fallback.assert_called_once_with(DAY)


def test_agent_dragon_tiger_labels_actual_fallback_source(monkeypatch):
    monkeypatch.setattr(enrichment, "_fetch_eastmoney_dragon_tiger_frame", fallback_frame_for_date)
    result = registry(unavailable_collector()).dragon_tiger_list(trade_date=DAY)
    assert result.output["source"] == "eastmoney"
    assert "东方财富" in result.summary
    assert "同花顺" not in result.summary


def fallback_frame_for_date(day):
    assert day == DAY
    return fallback_frame()


def test_dragon_tiger_fallback_preserves_multiple_records_for_one_stock(monkeypatch):
    frame = fallback_frame()
    frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    monkeypatch.setattr(enrichment, "_fetch_eastmoney_dragon_tiger_frame", Mock(return_value=frame))
    snapshot = enrichment.collect_preferred_dragon_tiger_snapshot(
        trade_date=DAY, collector=unavailable_collector(),
    )
    assert snapshot.stock_count == 2
    assert snapshot.matched_count == 3
    assert len(snapshot.items) == 3


def test_dragon_tiger_fallback_rejects_invalid_codes_before_padding(monkeypatch):
    invalid_codes = [None, "", "   ", 0, "000000", "1234567", "abc", "０００００１", -1]
    frame = pd.DataFrame([
        {"代码": code, "名称": "无效行"} for code in invalid_codes
    ] + [
        {"代码": "1", "名称": "补零的股票"},
        {"代码": " 000002 ", "名称": "正常股票"},
    ])
    monkeypatch.setattr(enrichment, "_fetch_eastmoney_dragon_tiger_frame", Mock(return_value=frame))
    snapshot = enrichment.collect_preferred_dragon_tiger_snapshot(
        trade_date=DAY, collector=unavailable_collector(),
    )
    assert snapshot.stock_count == snapshot.matched_count == 2
    assert [item.symbol for item in snapshot.items] == ["000001", "000002"]
    assert [item.name for item in snapshot.items] == ["补零的股票", "正常股票"]


def test_review_page_uses_preferred_collector(monkeypatch):
    monkeypatch.setattr(review, "_review_cache", {})
    load = Mock(return_value=HithinkDragonTigerSnapshot(DAY, "all", 0, [], "eastmoney"))
    monkeypatch.setattr(review, "collect_preferred_dragon_tiger_snapshot", load)
    repo = SimpleNamespace(list_enrichment_for_date=lambda _: [])
    response = review.load_dragon_tiger_review([], trade_date=DAY, repository=repo)
    assert response.source == "eastmoney"
    load.assert_called_once_with(trade_date=DAY, board_type="all", limit=200)


def test_eastmoney_dragon_tiger_keeps_name_and_change(monkeypatch):
    monkeypatch.setattr(enrichment.ak, "stock_lhb_detail_em", lambda **_: pd.DataFrame([
        {"代码": "000001", "名称": "测试甲", "涨跌幅": 9.5,
         "龙虎榜成交额": 400, "龙虎榜买入额": 300, "龙虎榜卖出额": 100,
         "龙虎榜净买额": 200, "上榜原因": "上榜原因"},
    ]))
    result = enrichment.collect_dragon_tiger_facts(DAY)["000001"]
    assert result.name == "测试甲"
    assert result.change_pct == 9.5
    assert result.net_buy_amount == 200
