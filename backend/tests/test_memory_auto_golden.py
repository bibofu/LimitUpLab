"""Curated safe_auto boundary cases used for calibration and regression."""

from types import SimpleNamespace

import pytest

from app.services.memory_auto_policy import evaluate_safe_auto_candidate


def candidate(**changes):
    fields = dict(
        candidate_action="upsert",
        memory_type="preference",
        confidence=0.95,
        stability="explicit_future",
        scope_type="tool",
        scope={"tool_name": "dragon_tiger_list"},
        structured={"display_columns": ["name"]},
        content_text="展示龙虎榜时只显示股票名称",
        source_quote="以后展示龙虎榜只显示股票名称",
    )
    fields.update(changes)
    return SimpleNamespace(**fields)


def consolidation(**changes):
    fields = {
        "decision": "add",
        "target_memory_id": None,
        "superseded_candidate_id": None,
    }
    fields.update(changes)
    return fields


CASES = [
    {
        "name": "structured_tool_display_add",
        "message": "以后展示龙虎榜只显示股票名称",
        "key": "tool_display:dragon_tiger_list",
        "candidate": candidate(),
        "eligible": True,
    },
    {
        "name": "tool_display_low_confidence",
        "message": "以后展示龙虎榜只显示股票名称",
        "key": "tool_display:dragon_tiger_list",
        "candidate": candidate(confidence=0.85),
        "eligible": False,
        "reason": "low_confidence_for_display",
    },
    {
        "name": "tool_display_unknown_column",
        "message": "以后展示龙虎榜只显示股票名称",
        "key": "tool_display:dragon_tiger_list",
        "candidate": candidate(structured={"display_columns": ["mystery"]}),
        "eligible": False,
        "reason": "unknown_display_column",
    },
    {
        "name": "global_closed_style",
        "message": "以后回答简洁一点",
        "key": "response_style",
        "candidate": candidate(
            content_text="concise", scope_type="global", scope={}, structured={},
            source_quote="以后回答简洁一点",
        ),
        "eligible": True,
    },
    {
        "name": "global_style_with_domain_phrase",
        "message": "以后分析财报时详细一点",
        "key": "response_style",
        "candidate": candidate(
            content_text="detailed", scope_type="global", scope={}, structured={},
            source_quote="以后分析财报时详细一点",
        ),
        "eligible": False,
        "reason": "typed_setting_has_unmapped_scope",
    },
    {
        "name": "tool_scoped_style_exception",
        "message": "以后分析财报时详细一点",
        "key": "response_style",
        "candidate": candidate(
            content_text="detailed", scope_type="tool",
            scope={"tool_name": "stock_financials"}, structured={},
            source_quote="以后分析财报时详细一点",
            confidence=0.85, stability="conditional_scope",
        ),
        "eligible": True,
    },
    {
        "name": "entity_scoped_style_exception",
        "message": "以后分析600519时详细一点",
        "key": "response_style",
        "candidate": candidate(
            content_text="detailed", scope_type="entity",
            scope={"symbol": "600519"}, structured={},
            source_quote="以后分析600519时详细一点",
        ),
        "eligible": True,
    },
    {
        "name": "entity_and_tool_scope_ambiguous",
        "message": "以后分析600519财报时详细一点",
        "key": "response_style",
        "candidate": candidate(
            content_text="detailed", scope_type="entity",
            scope={"symbol": "600519"}, structured={},
            source_quote="以后分析600519财报时详细一点",
        ),
        "eligible": False,
        "reason": "ambiguous_typed_entity_scope",
    },
    {
        "name": "delete_is_never_auto",
        "message": "以后不要再记住龙虎榜名称",
        "key": "tool_display:dragon_tiger_list",
        "candidate": candidate(candidate_action="delete"),
        "eligible": False,
        "reason": "not_an_upsert",
    },
    {
        "name": "update_is_never_auto",
        "message": "以后展示龙虎榜只显示代码",
        "key": "tool_display:dragon_tiger_list",
        "candidate": candidate(),
        "consolidation": consolidation(decision="update"),
        "eligible": False,
        "reason": "not_an_add",
    },
    {
        "name": "free_text_is_not_auto",
        "message": "以后研究首板优先看换手率",
        "key": "preference:first-board",
        "candidate": candidate(
            content_text="研究首板优先看换手率", scope_type="global",
            scope={}, structured={}, source_quote="以后研究首板优先看换手率",
        ),
        "eligible": False,
        "reason": "unsupported_memory_identity",
    },
    {
        "name": "quote_must_be_verbatim",
        "message": "以后回答简洁一点",
        "key": "response_style",
        "candidate": candidate(
            content_text="concise", scope_type="global", scope={}, structured={},
            source_quote="以后回答简单一点",
        ),
        "eligible": False,
        "reason": "source_not_verbatim",
    },
    {
        "name": "temporary_expression_is_not_auto",
        "message": "这次用表格",
        "key": "output_format",
        "candidate": candidate(
            content_text="table", scope_type="global", scope={}, structured={},
            source_quote="这次用表格",
        ),
        "eligible": False,
        "reason": "no_persistence_marker",
    },
    {
        "name": "global_research_topic",
        "message": "以后研究主题只看首板",
        "key": "research_topic",
        "candidate": candidate(
            memory_type="research_context", content_text="首板",
            scope_type="global", scope={}, structured={},
            source_quote="以后研究主题只看首板",
        ),
        "eligible": True,
    },
    {
        "name": "research_topic_is_not_tool_scoped",
        "message": "以后研究主题只看首板",
        "key": "research_topic",
        "candidate": candidate(
            memory_type="research_context", content_text="首板",
            scope_type="tool", scope={"tool_name": "stock_financials"},
            structured={}, source_quote="以后研究主题只看首板",
        ),
        "eligible": False,
        "reason": "unsupported_typed_scope",
    },
]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_safe_auto_boundary_cases(case):
    result = evaluate_safe_auto_candidate(
        case["candidate"],
        key=case["key"],
        message=case["message"],
        consolidation=case.get("consolidation") or consolidation(),
    )
    assert result.eligible is case["eligible"], result.reasons
    if case.get("reason"):
        assert case["reason"] in result.reasons
