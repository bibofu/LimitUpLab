"""Bounded conversation context and explicitly owner-scoped historical references."""

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.react_runtime.evidence import HISTORY_SCOPE

HISTORY_CHAR_BUDGET = 12000
MAX_HISTORY_EVIDENCE = 16


def prepare_history(request, history, evidence):
    """Router supplies authenticated session messages; never accept arbitrary refs."""
    history = [m for m in history if m.session_id == request.session_id and m.status != "error"]
    messages, references, used = [], [], 0
    # The session-memory boundary has already selected the bounded raw window.
    # Applying another message-count slice here used to shrink a valid 16-message
    # window to 8 between memory refreshes. Keep the independent character budget
    # as the final prompt-size guard, but do not silently redefine that window.
    for message in reversed(history):
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
                if key in evidence.records or evidence.scope_of(record) == HISTORY_SCOPE:
                    continue
                if record.get("result_state") not in {"ok", "empty", "partial"}:
                    continue
                evidence.restore_history(key, record)
                view = evidence.view(key, limit=3)
                references.append(view)
    return list(reversed(messages)), references
