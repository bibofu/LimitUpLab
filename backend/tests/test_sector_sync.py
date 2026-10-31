"""Sector sync mapping, lazy caches and failure boundaries."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.collectors.sector_collector import SectorSpotRow
from app.services.sector_resolution import SectorMatch
from app.services import sector_sync


TARGET = date(2026, 10, 30)


class Frame:
    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    def to_dict(self, orient):
        assert orient == "records"
        return list(self.rows)


class FakeRepository:
    def __init__(self, catalog=None):
        self._catalog = {item["thscode"]: dict(item) for item in (catalog or [])}
        self.snapshots = {}
        self.history = {}
        self.constituent_cache = {}
        self.updated = {}
        self.catalog_writes = []
        self.summary_flags = []

    def catalog(self, category=None, *, summary_only=False):
        rows = [
            item for item in self._catalog.values()
            if category is None or item["category"] == category
        ]
        if summary_only:
            rows = [item for item in rows if item.get("in_summary")]
        return sorted(rows, key=lambda item: item["name"])

    def catalog_updated_at(self):
        return None

    def upsert_catalog(self, rows):
        self.catalog_writes.extend(rows)
        for row in rows:
            self._catalog[row["thscode"]] = dict(row)

    def replace_summary_flags(self, thscodes):
        self.summary_flags = list(thscodes)
        for item in self._catalog.values():
            item["in_summary"] = item["thscode"] in set(thscodes)

    def upsert_daily_snapshot(self, rows):
        for row in rows:
            self.snapshots[(row["trade_date"], row["thscode"])] = dict(row)

    def upsert_daily_history(self, rows):
        for row in rows:
            self.history.setdefault(row["thscode"], {})[row["trade_date"]] = dict(row)

    def daily_history(self, thscode, end_date, limit):
        rows = sorted(
            (item for day, item in self.history.get(thscode, {}).items() if day <= end_date),
            key=lambda item: item["trade_date"],
        )
        return rows[-limit:]

    def constituents(self, thscode):
        return list(self.constituent_cache.get(thscode, []))

    def constituents_updated_at(self, thscode):
        return self.updated.get(thscode)

    def replace_constituents(self, thscode, rows):
        self.constituent_cache[thscode] = list(rows)
        self.updated[thscode] = datetime.now(timezone.utc)


class FakeCollector:
    def __init__(self, catalogs=None, members=None, history=None):
        self.catalogs = catalogs or {}
        self.members = members or []
        self.history = history or []
        self.calls = []

    def collect_index_catalog(self, category):
        self.calls.append(("catalog", category))
        return self.catalogs.get(category, [])

    def collect_index_constituents_by_code(self, thscode):
        self.calls.append(("constituents", thscode))
        return self.members

    def collect_index_history(self, thscode, *, start_date, end_date):
        self.calls.append(("history", thscode))
        return SimpleNamespace(items=self.history)


def test_catalog_sync_marks_the_90_industry_summary(monkeypatch):
    collector = FakeCollector(catalogs={
        "industry": [
            SimpleNamespace(thscode="881125.TI", name="汽车整车", category="industry"),
            SimpleNamespace(thscode="884001.TI", name="其他汽车零部件", category="industry"),
        ],
        "cn_concept": [
            SimpleNamespace(thscode="885728.TI", name="人工智能", category="cn_concept"),
        ],
    })
    monkeypatch.setattr(sector_sync, "_ths_summary_codes", lambda: {"881125.TI"})
    repo = FakeRepository()

    result = sector_sync.sync_catalog(repo, force=True, collector=collector)

    assert result == {"synced": True, "count": 3, "summary_count": 1}
    assert repo.summary_flags == ["881125.TI"]
    assert repo.catalog("industry", summary_only=True)[0]["name"] == "汽车整车"


def test_industry_daily_maps_names_to_catalog_codes(monkeypatch):
    repo = FakeRepository([
        {"thscode": "881125.TI", "name": "汽车整车", "category": "industry", "in_summary": True},
        {"thscode": "881121.TI", "name": "半导体", "category": "industry", "in_summary": True},
    ])
    monkeypatch.setattr(sector_sync, "_industry_summary", lambda: Frame([
        {"板块": "汽车整车", "涨跌幅": "-2.31", "总成交额": "244.7", "净流入": "25.35",
         "上涨家数": "10", "下跌家数": "13", "领涨股": "海马汽车",
         "领涨股-最新价": "4.5", "领涨股-涨跌幅": "10.0"},
        {"板块": "不存在的行业", "涨跌幅": "1.0"},
    ]))

    result = sector_sync.sync_industry_daily(repo, TARGET)

    assert result["rows"] == 1
    row = repo.snapshots[(TARGET, "881125.TI")]
    assert row["change_pct"] == -2.31
    assert row["amount_yi"] == 244.7
    assert row["net_inflow_yi"] == 25.35
    assert row["up_count"] == 10 and row["down_count"] == 13
    assert repo.summary_flags == ["881125.TI"]


def test_concept_daily_maps_the_cli_snapshot(monkeypatch):
    repo = FakeRepository([
        {"thscode": "885728.TI", "name": "人工智能", "category": "cn_concept", "in_summary": False},
    ])
    monkeypatch.setattr(sector_sync, "collect_concept_spot", lambda: [
        SectorSpotRow(
            sector_name="人工智能", rank=1, change_pct=-4.14, amount_yi=100.0,
            net_inflow_yi=None, up_count=None, down_count=None, leader_name=None,
            leader_price=None, leader_change_pct=None,
            source="hithink-finance.index.snapshot.cn_concept",
        ),
    ])

    result = sector_sync.sync_concept_daily(repo, TARGET)

    assert result["rows"] == 1
    row = repo.snapshots[(TARGET, "885728.TI")]
    assert row["change_pct"] == -4.14
    assert row["source"] == "hithink-finance.index.snapshot.cn_concept"


def test_constituents_are_fetched_once_and_then_cached():
    collector = FakeCollector(members=[
        SimpleNamespace(symbol="000625", name="长安汽车"),
        SimpleNamespace(symbol="002594", name="比亚迪"),
    ])
    repo = FakeRepository()
    member = SectorMatch("881125.TI", "汽车整车", "industry", True)

    first = sector_sync.ensure_constituents(repo, member, collector=collector)
    second = sector_sync.ensure_constituents(repo, member, collector=collector)

    assert [item["symbol"] for item in first] == ["000625", "002594"]
    assert second == first
    assert collector.calls == [("constituents", "881125.TI")]


def test_history_falls_back_to_the_cli_and_persists(monkeypatch):
    collector = FakeCollector(history=[
        SimpleNamespace(trade_date=TARGET - timedelta(days=1), close=100.0),
        SimpleNamespace(trade_date=TARGET, close=101.0),
    ])
    repo = FakeRepository()
    member = SectorMatch("881125.TI", "汽车整车", "industry", True)
    monkeypatch.setattr(
        sector_sync, "_akshare_industry_history",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("akshare down")),
    )

    rows = sector_sync.ensure_history(repo, member, TARGET, days=5, collector=collector)

    assert [item["close"] for item in rows] == [100.0, 101.0]
    assert rows[-1]["change_pct"] == 1.0
    assert collector.calls == [("history", "881125.TI")]
