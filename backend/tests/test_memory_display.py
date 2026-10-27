"""Deterministic tool display projections and their reference ordering."""

from app.agents.structured_references import references_from_committed_result
from app.services.memory_display import (
    build_display_blocks,
    display_answer_rule,
    display_policies_for,
)


def memory_item(**changes):
    value = {
        "memory_id": "memory-display",
        "scope_type": "tool",
        "scope": {"tool_name": "dragon_tiger_list"},
        "structured": {"display_columns": ["name"]},
        "value": "展示龙虎榜时只显示股票名称",
        "scope_key": 'tool:{"tool_name":"dragon_tiger_list"}',
    }
    value.update(changes)
    return value


def tool_observation():
    return {
        "name": "dragon_tiger_list",
        "row_count": 2,
        "truncated": False,
        "rows": [
            {"symbol": "000002", "name": "样本乙", "net_buy_amount": 200},
            {"symbol": "000001", "name": "样本甲", "net_buy_amount": 100},
        ],
    }


def test_display_policy_projects_only_requested_columns_and_keeps_order():
    policies = display_policies_for([memory_item()], "展示最新的龙虎榜")
    blocks = build_display_blocks([tool_observation()], policies)

    assert len(blocks) == 1
    block = blocks[0]
    assert block["columns"] == ["name"]
    assert block["rows"] == [{"name": "样本乙"}, {"name": "样本甲"}]
    assert block["ordered_symbols"] == ["000002", "000001"]
    assert block["row_count"] == 2
    assert block["display_policy_id"] == "memory-display"
    assert block["missing_columns"] == []


def test_display_answer_rule_prevents_row_duplication_without_extra_columns():
    policies = display_policies_for([memory_item()], "展示最新的龙虎榜")
    rule = display_answer_rule(policies)
    assert "dragon_tiger_list" in rule
    assert "name" in rule
    assert "不要重复该表" in rule
    assert "完整名单" in rule
    assert display_answer_rule([]) == ""


def test_current_field_request_overrides_long_term_display_policy():
    assert display_policies_for(
        [memory_item()], "展示最新龙虎榜的代码和名称",
    ) == []


def test_display_policy_can_be_derived_when_structured_fields_are_absent():
    policies = display_policies_for(
        [memory_item(structured={})], "展示最新的龙虎榜",
    )
    assert policies[0]["columns"] == ["name"]


def test_display_block_order_is_used_before_answer_prose_for_ordinals():
    response = {
        "run_id": "run-display",
        "task_status": "complete",
        "answer": "样本甲、样本乙",
        "tool_results": [{
            "name": "dragon_tiger_list",
            "status": "success",
            "output": {
                "rows": [
                    {"symbol": "000001", "name": "样本甲"},
                    {"symbol": "000002", "name": "样本乙"},
                ],
                "row_count": 2,
            },
        }],
        "display_blocks": [{
            "kind": "table",
            "source_tool": "dragon_tiger_list",
            "source_tool_index": 0,
            "columns": ["name"],
            "rows": [{"name": "样本乙"}, {"name": "样本甲"}],
            "row_count": 2,
            "ordered_symbols": ["000002", "000001"],
        }],
    }
    references = references_from_committed_result("run-display", response)
    assert len(references) == 1
    assert references[0]["reference_type"] == "display_block"
    assert references[0]["ordinal_resolvable"] is True
    assert references[0]["ordered_symbols"] == ["000002", "000001"]
