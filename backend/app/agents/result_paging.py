"""Bounded views of immutable tool receipts; projection never mutates evidence."""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.context_builder import estimate_tokens
from app.agents.tool_context import format_tool_observation

READ_RESULT_TOOL = "read_tool_result"
INLINE_TOKEN_BUDGET = 6000
PAGE_TOKEN_BUDGET = 5000
SOURCE_FIELDS = ("trade_date", "data_as_of", "as_of", "start_date", "end_date", "symbol",
                 "source", "sources", "data_missing", "source_errors", "truncated",
                 "source_truncated", "query_scope", "calendar_coverage_status")


class ResultPageError(ValueError):
    """The requested view cannot be served without guessing or losing a field."""


class ReadResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    result_id: str = Field(min_length=1, max_length=200)
    expected_hash: str | None = Field(default=None, min_length=64, max_length=64)
    section: Literal["rows", "metadata"] = "rows"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)
    fields: list[str] | None = Field(default=None, max_length=80)

    @field_validator("fields")
    @classmethod
    def field_names(cls, values):
        if values is not None and (not values or any(not value or len(value) > 120 for value in values)):
            raise ValueError("Use a nonempty list of field names or omit fields")
        return list(dict.fromkeys(values)) if values else values


READ_RESULT_DEFINITION = {"type": "function", "function": {
    "name": READ_RESULT_TOOL,
    "description": ("只读本轮已持久化的工具结果，不重新查询市场。大结果 manifest 给出 result_id 和 content_hash；"
                    "按 offset/limit 分页，fields 仅投影确实需要的字段，section=metadata 可读取来源/汇总/口径字段。"
                    "必须使用原结果的 content_hash，依据返回的 next_offset 继续读取；不能把一页当成完整名单。"),
    "parameters": ReadResultRequest.model_json_schema(),
}}

RESULT_READING_RULE = (
    "\n工具大结果可能返回 result_view.kind=manifest；这表示完整原始结果已持久化，但当前模型尚未读取股票行。"
    "可用 read_tool_result 按需读取已保存的行或元数据，不要重新查询市场来分页。传入 result_id 和 content_hash；"
    "每页保留原始顺序、总行数、next_offset及来源。用户要完整名单时必须读取覆盖全部所需行和字段，"
    "不能把预览、单页或未读部分说成完整名单；预算不够时明确哪些部分未完成。"
    "字段未投影、字段缺失和明确null是不同状态，均不能补造数值。"
)


def content_hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def attach_result_reference(value, result_id, run_id):
    """Decorate public traces only; the immutable receipt/hash stays unchanged."""
    return {**value, "immutable_result": {
        "result_id": result_id, "content_hash": content_hash(value),
        "url": f"/api/agents/chat/runs/{quote(run_id, safe='')}/results/{quote(result_id, safe='')}",
    }}


def _columns(rows):
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ResultPageError("This receipt has no tabular rows; request metadata instead")
    return list(dict.fromkeys(key for row in rows for key in row))


def model_observation(value, result_id, *, max_tokens=INLINE_TOKEN_BUDGET):
    """Keep small evidence lossless; replace oversized views with an explicit manifest."""
    compact = format_tool_observation(value)
    if estimate_tokens(compact) <= max_tokens:
        return compact
    rows = value.get("rows", [])
    columns = _columns(rows)
    manifest = {"name": value.get("name"), "status": value.get("status", "error"),
                "summary": value.get("summary", ""), "row_count": len(rows),
                "result_view": {"kind": "manifest", "result_id": result_id,
                                "content_hash": content_hash(value), "rows_returned": 0,
                                "total_rows": len(rows), "fields_available": columns,
                                "metadata_fields_available": [key for key in value if key != "rows"],
                                "reader": READ_RESULT_TOOL}}
    # Whole metadata fields only. Omitted fields remain independently readable;
    # even a huge source string is never silently cut into a misleading value.
    for key in (*SOURCE_FIELDS, "field_definitions"):
        if key in value:
            candidate = {**manifest, key: deepcopy(value[key])}
            if estimate_tokens(candidate) <= max_tokens:
                manifest = candidate
    if estimate_tokens(manifest) > max_tokens:
        # A huge summary is optional, unlike result identity and field names.
        manifest.pop("summary", None)
    if estimate_tokens(manifest) > max_tokens:
        raise ResultPageError("Receipt manifest exceeds context budget; narrow the original query")
    return manifest


def read_result_page(value, request: ReadResultRequest, *, max_tokens=None):
    """Project exact receipt fields and whole rows within a bounded model budget."""
    digest = content_hash(value)
    if request.expected_hash is not None and request.expected_hash != digest:
        raise ResultPageError("Result hash does not match this immutable receipt")
    result = {"name": READ_RESULT_TOOL, "status": value.get("status", "error"),
              "source_tool": value.get("name"), "result_id": request.result_id, "content_hash": digest,
              "section": request.section, "summary": "读取本轮已持久化工具结果；没有重新查询市场。"}
    if request.section == "metadata":
        available = [key for key in value if key != "rows"]
        fields = request.fields if request.fields is not None else available
        if not set(fields).issubset(available):
            raise ResultPageError("Requested metadata field is absent from this receipt")
        result.update(metadata={key: deepcopy(value[key]) for key in fields}, fields_available=available)
        if max_tokens is not None and estimate_tokens(result) > max_tokens:
            raise ResultPageError("Metadata view exceeds context budget; select fewer metadata fields")
        return result
    rows = value.get("rows")
    columns = _columns(rows)
    if request.offset > len(rows):
        raise ResultPageError("Row offset is outside the saved result")
    fields = request.fields if request.fields is not None else columns
    if not set(fields).issubset(columns):
        raise ResultPageError("Requested row field is absent from this receipt")
    definitions = deepcopy(value.get("field_definitions", {}))
    if request.fields is not None and isinstance(definitions.get("rows"), dict):
        definitions["rows"] = {key: item for key, item in definitions["rows"].items() if key in fields}
    result.update({key: deepcopy(value[key]) for key in SOURCE_FIELDS if key in value})
    result.update(rows=[], row_count=0, offset=request.offset, total_rows=len(rows),
                  fields_available=columns, selected_fields=fields, field_definitions=definitions,
                  next_offset=None, has_more=False)
    for row in rows[request.offset:request.offset + request.limit]:
        projected = {key: deepcopy(row[key]) for key in fields if key in row}
        candidate = {**result, "rows": [*result["rows"], projected], "row_count": result["row_count"] + 1}
        end = request.offset + candidate["row_count"]
        candidate.update(next_offset=end if end < len(rows) else None, has_more=end < len(rows))
        if max_tokens is not None and estimate_tokens(format_tool_observation(candidate)) > max_tokens:
            if not result["rows"]:
                raise ResultPageError("A whole row and its source metadata exceed context budget; select fewer fields")
            break
        result = candidate
    if max_tokens is not None and estimate_tokens(format_tool_observation(result)) > max_tokens:
        raise ResultPageError("Page metadata exceeds context budget; narrow the original query")
    return result
