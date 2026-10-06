"""Allowlisted structural metadata; never retain model text, IDs or argument values."""

import json
from typing import get_args

from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import ValidationError
from pydantic_core import ErrorType


ENUMS = {
    "response_kind": {"ai_message", "ai_message_chunk", "other", "none"},
    "content_kind": {"text", "blocks", "empty", "other"},
    "finish_reason": {"stop", "length", "tool_calls", "function_call", "content_filter", "unknown"},
    "tool_id_status": {"valid", "missing", "duplicate", "invalid", "not_applicable"},
    "failure_category": {"response_type", "tool_arguments", "tool_ids", "tool_selection", "request",
                         "schema_validation", "requirement_indices", "audit_fields", "native_protocol"},
}
JSON_FAILURES = {"empty", "unexpected_end", "unterminated_string", "syntax", "non_object", "non_string"}
SHAPES = {"object", "array", "string", "number", "boolean", "null", "other"}
ERROR_TYPES = set(get_args(ErrorType))


def safe_protocol_metadata(value):
    """Reapply the allowlist even to metadata supplied by custom providers."""
    if not isinstance(value, dict):
        return {}
    result = {key: item for key, allowed in ENUMS.items()
              if isinstance(item := value.get(key), str) and item in allowed}
    for key in ("tool_call_count", "invalid_tool_call_count", "tool_call_chunk_count"):
        if type(value.get(key)) is int and value[key] >= 0:
            result[key] = value[key]
    if type(value.get("expected_tool_matches")) is bool:
        result["expected_tool_matches"] = value["expected_tool_matches"]
    arguments = value.get("arguments")
    if isinstance(arguments, list):
        result["arguments"] = []
        for item in arguments[:8]:
            if not isinstance(item, dict) or not isinstance(item.get("shape"), str) or item["shape"] not in SHAPES:
                continue
            shape = {"shape": item["shape"]}
            if type(item.get("size")) is int and item["size"] >= 0:
                shape["size"] = item["size"]
            if isinstance(item.get("json_failure"), str) and item["json_failure"] in JSON_FAILURES:
                shape["json_failure"] = item["json_failure"]
            result["arguments"].append(shape)
    return result


def argument_structure(value, *, encoded=False):
    if encoded:
        if not isinstance(value, str):
            return {"shape": "other", "json_failure": "non_string"}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            category = ("empty" if not value.strip() else "unterminated_string"
                        if error.msg.startswith("Unterminated string") else "unexpected_end"
                        if error.pos >= len(value.rstrip()) else "syntax")
            return {"shape": "string", "size": len(value), "json_failure": category}
        result = argument_structure(parsed)
        if not isinstance(parsed, dict):
            result["json_failure"] = "non_object"
        return result
    shape = ("null" if value is None else "boolean" if isinstance(value, bool) else
             "object" if isinstance(value, dict) else "array" if isinstance(value, list) else
             "string" if isinstance(value, str) else "number" if isinstance(value, (int, float)) else "other")
    return {"shape": shape, **({"size": len(value)} if isinstance(value, (dict, list, str)) else {})}


def schema_owned_argument_shapes(value, schema):
    """Describe declared top-level fields only; never enumerate submitted object keys.

    The caller supplies its trusted schema. This distinguishes absent fields,
    explicit nulls and malformed child types without retaining any child values.
    """
    if not isinstance(value, dict):
        return [{"path": [], **argument_structure(value)}]
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if not isinstance(properties, dict):
        return []
    return [{"path": [field], "present": field in value,
             **(argument_structure(value[field]) if field in value else {})}
            for field in list(properties)[:16] if isinstance(field, str)]


def response_diagnostics(message, *, expected_tool=None, failure_category=None):
    kind = ("none" if message is None else "ai_message_chunk" if isinstance(message, AIMessageChunk)
            else "ai_message" if isinstance(message, AIMessage) else "other")
    result = {"response_kind": kind}
    if failure_category:
        result["failure_category"] = failure_category
    if not isinstance(message, AIMessage):
        return safe_protocol_metadata(result)
    calls, invalid = message.tool_calls, message.invalid_tool_calls
    content = message.content
    reason = message.response_metadata.get("finish_reason")
    ids = [call.get("id") for call in [*calls, *invalid]]
    id_status = ("not_applicable" if not ids else "missing" if any(value is None or value == "" for value in ids)
                 else "invalid" if any(not isinstance(value, str) for value in ids)
                 else "duplicate" if len(ids) != len(set(ids)) else "valid")
    result.update(finish_reason=reason if isinstance(reason, str) and reason in ENUMS["finish_reason"] else "unknown",
                  content_kind="empty" if not content else "text" if isinstance(content, str) else
                  "blocks" if isinstance(content, list) else "other",
                  tool_call_count=len(calls), invalid_tool_call_count=len(invalid), tool_id_status=id_status,
                  arguments=[*(argument_structure(call.get("args")) for call in calls[:8]),
                             *(argument_structure(call.get("args"), encoded=True) for call in invalid[:8])])
    if isinstance(message, AIMessageChunk):
        result["tool_call_chunk_count"] = len(message.tool_call_chunks)
        result["arguments"] = [argument_structure(call.get("args"), encoded=True)
                               for call in message.tool_call_chunks[:8]]
    if expected_tool is not None:
        result["expected_tool_matches"] = len(calls) == 1 and calls[0].get("name") == expected_tool
    return safe_protocol_metadata(result)


def schema_diagnostics(error, schema):
    """Only schema-owned field names and built-in error codes may cross this boundary."""
    fields = set()
    def collect(node):
        if isinstance(node, dict):
            fields.update(node.get("properties", {}))
            for child in node.values():
                collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)
    collect(schema)
    # Inputs are inspected only in memory for shape. Never return these raw
    # records, their messages, or arbitrary dictionary keys to diagnostics.
    errors = error.errors(include_url=False, include_context=False, include_input=True)
    sanitized = []
    for item in errors[:12]:
        path = [part if type(part) is int or isinstance(part, str) and part in fields
                else "<unknown>" for part in item["loc"][:12]]
        detail = {"path": path, "type": item["type"] if item["type"] in ERROR_TYPES else "custom_error"}
        if "<unknown>" not in path and "input" in item and item["type"] != "missing":
            # Missing-field errors attach their parent input, not a child value.
            detail["input_shape"] = argument_structure(item["input"])
        sanitized.append(detail)
    return {"failure_category": "schema_validation", "schema_error_count": len(errors),
            "schema_errors": sanitized}


def failure_diagnostics(error, *, schema, current=None):
    from app.services.llm_provider import NativeFunctionCallingError
    result = {**(current or {}), **safe_protocol_metadata(getattr(error, "diagnostics", None))}
    if isinstance(error, ValidationError):
        result.update(schema_diagnostics(error, schema))
    result.setdefault("failure_category", "native_protocol" if isinstance(error, NativeFunctionCallingError) else "request")
    return result
