import json

from langchain_core.messages import AIMessage, ToolMessage

from app.agents.context_builder import build_context, validate_working_messages
from app.agents.conversation_memory import compress_conversation
from app.services.memory_scope import clean_conversation_for_memory_revision, sanitize_working_messages_for_memory_revision
from test_memory_retrieval import SnapshotRepository, record, retrieve


def control_history():
    return [
        {"message_id": "u-save", "role": "user", "content": "请跨会话记住我喜欢简洁回答", "run_id": "save"},
        {"message_id": "a-save", "role": "assistant", "content": "已记住简洁偏好", "run_id": "save",
         "metadata": {"memory_scope": "long_term", "memory_action": {"action": "upsert"}}},
        {"message_id": "u-query", "role": "user", "content": "只看主板首板评级", "run_id": "query"},
        {"message_id": "a-query", "role": "assistant", "content": "按你的简洁偏好介绍结果", "run_id": "query",
         "metadata": {"memory_context_used": True, "memory_context_fingerprint": "old"}},
    ]


def test_control_turns_and_old_preference_shaped_assistant_prose_cannot_revive_after_delete():
    state = {"conversation_summary": "用户偏好简洁", "active_constraints": ["简洁"],
             "research_context": {"research_goal": "简洁"}, "recent_messages": control_history(),
             "memory_context_fingerprint": "old", "active_references": [{"source_run_id": "real-list"}]}
    current = retrieve(SnapshotRepository([], revision=2, fingerprint="new"))
    clean = clean_conversation_for_memory_revision(state, current)
    assert "简洁" not in json.dumps(clean, ensure_ascii=False)
    assert "只看主板首板评级" in clean["active_constraints"]
    assert clean["active_references"] == state["active_references"]
    assert state["conversation_summary"] == "用户偏好简洁"


def test_current_memory_is_ephemeral_and_lower_priority_than_current_query():
    retrieved = retrieve(SnapshotRepository([record(value="简洁")], fingerprint="same"))
    state = {"active_constraints": ["本会话希望详细解释"], "memory_context_fingerprint": "same"}
    built = build_context(system_prompt="研究规则", query="这次详细解释", conversation=state,
                          working_messages=[], long_term_memory=retrieved)
    payload = json.loads(built.messages[1].content.split("\n", 1)[1])
    assert payload["long_term_memories"][0]["value"] == "简洁"
    assert payload["active_constraints"] == ["本会话希望详细解释"]
    assert "低于本轮明确要求和有效会话约束" in built.messages[0].content
    assert "long_term_memories" not in state
    assert built.messages[-1].content == "这次详细解释"


def test_expired_memory_resumption_clears_old_ai_text_but_keeps_calls_and_evidence():
    retrieved = retrieve(SnapshotRepository([], revision=1, fingerprint="expired"))
    working = [AIMessage(content="我按已删除偏好回答", tool_calls=[{"id": "q", "name": "quote", "args": {}}]),
               ToolMessage(content='{"close":10}', tool_call_id="q"), AIMessage(content="旧偏好候选答案")]
    clean = sanitize_working_messages_for_memory_revision(working, retrieved, "before-expiry")
    validate_working_messages(clean)
    assert len(clean) == 2 and clean[0].content == "" and clean[0].tool_calls
    assert clean[1].content == working[1].content
    assert working[0].content == "我按已删除偏好回答"


def test_memory_management_never_becomes_rolling_session_summary():
    state = compress_conversation({}, control_history(), summary_trigger_tokens=1000, recent_window_tokens=500)
    assert [item["message_id"] for item in state["recent_messages"]] == ["u-query", "a-query"]
    assert state["last_committed_message_id"] == "a-query"


def test_optional_memory_is_removed_before_current_run_context_overflows():
    base = build_context(system_prompt="规则", query="当前问题", conversation={}, working_messages=[])
    retrieved = retrieve(SnapshotRepository([record(value="长偏好" * 80)], revision=0, fingerprint="empty-before"))
    built = build_context(system_prompt="规则", query="当前问题", conversation={}, working_messages=[],
                          long_term_memory=retrieved, max_input_tokens=base.diagnostics["estimated_input_tokens"] + 100)
    assert built.diagnostics["memory_hit_count"] == 0
    assert built.diagnostics["memory_context_budget_dropped"] == 1
    assert built.messages[-1].content == "当前问题"
