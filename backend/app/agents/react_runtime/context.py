"""Bounded conversation context and explicitly owner-scoped historical references."""

from copy import deepcopy

from langchain_core.messages import AIMessage, HumanMessage

HISTORY_CHAR_BUDGET = 12000
MAX_HISTORY_EVIDENCE = 16


def prepare_history(request, history, evidence):
    """Router supplies authenticated session messages; never accept arbitrary refs."""
    history = [m for m in history if m.session_id == request.session_id and m.status != "error"]
    messages, references, used = [], [], 0
    for message in reversed(history[-8:]):
        text = message.content
        if used + len(text) > HISTORY_CHAR_BUDGET:
            # Keep whole messages, not a misleading half of an old answer.
            continue
        used += len(text)
        messages.append((HumanMessage if message.role == "user" else AIMessage)(content=text))
        if message.role != "assistant":
            continue
        metadata = message.metadata or {}
        for trace in metadata.get("tool_results", []):
            if trace.get("name") != "react_execution":
                continue
            for key, record in trace.get("output", {}).get("evidence", {}).items():
                if len(references) >= MAX_HISTORY_EVIDENCE:
                    break
                if key in evidence.records or record.get("historical_reference"):
                    continue
                if record.get("result_state") not in {"ok", "empty", "partial"}:
                    continue
                restored = deepcopy(record)
                restored["historical_reference"] = True
                evidence.records[key] = restored
                view = evidence.view(key, limit=3)
                references.append(view)
    return list(reversed(messages)), references
