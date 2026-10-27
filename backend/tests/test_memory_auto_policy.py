"""Narrow deterministic policy for invisible tool-display memory activation."""

from types import SimpleNamespace

from app.services.memory_auto_policy import evaluate_safe_auto_candidate


MESSAGE = "以后展示龙虎榜只显示股票名称"


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
        source_quote=MESSAGE,
    )
    fields.update(changes)
    return SimpleNamespace(**fields)


def decision(**changes):
    fields = {
        "decision": "add",
        "target_memory_id": None,
        "superseded_candidate_id": None,
    }
    fields.update(changes)
    return fields


def evaluate(**changes):
    item = changes.pop("candidate", candidate(**changes.pop("candidate_changes", {})))
    return evaluate_safe_auto_candidate(
        item,
        key=changes.pop("key", "tool_display:dragon_tiger_list"),
        message=changes.pop("message", MESSAGE),
        consolidation=changes.pop("consolidation", decision(**changes.pop("decision_changes", {}))),
    )


def test_structured_tool_display_add_is_auto_eligible():
    result = evaluate()
    assert result.eligible is True
    assert result.risk == "low"
    assert result.activation_reason


def test_updates_deletes_targets_and_instability_are_never_auto_activated():
    assert not evaluate(decision_changes={"decision": "update"}).eligible
    assert not evaluate(candidate_changes={"candidate_action": "delete"}).eligible
    assert not evaluate(decision_changes={"target_memory_id": "memory_1"}).eligible
    assert not evaluate(candidate_changes={"confidence": 0.89}).eligible
    assert not evaluate(candidate_changes={"stability": "conditional_scope"}).eligible


def test_only_structured_known_tool_display_columns_are_safe():
    assert not evaluate(candidate_changes={
        "structured": {"display_columns": ["name"], "extra": "value"},
    }).eligible
    assert not evaluate(candidate_changes={
        "structured": {"display_columns": ["unknown_column"]},
    }).eligible
    assert not evaluate(candidate_changes={
        "structured": {"display_columns": ["name", "symbol"]},
    }, key="tool_display:stock_kline").eligible
    assert evaluate(candidate_changes={
        "scope": {"tool_name": "stock_kline"},
        "structured": {"display_columns": ["symbol"]},
    }, key="tool_display:stock_kline").eligible


def test_closed_global_typed_setting_add_is_auto_eligible():
    message = "以后回答简洁一点"
    result = evaluate(
        message=message,
        key="response_style",
        candidate_changes={
            "content_text": "concise",
            "scope_type": "global",
            "scope": {},
            "structured": {},
            "source_quote": message,
        },
    )
    assert result.eligible is True
    assert result.activation_reason

    assert evaluate(
        message="以后研究主题只看首板",
        key="research_topic",
        candidate_changes={
            "memory_type": "research_context",
            "content_text": "首板",
            "scope_type": "global",
            "scope": {},
            "structured": {},
            "source_quote": "以后研究主题只看首板",
        },
    ).eligible is True


def test_typed_auto_accepts_tool_exceptions_and_rejects_ambiguous_scopes():
    message = "以后回答简洁一点"
    base = {
        "content_text": "concise",
        "scope_type": "global",
        "scope": {},
        "structured": {},
        "source_quote": message,
    }
    assert not evaluate(
        message=message, key="response_style",
        candidate_changes={**base, "content_text": "随便"},
    ).eligible
    assert not evaluate(
        message=message, key="response_style",
        candidate_changes={**base, "structured": {"style": "concise"}},
    ).eligible
    scoped_message = "以后分析财报时详细一点"
    scoped_base = {
        "content_text": "detailed",
        "source_quote": scoped_message,
    }
    assert evaluate(
        message=scoped_message, key="response_style",
        candidate_changes={
            **base, **scoped_base, "scope_type": "tool",
            "scope": {"tool_name": "stock_financials"},
        },
    ).eligible
    assert evaluate(
        message=scoped_message, key="response_style",
        candidate_changes={
            **base, **scoped_base, "scope_type": "tool",
            "scope": {"tool_name": "stock_financials"},
            "confidence": 0.85, "stability": "conditional_scope",
        },
    ).eligible
    assert evaluate(
        message="以后分析600519时详细一点", key="response_style",
        candidate_changes={
            **base, "content_text": "detailed", "scope_type": "entity",
            "scope": {"symbol": "600519"},
            "source_quote": "以后分析600519时详细一点",
        },
    ).eligible
    assert not evaluate(
        message="以后分析600519财报时详细一点", key="response_style",
        candidate_changes={
            **base, "content_text": "detailed", "scope_type": "entity",
            "scope": {"symbol": "600519"},
            "source_quote": "以后分析600519财报时详细一点",
        },
    ).eligible
    assert not evaluate(
        message=message, key="response_style",
        candidate_changes={
            **base, "scope_type": "condition",
            "scope": {"condition": "分析财报时"},
        },
    ).eligible
    assert not evaluate(
        message=message, key="research_topic",
        candidate_changes={
            "memory_type": "research_context",
            "content_text": "首板",
            "scope_type": "tool",
            "scope": {"tool_name": "stock_financials"},
            "structured": {},
            "source_quote": message,
        },
    ).eligible
    scoped_message = "以后分析财报时详细一点"
    assert not evaluate(
        message=scoped_message, key="response_style",
        candidate_changes={
            **base,
            "content_text": "detailed",
            "source_quote": scoped_message,
        },
    ).eligible


def test_evidence_must_be_verbatim_and_contain_persistence_intent():
    assert not evaluate(candidate_changes={"source_quote": "不存在的原文"}).eligible
    assert not evaluate(
        message="这次先用表格",
        candidate_changes={"source_quote": "这次先用表格"},
    ).eligible
    assert not evaluate(candidate_changes={"scope_type": "global", "scope": {}}).eligible
