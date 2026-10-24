"""Index search/quote/constituent contracts and deterministic statistics."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.collectors.hithink_finance_collector import (
    HithinkDailyBarFact,
    HithinkIndexCatalogFact,
    HithinkIndexConstituentFact,
    HithinkIndexSearchFact,
    HithinkIndexSnapshotQuoteFact,
)
from app.services.index_research import (
    build_index_constituents,
    build_index_quote,
    resolve_index,
    search_indices,
)

SHANGHAI = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


class FakeIndexCollector:
    def __init__(self, *, search=None, catalog=None, snapshot=None, history=None, constituents=None):
        self.search = search or []
        self.catalog = catalog or {}
        self.snapshot = snapshot
        self.history = history or []
        self.constituents = constituents or []
        self.history_calls: list[tuple[str, date, date]] = []

    def collect_index_search(self, query, *, limit=20):
        return self.search

    def collect_index_catalog(self, category):
        return self.catalog.get(category, [])

    def collect_index_snapshot(self, thscode):
        return self.snapshot

    def collect_index_history(self, thscode, *, start_date, end_date):
        self.history_calls.append((thscode, start_date, end_date))
        return SimpleNamespace(items=self.history)

    def collect_index_constituents_by_code(self, thscode):
        return self.constituents


def broad_search() -> list[HithinkIndexSearchFact]:
    return [
        HithinkIndexSearchFact("000300.SH", "1B0300", "沪深300", "SH", "a-share-index", date(2005, 4, 8)),
        HithinkIndexSearchFact("399300.SZ", "399300", "沪深300", "SZ", "a-share-index", date(2005, 4, 8)),
    ]


def snapshot() -> HithinkIndexSnapshotQuoteFact:
    return HithinkIndexSnapshotQuoteFact(
        thscode="000300.SH",
        last_price=4105.33,
        price_change=69.03,
        change_pct=1.71023,
        open_price=3992.9,
        high_price=4109.52,
        low_price=3958.14,
        prev_price=4036.3,
        volume=23_132_521_000,
        turnover=558_204_980_000,
        captured_at=datetime(2026, 10, 25, 9, 30, tzinfo=SHANGHAI),
    )


def bars() -> list[HithinkDailyBarFact]:
    closes = [100.0, 102.0, 101.0, 99.0, 103.0]
    dates = [date(2026, 10, 19) + timedelta(days=offset) for offset in range(5)]
    return [
        HithinkDailyBarFact(
            trade_date=trade_date,
            open=close - 1,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=1000,
            turnover=10000,
        )
        for trade_date, close in zip(dates, closes)
    ]


def constituents() -> list[HithinkIndexConstituentFact]:
    return [
        HithinkIndexConstituentFact("600001", "600001.SH", "甲股份"),
        HithinkIndexConstituentFact("000001", "000001.SZ", "乙股份"),
        HithinkIndexConstituentFact("600002", "600002.SH", "丙股份"),
    ]


def test_search_resolves_broad_index_and_same_name_variant():
    payload = search_indices(
        query="沪深300",
        collector=FakeIndexCollector(search=broad_search()),
    )

    assert payload["resolved"]["thscode"] == "000300.SH"
    assert [row["thscode"] for row in payload["variants"]] == ["399300.SZ"]
    assert payload["matched_count"] == 2
    assert payload["complete"] is True
    assert payload["data_missing"] == []


def test_search_filters_to_catalog_category():
    collector = FakeIndexCollector(
        search=[
            HithinkIndexSearchFact("881121.TI", "881121", "半导体", "", "a-share-index", date(2007, 8, 1)),
            HithinkIndexSearchFact("000300.SH", "1B0300", "沪深300", "SH", "a-share-index", date(2005, 4, 8)),
        ],
        catalog={"industry": [HithinkIndexCatalogFact("881121.TI", "半导体", "industry")]},
    )

    payload = search_indices(query="半导体", category="industry", collector=collector)

    assert len(payload["rows"]) == 1
    assert payload["resolved"]["thscode"] == "881121.TI"
    assert payload["resolved"]["category"] == "industry"


def test_quote_history_returns_deterministic_statistics():
    payload = build_index_quote(
        index="沪深300",
        days=5,
        end_date="2026-10-23",
        collector=FakeIndexCollector(search=broad_search(), snapshot=snapshot(), history=bars()),
        now=NOW,
    )

    assert len(payload["rows"]) == 5
    assert payload["return_pct"] == 3.0
    assert payload["max_drawdown_pct"] == -2.94
    assert payload["max_drawdown_peak_date"] == "2026-10-20"
    assert payload["max_drawdown_trough_date"] == "2026-10-22"
    assert payload["positive_days"] == 2
    assert payload["negative_days"] == 2
    assert payload["flat_days"] == 0
    assert payload["data_as_of"] == "2026-10-23"
    assert payload["data_fresh"] is True
    assert payload["complete"] is True
    assert payload["rows"][0]["change_pct"] is None
    assert payload["rows"][1]["change_pct"] == 2.0


def test_quote_snapshot_only_has_no_history_rows():
    payload = build_index_quote(
        index="沪深300",
        collector=FakeIndexCollector(search=broad_search(), snapshot=snapshot()),
        now=NOW,
    )

    assert payload["rows"] == []
    assert payload["snapshot_last_price"] == 4105.33
    assert payload["snapshot_change_pct"] == 1.71023
    assert payload["data_as_of"] is None
    assert payload["requested_days"] is None
    assert payload["complete"] is True
    assert any("来源响应时间" in warning for warning in payload["warnings"])
    assert any("不能直接称为今天收盘" in warning for warning in payload["warnings"])


def test_quote_marks_requested_end_date_without_bar_explicitly():
    payload = build_index_quote(
        index="沪深300",
        days=5,
        end_date="2026-10-25",
        collector=FakeIndexCollector(search=broad_search(), snapshot=snapshot(), history=bars()),
        now=NOW,
    )

    assert payload["data_as_of"] == "2026-10-23"
    assert payload["data_fresh"] is False
    assert any("没有该指数日线" in warning for warning in payload["warnings"])


def test_constituents_truncation_and_weight_boundary():
    payload = build_index_constituents(
        index="沪深300",
        limit=2,
        collector=FakeIndexCollector(search=broad_search(), constituents=constituents()),
        now=NOW,
    )

    assert payload["member_count"] == 3
    assert payload["returned_count"] == 2
    assert payload["complete"] is False
    assert payload["truncated"] is True
    assert payload["has_weights"] is False
    assert any("权重" in warning for warning in payload["warnings"])
    assert any("名称可能是简称" in warning for warning in payload["warnings"])


def test_empty_constituents_report_missing_instead_of_zero():
    payload = build_index_constituents(
        index="沪深300",
        collector=FakeIndexCollector(search=broad_search(), constituents=[]),
        now=NOW,
    )

    assert payload["rows"] == []
    assert payload["data_missing"] == ["hithink_finance:no_constituents"]
    assert payload["complete"] is False


def test_ambiguous_index_requires_explicit_resolution():
    collector = FakeIndexCollector(
        search=[
            HithinkIndexSearchFact("881001.TI", "881001", "半导体材料", "", "a-share-index", None),
            HithinkIndexSearchFact("881002.TI", "881002", "半导体设备", "", "a-share-index", None),
        ]
    )

    with pytest.raises(ValueError, match="Ambiguous index"):
        resolve_index("半导体", collector=collector)


