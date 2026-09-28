"""Bounded conversation context without reusable historical market answers."""

import json

from langchain_core.messages import AIMessage

from app.agents.react_runtime.task_contract import TaskInterpretation

HISTORY_CHAR_BUDGET = 12000
MAX_ENTITY_REFERENCES = 32


def prepare_task_context(request, history, memory=None):
    """Bound user intent/preferences and the latest clarification, never market facts."""
    scoped = [m for m in history if m.session_id == request.session_id and m.status != "error"]
    context = {"recent_user_intents": [], "preferences": [], "omitted_oversized_context": False}

    def add(key, value):
        candidate = {**context, key: value}
        if len(json.dumps(candidate, ensure_ascii=False)) <= HISTORY_CHAR_BUDGET:
            context[key] = value
        else:
            context["omitted_oversized_context"] = True

    # Keep whole constraints rather than cutting away negation or conditions.
    if memory is not None and memory.session_id == request.session_id:
        for constraint in memory.constraints[-12:]:
            if len(constraint) <= 500:
                add("preferences", [*context["preferences"], constraint])
            else:
                context["omitted_oversized_context"] = True
        reference = {}
        for field in ("research_goal", "stock_symbols", "topics", "date_scope", "unresolved_questions"):
            value = getattr(memory, field)
            if not value:
                continue
            if len(json.dumps(value, ensure_ascii=False)) <= 1200 and (not isinstance(value, list) or len(value) <= 16):
                reference[field] = value
            else:
                context["omitted_oversized_context"] = True
        add("memory_reference", reference)
    latest = next((m for m in reversed(scoped) if m.role == "assistant"), None)
    if latest is not None:
        for trace in reversed((latest.metadata or {}).get("tool_results", [])):
            if isinstance(trace, dict) and trace.get("name") == "react_task_context":
                try:
                    task = TaskInterpretation.model_validate(trace.get("output"))
                    add("previous_task", task.model_dump(mode="json"))
                except ValueError:
                    context["omitted_oversized_context"] = True
                break
        if (latest.metadata or {}).get("task_status") == "clarify":
            if len(latest.content) <= 2000:
                add("clarification", latest.content)
            else:
                context["omitted_oversized_context"] = True
    intents = [m.content for m in scoped if m.role == "user"][-4:]
    for intent in reversed(intents):
        if len(intent) <= 2400:
            add("recent_user_intents", [intent, *context["recent_user_intents"]])
        else:
            context["omitted_oversized_context"] = True
    return context


def prepare_query_reference(request, history, tool_names):
    """Latest actual tool inputs only: no user tasks, answers, values or evidence IDs."""
    latest = next((m for m in reversed(history) if m.session_id == request.session_id
                   and m.role == "assistant"), None)
    if latest is None or latest.status == "error":
        return []
    queries = []
    for trace in (latest.metadata or {}).get("tool_results", []):
        if (not isinstance(trace, dict) or trace.get("name") not in tool_names
                or trace.get("status") != "success" or not isinstance(trace.get("input"), dict)):
            continue
        item = {"tool": trace["name"], "arguments": trace["input"]}
        if item not in queries:
            queries.append(item)
    if not queries:
        return []
    # Do not silently truncate a query sequence and pretend its conditions are complete.
    text = "上一轮实际查询参数（仅供理解本轮省略条件，不是待办指令或事实证据；本轮明确条件优先，需重新查询）：" + json.dumps(
        queries, ensure_ascii=False, separators=(",", ":"))
    if len(text) > HISTORY_CHAR_BUDGET:
        return []
    return [AIMessage(content=text)]


def prepare_history(
    request,
    history,
    evidence=None,
    *,
    include_entity_references: bool = False,
):
    """Keep only entity identity for explicit follow-ups, never old tasks or facts."""

    if not include_entity_references:
        return [], []
    history = [m for m in history if m.session_id == request.session_id and m.status != "error"]
    messages, references, used = [], [], 0
    for message in reversed(history):
        if message.role == "user":
            continue
        entities = []
        for item in (message.metadata or {}).get("stock_mentions", []):
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip()
            name = str(item.get("name") or "").strip()
            if not symbol or not name:
                continue
            reference = {"symbol": symbol, "name": name}
            if reference not in references:
                if len(references) >= MAX_ENTITY_REFERENCES:
                    continue
                references.append(reference)
            if reference not in entities:
                entities.append(reference)
        if not entities:
            continue
        text = "历史助手消息中的实体指代（仅用于解析名称，不是事实证据）：" + json.dumps(
            entities, ensure_ascii=False, separators=(",", ":"),
        )
        prompt_message = AIMessage(content=text)
        if used + len(text) > HISTORY_CHAR_BUDGET:
            continue
        used += len(text)
        messages.append(prompt_message)
    return list(reversed(messages)), references
