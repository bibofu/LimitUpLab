from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.collectors import sector_collector as module
from app.collectors.hithink_finance_collector import HithinkDailyBarFact, HithinkIndexCatalogFact


DAY = date(2026, 10, 16)
PREVIOUS = date(2026, 10, 15)


def bar(day, close, turnover=300_000_000):
    return HithinkDailyBarFact(day, close, close, close, close, 100, turnover)


@pytest.fixture
def collector(monkeypatch):
    module._catalog_cache.clear()
    module._ranking_cache.clear()
    module._ranking_pending.clear()
    collector = SimpleNamespace(
        collect_index_catalog=Mock(return_value=[
            HithinkIndexCatalogFact("880001.TI", "半导体", "industry"),
            HithinkIndexCatalogFact("880002.TI", "银行", "industry"),
        ]),
        collect_trading_calendar=Mock(return_value=[PREVIOUS, DAY]),
        collect_index_history=Mock(side_effect=lambda code, **_: SimpleNamespace(items=[
            bar(PREVIOUS, 100), bar(DAY, 106 if code == "880001.TI" else 98),
        ])),
    )
    monkeypatch.setattr(module, "HithinkFinanceCollector", lambda **_: collector)
    yield collector
    module._catalog_cache.clear()
    module._ranking_cache.clear()
    module._ranking_pending.clear()


def test_dated_ranking_is_complete_sorted_and_cached_independently_of_live_quotes(collector):
    result = module.collect_sector_ranking(DAY)
    assert result.complete
    assert result.trade_date == DAY
    assert result.previous_trade_date == PREVIOUS
    assert result.sector_count == 2
    assert [(row.sector_name, row.rank, row.change_pct) for row in result.rows] == [
        ("半导体", 1, 6), ("银行", 2, -2),
    ]
    assert result.rows[0].amount_yi == 3
    assert result.rows[0].up_count is None
    assert result.rows[0].source == "hithink-finance.index.history.industry"
    for call in collector.collect_index_history.call_args_list:
        assert call.kwargs == {"start_date": PREVIOUS, "end_date": DAY}
    result.rows.clear()
    assert len(module.collect_sector_ranking(DAY).rows) == 2
    assert collector.collect_index_history.call_count == 2


@pytest.mark.parametrize("items", [
    [bar(DAY, 106)],
    [bar(date(2026, 10, 14), 100), bar(DAY, 106)],
    [bar(PREVIOUS, 100), bar(date(2026, 10, 14), 106)],
    [bar(PREVIOUS, 0), bar(DAY, 106)],
    [bar(PREVIOUS, 100), bar(DAY, float("nan"))],
    [bar(PREVIOUS, 100), bar(DAY, 106), bar(DAY, 107)],
])
def test_missing_exact_dates_or_invalid_prices_never_become_ranked_zeroes(collector, items):
    original = collector.collect_index_history.side_effect
    collector.collect_index_history.side_effect = lambda code, **kwargs: (
        SimpleNamespace(items=items) if code == "880001.TI" else original(code, **kwargs)
    )
    result = module.collect_sector_ranking(DAY)
    assert not result.complete
    assert result.sector_count == 2
    assert [row.sector_name for row in result.rows] == ["银行"]
    assert "sector_history_coverage:1/2" in result.data_missing


def test_non_trading_date_does_not_relabel_the_previous_session(collector):
    result = module.collect_sector_ranking(date(2026, 10, 18))
    assert not result.complete
    assert not result.rows
    assert result.data_missing == ["sector_history_trading_dates_unavailable"]
    collector.collect_index_history.assert_not_called()


def test_calendar_failure_keeps_known_universe_coverage(collector):
    collector.collect_trading_calendar.side_effect = RuntimeError("calendar unavailable")
    result = module.collect_sector_ranking(DAY)
    assert not result.complete
    assert result.sector_count == 2
    assert result.data_missing == ["sector_history_calendar_unavailable:RuntimeError"]
    collector.collect_index_history.assert_not_called()


def test_failed_sector_query_is_a_coverage_gap_and_does_not_discard_other_results(collector):
    calls = Counter()

    def history(code, **_):
        calls[code] += 1
        if code == "880001.TI":
            raise RuntimeError("upstream failed")
        return SimpleNamespace(items=[bar(PREVIOUS, 100), bar(DAY, 98)])

    collector.collect_index_history.side_effect = history
    result = module.collect_sector_ranking(DAY)
    assert not result.complete
    assert len(result.rows) == 1
    assert result.data_missing == ["sector_history_coverage:1/2"]
    assert calls == {"880001.TI": 2, "880002.TI": 1}


@pytest.mark.parametrize("failure", ["exception", "missing_dates"])
def test_transient_failure_retries_only_missing_index_and_recovers_complete_ranking(collector, failure):
    calls = Counter()
    original = collector.collect_index_history.side_effect

    def history(code, **kwargs):
        calls[code] += 1
        if code == "880001.TI" and calls[code] == 1:
            if failure == "exception":
                raise RuntimeError("temporary provider failure")
            return SimpleNamespace(items=[bar(DAY, 106)])
        return original(code, **kwargs)

    collector.collect_index_history.side_effect = history
    result = module.collect_sector_ranking(DAY)
    assert result.complete
    assert not result.data_missing
    assert [(row.sector_name, row.change_pct) for row in result.rows] == [("半导体", 6), ("银行", -2)]
    assert calls == {"880001.TI": 2, "880002.TI": 1}
    assert module.collect_sector_ranking(DAY).complete
    assert calls == {"880001.TI": 2, "880002.TI": 1}


def test_retry_uses_remaining_original_budget(collector, monkeypatch):
    collector.collect_index_catalog.return_value = collector.collect_index_catalog.return_value[:1]
    clock = [0.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    budgets = []

    def history(_code, **_):
        budgets.append(collector.timeout_seconds)
        if len(budgets) == 1:
            clock[0] = 29.0
            raise RuntimeError("temporary failure near deadline")
        return SimpleNamespace(items=[bar(PREVIOUS, 100), bar(DAY, 106)])

    collector.collect_index_history.side_effect = history
    assert module.collect_sector_ranking(DAY).complete
    assert budgets == [10, 1]


def test_expired_original_budget_does_not_start_retry(collector, monkeypatch):
    collector.collect_index_catalog.return_value = collector.collect_index_catalog.return_value[:1]
    clock = [0.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])

    def history(_code, **_):
        clock[0] = 30.0
        raise RuntimeError("original budget exhausted")

    collector.collect_index_history.side_effect = history
    result = module.collect_sector_ranking(DAY)
    assert not result.complete
    assert "sector_history_timeout" in result.data_missing
    assert "sector_history_coverage:0/1" in result.data_missing
    collector.collect_index_history.assert_called_once()


def test_concurrent_users_share_one_history_collection(collector):
    entered, release = Event(), Event()
    original = collector.collect_trading_calendar.side_effect

    def calendar():
        entered.set()
        assert release.wait(2)
        return [PREVIOUS, DAY]

    collector.collect_trading_calendar.side_effect = calendar
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(module.collect_sector_ranking, DAY)
        assert entered.wait(2)
        second = pool.submit(module.collect_sector_ranking, DAY)
        release.set()
        assert first.result().complete
        assert second.result().complete
    assert collector.collect_index_history.call_count == 2
    collector.collect_trading_calendar.assert_called_once()
    collector.collect_trading_calendar.side_effect = original


def test_history_workers_run_in_parallel_with_a_global_deadline(collector, monkeypatch):
    entered, release = Event(), Event()
    calls = []

    def blocked(code, **_):
        calls.append(code)
        if len(calls) == 2:
            entered.set()
        release.wait(2)
        return SimpleNamespace(items=[bar(PREVIOUS, 100), bar(DAY, 106)])

    collector.collect_index_history.side_effect = blocked
    monkeypatch.setattr(module, "_HISTORY_RANKING_TIMEOUT_SECONDS", 0.05)
    try:
        result = module.collect_sector_ranking(DAY)
        assert entered.is_set()
        assert not result.complete
        assert "sector_history_timeout" in result.data_missing
        assert "sector_history_coverage:0/2" in result.data_missing
        assert len(calls) == 2
    finally:
        release.set()
