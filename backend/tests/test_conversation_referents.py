"""Conversation referents distinguish what the user means from current evidence."""

import json

from app.agents.context_builder import build_context
from app.agents.structured_references import (
    build_conversation_referents, compact_referent_hint, constraints_from_references,
    history_stock_hint, references_from_committed_result, resolve_reference,
)


def response(answer, tools, *, status="complete", run_id="run-1"):
    return {"task_status": status, "run_id": run_id, "answer": answer, "tool_results": tools}


def activity(symbol, name, *, call_id=None, date="2026-10-26"):
    return {"name": "stock_activity", "status": "success", "call_id": call_id, "output": {
        "name": "stock_activity", "status": "partial",
        "summary": f"已汇总 {name}({symbol}) 的收盘走势、1 条涨停记录和 1 条个股资讯。",
        "rows": [{"symbol": symbol, "name": name, "data_as_of": date}],
        "query_scope": {"scope_version": 1, "tool": "stock_activity",
                        "arguments": {"symbol": symbol, "days": 5, "news_limit": 1},
                        "explicit_arguments": {"symbol": name, "days": 5, "news_limit": 1}},
    }}


def kline(symbol="300750", name="宁德时代"):
    return {"name": "stock_kline", "status": "success", "output": {
        "name": "stock_kline", "status": "ok",
        "summary": f"{name}({symbol}) 最近 5 天",
        "symbol": symbol, "data_as_of": "2026-10-26",
        "rows": [{"trade_date": "2026-10-20", "close": 259.08}],
        "query_scope": {"scope_version": 1, "tool": "stock_kline",
                        "arguments": {"symbol": symbol, "days": 5},
                        "explicit_arguments": {"symbol": symbol, "days": 5}},
    }}


def promotion(*, promoted=True):
    stocks = ([{"symbol": "603337", "name": "杰克科技"},
               {"symbol": "605488", "name": "福莱新材"}] if promoted else [])
    return {"name": "daily_board_promotion", "status": "success", "output": {
        "name": "daily_board_promotion", "status": "ok",
        "summary": "1进2统计",
        "rows": [{
            "trade_date": "2026-10-26", "previous_trade_date": "2026-10-23",
            "sample_size": 80, "promoted_count": 15, "probability": 0.1875,
            "first_board_sample_size": 68, "first_board_promoted_count": 8,
            "first_board_probability": 0.1176, "promoted_stocks": stocks,
        }],
        "query_scope": {"scope_version": 1, "tool": "daily_board_promotion",
                        "arguments": {"days": 1}, "explicit_arguments": {"days": 1}},
    }}


def calendar():
    return {"name": "trading_calendar", "status": "success", "output": {
        "name": "trading_calendar", "status": "ok", "summary": "calendar",
        "rows": [{"today": "2026-10-26", "latest_closed_trade_date": "2026-10-26",
                  "previous_trade_date": "2026-10-23", "next_trade_date": "2026-10-27"}],
    }}


def messages(answer, *, mentions=None):
    return [
        {"role": "user", "content": "上一轮问题", "status": "success"},
        {"role": "assistant", "content": answer, "status": "success",
         "metadata": {"stock_mentions": mentions or []}},
    ]


def test_single_stock_tool_resolves_a_singular_pronoun():
    refs = build_conversation_referents("run-1", response("宁德时代（300750）", [kline()]))
    assert refs["stock_set"]["kind"] == "single"
    resolved = resolve_reference(
        "它近期走势怎么样", [], intent={"kind": "entities", "quote": "它"},
        referents=refs,
    )
    assert resolved["status"] == "resolved"
    assert resolved["symbols"] == ["300750"] and resolved["verified"] is True


def test_parallel_single_stock_tools_merge_into_one_ordered_set():
    refs = build_conversation_referents("run-1", response(
        "| 代码 | 名称 |\n|---|---|\n| 300750 | 宁德时代 |\n| 600519 | 贵州茅台 |",
        [activity("300750", "宁德时代"), activity("600519", "贵州茅台")],
    ))
    assert refs["stock_set"]["kind"] == "ordered_list"
    assert refs["stock_set"]["ordered_symbols"] == ["300750", "600519"]
    all_entities = resolve_reference(
        "他们", [], intent={"kind": "entities", "quote": "他们", "all_entities": True},
        referents=refs,
    )
    assert all_entities["status"] == "resolved"
    assert all_entities["symbols"] == ["300750", "600519"]
    second = resolve_reference(
        "第二只", [], intent={"kind": "entities", "quote": "第二只", "ordinal": 2},
        referents=refs,
    )
    assert second["symbols"] == ["600519"]
    singular = resolve_reference(
        "它", [], intent={"kind": "entities", "quote": "它"}, referents=refs,
    )
    assert singular["status"] == "clarify"


def test_analysis_scope_resolves_previous_trading_day_without_a_stock_list():
    refs = build_conversation_referents("run-1", response(
        "2026-10-26 1进2：8/68", [calendar(), promotion(promoted=False)],
    ))
    assert refs["analysis"]["tool"] == "daily_board_promotion"
    assert refs["date_scope"]["previous_trade_date"] == "2026-10-23"
    resolved = resolve_reference(
        "那上一个交易日呢", [], intent={
            "kind": "analysis", "quote": "那上一个交易日呢",
            "date_override": "previous_trade_date",
        }, referents=refs,
    )
    assert resolved["status"] == "resolved"
    assert resolved["kind"] == "analysis"
    assert resolved["tool"] == "daily_board_promotion"
    assert resolved["resolved_date"] == "2026-10-23"
    assert resolved["analysis_scope"]["arguments"] == {"days": 1}


def test_nested_promoted_stocks_produce_a_list_referent():
    refs = build_conversation_referents("run-1", response(
        "| 代码 | 名称 |\n|---|---|\n| 603337 | 杰克科技 |\n| 605488 | 福莱新材 |",
        [promotion()],
    ))
    assert [item["symbol"] for item in refs["stocks"]] == ["603337", "605488"]
    resolved = resolve_reference(
        "第一只", [], intent={"kind": "entities", "quote": "第一只", "ordinal": 1},
        referents=refs,
    )
    assert resolved["symbols"] == ["603337"]


def test_history_hint_resolves_a_unique_but_unverified_stock():
    recent = messages("宁德时代（300750），深交所创业板。")
    assert history_stock_hint(recent)["symbol"] == "300750"
    resolved = resolve_reference(
        "它近期走势怎么样", [], intent={"kind": "entities", "quote": "它"},
        referents={}, recent_messages=recent,
    )
    assert resolved["status"] == "resolved"
    assert resolved["symbols"] == ["300750"]
    assert resolved["verified"] is False and resolved["requires_tool_verification"] is True


def test_history_hint_does_not_pick_one_of_multiple_stocks():
    recent = messages("宁德时代（300750）和贵州茅台（600519）都属于消费相关行业。")
    assert history_stock_hint(recent) is None
    resolved = resolve_reference(
        "它近期走势怎么样", [], intent={"kind": "entities", "quote": "它"},
        referents={}, recent_messages=recent,
    )
    assert resolved["status"] == "clarify"


def test_multiple_list_groups_still_require_clarification():
    first = promotion()
    second = promotion()
    second["output"]["rows"][0]["trade_date"] = "2026-10-23"
    second["output"]["rows"][0]["promoted_stocks"] = [{"symbol": "000678", "name": "襄阳轴承"}]
    refs = build_conversation_referents("run-1", response(
        "| 603337 | 杰克科技 |", [first, second],
    ))
    assert refs["stock_set"]["kind"] == "multiple"
    resolved = resolve_reference(
        "第二只", [], intent={"kind": "entities", "quote": "第二只", "ordinal": 2},
        referents=refs,
    )
    assert resolved["status"] == "clarify"


def test_compact_hint_is_bounded_and_includes_analysis():
    refs = build_conversation_referents("run-1", response(
        "2026-10-26 1进2：8/68", [calendar(), promotion()],
    ))
    hint = compact_referent_hint({"referents": refs})
    assert hint["analysis"]["tool"] == "daily_board_promotion"
    assert hint["date_scope"]["previous_trade_date"] == "2026-10-23"
    assert len(hint["stocks"]) <= 10


def test_single_stock_query_does_not_become_a_session_filter():
    refs = references_from_committed_result("run-1", response(
        "| 300750 | 宁德时代 |\n| 600519 | 贵州茅台 |",
        [activity("300750", "宁德时代"), activity("600519", "贵州茅台")],
    ))
    assert constraints_from_references(refs) == []


def test_context_builder_uses_referents_and_exposes_verified_flag():
    refs = build_conversation_referents("run-1", response(
        "| 代码 | 名称 |\n|---|---|\n| 300750 | 宁德时代 |\n| 600519 | 贵州茅台 |",
        [kline(), activity("600519", "贵州茅台")],
    ))
    result = build_context(
        system_prompt="只使用本轮证据", query="他们近期走势",
        conversation={"referents": refs, "recent_messages": []}, working_messages=[],
        reference_intent={"kind": "entities", "quote": "他们", "all_entities": True},
    )
    assert result.clarification is None
    assert result.resolved_reference["symbols"] == ["300750", "600519"]
    assert result.resolved_reference["verified"] is True
    payload = json.loads(result.messages[1].content.split("\n", 1)[1])
    assert payload["resolved_reference"]["symbols"] == ["300750", "600519"]


def _first_board():
    return {"name": "first_board_ratings", "status": "success", "output": {
        "name": "first_board_ratings", "status": "ok",
        "rows": [
            {"symbol": "603037", "name": "凯众股份", "trade_date": "2026-10-27"},
            {"symbol": "601956", "name": "东贝集团", "trade_date": "2026-10-27"},
            {"symbol": "600828", "name": "茂业商业", "trade_date": "2026-10-27"},
        ],
        "query_scope": {"scope_version": 1, "tool": "first_board_ratings",
                        "arguments": {"trade_date": "2026-10-27"},
                        "explicit_arguments": {"trade_date": "2026-10-27"}},
    }}


def test_markdown_table_order_wins_over_an_incidental_bullet():
    refs = build_conversation_referents("run-1", response(
        "| 排名 | 代码 | 名称 |\n|---|---|---|\n"
        "| 1 | 603037 | 凯众股份 |\n| 2 | 601956 | 东贝集团 |\n\n"
        "- 凯众股份与东贝集团同为最高分。",
        [_first_board()],
    ))
    assert refs["stock_set"]["ordinal_resolvable"] is True
    assert refs["stock_set"]["ordered_symbols"] == ["603037", "601956"]
    resolved = resolve_reference(
        "上一张表的第一只", [],
        intent={"kind": "entities", "quote": "上一张表的第一只", "ordinal": 1},
        referents=refs,
    )
    assert resolved["status"] == "resolved"
    assert resolved["symbols"] == ["603037"]


def _market_analysis(tool, date):
    return {"name": tool, "status": "success", "output": {
        "name": tool, "status": "ok",
        "rows": [{"trade_date": date, "matched_count": 1}],
        "query_scope": {"scope_version": 1, "tool": tool,
                        "arguments": {"trade_date": date},
                        "explicit_arguments": {"trade_date": date}},
    }}


def test_multiple_analysis_scopes_with_one_date_resolve_the_date():
    refs = build_conversation_referents("run-1", response(
        "2026-10-27 市场结构",
        [_market_analysis("market_summary", "2026-10-27"),
         _market_analysis("market_event_pool", "2026-10-27")],
    ))
    assert len(refs["analyses"]) == 2
    resolved = resolve_reference(
        "沿用刚才这一天，开板率与炸板未回封率是多少？", [],
        intent={"kind": "analysis", "quote": "沿用刚才这一天",
                "date_override": "today"},
        referents=refs,
    )
    assert resolved["status"] == "resolved"
    assert resolved["kind"] == "analysis"
    assert resolved["resolved_date"] == "2026-10-27"
    assert resolved["analysis_scope"] is None
    assert resolved["query_scope_status"] == "multiple"


def test_multiple_analysis_scopes_with_different_dates_still_clarify():
    refs = build_conversation_referents("run-1", response(
        "两日市场结构",
        [_market_analysis("market_summary", "2026-10-27"),
         _market_analysis("market_event_pool", "2026-10-24")],
    ))
    resolved = resolve_reference(
        "沿用刚才这一天，开板率与炸板未回封率是多少？", [],
        intent={"kind": "analysis", "quote": "沿用刚才这一天",
                "date_override": "today"},
        referents=refs,
    )
    assert resolved["status"] == "clarify"
