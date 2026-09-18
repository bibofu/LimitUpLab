"""Bounded conversation context without reusable historical market answers."""

import json

from langchain_core.messages import AIMessage, HumanMessage

HISTORY_CHAR_BUDGET = 12000
MAX_ENTITY_REFERENCES = 32


def prepare_history(request, history, evidence=None):
    """Keep user turns and grounded entity identity, never old assistant facts."""
    history = [m for m in history if m.session_id == request.session_id and m.status != "error"]
    messages, references, used = [], [], 0
    # The session-memory boundary has already selected the bounded raw window.
    # Applying another message-count slice here used to shrink a valid 16-message
    # window to 8 between memory refreshes. Keep the independent character budget
    # as the final prompt-size guard, but do not silently redefine that window.
    for message in reversed(history):
        if message.role == "user":
            text = message.content
            prompt_message = HumanMessage(content=text)
        else:
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
