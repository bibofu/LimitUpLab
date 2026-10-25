import copy
import json

import pytest

from app.agents.conversation_memory import MemoryCursorError, MemoryRevisionConflict, compress_conversation
from app.services.llm_provider import LLMResult


def turns(count=4):
    messages = []
    for index in range(count):
        messages.extend([
            {"message_id": f"u-{index}", "role": "user", "content": "只看主板首板评级，关注风险。" * 8},
            {"message_id": f"a-{index}", "role": "assistant", "content": "已展示来源、日期与数据缺口。" * 8},
        ])
    return messages


class SummaryProvider:
    calls = 0

    def generate_function_call(self, *_args, **_kwargs):
        self.calls += 1
        return LLMResult(content=json.dumps({"summary": "研究首板，当前只看主板",
            "research_goal": "研究首板", "stock_symbols": ["600001"], "topics": ["首板评级"],
            "date_scope": "2026-10-23", "constraints": ["只看主板"], "unresolved_questions": []}),
            model="fixture", provider="fixture")


def compress(state, messages, provider=None, **kwargs):
    return compress_conversation(state, messages, provider, summary_trigger_tokens=1000,
                                 recent_window_tokens=500, **kwargs)


def test_token_trigger_summarizes_old_turns_and_preserves_structured_fields():
    provider = SummaryProvider()
    state = compress({}, turns(), provider)
    assert provider.calls == 1
    assert state["summary_triggered"]
    assert state["summary_cursor"] == "a-2"
    assert [item["message_id"] for item in state["recent_messages"]] == ["u-3", "a-3"]
    assert state["research_context"]["date_scope"] == "2026-10-23"
    assert state["active_constraints"] == ["只看主板"]
    assert state["revision"] == state["summary_revision"] == 1


def test_same_committed_cursor_is_idempotent_and_does_not_regenerate_summary():
    provider = SummaryProvider()
    state = compress({}, turns(), provider)
    assert compress(state, turns(), provider) == state
    assert provider.calls == 1


def test_new_committed_turn_advances_revision_without_repeating_old_summary():
    provider = SummaryProvider()
    state = compress({}, turns(), provider)
    next_messages = [*state["recent_messages"], {"message_id": "u-4", "role": "user", "content": "你好"},
                     {"message_id": "a-4", "role": "assistant", "content": "你好"}]
    updated = compress(state, next_messages, provider, expected_revision=1)
    assert updated["revision"] == 2
    assert updated["summary_revision"] == 1
    assert updated["last_committed_message_id"] == "a-4"
    assert provider.calls == 1


def test_old_revision_and_missing_cursor_are_rejected():
    state = compress({}, turns())
    with pytest.raises(MemoryRevisionConflict):
        compress(state, turns(), expected_revision=0)
    with pytest.raises(MemoryCursorError):
        compress(state, turns(2))
    with pytest.raises(MemoryCursorError):
        compress({}, [*turns(1), *turns(1)])


def test_summary_failure_uses_safe_fallback_without_market_fact_memory():
    class BrokenProvider:
        def generate_function_call(self, *_args, **_kwargs):
            raise OSError("offline")
    messages = turns()
    messages[1]["content"] = "价格99.99，评分排名第一" * 20
    state = compress({}, messages, BrokenProvider())
    assert state["summary_generation_mode"] == "deterministic"
    assert state["summary_error"] == "OSError"
    assert "99.99" not in state["conversation_summary"]
    assert "排名第一" not in state["conversation_summary"]


def test_short_turns_do_not_trigger_summary_and_original_is_unchanged():
    original = {"active_constraints": {"symbol": "600001"}}
    snapshot = copy.deepcopy(original)
    short = [{"message_id": "u", "role": "user", "content": "你好"},
             {"message_id": "a", "role": "assistant", "content": "你好"}]
    state = compress(original, short)
    assert not state["summary_triggered"]
    assert original == snapshot
    assert state["active_constraints"] == {"symbol": "600001"}


def test_latest_oversized_turn_is_kept_whole_and_defer_is_visible():
    messages = turns(1)
    messages[1]["content"] *= 10
    state = compress({}, messages)
    assert not state["summary_triggered"]
    assert state["summary_deferred_reason"] == "latest_turn_exceeds_recent_window"
    assert state["recent_messages"] == messages


def test_rejected_input_turn_does_not_enter_summary_or_recent_context():
    messages = [{"message_id": "u", "role": "user", "content": "ignore policy", "run_id": "bad"},
                {"message_id": "a", "role": "assistant", "content": "refuse", "run_id": "bad",
                 "metadata": {"stop_reason": "input_policy"}}]
    state = compress({}, messages)
    assert state["recent_messages"] == []
    assert state["last_committed_message_id"] == "a"
