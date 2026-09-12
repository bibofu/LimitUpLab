"""Full evidence stays outside model messages; bounded views preserve provenance."""

from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from uuid import uuid4

from fastapi.encoders import jsonable_encoder

COLLECTIONS = ("events", "top_candidates", "items", "stocks", "top_sectors", "bars", "candidates")
EVIDENCE_VERSION = "react-evidence-v2"


def keyed_rows(rows, key):
    """Set operations use distinct, non-null scalar identities in source order."""
    indexed = {}
    for row in rows:
        value = row.get(key) if isinstance(row, dict) else None
        if type(value) not in (str, int, float) or value == "" or (isinstance(value, float) and not isfinite(value)):
            raise ValueError(f"Set key missing or invalid: {key}")
        indexed.setdefault(value, row)
    return indexed


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
    if result.name == "stock_kline" and isinstance(raw, dict):
        return {**raw, "metric_definitions": {
            "return_Nd_pct": "(close[t] / close[t-N] - 1)*100; N trading intervals, N+1 closes. Displayed N bars have N-1 intervals. Not an adjustment explanation.",
            "trend": "Moving-average alignment label; not consecutive daily increases.",
            "max_drawdown_pct": "Peak-to-subsequent-trough in displayed bars, percent.",
        }}
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
        rows = rows_of(payload)
        metadata = payload if isinstance(payload, dict) else {}
        # Source truncation is different from the small model preview page.
        truncated = bool(metadata.get("source_truncated") or metadata.get("truncated")) or (
            tool != "compute_result" and isinstance(metadata.get("matched_count"), int)
            and metadata["matched_count"] > len(rows)
        )
        source_missing = metadata.get("data_missing") or []
        if state in {"ok", "empty"} and (source_missing or truncated or metadata.get("data_fresh") is False):
            state = "partial"
        self.records[key] = {
            "evidence_id": key, "tool": tool, "payload": payload, "result_state": state,
            "schema_version": EVIDENCE_VERSION, "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_truncated": truncated, "data_missing": source_missing, "historical_reference": False,
            "arguments": arguments, "sources": sources or (
                payload.get("sources") or ([payload["source"]] if payload.get("source") else [])
                if isinstance(payload, dict) else []
            ), "rows": rows,
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
            "source_truncated": record.get("source_truncated", False),
            "data_missing": record.get("data_missing", []),
            "historical_reference": record.get("historical_reference", False),
            "retrieved_at": record.get("retrieved_at"),
        }

    def compute(self, spec):
        source = self.get(spec.evidence_id)
        if source["result_state"] not in {"ok", "empty", "partial"}:
            raise ValueError("Evidence is not usable")
        rows = deepcopy(source["rows"])
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("This evidence is not a row collection")
        inputs = [spec.evidence_id]
        parents = [source]
        if spec.operation in {"intersection", "difference", "union"}:
            other = self.get(spec.other_id)
            if (other["result_state"] not in {"ok", "empty"} or source["result_state"] == "partial"
                    or source.get("source_truncated") or other.get("source_truncated")):
                raise ValueError("Set comparison requires complete source sets")
            left = keyed_rows(rows, spec.key)
            right = keyed_rows(other["rows"], spec.key)
            if spec.operation == "intersection":
                rows = [row for key, row in left.items() if key in right]
            elif spec.operation == "difference":
                rows = [row for key, row in left.items() if key not in right]
            else:
                # Keep the left row consistently; never silently replace its facts.
                rows = [left[key] for key in left] + [right[key] for key in right if key not in left]
            inputs.append(spec.other_id)
            parents.append(other)
        if spec.operation == "distinct":
            rows = list(keyed_rows(rows, spec.key).values())
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
            # An ungrouped empty count is zero, not an absent aggregate row.
            groups = {"all": []} if not rows and not spec.group_by and spec.aggregate == "count" else {}
            for row in rows:
                if spec.group_by and spec.group_by not in row:
                    raise ValueError("Group field missing")
                groups.setdefault(row.get(spec.group_by, "all"), []).append(row)
            rows = []
            for group, members in groups.items():
                numbers = [row.get(spec.metric) for row in members]
                if spec.aggregate != "count" and any(type(n) not in (int, float) or not isfinite(n) for n in numbers):
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
        derived_state = "partial" if any(p.get("source_truncated") or p["result_state"] == "partial" for p in parents) else "ok" if selected else "empty"
        missing = []
        for parent in parents:
            for item in parent.get("data_missing", []):
                if item not in missing:
                    missing.append(deepcopy(item))
        key = self.add(
            tool="compute_result", state=derived_state,
            payload={"items": selected, "matched_count": total, "returned_count": len(selected),
                     "operation": spec.model_dump(), "source_evidence_ids": inputs,
                     "data_missing": missing,
                     "source_truncated": any(p.get("source_truncated") for p in parents)},
            arguments=spec.model_dump(), sources=inputs,
        )
        # Computing an old set does not turn it into freshly fetched evidence.
        self.records[key]["historical_reference"] = any(p.get("historical_reference") for p in parents)
        return key
