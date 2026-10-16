from datetime import date
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app.collectors import sector_collector as module
from app.collectors.hithink_finance_collector import (
    HithinkDailyBarFact,
    HithinkIndexCatalogFact,
    HithinkIndexSnapshotFact,
)


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.delenv("LIMITUPLAB_SECTOR_SNAPSHOT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(module, "_spot_cache", None)
    monkeypatch.setattr(module, "_concept_spot_cache", None)
    for cache in (module._catalog_cache, module._history_cache, module._concept_history_cache):
        cache.clear()
    collector = SimpleNamespace(
        collect_index_catalog=Mock(return_value=[index("880001.TI", "半导体")]),
        collect_index_snapshots=Mock(return_value=[snapshot("880001.TI", "半导体", 2.5)]),
        collect_index_history=Mock(),
    )
    monkeypatch.setattr(module, "HithinkFinanceCollector", lambda **_kwargs: collector)
    monkeypatch.setattr(
        module, "run_in_killable_process",
        Mock(side_effect=lambda worker, *args, **_kwargs: worker(*args)),
    )
    for name in (
        "_load_ths_spot", "_load_eastmoney_spot", "_load_eastmoney_concept_spot",
        "_load_ths_history", "_load_eastmoney_history",
        "_load_eastmoney_concept_history", "_load_ths_concept_history",
    ):
        monkeypatch.setattr(module, name, Mock(side_effect=RuntimeError("fallback unavailable")))
    yield collector
    for cache in (module._catalog_cache, module._history_cache, module._concept_history_cache):
        cache.clear()


def index(code, name, category="industry"):
    return HithinkIndexCatalogFact(code, name, category)


def snapshot(code, name, change_pct, turnover=300_000_000):
    return HithinkIndexSnapshotFact(code, name, "industry", change_pct, turnover)


def ths_frame():
    return pd.DataFrame([{
        "板块": "半导体", "序号": 9, "涨跌幅": 9.9, "总成交额": 99,
        "净流入": 1.1, "上涨家数": 8, "下跌家数": 3,
        "领涨股": "测试公司", "领涨股-最新价": 12, "领涨股-涨跌幅": 10,
    }])


def em_frame(name="人工智能"):
    return pd.DataFrame([{"板块名称": name, "排名": 1, "涨跌幅": 1.2, "上涨家数": 23}])


def test_industry_cli_owns_prices_and_ths_supplements_breadth(cli, monkeypatch):
    monkeypatch.setattr(module, "_load_ths_spot", Mock(return_value=ths_frame()))
    rows = module.collect_sector_spot()
    assert rows[0].change_pct == 2.5
    assert rows[0].amount_yi == 3
    assert rows[0].rank == 1
    assert rows[0].net_inflow_yi == 1.1
    assert rows[0].up_count == 8
    assert rows[0].leader_name == "测试公司"
    assert rows[0].source == "hithink-finance.index.snapshot.industry+akshare-ths-industry-summary"
    module.run_in_killable_process.assert_called_once_with(
        module._load_ths_spot, timeout_seconds=5,
    )
    module._load_eastmoney_spot.assert_not_called()


def test_industry_supplement_timeout_preserves_cli_ranking_and_missing_fields(cli):
    module.run_in_killable_process.side_effect = TimeoutError("optional supplement exceeded deadline")
    rows = module.collect_sector_spot()
    assert rows[0].change_pct == 2.5
    assert rows[0].amount_yi == 3
    assert rows[0].source == "hithink-finance.index.snapshot.industry"
    assert rows[0].net_inflow_yi is None
    assert rows[0].up_count is None
    assert rows[0].down_count is None
    assert rows[0].leader_name is None
    module._load_ths_spot.assert_not_called()
    module._load_eastmoney_spot.assert_not_called()
    assert module.collect_sector_spot() == rows
    module.run_in_killable_process.assert_called_once()


def test_cli_success_survives_supplement_failure_and_is_cached(cli):
    first = module.collect_sector_spot()
    assert first[0].up_count is None
    assert first[0].source == "hithink-finance.index.snapshot.industry"
    assert module.collect_sector_spot() == first
    cli.collect_index_snapshots.assert_called_once()


def test_concepts_do_not_mix_same_name_eastmoney_breadth(cli):
    cli.collect_index_catalog.return_value = [index("885001.TI", "人工智能", "cn_concept")]
    cli.collect_index_snapshots.return_value = [snapshot("885001.TI", "人工智能", 3)]
    rows = module.collect_concept_spot()
    assert rows[0].up_count is None
    assert rows[0].change_pct == 3
    assert rows[0].source == "hithink-finance.index.snapshot.cn_concept"
    module._load_eastmoney_concept_spot.assert_not_called()
    module._load_ths_spot.assert_not_called()
    module.run_in_killable_process.assert_not_called()


def test_partial_cli_ranking_falls_back_as_a_whole(cli, monkeypatch):
    cli.collect_index_catalog.return_value.append(index("880002.TI", "汽车"))
    monkeypatch.setattr(module, "_load_ths_spot", Mock(return_value=ths_frame()))
    rows = module.collect_sector_spot()
    assert rows[0].change_pct == 9.9
    assert rows[0].source == "akshare-ths-industry-summary"


@pytest.mark.parametrize("change", [None, float("nan"), float("inf")])
def test_missing_change_cannot_be_silently_ranked_as_zero(cli, monkeypatch, change):
    cli.collect_index_snapshots.return_value = [snapshot("880001.TI", "半导体", change)]
    monkeypatch.setattr(module, "_load_eastmoney_spot", Mock(return_value=em_frame("半导体")))
    rows = module.collect_sector_spot()
    assert rows[0].source == "akshare-eastmoney-industry-spot"


def test_concept_failure_preserves_eastmoney_fallback(cli, monkeypatch):
    cli.collect_index_catalog.side_effect = RuntimeError("no CLI credentials")
    monkeypatch.setattr(module, "_load_eastmoney_concept_spot", Mock(return_value=em_frame()))
    assert module.collect_concept_spot()[0].source == "akshare-eastmoney-concept-spot"


def test_large_directory_is_batched_without_truncating(cli):
    indexes = [index(f"{880000 + i}.TI", f"行业{i}") for i in range(205)]
    cli.collect_index_snapshots.side_effect = lambda batch: [
        snapshot(item.thscode, item.name, 1) for item in batch
    ]
    rows = module._hithink_snapshots(cli, indexes)
    assert len(rows) == 205
    assert sorted(len(call.args[0]) for call in cli.collect_index_snapshots.call_args_list) == [5, 100, 100]


def test_batch_deadline_rejects_partial_ranking(cli, monkeypatch):
    release = Event()

    def blocked(_batch):
        release.wait(1)
        return [snapshot("880001.TI", "半导体", 2)]

    cli.collect_index_snapshots.side_effect = blocked
    monkeypatch.setattr(module, "_CLI_SNAPSHOT_TIMEOUT_SECONDS", 0.01)
    try:
        with pytest.raises(TimeoutError):
            module._hithink_snapshots(cli, [index("880001.TI", "半导体")])
    finally:
        release.set()


@pytest.mark.parametrize("configured,expected", [("90", 90), ("900", 300), ("nan", 60), ("bad", 60), ("-1", 60)])
def test_snapshot_budget_configuration_is_finite(monkeypatch, configured, expected):
    monkeypatch.setenv("LIMITUPLAB_SECTOR_SNAPSHOT_TIMEOUT_SECONDS", configured)
    assert module._snapshot_budget_seconds() == expected


def test_catalog_empty_or_duplicated_is_not_cached_as_complete(cli):
    cli.collect_index_catalog.return_value = []
    with pytest.raises(ValueError, match="catalog"):
        module._hithink_catalog(cli, "industry")
    cli.collect_index_catalog.return_value = [index("880001.TI", "半导体")] * 2
    with pytest.raises(ValueError, match="catalog"):
        module._hithink_catalog(cli, "industry")
    assert not module._catalog_cache


@pytest.mark.parametrize("category,collect", [
    ("industry", module.collect_sector_history),
    ("cn_concept", module.collect_concept_history),
])
def test_history_prefers_cli_and_computes_first_change_from_prior_close(cli, category, collect):
    cli.collect_index_catalog.return_value = [index("880001.TI", "半导体", category)]
    cli.collect_index_history.return_value = SimpleNamespace(items=[
        HithinkDailyBarFact(date(2026, 10, 15), 10, 12, 10, 12, 1, 1),
        HithinkDailyBarFact(date(2026, 10, 14), 10, 10, 10, 10, 1, 1),
    ])
    rows = collect("半导体", date(2026, 10, 15), date(2026, 10, 15))
    assert len(rows) == 1
    assert rows[0].change_pct == 20
    assert rows[0].source == f"hithink-finance.index.history.{category}"
    assert cli.collect_index_history.call_args.kwargs["start_date"] == date(2026, 10, 1)
    module._load_ths_history.assert_not_called()
    module._load_eastmoney_concept_history.assert_not_called()


@pytest.mark.parametrize("collect,loader,source,frame", [
    (module.collect_sector_history, "_load_ths_history", "akshare-ths-industry-history",
     pd.DataFrame([{"日期": "2026-10-15", "收盘价": 123}])),
    (module.collect_concept_history, "_load_eastmoney_concept_history", "akshare-eastmoney-concept-history",
     pd.DataFrame([{"日期": "2026-10-15", "收盘": 123, "涨跌幅": 1}])),
])
def test_unmatched_cli_history_falls_back_without_guessing_index(cli, monkeypatch, collect, loader, source, frame):
    monkeypatch.setattr(module, loader, Mock(return_value=frame))
    rows = collect("不匹配名称", date(2026, 10, 15), date(2026, 10, 15))
    assert rows[0].source == source
    cli.collect_index_history.assert_not_called()


def test_empty_requested_cli_history_falls_back(cli, monkeypatch):
    cli.collect_index_history.return_value = SimpleNamespace(items=[])
    monkeypatch.setattr(module, "_load_ths_history", Mock(return_value=pd.DataFrame([
        {"日期": "2026-10-15", "收盘价": 123},
    ])))
    rows = module.collect_sector_history("半导体", date(2026, 10, 15), date(2026, 10, 15))
    assert rows[0].source == "akshare-ths-industry-history"
