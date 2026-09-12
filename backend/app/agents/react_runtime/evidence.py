"""Full evidence stays outside model messages; bounded views preserve provenance."""

from copy import deepcopy
from uuid import uuid4

from fastapi.encoders import jsonable_encoder

COLLECTIONS = ("events", "top_candidates", "items", "stocks", "top_sectors", "bars", "candidates")


def compact(value, limit=8):
    if isinstance(value, list):
        rows = [compact(item, limit) for item in value[:limit]]
        if len(value) > limit:
            rows.append({"truncated_items": len(value) - limit})
        return rows
    if isinstance(value, dict):
        return {key: compact(item, limit) for key, item in value.items()}
    if isinstance(value, str) and len(value) > 1200:
        return value[:1200] + " [truncated]"
    return value


def payload_of(result):
    raw = jsonable_encoder(result.output)
    trace = jsonable_encoder(result.trace_output)
    if result.name == "limit_up_events" and isinstance(raw, list):
        return {**trace, "events": raw}
    if result.name == "first_board_ratings" and isinstance(raw, dict):
        return {**raw, "top_candidates": [
            {**item.get("facts", {}), **{k: v for k, v in item.items() if k != "facts"}}
            for item in raw.get("candidates", [])
        ]}
    return raw if isinstance(raw, (dict, list)) else trace


def rows_of(payload):
    if isinstance(payload, list):
        return payload
    for key in COLLECTIONS:
        if isinstance(payload.get(key), list):
            return payload[key]
    return [payload] if payload else []


class EvidenceStore:
    """Request-owned store; only explicitly loaded owner-scoped history can enter it."""

    def __init__(self):
        self.records = {}

    def add(self, *, tool, payload, state, arguments, sources=None):
        key = "ev_" + uuid4().hex[:16]
        self.records[key] = {
            "evidence_id": key, "tool": tool, "payload": payload, "result_state": state,
            "arguments": arguments, "sources": sources or (
                payload.get("sources") or ([payload["source"]] if payload.get("source") else [])
                if isinstance(payload, dict) else []
            ), "rows": rows_of(payload),
        }
        return key

    def get(self, key):
        if key not in self.records:
            raise ValueError("Unknown evidence_id in this authorized conversation")
        return self.records[key]

    def view(self, key, offset=0, limit=8):
        record = self.get(key)
        rows = record["rows"]
        metadata = record["payload"] if isinstance(record["payload"], dict) else {}
        metadata = {k: v for k, v in metadata.items() if k not in COLLECTIONS}
        return {
            "evidence_id": key, "tool": record["tool"], "result_state": record["result_state"],
            "arguments": record["arguments"], "metadata": compact(metadata, 4),
            # Do not compact the outer page again: that used to return five rows
            # even when read_evidence explicitly requested thirty.
            "rows": [compact(row, 20) for row in rows[offset:offset + limit]], "row_count": len(rows),
            "offset": offset, "truncated": offset + limit < len(rows), "sources": record["sources"],
        }

    def compute(self, spec):
        source = self.get(spec.evidence_id)
        if source["result_state"] not in {"ok", "empty", "partial"}:
            raise ValueError("Evidence is not usable")
        rows = deepcopy(source["rows"])
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("This evidence is not a row collection")
        inputs = [spec.evidence_id]
        if spec.operation in {"intersection", "difference", "union"}:
            other = self.get(spec.other_id)
            if other["result_state"] not in {"ok", "empty"} or source["result_state"] == "partial":
                raise ValueError("Set comparison requires complete source sets")
            if any(spec.key not in row for row in rows + other["rows"]):
                raise ValueError("Set key is missing")
            keys = {row[spec.key] for row in other["rows"]}
            if spec.operation == "intersection":
                rows = [row for row in rows if row[spec.key] in keys]
            elif spec.operation == "difference":
                rows = [row for row in rows if row[spec.key] not in keys]
            else:
                rows = list({row[spec.key]: row for row in rows + other["rows"]}.values())
            inputs.append(spec.other_id)
        for predicate in spec.filters:
            if any(predicate.field not in row or row[predicate.field] is None for row in rows):
                raise ValueError(f"Filter field missing: {predicate.field}")
            def matches(row):
                value, target = row[predicate.field], predicate.value
                if predicate.operator == "eq": return value == target
                if predicate.operator == "ne": return value != target
                if predicate.operator == "in": return value in target
                if predicate.operator == "gt": return value > target
                if predicate.operator == "ge": return value >= target
                if predicate.operator == "lt": return value < target
                return value <= target
            rows = [row for row in rows if matches(row)]
        if spec.operation == "aggregate":
            groups = {}
            for row in rows:
                if spec.group_by and spec.group_by not in row:
                    raise ValueError("Group field missing")
                groups.setdefault(row.get(spec.group_by, "all"), []).append(row)
            rows = []
            for group, members in groups.items():
                numbers = [row.get(spec.metric) for row in members]
                if spec.aggregate != "count" and any(not isinstance(n, (int, float)) for n in numbers):
                    raise ValueError("Aggregate requires available numeric values")
                value = len(members) if spec.aggregate == "count" else {
                    "sum": lambda: sum(numbers), "mean": lambda: sum(numbers) / len(numbers),
                    "min": lambda: min(numbers), "max": lambda: max(numbers),
                }[spec.aggregate]()
                rows.append({"group": group, "metric": spec.metric or "count", "value": value})
        if spec.sort_by:
            if any(row.get(spec.sort_by) is None for row in rows):
                raise ValueError(f"Sort field missing: {spec.sort_by}")
            rows.sort(key=lambda row: row[spec.sort_by], reverse=spec.descending)
        total = len(rows)
        selected = rows[spec.offset:spec.offset + spec.limit]
        return self.add(
            tool="compute_result", state="empty" if not selected else source["result_state"],
            payload={"items": selected, "matched_count": total, "returned_count": len(selected),
                     "operation": spec.model_dump(), "source_evidence_ids": inputs},
            arguments=spec.model_dump(), sources=inputs,
        )
