"""Input-review payload and schema compatibility, without a live model.

The fixtures below do not classify natural language. Concept-vs-fact routing,
follow-up recovery, date negation, per-group limits and injection intent still
require live question/answer verification after a prompt change.
"""

from copy import deepcopy
from datetime import date
import json

from langchain_core.messages import AIMessage

from app.agents.react_runtime.display_fields import DISPLAY_FIELD_CATALOG
from app.services.prompt_security import review_input


class CapturingReviewer:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def generate_messages(self, messages, tools, **kwargs):
        self.calls.append((messages, tools, kwargs))
        return AIMessage(content="", tool_calls=[{
            "id": "input-review", "name": "submit_input_security_review", "args": self.decision,
        }])


def decision(**updates):
    return {
        "decision": "allow", "signals": [], "reason": "scripted protocol fixture",
        "request_kind": "research", "context_mode": "standalone",
        "current_task": "查询2026-09-18的龙虎榜，仅列代码和席位交易明细条数。",
        "date_scope": {"mode": "dates", "dates": ["2026-09-18"], "trading_day_comparisons": []},
        "pending_slots": [],
        "output_contract": {
            "mode": "table_only", "table_required": True,
            "field_requests": [{"requested": "代码", "field": "symbol"},
                               {"requested": "席位交易明细条数", "field": None}],
        },
        **updates,
    }


def test_review_receives_all_field_meanings_without_tool_planning_metadata():
    original_catalog = deepcopy(DISPLAY_FIELD_CATALOG)
    provider = CapturingReviewer(decision())
    review_input(provider, message="只列代码和席位交易明细条数。", timeout_seconds=5)

    assert len(provider.calls) == 1
    messages, tools, _ = provider.calls[0]
    catalog = json.loads(messages[-1].content)["display_field_catalog"]
    assert catalog == {key: value["meaning"] for key, value in original_catalog.items()}
    assert all(isinstance(meaning, str) for meaning in catalog.values())
    assert DISPLAY_FIELD_CATALOG == original_catalog

    # The semantic catalog and the wire enum must stay in sync as fields grow.
    assert len(tools) == 1
    schema = tools[0]["function"]["parameters"]
    field_schema = schema["$defs"]["ReviewFieldRequest"]["properties"]["field"]
    allowed = next(item["enum"] for item in field_schema["anyOf"] if "enum" in item)
    assert set(allowed) == set(catalog)
    output_fields = schema["$defs"]["ReviewOutputContract"]["properties"]
    assert "fields" not in output_fields and "field_resolution" not in output_fields


def test_unbound_column_keeps_clear_request_without_creating_a_user_slot():
    provider = CapturingReviewer(decision())
    context = {"preferences": ["简洁中文"], "memory_reference": {"object": "历史对象"}}
    task = review_input(provider, message="查询2026-09-18的龙虎榜，仅列代码和席位交易明细条数。",
                        timeout_seconds=5, anchor_date=date(2026, 10, 17),
                        task_context=context, page_defaults={"symbol": "600000"})

    payload = json.loads(provider.calls[0][0][-1].content)
    assert payload["trusted_defaults"] == {"anchor_date": "2026-10-17", "page": {"symbol": "600000"}}
    assert payload["task_context"] == context
    assert task.pending_slots == []
    assert task.current_task == provider.decision["current_task"]
    assert task.requested_date == date(2026, 9, 18)
    assert task.output_contract.fields == ["symbol", "unavailable_requested_2"]
    assert task.output_contract.unavailable_fields == {"unavailable_requested_2": "席位交易明细条数"}
    assert task.output_contract.field_resolution.reason == "unbound_requested_field"


def test_fixed_prompt_material_stays_within_reviewed_character_budgets():
    """A growth guard, not a tokenizer estimate or a claim of semantic quality."""
    from app.agents.react_runtime.runtime import SYSTEM as MAIN_SYSTEM
    from app.services.prompt_security import SYSTEM as INPUT_SYSTEM

    provider = CapturingReviewer(decision())
    review_input(provider, message="", timeout_seconds=5)
    messages, tools, _ = provider.calls[0]
    fixed_chars = sum(len(message.content) for message in messages) + len(json.dumps(tools, ensure_ascii=False))
    assert len(MAIN_SYSTEM) <= 3600
    assert len(INPUT_SYSTEM) <= 2700
    assert fixed_chars <= 14000
