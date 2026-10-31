"""Fast local sector tools: no upstream call when the local cache is warm."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.agents.tools import AgentToolRegistry


TARGET = date(2026, 10, 30)


class ExplodingCollector:
    def __getattr__(self, name):
        raise AssertionError(f"upstream collector must not be used: {name}")


def _catalog():
    return [
        {"thscode": "881125.TI", "name": "汽车整车", "category": "industry", "in_summary": True},
        {"thscode": "881126.TI", "name": "汽车零部件", "category": "industry", "in_summary": True},
        {"thscode": "881128.TI", "name": "汽车服务及其他", "category": "industry", "in_summary": True},
        {"thscode": "881121.TI", "name": "半导体", "category": "industry", "in_summary": True},
        {"thscode": "885728.TI", "name": "人工智能", "category": "cn_concept", "in_summary": False},
    ]


def _daily(code, name, category, change, *, up=3, down=1, amount=10.0):
    return {
        "trade_date": TARGET, "thscode": code, "name": name, "category": category,
        "change_pct": change, "amount_yi": amount, "net_inflow_yi": 1.5,
        "up_count": up, "down_count": down, "leader_name": "样例",
        "leader_price": 10.0, "leader_change_pct": 5.0, "source": "test-snapshot",
        "captured_at": datetime.now(timezone.utc),
    }


class FakeSectorRepository:
    def __init__(self, catalog=None, daily=None, history=None, constituents=None):
        self._catalog = catalog or []
        self._daily = daily or {}
        self._history = history or {}
        self._constituents = constituents or {}
        self._updated = {code: datetime.now(timezone.utc) for code in self._constituents}
        self.refreshes = []

    def catalog(self, category=None, *, summary_only=False):
        rows = [item for item in self._catalog if category is None or item["category"] == category]
        if summary_only:
            rows = [item for item in rows if item.get("in_summary")]
        return list(rows)

    def catalog_updated_at(self):
        return datetime.now(timezone.utc)

    def daily_ranking(self, trade_date, category, *, summary_only=False):
        rows = [
            item for item in self._daily.get(trade_date, [])
            if item["category"] == category and (not summary_only or self._summary(item["thscode"]))
        ]
        return sorted(rows, key=lambda item: -item["change_pct"])

    def latest_daily_date(self, category, *, summary_only=False, on_or_before=None):
        dates = [
            day for day, rows in self._daily.items()
            if (on_or_before is None or day <= on_or_before)
            and any(item["category"] == category and (not summary_only or self._summary(item["thscode"]))
                    for item in rows)
        ]
        return max(dates, default=None)

    def previous_daily_date(self, trade_date, category, *, summary_only=False):
        dates = [
            day for day, rows in self._daily.items()
            if day < trade_date and any(item["category"] == category for item in rows)
        ]
        return max(dates, default=None)

    def coverage(self, trade_date, category, *, summary_only=False):
        covered = len(self.daily_ranking(trade_date, category, summary_only=summary_only))
        expected = len(self.catalog(category, summary_only=summary_only))
        return covered, expected

    def daily_row(self, trade_date, thscode):
        for item in self._daily.get(trade_date, []):
            if item["thscode"] == thscode:
                return item
        return None

    def daily_history(self, thscode, end_date, limit):
        return [item for item in self._history.get(thscode, []) if item["trade_date"] <= end_date][-limit:]

    def constituents(self, thscode):
        return list(self._constituents.get(thscode, []))

    def constituents_updated_at(self, thscode):
        return self._updated.get(thscode)

    def replace_constituents(self, thscode, rows):
        self._constituents[thscode] = list(rows)
        self._updated[thscode] = datetime.now(timezone.utc)
        self.refreshes.append(thscode)

    def _summary(self, thscode):
        return next((item.get("in_summary") for item in self._catalog if item["thscode"] == thscode), False)


def _history_rows(code, *, days=30, source="test-history"):
    start = TARGET - timedelta(days=days - 1)
    return [
        {"trade_date": start + timedelta(days=offset), "thscode": code,
         "close": 100 + offset, "change_pct": 1.0, "source": source}
        for offset in range(days)
    ]


def _registry(repo):
    return AgentToolRegistry(
        events=[], first_board_repository=SimpleNamespace(database_url=None),
        hithink_collector=ExplodingCollector(), sector_repository=repo,
    )


def test_whole_market_ranking_reads_only_the_local_daily_table():
    catalog = [item for item in _catalog() if item["thscode"] != "881128.TI"]
    repo = FakeSectorRepository(catalog=catalog, daily={TARGET: [
        _daily("881125.TI", "汽车整车", "industry", 2.0),
        _daily("881126.TI", "汽车零部件", "industry", -1.0),
        _daily("881121.TI", "半导体", "industry", 5.0),
    ]})
    result = _registry(repo).sector_performance(trade_date=TARGET)
    output = result.output
    assert result.result_status == "ok"
    assert output.data_fresh is True
    assert [item.sector_name for item in output.top_sectors] == ["半导体", "汽车整车", "汽车零部件"]
    assert output.sector_count == 3 and output.covered_sector_count == 3
    assert output.ranking_complete is True


def test_named_sector_uses_cached_history_and_reports_rank():
    repo = FakeSectorRepository(
        catalog=_catalog(),
        daily={TARGET: [
            _daily("881121.TI", "半导体", "industry", -5.44, up=6, down=184),
            _daily("881125.TI", "汽车整车", "industry", 2.0),
        ]},
        history={"881121.TI": _history_rows("881121.TI")},
    )
    result = _registry(repo).sector_performance(sector="半导体", trade_date=TARGET)
    output = result.output
    assert result.result_status == "ok"
    assert output.sector_name == "半导体"
    assert output.change_pct == -5.44
    assert output.rank == 2  # second in the two-row ranking
    assert output.up_count == 6 and output.down_count == 184
    assert output.return_5d_pct is not None and output.return_20d_pct is not None
    assert len(output.history) == 20


def test_group_returns_member_breakdown_and_equal_weight_average():
    repo = FakeSectorRepository(
        catalog=_catalog(),
        daily={TARGET: [
            _daily("881125.TI", "汽车整车", "industry", -2.0),
            _daily("881126.TI", "汽车零部件", "industry", -4.0),
            _daily("881128.TI", "汽车服务及其他", "industry", -3.0),
        ]},
        history={code: _history_rows(code) for code in ("881125.TI", "881126.TI", "881128.TI")},
    )
    result = _registry(repo).sector_performance(sector="汽车", trade_date=TARGET)
    output = result.output
    assert output.aggregation == "equal_weight"
    assert output.sector_type == "industry_group"
    assert [item.sector_name for item in output.members] == [
        "汽车整车", "汽车零部件", "汽车服务及其他",
    ]
    assert output.change_pct == -3.0
    assert len(output.matched_sectors) == 3


def test_sector_stocks_lists_a_group_without_truncation():
    repo = FakeSectorRepository(catalog=_catalog(), constituents={
        "881125.TI": [{"symbol": "000625", "name": "长安汽车"}, {"symbol": "002594", "name": "比亚迪"}],
        "881126.TI": [{"symbol": "000625", "name": "长安汽车"}, {"symbol": "600741", "name": "华域汽车"}],
        "881128.TI": [{"symbol": "600104", "name": "上汽集团"}],
    })
    result = _registry(repo).sector_stocks(sector="汽车", limit=500)
    output = result.output
    assert output.group is True
    assert output.member_count == 4
    assert output.row_count == 4 and output.truncated is False
    assert [item.symbol for item in output.items] == ["000625", "002594", "600104", "600741"]
    assert [(item.sector_name, item.member_count) for item in output.matched_sectors] == [
        ("汽车整车", 2), ("汽车零部件", 2), ("汽车服务及其他", 1),
    ]


def test_missing_ranking_returns_an_explicit_gap(monkeypatch):
    repo = FakeSectorRepository(catalog=_catalog(), daily={})
    monkeypatch.setattr(
        "app.services.sector_sync.sync_industry_daily",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    result = _registry(repo).sector_performance(trade_date=TARGET)
    output = result.output
    assert result.result_status == "partial"
    assert output.data_fresh is False
    assert "sector_ranking_unavailable" in output.data_missing
    assert output.top_sectors == []
