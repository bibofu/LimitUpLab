from datetime import date, timedelta
import pytest

from app.post_limit_query_contract import (
    PostLimitQueryContract,
    build_post_limit_query_contract,
    looks_like_post_limit_path_question,
    looks_like_post_limit_question,
    looks_like_post_limit_statistics_question,
)
from app.repositories.post_limit_repository import PostLimitDataset
from app.repositories import SQLiteFirstBoardRepository
from app.services.post_limit import (
    _attach_outcome,
    _matched_shapes,
    build_post_limit_path,
    build_post_limit_screen,
    build_post_limit_statistics,
    matches_high_drawdown,
    matches_volume_consolidation,
)
from app.agents.tools import AgentToolRegistry
from app.models import AgentChatResponse


# Prepare the dates fixture or observation used by the surrounding regression scenario.
def _dates(count=40):
    result = []
    day = date(2026, 7, 1)
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


# Build the PostLimitDataset fixture used by the surrounding regression scenario.
def _dataset():
    dates = _dates()
    events = [
        {"symbol": "999999", "name": "日期占位", "trade_date": day, "closed_limit": 0,
         "board_height": 0, "industry": "", "concept": ""}
        for day in dates
    ]
    bars = []
    for symbol, name in (("600001", "回撤样本"), ("600002", "整理样本"), ("600003", "二进三样本")):
        for day in dates:
            bars.append({"symbol": symbol, "trade_date": day, "open": 10.0, "high": 10.1,
                         "low": 9.9, "close": 10.0, "volume": 1000.0, "source": "test"})
    anchor = dates[32]
    events.extend([
        {"symbol": "600001", "name": "回撤样本", "trade_date": anchor, "closed_limit": 1,
         "board_height": 1, "industry": "机械", "concept": "机器人"},
        {"symbol": "600002", "name": "整理样本", "trade_date": anchor, "closed_limit": 1,
         "board_height": 1, "industry": "电子", "concept": "芯片"},
        {"symbol": "600003", "name": "二进三样本", "trade_date": dates[38], "closed_limit": 1,
         "board_height": 1, "industry": "软件", "concept": "数据"},
        {"symbol": "600003", "name": "二进三样本", "trade_date": dates[39], "closed_limit": 1,
         "board_height": 2, "industry": "软件", "concept": "数据"},
    ])
    by_key = {(bar["symbol"], bar["trade_date"]): bar for bar in bars}
    for symbol in ("600001", "600002"):
        by_key[(symbol, anchor)].update(open=10.0, high=11.0, low=10.0, close=11.0, volume=1000.0)
    by_key[("600001", dates[33])].update(open=11.0, high=12.0, low=10.9, close=11.6, volume=1200.0)
    by_key[("600001", dates[34])].update(open=10.5, high=10.6, low=10.35, close=10.35, volume=900.0)
    by_key[("600001", dates[35])].update(open=10.4, high=10.55, low=10.4, close=10.5, volume=800.0)
    for index, close in zip((33, 34, 35), (10.9, 10.85, 10.9)):
        by_key[("600002", dates[index])].update(
            open=close, high=11.1, low=10.7, close=close, volume=500.0
        )
    by_key[("600003", dates[38])].update(open=10.0, high=11.0, low=10.0, close=11.0)
    by_key[("600003", dates[39])].update(open=11.0, high=12.1, low=11.0, close=12.1)
    return PostLimitDataset(events, list(by_key.values()), dates, dates, date.fromisoformat(dates[-1]))


# Regression scenario: query contract routes shapes and user numeric overrides.
def test_query_contract_routes_shapes_and_user_numeric_overrides():
    contract = build_post_limit_query_contract(
        "2026-09-07近10日涨停后从高位回撤15%以上的2板票，题材为机器人，按回撤从高到低排序，前5只",
        planner_arguments={
            "recent_limit_days": 3, "min_peak_drawdown_pct": 5,
            "board_height": 4, "query": "芯片", "sort_by": "volume_ratio",
        },
    )
    assert looks_like_post_limit_question("有哪些涨停后从高位大幅回撤的票")
    assert looks_like_post_limit_question("哪些涨停票从高点回撤了")
    assert looks_like_post_limit_path_question("它涨停后怎么走的")
    assert contract.shape == "high_drawdown"
    assert contract.data_as_of == date(2026, 9, 7)
    assert contract.recent_limit_days == 10
    assert contract.min_peak_drawdown_pct == 15
    assert contract.board_height == 2
    assert contract.query == "机器人"
    assert contract.sort_by == "peak_drawdown_pct" and contract.sort_order == "desc"
    assert contract.limit == 5
    assert build_post_limit_query_contract("量比低于0.6的横盘缩量票").max_volume_ratio == .6
    stats = build_post_limit_query_contract("比较近7日横盘缩量和回撤企稳的历史表现")
    assert looks_like_post_limit_statistics_question("统计高位回撤历史表现")
    assert stats.mode == "statistics" and stats.statistics_days == 7
    assert stats.shapes == ("volume_consolidation", "pullback_stabilizing")
    assert build_post_limit_query_contract("按题材分组统计横盘缩量历史表现").group_by == "concept"
    assert looks_like_post_limit_path_question("600001涨停后的逐日走势")
    assert looks_like_post_limit_path_question("回撤样本涨停后的走势")
    anchored_path = build_post_limit_query_contract("600001从9月1日涨停后怎么走，截至9月7日")
    assert anchored_path.anchor_date == date(2026, 9, 1)
    assert anchored_path.data_as_of == date(2026, 9, 7)


# Regression scenario: post limit trace keeps all candidate names for stock links.
def test_post_limit_trace_keeps_all_candidate_names_for_stock_links(
    monkeypatch,
    tmp_path,
):
    dataset = _dataset()
    # The inline callback supplies the fixture value or replacement behavior used by this test; it
    # is evaluated only when the code under test calls it.
    monkeypatch.setattr(
        "app.agents.tools.load_post_limit_dataset",
        lambda *_args, **_kwargs: dataset,
    )
    registry = AgentToolRegistry(
        events=[],
        first_board_repository=SQLiteFirstBoardRepository(tmp_path / "links.sqlite"),
    )
    contract = build_post_limit_query_contract(
        "近期涨停后回撤比较多的股票有哪些",
        request_trade_date=date.fromisoformat(dataset.calendar[35]),
    )

    result = registry.post_limit_screen(contract)
    traced_candidates = result.trace().output["candidates"]
    response = AgentChatResponse(
        session_id="stock-links",
        intent="post_limit_screen",
        answer="回撤样本（600001）",
        tool_calls=["post_limit_screen", "template_general_answer"],
        tool_results=[result.trace()],
        generated_by="test",
    )

    assert traced_candidates == [
        {
            "symbol": "600001",
            "name": "回撤样本",
            "anchor_date": dataset.calendar[32],
        }
    ]
    assert [item.model_dump(mode="json") for item in response.stock_mentions] == [
        {
            "name": "回撤样本",
            "symbol": "600001",
            "trade_date": dataset.calendar[35],
        }
    ]


# Regression scenario: premarket observation shapes default to seven event days.
def test_premarket_observation_shapes_default_to_seven_event_days():
    high_drawdown = build_post_limit_query_contract(
        "有哪些涨停后从高位大幅回撤的票",
        planner_arguments={"recent_limit_days": 5},
    )
    volume_consolidation = build_post_limit_query_contract("有哪些缩量整理的股票")
    combined = build_post_limit_query_contract("筛选高位回撤和回撤企稳的股票")
    other_shape = build_post_limit_query_contract(
        "断板修复有哪些",
        planner_arguments={"recent_limit_days": 5},
    )
    stock_path = build_post_limit_query_contract("600001涨停后的逐日走势")

    assert high_drawdown.version == "post-limit-query-v3"
    assert high_drawdown.recent_limit_days == 7
    assert volume_consolidation.recent_limit_days == 7
    assert combined.recent_limit_days == 7
    assert other_shape.recent_limit_days == 5
    assert stock_path.recent_limit_days == 5


# Regression scenario: explicit event window overrides seven day default.
def test_explicit_event_window_overrides_seven_day_default():
    contract = build_post_limit_query_contract(
        "近10个交易日涨停后从高位回撤的股票",
        planner_arguments={"recent_limit_days": 5},
    )

    assert contract.recent_limit_days == 10


# Regression scenario: inclusive rule boundaries.
def test_inclusive_rule_boundaries():
    assert matches_high_drawdown(1, 10)
    assert matches_high_drawdown(4, 10)
    assert not matches_high_drawdown(0, 20)
    assert matches_volume_consolidation(2, 8, -10, .75)
    assert matches_volume_consolidation(4, 8, 8, .75)
    assert not matches_volume_consolidation(4, 8.0001, 0, .75)


# Regression scenario: strong nonconsecutive and broken board repair rules.
def test_strong_nonconsecutive_and_broken_board_repair_rules():
    base = {
        "anchor_age": 2, "anchor_change_pct": 3, "peak_drawdown_pct": 2,
        "range_pct": 10, "volume_ratio": 1, "latest_close_above_previous": True,
        "close_position_pct": 80, "two_closes_rising": True, "range_low": 9.6,
        "anchor_close": 10, "latest_close_above_previous_high": False,
    }
    assert "strong_nonconsecutive" in _matched_shapes(
        base, {"board_height": 1}, PostLimitQueryContract(shape="strong_nonconsecutive")
    )
    repair = {
        **base, "anchor_change_pct": -5, "two_closes_rising": False,
        "latest_close_above_previous_high": True,
    }
    assert "broken_board_repair" in _matched_shapes(
        repair, {"board_height": 2}, PostLimitQueryContract(shape="broken_board_repair")
    )


# Regression scenario: screen uses peak before observation day and preserves overlap.
def test_screen_uses_peak_before_observation_day_and_preserves_overlap():
    dataset = _dataset()
    end = date.fromisoformat(dataset.calendar[35])
    result = build_post_limit_screen(
        dataset,
        PostLimitQueryContract(
            shapes=("high_drawdown", "pullback_stabilizing"),
            shape="high_drawdown", data_as_of=end, limit=100,
        ),
    )
    item = next(item for item in result["candidates"] if item["symbol"] == "600001")
    assert item["peak_date"] == dataset.calendar[33]
    assert item["peak_drawdown_pct"] == 12.5
    assert item["matched_shapes"] == ["high_drawdown", "pullback_stabilizing"]


# Regression scenario: seven day agent window reports a sixth day event gap.
@pytest.mark.parametrize("offset", [5, 6])
def test_seven_day_agent_window_reports_a_sixth_day_event_gap(offset):
    dataset = _dataset()
    end = date.fromisoformat(dataset.calendar[35])
    missing_day = dataset.calendar[35 - offset]
    changed = PostLimitDataset(
        [event for event in dataset.events if event["trade_date"] != missing_day],
        dataset.bars,
        dataset.calendar,
        [day for day in dataset.event_dates if day != missing_day],
        dataset.latest_data_date,
    )
    contract = build_post_limit_query_contract(
        "有哪些涨停后从高位大幅回撤的票",
        request_trade_date=end,
    )

    result = build_post_limit_screen(changed, contract)

    assert result["query_contract"]["recent_limit_days"] == 7
    assert result["status"] == "data_missing"
    assert result["data_missing"] == ["recent_event_dates"]


# Regression scenario: direct contract and statistics use seven days.
@pytest.mark.parametrize("shape", ["high_drawdown", "volume_consolidation"])
def test_direct_contract_and_statistics_use_seven_days(shape):
    contract = PostLimitQueryContract(shape=shape, mode="statistics")
    assert contract.to_dict()["recent_limit_days"] == 7
    assert PostLimitQueryContract(shape=shape, recent_limit_days=10).recent_limit_days == 10


# Regression scenario: other shapes ignore injected seven day capability default.
@pytest.mark.parametrize("message", ["回撤企稳有哪些", "强势不连板有哪些", "断板修复有哪些", "2进3有哪些"])
def test_other_shapes_ignore_injected_seven_day_capability_default(message):
    assert build_post_limit_query_contract(message, planner_arguments={"recent_limit_days": 7}).recent_limit_days == 5


# Regression scenario: statistics separates event lookback and signal day count.
def test_statistics_separates_event_lookback_and_signal_day_count():
    contract = build_post_limit_query_contract("统计近3个信号日、回看近10个交易日有收盘涨停的缩量整理历史表现")
    assert contract.recent_limit_days == 10
    assert contract.statistics_days == 3
    assert build_post_limit_query_contract("比较近7日横盘缩量和回撤企稳的历史表现").recent_limit_days == 7


# Regression scenario: statistics discloses missing event windows.
def test_statistics_discloses_missing_event_windows():
    dataset = _dataset()
    missing_day = dataset.calendar[29]
    dataset.events[:] = [e for e in dataset.events if e["trade_date"] != missing_day]
    result = build_post_limit_statistics(dataset, PostLimitQueryContract(mode="statistics"))
    assert "recent_event_dates" in result["data_missing"]
    assert any(missing_day in warning for warning in result["warnings"])
    assert not result["comparison_allowed"]


# Regression scenario: repeated limit up resets anchor and future bars are isolated.
def test_repeated_limit_up_resets_anchor_and_future_bars_are_isolated():
    dataset = _dataset()
    later_anchor = dataset.calendar[34]
    events = [*dataset.events, {
        "symbol": "600001", "name": "回撤样本", "trade_date": later_anchor,
        "closed_limit": 1, "board_height": 1, "industry": "机械", "concept": "机器人",
    }]
    bars = [dict(item) for item in dataset.bars]
    bar_map = {(bar["symbol"], bar["trade_date"]): bar for bar in bars}
    bar_map[("600001", dataset.calendar[34])].update(open=11.6, high=12.76, low=11.6, close=12.76)
    bar_map[("600001", dataset.calendar[35])].update(open=12.0, high=12.2, low=11.35, close=11.4)
    future_day = dataset.calendar[36]
    bar_map[("600001", future_day)].update(open=10, high=99, low=9.9, close=10)
    changed = PostLimitDataset(events, list(bar_map.values()), dataset.calendar, dataset.event_dates, dataset.latest_data_date)
    result = build_post_limit_screen(
        changed,
        PostLimitQueryContract(shape="high_drawdown", data_as_of=date.fromisoformat(dataset.calendar[35])),
    )
    item = next(row for row in result["candidates"] if row["symbol"] == "600001")
    assert item["anchor_date"] == later_anchor
    assert item["peak_price"] == 12.76


# Regression scenario: missing and mixed source are disclosed separately.
def test_missing_and_mixed_source_are_disclosed_separately():
    dataset = _dataset()
    end = date.fromisoformat(dataset.calendar[35])
    bars = [dict(item) for item in dataset.bars if not (
        item["symbol"] == "600001" and item["trade_date"] == dataset.calendar[20]
    )]
    for item in bars:
        if item["symbol"] == "600002" and item["trade_date"] == dataset.calendar[34]:
            item["source"] = "other"
    changed = PostLimitDataset(dataset.events, bars, dataset.calendar, dataset.event_dates, dataset.latest_data_date)
    result = build_post_limit_screen(
        changed,
        PostLimitQueryContract(
            shapes=("high_drawdown", "volume_consolidation"),
            data_as_of=end, limit=100,
        ),
    )
    assert result["exclusions"]["missing_history20"] >= 1
    assert result["exclusions"]["mixed_or_missing_source"] >= 1
    assert set(result["data_missing"]) >= {"missing_history20", "mixed_or_missing_source"}


# Regression scenario: consolidation and second to third presets.
def test_consolidation_and_second_to_third_presets():
    dataset = _dataset()
    consolidation = build_post_limit_screen(
        dataset,
        PostLimitQueryContract(shape="volume_consolidation", data_as_of=date.fromisoformat(dataset.calendar[35])),
    )
    assert [item["symbol"] for item in consolidation["candidates"]] == ["600002"]
    relay = build_post_limit_screen(
        dataset,
        PostLimitQueryContract(shape="second_to_third", data_as_of=dataset.latest_data_date),
    )
    assert [item["symbol"] for item in relay["candidates"]] == ["600003"]


# Regression scenario: single stock path is anchored and annotated.
def test_single_stock_path_is_anchored_and_annotated():
    dataset = _dataset()
    result = build_post_limit_path(
        dataset,
        PostLimitQueryContract(data_as_of=date.fromisoformat(dataset.calendar[35])),
        symbol="600001",
    )
    assert result["anchor"]["anchor_date"] == dataset.calendar[32]
    assert [item["day"] for item in result["path"]] == ["T+0", "T+1", "T+2", "T+3"]
    assert result["path"][-1]["change_from_anchor_close_pct"] == -4.5455
    assert "收盘低于涨停收盘" in result["path"][2]["states"]


# Regression scenario: outcome uses exact d1 open and reports mae mfe.
def test_outcome_uses_exact_d1_open_and_reports_mae_mfe():
    dataset = _dataset()
    bars = {(bar["symbol"], bar["trade_date"]): bar for bar in dataset.bars}
    signal_day = dataset.calendar[32]
    signal = {"symbol": "600001", "signal_date": signal_day}
    outcome, issue = _attach_outcome(signal, bars, dataset.calendar, {})
    assert issue is None
    assert outcome["outcome_start"] == dataset.calendar[33]
    assert outcome["outcome_end"] == dataset.calendar[37]
    assert outcome["d1_close_pct"] == 5.4545
    assert outcome["mae5_pct"] <= outcome["mfe5_pct"]
    immature, issue = _attach_outcome(
        {"symbol": "600001", "signal_date": dataset.calendar[-2]},
        bars, dataset.calendar, {},
    )
    assert immature is None and issue == "immature"


# Regression scenario: statistics uses first trigger and marks small sample.
def test_statistics_uses_first_trigger_and_marks_small_sample():
    dataset = _dataset()
    result = build_post_limit_statistics(
        dataset,
        PostLimitQueryContract(
            mode="statistics", shape="high_drawdown",
            shapes=("high_drawdown",), data_as_of=dataset.latest_data_date,
            statistics_days=7,
        ),
    )
    assert result["snapshot_kind"] == "recomputed_historical_research"
    assert result["signal_count"] == 1
    assert result["complete_sample_count"] == 1
    assert result["sample_quality"] == "insufficient"
    assert result["summaries"][0]["d1_mean_pct"] == result["summaries"][0]["d1_median_pct"]
    assert result["comparison_allowed"] is False
