"""The model sees the complete business contract without duplicated collections."""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.agents.runner.tool_view import dump, model_result
from app.agents.tool_payload import payload_of
from app.agents.tool_result import ToolResult


def view(name, payload, status="ok"):
    return model_result(name, SimpleNamespace(summary="fixture"), payload, status)


def test_all_68_limit_up_rows_are_visible_once():
    events = [{"symbol": f"600{index:03}", "name": f"样本{index}"} for index in range(68)]
    result = view("limit_up_events", {
        "events": events, "matched_count": 68, "returned_count": 68,
        "sector_summary": [{"industry": "机械", "count": 68}], "complete": True,
    })
    assert result["rows"] == events
    assert result["row_count"] == 68
    assert result["returned_count"] == 68
    assert result["truncated"] is False
    assert result["status"] == "ok"
    assert result["sector_summary"][0]["count"] == 68
    assert "events" not in result
    assert all(dump(result).count(row["symbol"]) == 1 for row in events)


def test_60_bar_tail_and_server_indicators_reach_the_model():
    end = date(2026, 10, 23)
    bars = [{"trade_date": str(end - timedelta(days=59-index)), "close": 100+index,
             "high": 101+index, "low": 99+index, "source": "tencent.qt.gtimg.cn"}
            for index in range(60)]
    payload = payload_of(ToolResult(name="stock_kline", input={}, summary="test", output={
        "bars": bars, "data_as_of": str(end), "ma5": 157.0, "ma10": 154.5,
        "ma20": 149.5, "volume_ratio": 0.75, "max_drawdown_pct": -1.98,
    }))
    result = view("stock_kline", payload)
    assert result["rows"] == bars
    assert result["rows"][-1]["trade_date"] == str(end)
    assert result["row_count"] == 60 and result["truncated"] is False
    assert [result[key] for key in ("ma5", "ma10", "ma20", "volume_ratio")] == [157, 154.5, 149.5, .75]
    assert result["max_drawdown_pct"] == -1.98
    assert result["display_window_return"]["end_date"] == str(end)
    assert result["close_max_drawdown"]["drawdown_pct"] == 0
    assert "high_low_max_drawdown" in result["metric_definitions"]
    assert "bars" not in result


def test_rules_coverage_and_additional_collections_survive_without_primary_duplication():
    payload = {"candidates": [{"symbol": "600001"}],
               "rules": {"high_drawdown": "距最近涨停1–4日；前日以前峰值至最新收盘回撤至少10%。"},
               "coverage_ratio": .5, "exclusions": {"missing_history20": 1},
               "data_missing": ["missing_history20"], "candidate_statistics": {"sample_count": 1}}
    result = view("post_limit_screen", payload)
    assert result["rules"] == payload["rules"]
    assert result["coverage_ratio"] == .5 and result["status"] == "partial"
    assert result["exclusions"] == payload["exclusions"]
    assert result["candidate_statistics"] == {"sample_count": 1}
    assert "candidates" not in result
    result = view("sector_performance", {"top_sectors": [{"name": "上涨板块"}],
                                         "bottom_sectors": [{"name": "下跌板块"}]})
    assert result["rows"] == [{"name": "上涨板块"}]
    assert result["bottom_sectors"] == [{"name": "下跌板块"}]
    assert dump(result).count("上涨板块") == dump(result).count("下跌板块") == 1


def test_noncollection_report_keeps_all_lists_and_scope_in_one_payload():
    payload = {"picks": [{"symbol": "600001"}], "items": [{"symbol": "600002"}],
               "events": [{"symbol": "600003"}], "review_date": "2026-10-23",
               "cohort_scope": {"start": "2026-10-19", "end": "2026-10-22"},
               "data_missing": ["one_immature_pick"]}
    result = view("review_high_score_picks", payload)
    assert result["rows"] == [payload]
    assert result["status"] == "partial"
    assert all(dump(result).count(symbol) == 1 for symbol in ("600001", "600002", "600003"))


def test_ratings_flatten_facts_once_without_original_candidate_copy():
    payload = payload_of(ToolResult(name="first_board_ratings", input={}, summary="test", output={
        "candidates": [{"symbol": "600001", "score": 80, "facts": {"ma5": 10.5}}],
        "policy_version": "v3",
    }))
    result = view("first_board_ratings", payload)
    assert result["rows"] == [{"symbol": "600001", "score": 80, "ma5": 10.5}]
    assert "candidates" not in payload and "top_candidates" not in result
    assert result["policy_version"] == "v3"
    assert dump(result).count("600001") == 1


@pytest.mark.parametrize("metadata", [
    {"truncated": True}, {"source_truncated": True}, {"complete": False},
    {"complete_matching_query": False}, {"data_missing": ["history"]},
])
def test_upstream_incompleteness_remains_partial(metadata):
    result = view("limit_up_events", {"events": [{"symbol": "600001"}], **metadata})
    assert result["status"] == "partial"
    assert result["truncated"] is bool(metadata.get("truncated") or metadata.get("source_truncated"))


def test_error_details_survive_without_becoming_successful_empty_rows():
    result = view("stock_kline", {"status": "error", "error": "source failed"}, "error")
    assert result["status"] == "error" and result["error"] == "source failed"
    assert result["payload_metadata"]["status"] == "error"
    with pytest.raises(ValueError, match="must be an array"):
        view("stock_kline", {"bars": "invalid"})


def test_stock_news_identity_cannot_override_tool_name_in_model_or_trace():
    from app.agents.runner.adapter import _traces
    from app.agents.runner.runner import AgentRunner

    value = view("stock_news", {
        "name": "理工能科", "symbol": "002322", "items": [{"title": "新闻", "name": "理工能科"}],
        "payload_metadata": {"provider_scope": "stock"},
    })
    runner = SimpleNamespace(progress=None, execute=lambda call: value)
    state = AgentRunner.tools(runner, {"messages": [], "pending": [{
        "name": "stock_news", "args": {"symbol": "002322"}, "id": "news_1",
    }]})
    trace, = _traces(SimpleNamespace(tool_messages=state["tool_messages"]))
    assert trace.name == value["name"] == "stock_news"
    assert trace.output["payload_metadata"]["name"] == "理工能科"
    assert trace.output["payload_metadata"]["payload_metadata"] == {"provider_scope": "stock"}
    assert state["messages"][0].name == "stock_news"
    assert trace.output["rows"][0]["name"] == "理工能科"
