"""Bounded conversation context without reusable historical market answers."""

import json

from langchain_core.messages import AIMessage

HISTORY_CHAR_BUDGET = 12000
MAX_ENTITY_REFERENCES = 32


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
