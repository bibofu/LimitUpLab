"""The market comparison must follow complete exchange sessions and first-board facts."""

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents.review_market_leaders import build_market_leader_profiles
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


def build(events, *, repository=None, start=D[0], end=D[4], calendar=D):
    return build_market_leader_profiles(
        events=events, repository=repository or Repository(),
        start_date=start, end_date=end, trade_dates=calendar,
    )


def test_all_first_board_dates_supply_controls_and_later_events_do_not_rewrite_first_board_facts():
    events = [event(D[0]), event(D[1], height=2), event(D[2], height=3), event(D[3], height=4),
              event(D[0], "600002"), event(D[1], "600003"), market_day(D[4])]
    repo = Repository([snapshot(D[0]), snapshot(D[2], cap=9_000_000_000, label="高位突破首板")])
    original = deepcopy(events)
    result = build(events, repository=repo)
    assert len(result["positive_profiles"]) == 1
    assert {row["symbol"] for row in result["negative_profiles"]} == {"600002", "600003"}
    assert result["unknown_count"] == 0
    assert result["detected_count"] == result["matched_count"] == 1
    leader, = result["leaders"]
    assert leader["first_board_date"] == D[0]
    assert leader["max_board_height"] == 4 and leader["latest_date"] == D[3]
    assert leader["position_label"] == "低位启动首板"
    assert leader["float_market_cap"] == 3_000_000_000
    assert leader["first_limit_time"] == "09:31"
    assert leader["break_count"] == 2 and leader["turnover_rate"] == 4.0
    assert repo.enrichment_calls == [D[0], D[1]]
    assert len(repo.bar_calls) == 1
    assert events == original


@pytest.mark.parametrize("kind", ["future_d2", "missing_market_day", "missing_calendar", "wrong_height"])
def test_unavailable_horizon_or_inconsistent_chain_is_unknown_instead_of_a_loser(kind):
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
    result = build(events, end=end, calendar=calendar)
    assert result["unknown_count"] == 1
    assert result["positive_profiles"] == result["negative_profiles"] == []
    if result["leaders"]:
        assert all(leader["status"] == "unresolved" for leader in result["leaders"])
        assert all(leader["first_board_date"] is None for leader in result["leaders"])


def test_calendar_adjacency_does_not_skip_a_missing_session_or_mistake_weekend_for_a_gap():
    events = [event(D[4]), event(D[5], height=2), event(D[6], height=3)]
    result = build(events, start=D[4], end=D[6])
    assert len(result["positive_profiles"]) == 1  # Friday -> Monday -> Tuesday.
    missing = build([events[0], events[2]], start=D[4], end=D[6])
    assert missing["unknown_count"] == 1
    assert str(D[5]) in " ".join(missing["leaders"][0]["data_missing"])


def test_an_absent_stock_or_failed_board_on_an_observed_day_is_not_a_three_board_success():
    events = [event(D[0]), event(D[0], "600002"), market_day(D[1]), market_day(D[2]),
              event(D[1], "600002", height=7, closed=False)]
    result = build(events, end=D[2])
    assert len(result["negative_profiles"]) == 2
    assert result["positive_profiles"] == result["leaders"] == []
    assert any("不保证全市场采集完整" in note for note in result["notes"])


def test_repeat_cycles_of_one_symbol_are_distinct_and_do_not_share_maximum_height():
    events = [event(D[0]), event(D[1], height=2), event(D[2], height=3),
              event(D[3], closed=False), event(D[4]), event(D[5], height=2),
              event(D[6], height=3), event(D[7], height=4)]
    result = build(events, end=D[7])
    assert len(result["positive_profiles"]) == 2
    assert result["detected_count"] == 2
    assert {(item["first_board_date"], item["max_board_height"]) for item in result["leaders"]} == {(D[0], 3), (D[4], 4)}


def test_a_verified_three_board_episode_does_not_claim_a_later_unverified_five_board_chain():
    events = [event(D[0]), event(D[1], height=2), event(D[2], height=3), event(D[4], height=5)]
    result = build(events)
    leader, = result["leaders"]
    assert len(result["positive_profiles"]) == 1
    assert result["matched_count"] == 1
    assert leader["first_board_date"] == D[0]
    assert leader["status"] == "incomplete_chain"
    assert leader["max_board_height"] == 3 and leader["latest_date"] == D[4]
    assert any("5板" in missing for missing in leader["data_missing"])


def test_outside_window_anchors_are_shown_but_never_enter_this_cohort_or_its_controls():
    events = [event(D[0]), event(D[1], height=2), event(D[2], height=3),
              event(D[3], height=4), event(D[3], "600002"), market_day(D[4]), market_day(D[5])]
    result = build(events, start=D[2], end=D[5])
    assert result["detected_count"] == 1 and result["matched_count"] == 0
    assert result["leaders"][0]["status"] == "outside_window"
    assert result["leaders"][0]["first_board_date"] == D[0]
    assert result["positive_profiles"] == []
    assert [item["symbol"] for item in result["negative_profiles"]] == ["600002"]


def test_post_cutoff_events_never_supply_outcomes_or_raise_the_visible_board_height():
    events = [event(D[0]), event(D[1], height=2), event(D[2], height=3), event(D[3], height=4)]
    pending = build(events, end=D[1])
    assert pending["unknown_count"] == 1 and pending["leaders"] == []
    ready = build(events, end=D[2])
    assert ready["leaders"][0]["max_board_height"] == 3
    assert ready["leaders"][0]["latest_date"] == D[2]


def test_missing_anchor_snapshot_does_not_borrow_a_later_cap_or_estimate_from_turnover():
    events = [event(D[0], amount=8_000_000_000), event(D[1], height=2), event(D[2], height=3)]
    result = build(events, repository=Repository([snapshot(D[2])]), end=D[2])
    profile, = result["positive_profiles"]
    assert profile["float_market_cap"] is None
    assert profile["float_market_cap_source"] is None
    assert "首板当日流通市值快照缺失" in profile["data_missing"]


def test_position_recomputation_is_batched_anchor_bound_and_explicitly_marked():
    events = [event(D[0]), event(D[0], "600002"), event(D[1], height=2), event(D[2], height=3)]
    history = [bar(D[0] - timedelta(days=offset), symbol) for symbol in ("600001", "600002") for offset in range(21)]
    history += [bar(D[3], symbol, close=30) for symbol in ("600001", "600002")]
    repo = Repository(bars=history)
    seen = []
    def classify(bars, cutoff):
        seen.append((bars, cutoff))
        return {"primary": {"regime": "low_base_breakout", "label": "低位启动首板"}}
    with patch("app.agents.review_market_leaders.classify_stock_position", side_effect=classify):
        result = build(events, repository=repo, end=D[2])
    assert len(repo.bar_calls) == 1 and repo.bar_calls[0][0] == ["600001", "600002"]
    assert len(seen) == 2 and all(len(bars) == 21 and max(bar.trade_date for bar in bars) == cutoff == D[0] for bars, cutoff in seen)
    profiles = result["positive_profiles"] + result["negative_profiles"]
    assert all(item["position_source"] == "recomputed_local_daily_bars" for item in profiles)
    assert all("首板位置数据不足" not in item["data_missing"] for item in profiles)
    assert any("2 个首板位置" in note and "重算" in note for note in result["notes"])


def test_missing_anchor_bar_or_too_short_history_cannot_produce_a_position():
    events = [event(D[0]), event(D[1], height=2), event(D[2], height=3)]
    for offsets in (range(1, 24), range(20)):
        repo = Repository(bars=[bar(D[0] - timedelta(days=offset)) for offset in offsets])
        with patch("app.agents.review_market_leaders.classify_stock_position") as classify:
            profile, = build(events, repository=repo)["positive_profiles"]
        assert profile["position_label"] is None
        classify.assert_not_called()


def test_exact_snapshot_position_avoids_bar_reads_and_invalid_features_stay_missing():
    events = [event(D[0], first_limit_time=time(0), turnover_rate=0), event(D[1], height=2), event(D[2], height=3)]
    repo = Repository([snapshot(D[0], cap=float("nan"))])
    result = build(events, repository=repo)
    profile, = result["positive_profiles"]
    assert repo.bar_calls == []
    assert profile["position_source"] == "first_board_snapshot"
    assert profile["first_limit_minutes"] is None and profile["turnover_rate"] is None
    assert profile["float_market_cap"] is None
    assert len(profile["data_missing"]) == 3


@pytest.mark.parametrize("source,notice", [
    ("derived_from_amount_and_turnover", "当日估计值而非实测流通市值"),
    (None, "缺少来源标识"),
])
def test_snapshot_cap_provenance_survives_and_is_explained_even_if_the_ui_omits_extra_fields(source, notice):
    saved = snapshot(D[0])
    saved.float_market_cap_source = source
    events = [event(D[0]), event(D[1], height=2), event(D[2], height=3)]
    result = build(events, repository=Repository([saved]), end=D[2])
    profile, = result["positive_profiles"]
    assert profile["float_market_cap"] == saved.float_market_cap
    assert profile["float_market_cap_source"] == source
    assert result["leaders"][0]["float_market_cap_source"] == source
    assert any(notice in note for note in result["notes"])
