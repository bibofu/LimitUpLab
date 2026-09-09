from datetime import date, datetime, timedelta, timezone
import json
import pytest

from app.post_limit_query_contract import (
    PostLimitQueryContract,
    build_post_limit_query_contract,
    looks_like_post_limit_path_question,
    looks_like_post_limit_question,
    looks_like_post_limit_statistics_question,
)
from app.repositories.post_limit_repository import PostLimitDataset
from app.services.post_limit import (
    _attach_outcome,
    _matched_shapes,
    build_post_limit_path,
    build_post_limit_screen,
    build_post_limit_statistics,
    matches_high_drawdown,
    matches_volume_consolidation,
)
from app.agents.chat import answer_first_board_chat
from app.agents.tools import AgentToolRegistry, ToolResult
from app.agents.tool_policy import QuestionSignals
from app.models import AgentChatRequest, AgentRun
from app.services.llm_provider import DisabledLLMProvider, LLMProvider, LLMResult


def _dates(count=40):
    result = []
    day = date(2026, 7, 1)
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


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


def test_drawdown_magnitude_wording_routes_to_complete_screen_list():
    message = "近期涨停后回撤比较多的股票有哪些"
    contract = build_post_limit_query_contract(
        message,
        planner_arguments={
            "mode": "statistics",
            "shape": "pullback_stabilizing",
            "recent_limit_days": 5,
        },
    )

    assert looks_like_post_limit_question(message)
    assert not looks_like_post_limit_statistics_question(message)
    assert contract.mode == "screen"
    assert contract.shape == "high_drawdown"
    assert contract.shapes == ("high_drawdown",)
    assert contract.recent_limit_days == 7
    assert contract.exhaustive
    assert contract.limit == 100
    signals = QuestionSignals.from_message(message)
    assert signals.post_limit_screen
    assert not signals.post_limit_statistics


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


def test_explicit_event_window_overrides_seven_day_default():
    contract = build_post_limit_query_contract(
        "近10个交易日涨停后从高位回撤的股票",
        planner_arguments={"recent_limit_days": 5},
    )

    assert contract.recent_limit_days == 10


def test_policy_signal_prevents_generic_limit_up_and_kline_routing():
    screen = QuestionSignals.from_message("有哪些涨停后从高位大幅回撤的票")
    assert screen.post_limit_screen
    assert not screen.limit_up_events
    assert not screen.stock_kline
    path = QuestionSignals.from_message("600001涨停后的走势")
    assert path.post_limit_path
    assert not path.limit_up_events
    stats = QuestionSignals.from_message("比较横盘缩量与回撤企稳的历史表现")
    assert stats.post_limit_statistics
    assert not stats.post_limit_screen


def test_inclusive_rule_boundaries():
    assert matches_high_drawdown(1, 10)
    assert matches_high_drawdown(4, 10)
    assert not matches_high_drawdown(0, 20)
    assert matches_volume_consolidation(2, 8, -10, .75)
    assert matches_volume_consolidation(4, 8, 8, .75)
    assert not matches_volume_consolidation(4, 8.0001, 0, .75)


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


def test_disabled_llm_still_returns_grounded_post_limit_screen(monkeypatch):
    payload = {
        "data_as_of": "2026-09-07",
        "latest_data_date": "2026-09-07",
        "rule_version": "post_limit_research_v3",
        "shapes": ["high_drawdown"],
        "shape_labels": {"high_drawdown": "高位大幅回撤"},
        "rules": {"high_drawdown": "峰值回撤不低于10%"},
        "pool_count": 20,
        "evaluable_count": 10,
        "coverage_ratio": .5,
        "matched_count": 1,
        "candidates": [{
            "symbol": "600001", "name": "回撤样本", "anchor_date": "2026-09-01",
            "board_height": 1, "anchor_age": 3, "anchor_change_pct": -5,
            "peak_date": "2026-09-02", "peak_drawdown_pct": 12,
            "range_pct": 15, "volume_ratio": .7, "concept": "机器人",
        }],
        "data_missing": ["missing_history20=10"],
        "warnings": [],
        "query_contract": {"recent_limit_days": 7},
    }

    def fake_screen(self, contract):
        return ToolResult(
            name="post_limit_screen", input=contract.to_dict(), output=payload,
            summary="1只符合涨停后形态。", result_status="partial",
        )

    monkeypatch.setattr(AgentToolRegistry, "post_limit_screen", fake_screen)
    response = answer_first_board_chat(
        AgentChatRequest(session_id="post-limit", message="有哪些涨停后从高位大幅回撤的票"),
        events=[],
        llm_provider=DisabledLLMProvider(),
    )
    assert response.tool_calls == ["post_limit_screen", "template_general_answer"]
    assert "limit_up_events" not in response.tool_calls
    assert "回撤样本（600001）" in response.answer
    assert "覆盖率 50.0%" in response.answer
    assert "回看最近7个交易日的收盘涨停" in response.answer


def test_wrong_planner_tool_is_replaced_by_only_post_limit_screen(monkeypatch):
    payload = {
        "data_as_of": "2026-09-07", "latest_data_date": "2026-09-07",
        "rule_version": "post_limit_research_v3", "shapes": ["high_drawdown"],
        "shape_labels": {"high_drawdown": "高位大幅回撤"}, "rules": {},
        "pool_count": 1, "evaluable_count": 1, "coverage_ratio": 1,
        "matched_count": 0, "candidates": [], "data_missing": [], "warnings": [],
    }

    def fake_screen(self, contract):
        return ToolResult(
            name="post_limit_screen", input=contract.to_dict(), output=payload,
            summary="0只符合涨停后形态。",
        )

    class WrongPlanner(LLMProvider):
        def generate(self, system_prompt, user_prompt):
            if "first job is to decide which tools are needed" in system_prompt:
                return LLMResult(
                    content=json.dumps({
                        "intent_label": "limit_up_list", "safety": "normal",
                        "capabilities": ["limit_up_events"],
                        "tool_calls": [
                            {"name": "limit_up_events", "arguments": {}},
                            {"name": "stock_kline", "arguments": {"symbol": "600001"}},
                        ],
                    }),
                    model="wrong-planner", provider="test",
                )
            return LLMResult(content="当前没有符合条件且数据完整的股票。", model="answer", provider="test")

    monkeypatch.setattr(AgentToolRegistry, "post_limit_screen", fake_screen)
    response = answer_first_board_chat(
        AgentChatRequest(session_id="wrong-plan", message="有哪些涨停后从高位大幅回撤的票"),
        events=[],
        llm_provider=WrongPlanner(),
    )
    assert "post_limit_screen" in response.tool_calls
    assert "limit_up_events" not in response.tool_calls
    assert "stock_kline" not in response.tool_calls


def test_pronoun_followup_reuses_previous_symbol_and_anchor(monkeypatch):
    captured = {}
    payload = {
        "data_as_of": "2026-09-07", "latest_data_date": "2026-09-07",
        "rule_version": "post_limit_research_v3", "symbol": "600001", "name": "回撤样本",
        "anchor": {"anchor_date": "2026-09-01"}, "metrics": {}, "matched_shapes": ["high_drawdown"],
        "path": [{
            "trade_date": "2026-09-01", "day": "T+0", "close": 11,
            "change_from_anchor_close_pct": 0, "drawdown_from_running_peak_pct": 0,
            "volume_vs_anchor": 1,
        }],
        "data_missing": [], "warnings": [],
    }

    def fake_path(self, contract, symbol):
        captured["symbol"] = symbol
        captured["anchor_date"] = contract.anchor_date
        return ToolResult(
            name="post_limit_path", input={**contract.to_dict(), "symbol": symbol},
            output=payload, summary="路径已返回。",
        )

    prior = AgentRun(
        run_id="prior-post-limit", session_id="post-limit", run_type="agent_chat",
        status="success", intent="post_limit_screen", tool_calls=["post_limit_screen"],
        input_json={"message": "有哪些涨停后大幅回撤的票"},
        output_json={
            "tool_results": [{
                "name": "post_limit_screen", "input": {},
                "output": {"candidates": [{"symbol": "600001", "anchor_date": "2026-09-01"}]},
            }],
        },
        started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(AgentToolRegistry, "post_limit_path", fake_path)
    response = answer_first_board_chat(
        AgentChatRequest(session_id="post-limit", message="这只为什么入选"),
        events=[], recent_runs=[prior], llm_provider=DisabledLLMProvider(),
    )
    assert response.tool_calls == ["post_limit_path", "template_general_answer"]
    assert captured == {"symbol": "600001", "anchor_date": date(2026, 9, 1)}


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


@pytest.mark.parametrize("shape", ["high_drawdown", "volume_consolidation"])
def test_direct_contract_and_statistics_use_seven_days(shape):
    contract = PostLimitQueryContract(shape=shape, mode="statistics")
    assert contract.to_dict()["recent_limit_days"] == 7
    assert PostLimitQueryContract(shape=shape, recent_limit_days=10).recent_limit_days == 10


@pytest.mark.parametrize("message", ["回撤企稳有哪些", "强势不连板有哪些", "断板修复有哪些", "2进3有哪些"])
def test_other_shapes_ignore_injected_seven_day_capability_default(message):
    assert build_post_limit_query_contract(message, planner_arguments={"recent_limit_days": 7}).recent_limit_days == 5


def test_statistics_separates_event_lookback_and_signal_day_count():
    contract = build_post_limit_query_contract("统计近3个信号日、回看近10个交易日有收盘涨停的缩量整理历史表现")
    assert contract.recent_limit_days == 10
    assert contract.statistics_days == 3
    assert build_post_limit_query_contract("比较近7日横盘缩量和回撤企稳的历史表现").recent_limit_days == 7


def test_statistics_discloses_missing_event_windows():
    dataset = _dataset()
    missing_day = dataset.calendar[29]
    dataset.events[:] = [e for e in dataset.events if e["trade_date"] != missing_day]
    result = build_post_limit_statistics(dataset, PostLimitQueryContract(mode="statistics"))
    assert "recent_event_dates" in result["data_missing"]
    assert any(missing_day in warning for warning in result["warnings"])
    assert not result["comparison_allowed"]
    from app.agents.chat_templates import _template_post_limit_statistics
    answer = _template_post_limit_statistics(result)
    assert "事件回看窗口记录不完整" in answer
    assert "recent_event_dates 0个" not in answer


@pytest.mark.parametrize("days", [5, 7, 10])
def test_answer_renders_actual_event_window(days):
    from app.agents.chat_templates import _template_post_limit_screen, _template_post_limit_statistics
    payload = {"query_contract": {"recent_limit_days": days}, "shapes": ["high_drawdown"]}
    assert f"最近{days}个交易日" in _template_post_limit_screen(payload)
    assert f"最近{days}个交易日" in _template_post_limit_statistics(payload)


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
