"""Deterministic session ledger for recap questions."""

from __future__ import annotations

import json

from app.agents.context_builder import build_context
from app.agents.session_history import (
    build_session_ledger,
    format_session_summary,
    is_session_recap_query,
    is_session_summary_query,
)


def test_recap_query_is_detected_without_treating_a_data_query_as_recap():
    assert is_session_recap_query("请回顾本会话：我们比较过哪些指数？")
    assert is_session_recap_query("这是本轮连续测试的第20问，本会话前面查询过凯众股份")
    assert not is_session_recap_query("凯众股份最近五个交易日的成交量是多少？")


def test_ledger_keeps_question_tools_and_stock_mentions():
    history = [
        {"role": "user", "content": "研究凯众股份603037的主营业务", "status": "success"},
        {"role": "assistant", "content": "凯众股份资料", "status": "success", "metadata": {
            "task_status": "complete", "stop_reason": "answered",
            "tool_calls": ["stock_profile", "read_tool_result"],
            "stock_mentions": [{"symbol": "603037", "name": "凯众股份"}],
        }},
        {"role": "user", "content": "现在比较三大指数", "status": "success"},
        {"role": "assistant", "content": "三大指数对比", "status": "success", "metadata": {
            "task_status": "complete", "tool_calls": ["market_index_trend"],
            "stock_mentions": [],
        }},
    ]
    ledger = build_session_ledger(history)
    assert len(ledger) == 2
    assert ledger[0]["question"].startswith("研究凯众股份")
    assert ledger[0]["tools"] == ["stock_profile"]
    assert ledger[0]["stocks"] == [{"symbol": "603037", "name": "凯众股份"}]
    assert ledger[1]["tools"] == ["market_index_trend"]
    assert ledger[1]["answer_status"] == "complete"


def test_ledger_is_bounded_and_ignores_read_tool_result():
    history = []
    for index in range(35):
        history.append({"role": "user", "content": f"问题 {index}", "status": "success"})
        history.append({"role": "assistant", "content": f"回答 {index}", "status": "success",
                        "metadata": {"tool_calls": ["read_tool_result"]}})
    ledger = build_session_ledger(history)
    assert len(ledger) == 30
    assert ledger[0]["question"] == "问题 5"
    assert all(turn["tools"] == [] for turn in ledger)


def test_context_payload_contains_the_ledger_only_when_supplied():
    ledger = [{"question": "研究凯众股份", "tools": ["stock_profile"], "stocks": []}]
    built = build_context(
        system_prompt="测试", query="回顾本会话",
        conversation={"session_ledger": ledger,
                      "session_facts": {"first_detailed_stock": {"symbol": "603037"}}},
        working_messages=[],
    )
    payload = json.loads(built.messages[1].content.split("\n", 1)[1])
    assert payload["session_ledger"] == ledger
    assert payload["session_facts"]["first_detailed_stock"] == {"symbol": "603037"}
    assert payload["historical_market_facts_are_evidence"] is False


def test_session_facts_derive_recap_answers_deterministically():
    from app.agents.session_history import build_session_facts

    ledger = [
        {"question": "请列出首板评分前五", "tools": ["first_board_ratings"],
         "stocks": [{"symbol": "603037", "name": "凯众股份"}]},
        {"question": "我指的是凯众股份603037，请介绍主营业务", "tools": ["stock_profile"],
         "stocks": [{"symbol": "603037", "name": "凯众股份"}]},
        {"question": "比较上证指数、深证成指和创业板指", "tools": ["market_index_trend"],
         "stocks": []},
        {"question": "天娱数科002354为什么上榜？请列出席位数据", "tools": [],
         "stocks": [{"symbol": "002354", "name": "天娱数科"}]},
    ]
    facts = build_session_facts(ledger)
    assert facts["first_detailed_stock"] == {"symbol": "603037", "name": "凯众股份"}
    assert facts["compared_indices"] == ["上证指数", "深证成指", "创业板指"]
    assert facts["dragon_tiger_followup_stock"] == {"symbol": "002354", "name": "天娱数科"}


def test_session_facts_normalize_chinese_date_and_fill_stock_name():
    from app.agents.session_history import build_session_facts

    ledger = [
        {"question": "明确查询2026年10月27日的开板率", "tools": [], "stocks": []},
        {"question": "介绍凯众股份603037", "tools": ["stock_profile"],
         "stocks": [{"symbol": "603037", "name": "凯众股份"}]},
        {"question": "天娱数科002354为什么上榜？", "tools": [],
         "stocks": [{"symbol": "002354", "name": ""}]},
        {"question": "龙虎榜前五", "tools": ["dragon_tiger_list"],
         "stocks": [{"symbol": "002354", "name": "天娱数科"}]},
    ]
    facts = build_session_facts(ledger)
    assert facts["first_explicit_date"] == "2026-10-27"
    assert facts["first_detailed_stock"] == {"symbol": "603037", "name": "凯众股份"}
    assert facts["dragon_tiger_followup_stock"] == {"symbol": "002354", "name": "天娱数科"}


def test_format_session_recap_uses_facts_and_is_not_market_evidence():
    from app.agents.session_history import format_session_recap

    text = format_session_recap({
        "first_detailed_stock": {"symbol": "603037", "name": "凯众股份"},
        "first_explicit_date": "2026-10-27",
        "compared_indices": ["上证指数", "深证成指", "创业板指"],
        "dragon_tiger_followup_stock": {"symbol": "002354", "name": "天娱数科"},
    })
    assert "凯众股份（603037）" in text
    assert "上证指数、深证成指、创业板指" in text
    assert "天娱数科（002354）" in text
    assert "不是本轮行情证据" in text


def test_fact_coverage_is_narrower_than_recap_detection():
    from app.agents.session_history import session_facts_cover_query

    assert session_facts_cover_query("请回顾本会话：最先详细研究的股票和指数")
    assert session_facts_cover_query("本会话龙虎榜追问的是谁")
    assert not session_facts_cover_query("请回顾本会话的第三问原文")


def test_ledger_falls_back_to_stock_code_in_user_question():
    history = [
        {"role": "user", "content": "天娱数科002354为什么上榜？请列出席位数据", "status": "success"},
        {"role": "assistant", "content": "缺少本轮龙虎榜工具结果", "status": "success",
         "metadata": {"task_status": "partial", "tool_calls": [], "stock_mentions": []}},
    ]
    ledger = build_session_ledger(history)
    assert ledger[0]["stocks"] == [{"symbol": "002354", "name": ""}]


def test_recap_question_does_not_become_the_dragon_tiger_followup():
    from app.agents.session_history import build_session_facts

    ledger = [
        {"question": "天娱数科002354为什么上榜？请列出席位", "tools": [],
         "stocks": [{"symbol": "002354", "name": "天娱数科"}]},
        {"question": "请回顾本会话的龙虎榜追问股票", "tools": [],
         "stocks": [{"symbol": "603037", "name": "凯众股份"}]},
    ]
    facts = build_session_facts(ledger)
    assert facts["dragon_tiger_followup_stock"] == {"symbol": "002354", "name": "天娱数科"}


def test_summary_request_is_a_session_recap():
    assert is_session_recap_query("请总结本次复盘中的主要风险和缺失数据")
    assert is_session_summary_query("请总结本次复盘中的主要风险和缺失数据")
    assert not is_session_summary_query("请回顾本会话最先研究的股票")


def test_session_summary_lists_turns_and_gaps_without_market_values():
    summary = format_session_summary([
        {"question": "本轮市场情绪", "answer_status": "complete",
         "stop_reason": "answered", "tools": ["market_summary"]},
        {"question": "近5日对比", "answer_status": "partial",
         "stop_reason": "answered", "tools": ["limit_up_events"]},
    ])
    assert "本会话索引摘要" in summary
    assert "已提交轮次：2" in summary
    assert "第 2 轮" in summary
    assert "market_summary" in summary
    assert "不含可复核的行情数值" in summary
