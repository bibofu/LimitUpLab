"""Model-side reference resolution contracts.

The chat path no longer classifies pronouns before the model call. It passes a
bounded structured referent block; the main model resolves the pronoun and any
ambiguous case is left to its own clarification response.
"""

from __future__ import annotations

import json

import pytest

from app.agents.context_builder import build_context
from app.agents.reference_intent import ReferenceIntent, validated_reference_intent


REFERENTS = {
    "referent_schema_version": 1,
    "source_run_id": "run-1",
    "stocks": [
        {"symbol": "300750", "name": "宁德时代"},
        {"symbol": "600519", "name": "贵州茅台"},
    ],
    "stock_set": {
        "kind": "list",
        "ordered_symbols": ["300750", "600519"],
        "ordinal_resolvable": True,
        "groups": [{"tool": "first_board_ratings", "kind": "list", "symbols": ["300750", "600519"]}],
    },
    "analyses": [{
        "tool": "stock_kline", "metric_keys": ["close"],
        "as_of": "2026-10-30", "previous_trade_date": "2026-10-29",
    }],
    "date_scope": {"as_of": "2026-10-30", "previous_trade_date": "2026-10-29"},
}


def context(query: str, referents: dict | None = None):
    return build_context(
        system_prompt="只使用本轮证据", query=query, working_messages=[],
        conversation={"referents": REFERENTS if referents is None else referents},
    )


def payload(result):
    return json.loads(result.messages[1].content.split("\n", 1)[1])


def test_previous_entities_reach_the_model_without_pre_resolving():
    data = payload(context("它们最近走势"))
    referents = data["conversation_referents"]
    assert referents["stocks"] == [
        {"symbol": "300750", "name": "宁德时代"},
        {"symbol": "600519", "name": "贵州茅台"},
    ]
    assert referents["stock_set"]["ordered_symbols"] == ["300750", "600519"]
    assert referents["stock_set"]["ordinal_resolvable"] is True
    assert referents["analyses"][0]["tool"] == "stock_kline"
    assert referents["date_scope"]["previous_trade_date"] == "2026-10-29"
    assert "resolved_reference" not in data


def test_entity_names_are_bounded_but_the_full_order_is_retained():
    stocks = [{"symbol": f"{index:06d}", "name": f"股票{index}"} for index in range(250)]
    referents = {
        "stocks": stocks,
        "stock_set": {
            "kind": "list",
            "ordered_symbols": [item["symbol"] for item in stocks],
            "ordinal_resolvable": True,
            "groups": [],
        },
    }
    data = payload(context("这些股票", referents))
    block = data["conversation_referents"]
    assert len(block["stocks"]) == 200
    assert block["stocks_truncated"] is True
    assert len(block["stock_set"]["ordered_symbols"]) == 250


def test_multiple_result_groups_are_exposed_for_the_model_to_ask_about():
    referents = {
        "stocks": REFERENTS["stocks"],
        "stock_set": {
            "kind": "multiple",
            "ordered_symbols": ["300750", "600519"],
            "ordinal_resolvable": False,
            "groups": [
                {"tool": "stock_kline", "kind": "single", "symbols": ["300750"]},
                {"tool": "stock_news", "kind": "single", "symbols": ["600519"]},
            ],
        },
    }
    block = payload(context("它们", referents))["conversation_referents"]
    assert block["stock_set"]["kind"] == "multiple"
    assert [group["tool"] for group in block["stock_set"]["groups"]] == ["stock_kline", "stock_news"]


def test_context_omits_referents_when_there_is_no_prior_entity_set():
    data = payload(context("查询今天涨停", {}))
    assert data["conversation_referents"] == {}
    assert data["active_references"] == []


@pytest.mark.parametrize("intent", [
    {}, {"kind": "unknown"}, {"kind": "entities"}, {"kind": "none", "ordinal": 2},
    {"kind": "conditions", "quote": "沿用条件", "ordinal": 2},
    {"kind": "entities", "quote": "第二只", "ordinal": "2"},
    {"kind": "entities", "quote": "第二只", "ordinal": 2, "all_entities": True},
    {"kind": "conditions", "quote": "沿用条件", "all_entities": True},
    {"kind": "analysis"},
    {"kind": "analysis", "quote": "上一个交易日", "ordinal": 2},
    {"kind": "analysis", "quote": "上一个交易日", "date_override": "yesterday"},
    {"kind": "none", "date_override": "previous_trade_date"},
    {"kind": "entities", "quote": "第二只", "date_override": "previous_trade_date"},
])
def test_reference_resolver_schema_rejects_contradictory_meaning(intent):
    with pytest.raises(ValueError):
        ReferenceIntent.model_validate(intent)


def test_nonverbatim_reference_quote_is_rejected():
    with pytest.raises(ValueError, match="exact excerpt"):
        validated_reference_intent({"kind": "entities", "quote": "上一次的第二只", "ordinal": 2}, "查询今天第40只")
