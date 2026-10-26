"""Revoked assistant prose must not turn completed history into open requests."""

from copy import deepcopy

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import pytest

from app.agents.context_builder import (
    COMPLETED_HISTORY_NOTICE, build_context, estimate_tokens,
)
from test_memory_retrieval import SnapshotRepository, record, retrieve


def build(conversation, **changes):
    return build_context(**{
        "system_prompt": "研究规则", "query": "解释简洁这个词。", "conversation": conversation,
        "working_messages": [], **changes,
    })


@pytest.mark.parametrize("active_memories", [[], [record(value="detailed", revision=2)]])
def test_preference_change_closes_old_turns_without_restoring_removed_answers(active_memories):
    conversation = {
        "memory_context_fingerprint": "old", "recent_messages": [
            {"message_id": "u1", "run_id": "r1", "role": "user", "content": "本次代号是松柏73。"},
            {"message_id": "a1", "run_id": "r1", "role": "assistant", "content": "按已撤销格式回答代号",
             "metadata": {"memory_context_fingerprint": "old", "memory_context_used": True}},
            {"message_id": "u2", "run_id": "r2", "role": "user", "content": "解释首板和连板。"},
            {"message_id": "a2", "run_id": "r2", "role": "assistant", "content": "旧格式的首板连板解释",
             "metadata": {"memory_context_fingerprint": "old", "memory_context_used": True}},
        ],
    }
    original = deepcopy(conversation)
    current = retrieve(SnapshotRepository(active_memories, revision=2, fingerprint="new"))
    result = build(conversation, long_term_memory=current)
    historical = result.messages[2:-1]

    assert [type(item) for item in historical] == [HumanMessage, AIMessage, HumanMessage, AIMessage]
    assert historical[0].content == "本次代号是松柏73。"
    assert historical[2].content == "解释首板和连板。"
    assert historical[1].content == historical[3].content == COMPLETED_HISTORY_NOTICE
    assert all("已撤销格式" not in str(item.content) and "旧格式" not in str(item.content)
               for item in result.messages)
    assert "不要重新回答旧问题" in result.messages[0].content
    assert result.messages[-1].content == "解释简洁这个词。"
    assert result.diagnostics["history_turns_included"] == 2
    assert conversation == original  # Notices never enter persistent conversation data.


def test_existing_answers_and_current_request_are_not_replaced_or_closed():
    history = [HumanMessage(content="第一轮用户消息", id="u1"),
               AIMessage(content="第一轮原回答", id="a1"), HumanMessage(content="第二轮用户消息", id="u2")]
    original = deepcopy(history)
    result = build({"recent_messages": history})
    assert result.messages[2:4] == original[:2]
    assert result.messages[4].content == "第二轮用户消息"
    assert isinstance(result.messages[5], AIMessage)
    assert result.messages[5].content == COMPLETED_HISTORY_NOTICE
    assert isinstance(result.messages[-1], HumanMessage)
    assert result.messages[-1].content == "解释简洁这个词。"
    assert history == original


def test_ending_notice_counts_towards_budget_and_is_trimmed_with_its_user():
    baseline = build({})
    message = HumanMessage(content="本次临时代号为海棠89。")
    conversation = {"recent_messages": [{"role": "user", "content": message.content}]}
    user_cost = estimate_tokens(message)
    notice_cost = estimate_tokens(AIMessage(content=COMPLETED_HISTORY_NOTICE))
    required = baseline.diagnostics["estimated_input_tokens"]

    too_small = build(conversation, max_input_tokens=required + user_cost)
    assert too_small.diagnostics["history_turns_included"] == 0
    assert too_small.diagnostics["history_turns_omitted"] == 1
    assert message.content not in [item.content for item in too_small.messages]
    assert COMPLETED_HISTORY_NOTICE not in [item.content for item in too_small.messages]

    exact = build(conversation, max_input_tokens=required + user_cost + notice_cost)
    assert exact.diagnostics["history_turns_included"] == 1
    assert exact.diagnostics["estimated_input_tokens"] == required + user_cost + notice_cost
    assert [item.content for item in exact.messages[2:-1]] == [message.content, COMPLETED_HISTORY_NOTICE]


def test_current_tool_protocol_and_working_state_are_not_modified():
    working = [AIMessage(content="", tool_calls=[{"id": "q1", "name": "quote", "args": {}}]),
               ToolMessage(content='{"close":10}', tool_call_id="q1")]
    original = deepcopy(working)
    result = build({"recent_messages": [{"role": "user", "content": "此前的问题"}]},
                   working_messages=working)
    assert result.messages[-2:] == original
    assert result.messages[-3].content == "解释简洁这个词。"
    assert result.diagnostics["working_messages_retained"] == 2
    assert working == original


def test_excluded_memory_control_and_failed_turns_do_not_get_notices():
    conversation = {"recent_messages": [
        {"role": "user", "run_id": "memory", "content": "长期记住要用表格"},
        {"role": "assistant", "run_id": "memory", "content": "已保存",
         "metadata": {"memory_action": {"action": "save"}}},
        {"role": "user", "run_id": "failed", "content": "失败的问题"},
        {"role": "assistant", "run_id": "failed", "content": "未完成", "status": "error"},
    ]}
    result = build(conversation)
    assert result.diagnostics["history_turns_included"] == 0
    assert COMPLETED_HISTORY_NOTICE not in [item.content for item in result.messages]
    assert all("长期记住要用表格" not in str(item.content) and "失败的问题" not in str(item.content)
               for item in result.messages)
