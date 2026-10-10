"""Leader descriptions use verified episodes and exact first/second-board dates."""

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents.review_digest_leaders import build_digest_leaders
from app.models import StockDailyBar
from app.services.sample_data import SAMPLE_EVENTS


D = [date(2026, 9, day) for day in (7, 8, 9, 10, 11, 14, 15, 16, 17, 18)]


def event(day, symbol="600001", height=1, **extra):
    return SAMPLE_EVENTS[2].model_copy(update={
        "trade_date": day, "symbol": symbol, "name": symbol, "board_height": height,
        "closed_limit": True, "first_limit_time": time(9, 31), "break_count": 0,
        "turnover_rate": 4.0, "industry": "首板行业", "concept": "题材甲、题材乙", **extra,
    })


def chain(start=0, stop=3, symbol="600001"):
    return [event(D[index], symbol, index - start + 1) for index in range(start, stop)]


def bar(day, symbol="600001", **extra):
    return StockDailyBar(
        symbol=symbol, trade_date=day, volume=1000, amount=10000, source="fixture",
        created_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
        **{"open": 10, "high": 11, "low": 9, "close": 10, **extra},
    )


def snapshot(day, symbol="600001", **extra):
    return SimpleNamespace(
        **{"trade_date": day, "symbol": symbol, "float_market_cap": 3_000_000_000,
           "float_market_cap_source": "fixture", "position": {"primary": {
               "regime": "low_base_breakout", "label": "低位启动首板",
           }}, **extra},
    )


class Repository:
    def __init__(self, snapshots=(), bars=()):
        self.snapshots, self.bars = list(snapshots), list(bars)
        self.bar_calls, self.snapshot_calls = [], []

    def list_enrichment_for_date(self, day):
        self.snapshot_calls.append(day)
        return [item for item in self.snapshots if item.trade_date == day]

    def list_daily_bars_for_symbols(self, symbols, *, end_date):
        self.bar_calls.append((symbols, end_date))
        return self.bars  # Deliberately return future and unrelated data to test local guards.


def build(events, *, repository=None, end=D[4], calendar=D):
    return build_digest_leaders(events=events, repository=repository or Repository(),
                                end_date=end, trade_dates=calendar)


def test_existing_high_board_before_window_is_included_but_expired_episode_is_not():
    events = chain(0, 7) + chain(0, 3, "600002")
    events += [event(D[7], "600003", height=9, closed_limit=False)]
    repo = Repository([snapshot(D[0]), snapshot(D[6], float_market_cap=99e8)],
                      [bar(D[0]), bar(D[1], open=10.5)])
    events[6] = event(D[6], height=7, industry="后来行业", concept="后来题材")
    stocks, notes = build(events, repository=repo, end=D[8])
    stock, = stocks
    assert stock.first_board_date == D[0] and stock.max_board_height == 7
    assert stock.industry == "首板行业" and stock.concepts == ["题材乙", "题材甲"]
    assert stock.float_market_cap == 3e9 and stock.position_label == "低位启动首板"
    assert stock.second_board_date == D[1] and stock.second_open_pct == pytest.approx(5)
    assert "首板日在本期观察窗外" not in stock.data_missing
    assert len(repo.bar_calls) == 1 and repo.snapshot_calls == [D[0]]
    assert any("窗口前已达三板" in note for note in notes)


def test_two_cycles_of_the_same_symbol_remain_separate_within_last_five_dates():
    stocks, _ = build(chain(3, 6) + chain(7, 10), end=D[9])
    assert {(row.first_board_date, row.second_board_date, row.max_board_height) for row in stocks} == {
        (D[3], D[4], 3), (D[7], D[8], 3),
    }


def test_exact_second_day_and_cutoff_prevent_later_bars_events_or_snapshots_from_filling_gaps():
    repo = Repository([snapshot(D[3])], [bar(D[0]), bar(D[2], open=11), bar(D[3], open=20)])
    stocks, _ = build(chain(0, 4), repository=repo, end=D[2])
    stock, = stocks
    assert stock.max_board_height == 3 and stock.second_board_date == D[1]
    assert stock.second_open_pct is None and stock.second_board_shape is None
    assert stock.float_market_cap is None
    assert any("二板开盘K线缺失" in item for item in stock.data_missing)


@pytest.mark.parametrize("prices,shape", [
    ({"open": 11, "high": 11, "low": 11, "close": 11}, "一字板"),
    ({"open": 10, "high": 11, "low": 10, "close": 11}, "有价格波动"),
    ({"open": 11, "high": 10, "low": 9, "close": 11}, None),
    ({"open": 11, "high": float("nan"), "low": 9, "close": 11}, None),
])
def test_board_shape_requires_complete_legal_ohlc_and_verified_limit_event(prices, shape):
    repo = Repository(bars=[bar(D[0]), bar(D[1], **prices)])
    stock, = build(chain(), repository=repo)[0]
    assert stock.second_board_shape == shape
    events = chain()
    events[1] = event(D[1], height=2, closed_limit=False)
    stock, = build(events, repository=repo)[0]
    assert stock.first_board_date is None and stock.second_board_shape is None


def test_missing_features_stay_missing_while_known_zero_breaks_are_preserved():
    events = chain()
    events[0] = event(D[0], first_limit_time=time(0), break_count=None, turnover_rate=None,
                      industry="", concept="未知")
    events[1] = event(D[1], height=2, first_limit_time=time(0), break_count=0, turnover_rate=0)
    stock, = build(events)[0]
    assert stock.first_limit_time is None and stock.break_count is None and stock.turnover_rate is None
    assert stock.industry is None and stock.concepts == []
    assert stock.second_limit_time is None and stock.second_break_count == 0
    assert stock.second_turnover_rate is None and stock.second_open_pct is None
    assert {"首板行业缺失", "首板题材缺失", "二板首次封板时间缺失", "二板换手率缺失"} <= set(stock.data_missing)


def test_unknown_anchor_does_not_guess_first_or_second_features_or_claim_source_height():
    repo = Repository([snapshot(D[0])], [bar(D[0]), bar(D[1])])
    stock, = build([event(D[0]), event(D[2], height=3)], repository=repo)[0]
    assert stock.first_board_date is stock.second_board_date is stock.max_board_height is None
    assert stock.industry is stock.float_market_cap is stock.second_open_pct is None
    assert repo.bar_calls == repo.snapshot_calls == []
    assert any("来源报3板" in item for item in stock.data_missing)


def test_verified_chain_keeps_verified_maximum_when_later_report_has_a_gap():
    stock, = build(chain() + [event(D[4], height=5)])[0]
    assert stock.first_board_date == D[0] and stock.max_board_height == 3
    assert any("5板" in item for item in stock.data_missing)


def test_exact_duplicates_do_not_multiply_cycles_and_conflicts_do_not_choose_last_record():
    events = chain()
    assert len(build(events + [events[1]])[0]) == 1
    for conflicting in ([*events, event(D[1], height=7)], [event(D[1], height=7), *events]):
        stocks, notes = build(conflicting)
        assert all(stock.first_board_date is None for stock in stocks)
        assert all(stock.max_board_height is None for stock in stocks)
        assert any("记录未用于核验" in note for note in notes)


def test_conflicting_high_event_keeps_existing_verified_episode_without_inflating_height():
    events = chain(0, 4) + [event(D[3], height=4, first_limit_time=time(10))]
    stocks, notes = build(events)
    stock, = stocks
    assert stock.first_board_date == D[0] and stock.max_board_height == 3
    assert any("事件记录冲突" in missing for missing in stock.data_missing)
    assert any("冲突" in note for note in notes)


def test_conflicting_exact_day_bars_are_not_used_for_open_gap_or_shape():
    repo = Repository(bars=[bar(D[0]), bar(D[1]), bar(D[1], open=10.5)])
    stock, = build(chain(), repository=repo)[0]
    assert stock.second_open_pct is None and stock.second_board_shape is None
    assert any("K线记录冲突" in item for item in stock.data_missing)


def test_position_recompute_and_estimated_cap_provenance_are_reported_and_batched():
    history = [bar(D[0] - timedelta(days=offset)) for offset in range(21)]
    history += [bar(D[1]), bar(D[3], close=30)]
    repo = Repository([snapshot(D[0], position=None, float_market_cap_source="derived_from_amount_and_turnover")], history)
    with patch("app.agents.review_market_leaders.classify_stock_position",
               return_value={"primary": {"regime": "low_base_breakout", "label": "低位启动首板"}}) as classify:
        stocks, notes = build(chain(), repository=repo, end=D[2])
    assert len(repo.bar_calls) == 1 and stocks[0].position_label == "低位启动首板"
    assert max(item.trade_date for item in classify.call_args.args[0]) == D[0]
    assert any("本地K线重算" in note for note in notes)
    assert any("当日估计值而非实测" in note for note in notes)


def test_calendar_shortfall_is_explicit_and_missing_window_days_do_not_shift_back():
    stocks, notes = build(chain(), end=D[2], calendar=D[:3])
    assert stocks[0].first_board_date == D[0]
    assert any("不足5日" in note for note in notes)
    assert build(chain(), calendar=[])[0] == []
    stocks, notes = build(chain(), end=D[8])
    assert stocks == []
    assert any("不以更早交易日补位" in note for note in notes)


def test_event_outside_verified_calendar_does_not_create_an_extra_window_session():
    stocks, notes = build([event(date(2026, 9, 12), height=3)], end=D[7])
    assert stocks == []
    assert any("未列入已核验交易日历" in note for note in notes)
