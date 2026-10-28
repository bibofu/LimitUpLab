"""Required-tool guard for data questions without current-run evidence."""

from __future__ import annotations

from time import perf_counter
from types import SimpleNamespace

from app.agents.grounding import missing_required_tool, required_tool_for_query
from app.agents.runner.durable_runtime import result_from_state


def _control():
    return SimpleNamespace(row={"run_id": "run-test", "owner_id": "owner-test"}, lifecycle=None)


def _state(query, tool_calls=(), *, status="complete", answer="2026-10-27 收盘 11.87", memory_action=None):
    return {
        "current_query": query,
        "tool_calls": list(tool_calls),
        "status": status,
        "answer": answer,
        "tool_messages": [],
        "display_policies": [],
        "memory_dependencies": [],
        "memory_context_fingerprint": "",
        "memory_owner_revision": 0,
        "memory_action": memory_action or {},
    }


def _result(query, tool_calls=(), **kwargs):
    runner = SimpleNamespace(started=perf_counter())
    return result_from_state(_state(query, tool_calls, **kwargs), runner, _control())


def test_kline_question_requires_stock_kline_in_current_run():
    query = "把窗口扩大到二十个交易日，概括价格与成交量变化，并列出数据缺口"
    assert required_tool_for_query(query) == "stock_kline"
    result = _result(query)
    assert result.status == "partial"
    assert result.stop_reason == "tool_evidence_required"
    assert "stock_kline" in result.answer
    assert result.memory_context_used is False


def test_dragon_tiger_question_requires_raw_tool_rows():
    query = "天娱数科002354在2026年10月27日为什么上榜？请列出可取得的席位买卖数据"
    assert required_tool_for_query(query) == "dragon_tiger_list"
    assert _result(query).stop_reason == "tool_evidence_required"


def test_index_comparison_requires_index_tool_in_current_run():
    query = "把上证指数、深证成指和创业板指的区间涨跌幅及上涨、下跌天数放在一张表里"
    assert required_tool_for_query(query) == "market_index_trend"
    assert _result(query).stop_reason == "tool_evidence_required"


def test_question_with_required_tool_call_keeps_the_answer():
    result = _result(
        "最近五个交易日的收盘价和成交量分别是多少？",
        ["stock_kline"], answer="本轮工具结果",
    )
    assert result.status == "complete"
    assert result.answer == "本轮工具结果"


def test_structural_clarification_is_not_overridden():
    result = _result("沿用刚才这一天，开板率和炸板未回封率是多少？", status="clarify",
                     answer="请明确要继续哪个指标或日期。")
    assert result.status == "clarify"
    assert result.answer.startswith("请明确")


def test_session_recap_does_not_require_a_market_tool():
    query = "请回顾本会话，我们比较过哪些指数？"
    assert required_tool_for_query(query) is None
    result = _result(query, answer="本会话比较过三个指数。")
    assert result.status == "complete"
    assert result.answer == "本会话比较过三个指数。"


def test_memory_command_is_not_blocked_by_market_terms():
    query = "以后展示龙虎榜只显示股票名称"
    assert _result(query, memory_action={"action": "save"}).status == "complete"


def test_non_data_clarification_is_allowed():
    assert missing_required_tool("什么是K线？", [], "complete") is False


def test_rate_question_requires_market_summary():
    query = "2026-10-27 的开板率与炸板未回封率分别是多少？请给出分子、分母。"
    assert required_tool_for_query(query) == "market_summary"
    assert _result(query, answer="41/97 ≈ 42.3%").stop_reason == "tool_evidence_required"
    assert _result(query, ["market_summary"], answer="56/69 ≈ 81.16%").status == "complete"


def test_event_window_question_does_not_require_stock_kline():
    query = "凯众股份603037截至2026年10月27日最近二十个交易日出现过哪些涨停或炸板记录？"
    assert required_tool_for_query(query) is None
    assert _result(query, answer="没有记录。").status == "complete"


def test_unavailable_rate_answer_with_dates_is_not_treated_as_a_numeric_claim():
    query = "沿用刚才这一天，开板率与炸板未回封率分别是多少？"
    answer = "2026-10-28 15:22 的数据尚未落定，分子和分母都缺，无法计算。"
    assert _result(query, answer=answer).status == "complete"
