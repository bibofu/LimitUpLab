"""Registry and model-view integration for the three index tools."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.agents.runner.tool_view import model_result
from app.agents.tool_payload import payload_of
from app.agents.tools import AgentToolRegistry
from app.collectors.hithink_finance_collector import (
    HithinkDailyBarFact,
    HithinkIndexConstituentFact,
    HithinkIndexSearchFact,
    HithinkIndexSnapshotQuoteFact,
)

SHANGHAI = timezone(timedelta(hours=8))


class FakeIndexCollector:
    def collect_index_search(self, query, *, limit=20):
        return [
            HithinkIndexSearchFact("000300.SH", "1B0300", "沪深300", "SH", "a-share-index", date(2005, 4, 8)),
            HithinkIndexSearchFact("399300.SZ", "399300", "沪深300", "SZ", "a-share-index", date(2005, 4, 8)),
        ]

    def collect_index_snapshot(self, thscode):
        return HithinkIndexSnapshotQuoteFact(
            thscode="000300.SH", last_price=4105.33, price_change=69.03,
            change_pct=1.71023, open_price=3992.9, high_price=4109.52,
            low_price=3958.14, prev_price=4036.3, volume=23_132_521_000,
            turnover=558_204_980_000,
            captured_at=datetime(2026, 10, 25, 9, 30, tzinfo=SHANGHAI),
        )

    def collect_index_history(self, thscode, *, start_date, end_date):
        closes = [100.0, 102.0, 101.0, 99.0, 103.0]
        return SimpleNamespace(items=[
            HithinkDailyBarFact(
                trade_date=date(2026, 10, 19) + timedelta(days=offset),
                open=close - 1, high=close + 1, low=close - 1, close=close,
                volume=1000, turnover=10000,
            )
            for offset, close in enumerate(closes)
        ])

    def collect_index_constituents_by_code(self, thscode):
        return [
            HithinkIndexConstituentFact("600001", "600001.SH", "甲股份"),
            HithinkIndexConstituentFact("000001", "000001.SZ", "乙股份"),
            HithinkIndexConstituentFact("600002", "600002.SH", "丙股份"),
        ]


def test_registry_index_tools_and_model_view_preserve_collection():
    registry = AgentToolRegistry(
        events=[],
        first_board_repository=SimpleNamespace(database_url=None),
        hithink_collector=FakeIndexCollector(),
    )

    search = registry.index_search("沪深300")
    quote = registry.index_quote("沪深300", days=5, end_date="2026-10-23")
    members = registry.index_constituents("沪深300", limit=2)

    view = model_result(
        "index_quote", quote, payload_of(quote), quote.result_status,
    )

    assert search.name == "index_search"
    assert search.result_status == "ok"
    assert quote.name == "index_quote"
    assert quote.result_status == "ok"
    assert members.name == "index_constituents"
    assert members.result_status == "ok"
    assert view["row_count"] == 5
    assert view["return_pct"] == 3.0
    assert "max_drawdown_pct" in view["field_definitions"]["metadata"]
    assert "rows" in view["field_definitions"]
