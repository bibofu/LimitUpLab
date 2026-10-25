"""Explicit, checkpointed memory commands outside the business tool loop."""

import json

from app.agents.memory_policy import MEMORY_LABELS, MemoryIntent
from app.memory_models import MemoryWrite
from app.repositories.long_term_memory_repository import LongTermMemoryRepository, MemoryConflict, MemoryNotFound
from app.services.memory_retrieval import retrieve_memories


def repository_for(control):
    return LongTermMemoryRepository(control.journal.database_url)


def retrieve_for_run(control, query):
    return retrieve_memories(repository_for(control), owner_id=control.row["owner_id"], query=query)


def prepare_memory_request(intent, control, owner_revision):
    """Freeze CAS expectations BEFORE checkpointing a mutation proposal.

    Retrying a graph node must use identical operation inputs, even after its
    receipt has committed or another session has edited/deleted the record.
    """
    result = {"intent": intent.model_dump(), "expected_owner_revision": owner_revision}
    if intent.action not in {"save", "delete"}:
        return result
    current = repository_for(control).get_by_key(
        control.row["owner_id"], intent.memory_type, intent.key, include_inactive=True,
    )
    result.update(expected_revision=current.revision if current else 0,
                  memory_id=current.memory_id if current else None,
                  restore_deleted=bool(current and current.deleted_at))
    if intent.action == "save" and intent.memory_type == "research_archive" and not intent.source_run_id:
        with control.journal.connection() as db:
            previous = db.execute("""SELECT run_id FROM react_runs WHERE owner_id=? AND session_id=?
                AND run_id<>? AND response_json IS NOT NULL ORDER BY started_at DESC LIMIT 1""",
                (control.row["owner_id"], control.row["session_id"], control.row["run_id"])).fetchone()
        result["intent"]["source_run_id"] = previous["run_id"] if previous else None
    return result


def _label(value):
    return MEMORY_LABELS.get(value, value)


def _listing(repository, owner_id, intent):
    kinds = (intent.memory_type,) if intent.memory_type else None
    snapshot = repository.read_context(owner_id, memory_types=kinds,
                                       exact_keys=[intent.key] if intent.key else None, limit=100)
    items = snapshot["items"]
    if not items:
        return "当前没有符合条件的有效长期记忆。"
    lines = ["当前有效的长期记忆："]
    for item in items[:10]:
        if item["memory_type"] == "research_archive":
            source = item["metadata"]
            lines.append(f"- 历史研究档案「{item['key']}」（截至 {source['as_of']}，"
                         f"版本 {source['report_version']}，来源任务 {item['source_run_id']}）。"
                         "这是历史研究记录，不代表当前市场事实。")
            if intent.memory_type == "research_archive" and intent.key:
                lines.append(item["value"])
        else:
            lines.append(f"- {_label(item['key'])}：{_label(item['value'])}（版本 {item['revision']}）。")
    if len(items) > 10:
        lines.append("其余记忆可按类型或档案名称查询。")
    return "\n".join(lines)


def execute_memory_request(state, control):
    request = state["memory_request"]
    intent = MemoryIntent.model_validate(request["intent"])
    repository = repository_for(control)
    owner_id = control.row["owner_id"]
    operation_id = control.row["run_id"] + ":memory"
    action = {"action": intent.action, "memory_type": intent.memory_type,
              "key": intent.key, "operation_id": operation_id}
    try:
        if intent.action == "unsupported":
            answer = ("目前长期记忆支持回答详略、展示格式、研究主题和有来源的历史报告。"
                      "请一次明确提出一项保存、修改或删除请求；当前没有执行任何记忆变更。")
        elif intent.action == "list":
            answer = _listing(repository, owner_id, intent)
        elif intent.action == "delete" and not request["memory_id"]:
            answer = "这项长期记忆不存在，无需删除。"
        else:
            if intent.action == "save":
                write = MemoryWrite(
                    memory_type=intent.memory_type, key=intent.key, value=intent.value or "source report",
                    expected_revision=request["expected_revision"], restore_deleted=request["restore_deleted"],
                    source_run_id=intent.source_run_id,
                    source_user_message=json.loads(control.row["request_json"])["message"],
                )
                result = repository.put(owner_id, write, operation_id, control=control,
                                        expected_owner_revision=request["expected_owner_revision"])
            else:
                result = repository.delete(owner_id, request["memory_id"], request["expected_revision"],
                                           operation_id, control=control,
                                           expected_owner_revision=request["expected_owner_revision"])
            current = result.current
            action["memory_id"] = current.memory_id
            if current.deleted_at:
                answer = "这项长期记忆已删除，之后的问答不会再使用它。"
            elif current.expired:
                answer = "这项长期记忆已过期，之后的问答不会再使用它。"
            elif result.replayed and current.revision != result.operation_revision:
                answer = "原记忆操作已完成；这项记忆后来已更新，本次恢复未覆盖较新的版本。"
            elif intent.action == "delete":
                answer = "原删除操作已完成；这项记忆后来被明确重新保存，本次恢复未修改它。"
            elif current.memory_type == "research_archive":
                answer = (f"已保存历史研究档案「{current.key}」，截至 {current.metadata['as_of']}。"
                          "档案保留原报告与证据来源，不作为当前行情或交易依据。")
            else:
                answer = (f"已保存长期记忆：{_label(current.key)}为{_label(current.value)}。"
                          "同一用户的新会话也会使用；当前问题中的明确要求优先。")
        status, reason = "answered", "memory_managed"
    except (MemoryConflict, MemoryNotFound):
        answer = "长期记忆已被其他操作修改。本次没有覆盖较新的记录，请重新提出记忆请求。"
        status, reason = "clarify", "memory_conflict"
    except ValueError:
        answer = "这项内容暂不能保存为长期记忆。历史报告需要已完成的研究结果、明确日期及可核验来源；本次未保存。"
        status, reason = "clarify", "memory_policy"
    retrieved = retrieve_for_run(control, state["current_query"])
    return {"answer": answer, "status": status, "stop_reason": reason, "done": True,
            "memory_action": action, "memory_owner_revision": retrieved.owner_revision,
            "memory_context_fingerprint": retrieved.context_fingerprint, "memory_context_used": False,
            "context_diagnostics": retrieved.diagnostics}
