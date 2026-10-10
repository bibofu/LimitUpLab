"""Shared leader helpers retain exact dates, provenance and missing-data boundaries."""

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents.review_market_leaders import _build_profiles, _leader_episodes
from app.models import StockDailyBar
from app.services.sample_data import SAMPLE_EVENTS


D = [date(2026, 9, day) for day in (7, 8, 9, 10, 11, 14, 15, 16, 17, 18)]


def event(day, symbol="600001", height=1, closed=True, **extra):
    return SAMPLE_EVENTS[2].model_copy(update={
        "trade_date": day, "symbol": symbol, "name": f"样本{symbol}",
        "board_height": height, "closed_limit": closed,
        "first_limit_time": time(9, 31), "break_count": 2, "turnover_rate": 4.0,
        **extra,
    })


def market_day(day):
    return event(day, "999999", closed=False)


def snapshot(day, symbol="600001", cap=3_000_000_000, label="低位启动首板"):
    return SimpleNamespace(
        trade_date=day, symbol=symbol, float_market_cap=cap,
        float_market_cap_source="fixture", position={"primary": {
            "label": label, "regime": "low_base_breakout" if label else "unclassified",
        }},
    )


def bar(day, symbol="600001", close=10):
    return StockDailyBar(
        symbol=symbol, trade_date=day, open=9, high=max(11, close), low=8,
        close=close, volume=1_000, amount=10_000, source="fixture",
        created_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
    )


class Repository:
    def __init__(self, snapshots=(), bars=()):
        self.snapshots, self.bars = list(snapshots), list(bars)
        self.enrichment_calls, self.bar_calls = [], []

    def list_enrichment_for_date(self, day):
        self.enrichment_calls.append(day)
        return [item for item in self.snapshots if item.trade_date == day]

    def list_daily_bars_for_symbols(self, symbols, *, end_date):
        self.bar_calls.append((symbols, end_date))
        # Deliberately include future observations; the builder must enforce each anchor itself.
        return [item for item in self.bars if item.symbol in symbols]


def profiles(events, repository=None, end=D[4]):
    feature_events = {(item.trade_date, item.symbol): item for item in events}
    return _build_profiles(feature_events, repository or Repository(), end)


def episodes(events, *, start=D[0], end=D[4], calendar=D):
    calendar = sorted(day for day in calendar if day <= end)
    return _leader_episodes(
        {(item.trade_date, item.symbol): item for item in events},
        calendar, {day: index for index, day in enumerate(calendar)}, start, end,
    )


def test_first_board_profiles_use_exact_snapshot_dates_and_do_not_mutate_events():
    events = [event(D[0]), event(D[0], "600002"), event(D[1], "600003")]
    repo = Repository([snapshot(D[0]), snapshot(D[2], cap=9_000_000_000, label="高位突破首板")])
    original_events, original_snapshots = deepcopy(events), deepcopy(repo.snapshots)
    result = profiles(events, repo)
    assert set(result) == {(D[0], "600001"), (D[0], "600002"), (D[1], "600003")}
    profile = result[(D[0], "600001")]
    assert profile["first_board_date"] == D[0]
    assert profile["position_label"] == "低位启动首板"
    assert profile["float_market_cap"] == 3_000_000_000
    assert profile["first_limit_time"] == "09:31"
    assert profile["break_count"] == 2 and profile["turnover_rate"] == 4.0
    assert repo.enrichment_calls == [D[0], D[1]]
    assert len(repo.bar_calls) == 1
    assert events == original_events and repo.snapshots == original_snapshots


@pytest.mark.parametrize("kind", ["future_d2", "missing_market_day", "missing_calendar", "wrong_height"])
def test_unavailable_horizon_or_inconsistent_chain_cannot_verify_an_anchor(kind):
    events = [event(D[0]), event(D[1], height=2), event(D[2], height=3)]
    end, calendar = D[2], D
    if kind == "future_d2":
        end = D[1]
    elif kind == "missing_market_day":
        events = [events[0], events[2]]
    elif kind == "missing_calendar":
        calendar = []
    else:
        events[1] = event(D[1], height=4)
    result = episodes(events, end=end, calendar=calendar)
    if kind == "future_d2":
        assert result == []
    else:
        assert result and all(leader["status"] == "unresolved" for leader in result)
        assert all(leader["first_board_date"] is None for leader in result)


def test_calendar_adjacency_does_not_skip_a_missing_session_or_mistake_weekend_for_a_gap():
    events = [event(D[4]), event(D[5], height=2), event(D[6], height=3)]
    leader, = episodes(events, start=D[4], end=D[6])
    assert leader["first_board_date"] == D[4]  # Friday -> Monday -> Tuesday.
    missing, = episodes([events[0], events[2]], start=D[4], end=D[6])
    assert missing["first_board_date"] is None
    assert str(D[5]) in " ".join(missing["data_missing"])


def test_an_absent_stock_or_failed_board_does_not_create_a_leader_episode():
    events = [event(D[0]), event(D[0], "600002"), market_day(D[1]), market_day(D[2]),
              event(D[1], "600002", height=7, closed=False)]
    assert episodes(events, end=D[2]) == []


def test_repeat_cycles_of_one_symbol_are_distinct_and_do_not_share_maximum_height():
    events = [event(D[0]), event(D[1], height=2), event(D[2], height=3),
              event(D[3], closed=False), event(D[4]), event(D[5], height=2),
              event(D[6], height=3), event(D[7], height=4)]
    original = deepcopy(events)
    result = episodes(events, end=D[7])
    assert len(result) == 2
    assert {(item["first_board_date"], item["max_board_height"]) for item in result} == {(D[0], 3), (D[4], 4)}
    assert events == original


def test_a_verified_three_board_episode_does_not_claim_a_later_unverified_five_board_chain():
    events = [event(D[0]), event(D[1], height=2), event(D[2], height=3), event(D[4], height=5)]
    leader, = episodes(events)
    assert leader["first_board_date"] == D[0]
    assert leader["status"] == "incomplete_chain"
    assert leader["max_board_height"] == 3 and leader["latest_date"] == D[4]
    assert any("5板" in missing for missing in leader["data_missing"])


def test_outside_window_anchor_is_preserved_and_marked():
    events = [event(D[0]), event(D[1], height=2), event(D[2], height=3),
              event(D[3], height=4), event(D[3], "600002"), market_day(D[4]), market_day(D[5])]
    leader, = episodes(events, start=D[2], end=D[5])
    assert leader["status"] == "outside_window"
    assert leader["first_board_date"] == D[0]


def test_post_cutoff_events_never_create_episodes_or_raise_the_visible_board_height():
    events = [event(D[0]), event(D[1], height=2), event(D[2], height=3), event(D[3], height=4)]
    assert episodes(events, end=D[1]) == []
    ready, = episodes(events, end=D[2])
    assert ready["max_board_height"] == 3
    assert ready["latest_date"] == D[2]


def test_missing_anchor_snapshot_does_not_borrow_a_later_cap_or_estimate_from_turnover():
    events = [event(D[0], amount=8_000_000_000)]
    profile, = profiles(events, Repository([snapshot(D[2])]), end=D[2]).values()
    assert profile["float_market_cap"] is None
    assert profile["float_market_cap_source"] is None
    assert "首板当日流通市值快照缺失" in profile["data_missing"]


def test_position_recomputation_is_batched_anchor_bound_and_explicitly_marked():
    events = [event(D[0]), event(D[0], "600002")]
    history = [bar(D[0] - timedelta(days=offset), symbol) for symbol in ("600001", "600002") for offset in range(21)]
    history += [bar(D[3], symbol, close=30) for symbol in ("600001", "600002")]
    repo = Repository(bars=history)
    seen = []
    def classify(bars, cutoff):
        seen.append((bars, cutoff))
        return {"primary": {"regime": "low_base_breakout", "label": "低位启动首板"}}
    with patch("app.agents.review_market_leaders.classify_stock_position", side_effect=classify):
        result = profiles(events, repo, end=D[2])
    assert len(repo.bar_calls) == 1 and repo.bar_calls[0][0] == ["600001", "600002"]
    assert len(seen) == 2 and all(len(bars) == 21 and max(bar.trade_date for bar in bars) == cutoff == D[0] for bars, cutoff in seen)
    assert all(item["position_source"] == "recomputed_local_daily_bars" for item in result.values())
    assert all("首板位置数据不足" not in item["data_missing"] for item in result.values())


def test_missing_anchor_bar_or_too_short_history_cannot_produce_a_position():
    events = [event(D[0])]
    for offsets in (range(1, 24), range(20)):
        repo = Repository(bars=[bar(D[0] - timedelta(days=offset)) for offset in offsets])
        with patch("app.agents.review_market_leaders.classify_stock_position") as classify:
            profile, = profiles(events, repo).values()
        assert profile["position_label"] is None
        classify.assert_not_called()


def test_exact_snapshot_position_avoids_bar_reads_and_invalid_features_stay_missing():
    events = [event(D[0], first_limit_time=time(0), turnover_rate=0)]
    repo = Repository([snapshot(D[0], cap=float("nan"))])
    profile, = profiles(events, repo).values()
    assert repo.bar_calls == []
    assert profile["position_source"] == "first_board_snapshot"
    assert profile["first_limit_minutes"] is None and profile["turnover_rate"] is None
    assert profile["float_market_cap"] is None
    assert len(profile["data_missing"]) == 3


@pytest.mark.parametrize("source", ["derived_from_amount_and_turnover", None])
def test_snapshot_cap_provenance_survives_without_inventing_a_missing_source(source):
    saved = snapshot(D[0])
    saved.float_market_cap_source = source
    profile, = profiles([event(D[0])], Repository([saved]), end=D[2]).values()
    assert profile["float_market_cap"] == saved.float_market_cap
    assert profile["float_market_cap_source"] == source
