"""Turn validated tool output into a self-describing model-facing result."""

from __future__ import annotations

import json
from typing import Any

from app.agents.tool_payload import TOOL_COLLECTIONS, rows_of
from app.agents.tool_output_contracts import field_definitions_for


def dump(value: Any) -> str:
    """Serialize tool observations without exposing non-JSON provider objects."""

    return json.dumps(value, ensure_ascii=False, default=str)


def business_definitions(gateway) -> list[dict[str, Any]]:
    """Return only business tool definitions, never runtime control tools."""

    definitions = []
    for name, tool in gateway.structured.items():
        parameters = tool.args_schema.model_json_schema()
        parameters["additionalProperties"] = False
        definitions.append({"type": "function", "function": {
            "name": name,
            "description": tool.description,
            "parameters": parameters,
        }})
    return definitions


def model_result(name: str, result, payload: Any, status: str, *, arguments=None, allowed_arguments=None) -> dict[str, Any]:
    """Expose every contracted fact once, without applying another row limit."""

    try:
        rows = rows_of(payload, tool=name)
    except ValueError:
        if status != "error":
            raise
        rows = []
    metadata = payload if isinstance(payload, dict) else {}
    truncated = bool(metadata.get("truncated") or metadata.get("source_truncated"))
    incomplete = (
        truncated or metadata.get("complete") is False
        or metadata.get("complete_matching_query") is False
        or bool(metadata.get("data_missing"))
        or metadata.get("status") == "data_missing"
    )
    if status in {"ok", "empty"} and incomplete:
        status = "partial"
    message: dict[str, Any] = {
        "name": name,
        "status": status,
        "summary": result.summary,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "field_definitions": field_definitions_for(
            name, rows=rows, metadata=metadata
        ),
    }
    if arguments is not None:
        allowed = set(allowed_arguments if allowed_arguments is not None else arguments)
        effective = {**arguments, **{key: value for key, value in (getattr(result, "input", {}) or {}).items()
                                    if key in allowed and value is not None}}
        message["query_scope"] = json.loads(dump({
            "scope_version": 1, "tool": name, "arguments": effective, "explicit_arguments": arguments,
        }))
    collection = TOOL_COLLECTIONS.get(name)
    if collection:
        conflicts = {}
        for key, value in metadata.items():
            if key == collection:
                continue
            if key in message or key == "payload_metadata":
                conflicts[key] = value
            else:
                message[key] = value
        if conflicts:
            message["payload_metadata"] = conflicts
    return message
