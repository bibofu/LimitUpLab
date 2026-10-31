"""PostgreSQL sector catalog, daily metrics and constituent cache."""

from datetime import date, datetime, timezone

from app.repositories.sector_repository import SectorRepository


def _catalog():
    return [
        {"thscode": "881001.TI", "name": "汽车整车", "category": "industry", "in_summary": True},
        {"thscode": "881002.TI", "name": "汽车零部件", "category": "industry", "in_summary": True},
        {"thscode": "884001.TI", "name": "其他汽车零部件", "category": "industry", "in_summary": False},
        {"thscode": "885001.TI", "name": "人工智能", "category": "cn_concept", "in_summary": False},
    ]


def _snapshot(day, code, change, *, name=None):
    return {
        "trade_date": day, "thscode": code, "change_pct": change,
        "amount_yi": 10.0, "net_inflow_yi": 1.5, "up_count": 3, "down_count": 1,
        "leader_name": "样例", "leader_price": 10.0, "leader_change_pct": 5.0,
        "source": "test-snapshot", "captured_at": datetime.now(timezone.utc),
    }


def test_catalog_summary_flags_and_rankings(postgresql_database):
    repo = SectorRepository(postgresql_database)
    repo.upsert_catalog(_catalog())

    assert {item["name"] for item in repo.catalog("industry", summary_only=True)} == {
        "汽车整车", "汽车零部件",
    }
    assert len(repo.catalog("industry")) == 3

    repo.replace_summary_flags(["881001.TI"])
    assert [item["name"] for item in repo.catalog(summary_only=True)] == ["汽车整车"]

    day = date(2026, 10, 30)
    repo.upsert_daily_snapshot([
        _snapshot(day, "881001.TI", 2.5),
        _snapshot(day, "881002.TI", 1.0),
        _snapshot(day, "884001.TI", 3.0),
        _snapshot(day, "885001.TI", 4.0),
    ])
    summary = repo.daily_ranking(day, "industry", summary_only=True)
    assert [item["name"] for item in summary] == ["汽车整车"]
    full = repo.daily_ranking(day, "industry")
    assert [item["name"] for item in full] == ["其他汽车零部件", "汽车整车", "汽车零部件"]
    assert repo.coverage(day, "industry", summary_only=True) == (1, 1)
    assert repo.latest_daily_date("industry", summary_only=True) == day
    assert repo.previous_daily_date(day, "industry") is None


def test_history_merge_preserves_the_snapshot_change_and_fills_close(postgresql_database):
    repo = SectorRepository(postgresql_database)
    repo.upsert_catalog(_catalog())
    day = date(2026, 10, 30)
    earlier = date(2026, 10, 29)
    repo.upsert_daily_snapshot([_snapshot(day, "881001.TI", 2.5)])
    repo.upsert_daily_history([
        {"trade_date": earlier, "thscode": "881001.TI", "close": 100.0,
         "change_pct": -1.0, "source": "test-history"},
        {"trade_date": day, "thscode": "881001.TI", "close": 102.55,
         "change_pct": 2.55, "source": "test-history"},
    ])

    history = repo.daily_history("881001.TI", day, 5)
    assert [item["trade_date"] for item in history] == [earlier, day]
    assert history[-1]["close"] == 102.55
    row = repo.daily_row(day, "881001.TI")
    assert row["change_pct"] == 2.5 and row["source"] == "test-snapshot"
    assert row["close"] == 102.55


def test_constituents_cache_replaces_and_reads(postgresql_database):
    repo = SectorRepository(postgresql_database)
    repo.upsert_catalog(_catalog())
    assert repo.constituents("881001.TI") == []
    assert repo.constituents_updated_at("881001.TI") is None

    repo.replace_constituents("881001.TI", [
        {"symbol": "000625", "name": "长安汽车"},
        {"symbol": "002594", "name": "比亚迪"},
    ])
    rows = repo.constituents("881001.TI")
    assert [item["symbol"] for item in rows] == ["000625", "002594"]
    assert repo.constituents_updated_at("881001.TI") is not None

    repo.replace_constituents("881001.TI", [{"symbol": "600104", "name": "上汽集团"}])
    assert [item["symbol"] for item in repo.constituents("881001.TI")] == ["600104"]
