"""Fixed-world context-contract ablation; no external model or market service.

The measured outcome is whether required data reaches the model with its proper
scope. It is deliberately NOT an estimate of LLM answer accuracy or trading
performance. All three modes use the same query, tool output and token budget.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from statistics import mean

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.context_builder import build_context
from app.memory_models import MemoryWrite
from app.services.memory_retrieval import retrieve_memories
from long_term_memory_world import OLD_DATE, SOURCE


MODES = ("no_memory", "short_term", "short_and_long_term")


@dataclass
class ContextCase:
    name: str
    query: str
    conversation: dict
    retrieval: object
    check: object
    expected: tuple[bool, bool, bool]
    working: tuple = ()


def payload(result):
    return json.loads(result.messages[1].content.split("\n", 1)[1])


def memory_value(result, key):
    return next((item["value"] for item in payload(result).get("long_term_memories", [])
                 if item["key"] == key), None)


def message_content(result):
    return [item.content for item in result.messages[2:]]


def build_cases(world, repository):
    def save(owner, key="response_style", value="concise", **changes):
        kind = changes.pop("memory_type", "preference")
        return repository.put(owner, MemoryWrite(memory_type=kind, key=key, value=value,
                              source_user_message="显式长期保存固定评测偏好", **changes), key).current

    save(world.owner)
    save(world.owner, "research_topic", "首板", memory_type="research_context")
    source = world.source_run("生成合成历史报告")
    save(world.owner, "historical-report", "", memory_type="research_archive", source_run_id=source["run_id"])
    current = retrieve_memories(repository, owner_id=world.owner, query="解释首板")
    archives = retrieve_memories(repository, owner_id=world.owner, query="查看历史研究档案",
                                memory_types=("research_archive",), allow_archives=True)
    foreign = retrieve_memories(repository, owner_id=world.other_owner, query="解释首板")
    references = [{"reference_id": "fixture-list", "source_run_id": source["run_id"],
                   "ordered_symbols": ["600001", "600002"], "trade_date": OLD_DATE,
                   "committed_status": "committed", "ordinal_resolvable": True}]

    def archive_is_sourced(result):
        memories = payload(result).get("long_term_memories", [])
        return len(memories) == 1 and memories[0]["historical"] is True \
            and memories[0]["current_market_evidence"] is False \
            and memories[0]["archive_source"]["as_of"] == OLD_DATE \
            and SOURCE + "/historical" in memories[0]["archive_source"]["source_references"]

    current_observation = world.current_tool()
    working = (
        AIMessage(content="", tool_calls=[{"id": "current", "name": "fixture_current", "args": {"symbol": "600001"}}]),
        ToolMessage(content=json.dumps(current_observation, ensure_ascii=False), tool_call_id="current"),
    )

    def preserves_current_evidence(result):
        tools = [message for message in result.messages if isinstance(message, ToolMessage)]
        return len(tools) == 1 and json.loads(tools[0].content) == current_observation \
            and all(item["memory_type"] != "research_archive"
                    for item in payload(result).get("long_term_memories", []))

    # Delete changes owner revision. A stale checkpoint includes derived prose;
    # rebuilding under the fresh snapshot must remove it without text editing.
    erased = save(world.owner + "-deleted")
    previous = retrieve_memories(repository, owner_id=world.owner + "-deleted", query="解释首板")
    repository.delete(world.owner + "-deleted", erased.memory_id, erased.revision, "delete")
    deleted = retrieve_memories(repository, owner_id=world.owner + "-deleted", query="解释首板")
    stale = {"memory_context_fingerprint": previous.context_fingerprint,
             "recent_messages": [
                 {"message_id": "u1", "role": "user", "content": "解释首板", "status": "success"},
                 {"message_id": "a1", "role": "assistant", "content": "STALE_STYLE_DERIVED_TEXT",
                  "status": "success", "metadata": {"memory_context_used": True,
                    "memory_context_fingerprint": previous.context_fingerprint}}]}
    clock = datetime(2030, 1, 1, tzinfo=timezone.utc)
    save(world.owner + "-expired", expires_at=clock)
    unexpired = retrieve_memories(repository, owner_id=world.owner + "-expired", query="解释首板", now=clock - timedelta(seconds=1))
    expired = retrieve_memories(repository, owner_id=world.owner + "-expired", query="解释首板", now=clock)
    expired_stale = {**stale, "memory_context_fingerprint": unexpired.context_fingerprint}
    assert unexpired.owner_revision == expired.owner_revision
    assert unexpired.context_fingerprint != expired.context_fingerprint
    return [
        ContextCase("same_session_ordinal", "第二只是什么", {"active_references": references}, current,
                    lambda result: bool(result.resolved_reference and result.resolved_reference["symbols"] == ["600002"]),
                    (False, True, True)),
        ContextCase("same_session_date_constraint", "延续当前研究日期", {
                    "active_constraints": {"trade_date": OLD_DATE}, "memory_context_fingerprint": current.context_fingerprint}, current,
                    lambda result: payload(result)["active_constraints"] == {"trade_date": OLD_DATE}, (False, True, True)),
        ContextCase("cross_session_response_style", "解释首板", {}, current,
                    lambda result: memory_value(result, "response_style") == "concise", (False, False, True)),
        ContextCase("cross_session_research_topic", "继续长期研究主题", {}, current,
                    lambda result: memory_value(result, "research_topic") == "首板", (False, False, True)),
        ContextCase("explicit_historical_archive", "查看历史研究档案", {}, archives, archive_is_sourced, (False, False, True)),
        ContextCase("current_evidence_not_replaced_by_archive", "查看本轮固定行情", {}, current,
                    preserves_current_evidence, (True, True, True), working),
        ContextCase("other_owner_receives_no_preference", "解释首板", {}, foreign,
                    lambda result: not payload(result).get("long_term_memories"), (True, True, True)),
        ContextCase("deleted_preference_not_revived_by_checkpoint", "解释连板", stale, deleted,
                    lambda result: "STALE_STYLE_DERIVED_TEXT" not in message_content(result), (True, False, True)),
        ContextCase("expired_preference_not_revived_by_checkpoint", "解释连板", expired_stale, expired,
                    lambda result: "STALE_STYLE_DERIVED_TEXT" not in message_content(result), (True, False, True)),
    ]


def run_ablation(world, repository):
    rows = []
    for case in build_cases(world, repository):
        for index, mode in enumerate(MODES):
            result = build_context(system_prompt="只根据当前工具证据做研究解释。", query=case.query,
                                   conversation={} if mode == "no_memory" else case.conversation,
                                   working_messages=[HumanMessage(content=case.query), *case.working],
                                   long_term_memory=case.retrieval if mode == "short_and_long_term" else None,
                                   max_input_tokens=8000)
            passed = bool(case.check(result))
            assert passed is case.expected[index], (case.name, mode, result.messages)
            rows.append({"case": case.name, "mode": mode, "context_contract_pass": passed,
                         "estimated_input_tokens": result.diagnostics["estimated_input_tokens"],
                         "memory_hit_count": result.diagnostics["memory_hit_count"],
                         "long_term_memory_tokens": result.diagnostics["long_term_memory_tokens"]})
    aggregates = {}
    for mode in MODES:
        selected = [row for row in rows if row["mode"] == mode]
        aggregates[mode] = {"cases": len(selected), "context_contract_passes": sum(row["context_contract_pass"] for row in selected),
                            "mean_estimated_input_tokens": round(mean(row["estimated_input_tokens"] for row in selected), 2)}
    return {"evaluation": "deterministic_context_contract_ablation_v1", "world": SOURCE,
            "external_model_calls": 0, "token_measurement": "conservative estimate, not provider billing",
            "scope": "Context availability, isolation and provenance only; not LLM answer quality.",
            "aggregates": aggregates, "rows": rows}
