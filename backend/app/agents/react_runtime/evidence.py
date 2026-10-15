"""Full evidence stays outside model messages; bounded views preserve provenance."""

from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from app.agents.react_runtime.selection_provenance import row_selection_matches

from app.agents.tool_schemas_market import MARKET_TOOL_SCHEMAS
from app.agents.tool_schemas_news import NEWS_TOOL_SCHEMAS
from app.agents.tool_schemas_research import RESEARCH_TOOL_SCHEMAS
from app.agents.tool_schemas_review import REVIEW_TOOL_SCHEMAS

TOOL_COLLECTIONS = {
    contract.name: contract.collection
    for contract in (*MARKET_TOOL_SCHEMAS, *NEWS_TOOL_SCHEMAS,
                     *RESEARCH_TOOL_SCHEMAS, *REVIEW_TOOL_SCHEMAS)
    if contract.collection
} | {"compute_result": "items"}
# Legacy/unregistered evidence keeps its previous generic collection behavior.
# Declared tools always select their own contract, regardless of payload order.
COLLECTIONS = tuple(dict.fromkeys((
    "events", "top_candidates", "items", "stocks", "top_sectors", "bars", "candidates",
    *TOOL_COLLECTIONS.values(),
)))
EVIDENCE_VERSION = "react-evidence-v4"
CURRENT_SCOPE = "current_run"
HISTORY_SCOPE = "conversation_history"


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
    collection = TOOL_COLLECTIONS.get(result.name)
    if isinstance(raw, list) and collection:
        raw = {**trace, collection: raw}
    outcome = result.trace().result
    if outcome is not None and outcome.status == "error":
        return raw if isinstance(raw, (dict, list)) else trace
    if result.name == "first_board_ratings" and isinstance(raw, dict):
        candidates = raw.get("candidates")
        if not isinstance(candidates, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("facts", {}), dict)
            for item in candidates
        ):
            raise ValueError("Invalid first_board_ratings collection: candidates must be object rows")
        raw = {**raw, "top_candidates": [
            {**item.get("facts", {}), **{k: v for k, v in item.items() if k != "facts"}}
            for item in candidates
        ]}
    if result.name == "stock_kline" and isinstance(raw, dict):
        raw = {**raw, "metric_definitions": {
            "return_Nd_pct": "(close[t] / close[t-N] - 1)*100; N trading intervals, N+1 closes. Displayed N bars have N-1 intervals. Not an adjustment explanation.",
            "trend": "Moving-average alignment label; not consecutive daily increases.",
            "max_drawdown_pct": "Peak-to-subsequent-trough in displayed bars, percent.",
        }}
    payload = raw if isinstance(raw, (dict, list)) else trace
    # Validate inside ToolGateway.execute so malformed output follows the normal
    # execution-failure path, instead of escaping while evidence is being stored.
    if collection:
        rows_of(payload, tool=result.name)
    return payload


def rows_of(payload, *, tool=None):
    collection = TOOL_COLLECTIONS.get(tool)
    if isinstance(payload, list):
        rows = payload
    elif collection:
        if not isinstance(payload, dict) or not isinstance(payload.get(collection), list):
            raise ValueError(f"Invalid {tool} collection: {collection} must be an array")
        rows = payload[collection]
    else:
        for key in COLLECTIONS:
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload] if payload else []
    if collection and any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Invalid {tool} collection: {collection} must contain object rows")
    return rows


class EvidenceStore:
    """Request-owned store; only explicitly loaded owner-scoped history can enter it."""

    def __init__(self):
        self.records = {}
        # Runtime-owned proofs, never accepted from a tool payload or restored history.
        self._ranked_prefixes = {}
        self._rank_scopes = {}
        self._selections = {}

    def checkpoint(self):
        """Persist runtime-owned proofs separately from untrusted tool payloads."""
        return deepcopy({"records": self.records, "ranked_prefixes": self._ranked_prefixes,
                         "rank_scopes": self._rank_scopes, "selections": self._selections})

    def restore_checkpoint(self, snapshot):
        # Legacy checkpoints contained records only. Never recover their proof
        # claims from tool metadata or from historical conversation evidence.
        if "records" not in snapshot:
            self.records = deepcopy(snapshot)
            self._ranked_prefixes, self._rank_scopes, self._selections = {}, {}, {}
            return
        self.records = deepcopy(snapshot["records"])
        for attribute, field in (("_ranked_prefixes", "ranked_prefixes"),
                                 ("_rank_scopes", "rank_scopes"), ("_selections", "selections")):
            setattr(self, attribute, {key: deepcopy(value) for key, value in snapshot.get(field, {}).items()
                                     if key in self.records and self.scope_of(self.records[key]) == CURRENT_SCOPE})

    def row_selection_matches(self, key, *, offset, limit, sort_by=None, descending=None):
        return row_selection_matches(self, key, offset=offset, limit=limit,
                                     sort_by=sort_by, descending=descending)

    @staticmethod
    def scope_of(record):
        """Read old persisted records conservatively while keeping scope explicit."""
        if record.get("historical_reference"):
            return HISTORY_SCOPE
        return record.get("evidence_scope", CURRENT_SCOPE)

    def restore_history(self, key, record):
        """Import an authenticated prior-turn record into the context-only scope."""
        restored = deepcopy(record)
        restored["evidence_id"] = key
        restored["evidence_scope"] = HISTORY_SCOPE
        restored["historical_reference"] = True
        self.records[key] = restored

    def current_records(self):
        return [record for record in self.records.values() if self.scope_of(record) == CURRENT_SCOPE]

    def add(self, *, tool, payload, state, arguments, sources=None):
        key = "ev_" + uuid4().hex[:16]
        rows = [] if state == "error" else rows_of(payload, tool=tool)
        metadata = payload if isinstance(payload, dict) else {}
        collection = TOOL_COLLECTIONS.get(tool)
        has_row_collection = (isinstance(metadata.get(collection), list) if collection
                              else any(isinstance(metadata.get(name), list) for name in COLLECTIONS))
        # Source truncation is different from the small model preview page.
        truncated = bool(metadata.get("source_truncated") or metadata.get("truncated")) or (
            tool != "compute_result" and has_row_collection
            and metadata.get("result_mode") != "count"
            and isinstance(metadata.get("matched_count"), int)
            and metadata["matched_count"] > len(rows)
        )
        source_missing = metadata.get("data_missing") or []
        # This production tool sorts the entire filtered collection before taking
        # its bounded prefix. Matching length alone is NOT proof of a ranking.
        sort_by = metadata.get("sort_by")
        direction = metadata.get("sort_order")
        if (tool == "limit_up_events" and state == "ok" and truncated and rows
                and not source_missing and metadata.get("data_fresh") is not False
                and not metadata.get("source_errors")
                and not metadata.get("source_truncated") and not metadata.get("truncated")
                and not metadata.get("group_by") and metadata.get("recent_trade_days") == 1
                and isinstance(metadata.get("trade_date"), str) and metadata["trade_date"]
                and sort_by in {"amount", "board_height", "turnover_rate", "break_count"}
                and direction in {"asc", "desc"}
                and arguments.get("sort_by") == sort_by and arguments.get("sort_order") == direction
                and metadata.get("returned_count") == len(rows)
                and type(metadata.get("matched_count")) is int
                and metadata["matched_count"] > len(rows)
                and all(isinstance(r, dict) and type(r.get(sort_by)) in {int, float}
                        and isfinite(r[sort_by]) and isinstance(r.get("symbol"), str)
                        and r["symbol"] and r.get("trade_date") == metadata.get("trade_date") for r in rows)
                and len({r["symbol"] for r in rows}) == len(rows)):
            ordered = sorted(rows, key=lambda r: r["symbol"])
            ordered.sort(key=lambda r: r[sort_by], reverse=direction == "desc")
            if ordered == rows:
                self._ranked_prefixes[key] = {"sort_by": sort_by, "descending": direction == "desc"}
        if state in {"ok", "empty"} and (source_missing or truncated or metadata.get("data_fresh") is False):
            state = "partial"
        self.records[key] = {
            "evidence_id": key, "tool": tool, "payload": payload, "result_state": state,
            "schema_version": EVIDENCE_VERSION, "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_truncated": truncated, "data_missing": source_missing,
            "evidence_scope": CURRENT_SCOPE, "historical_reference": False,
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

    def complete_rank_scope(self, key):
        """Only a current, explicitly selected ranked slice can waive list truncation."""
        record = self.get(key)
        if (self.scope_of(record) != CURRENT_SCOPE or record.get("data_missing")
                or record["result_state"] not in {"ok", "partial", "empty"}):
            return None
        return deepcopy(self._rank_scopes.get(key))

    def view(self, key, offset=0, limit=8):
        record = self.get(key)
        rows = record["rows"]
        metadata = record["payload"] if isinstance(record["payload"], dict) else {}
        metadata = {k: v for k, v in metadata.items() if k not in COLLECTIONS}
        if record["tool"] == "first_board_ratings":
            metadata = {**metadata, "returned_candidate_count": len(rows), "count_scope": {
                "universe_count": "筛选前涨停事件总体数量，不是入池候选数",
                "returned_candidate_count": "本次返回候选数量；指定symbols时仅代表定向返回，不代表全池",
                "symbols_filtered": bool(record["arguments"].get("symbols")),
            }}
        view = {
            "evidence_id": key, "tool": record["tool"], "result_state": record["result_state"],
            "arguments": record["arguments"], "metadata": compact(metadata, 4),
            # Do not compact the outer page again: that used to return five rows
            # even when read_evidence explicitly requested thirty.
            "rows": [compact(row, 20) for row in rows[offset:offset + limit]], "row_count": len(rows),
            "offset": offset, "truncated": offset + limit < len(rows), "sources": record["sources"],
            "display_hint": "rows仅为预览；finish.table可直接渲染该evidence_id的全部row_count行，无需逐页读取。完整性以source_truncated为准。",
            "source_truncated": record.get("source_truncated", False),
            "data_missing": record.get("data_missing", []),
            "evidence_scope": self.scope_of(record),
            "historical_reference": record.get("historical_reference", False),
            "retrieved_at": record.get("retrieved_at"),
        }
        if record["result_state"] == "error":
            view["display_hint"] = (
                "这是可引用的查询失败记录，不是行情结果；不能交表、计算、证明零或无记录，也不展示内部证据ID。"
                "当前查询已无法交付，下一步应直接finish(status=partial)，引用本ID并说明用户所需结果未取得。"
                "无需穷尽工具、改日期/limit或查询别的统计来证明失败。只有用户另有独立交付，"
                "或已明确掌握可补原缺口的独立备用来源时才继续；不要把不同工具名猜成备用来源。"
            )
        if self.complete_rank_scope(key):
            view["complete_rank_scope"] = self.complete_rank_scope(key)
            view["display_hint"] += " complete_rank_scope仅证明显式排名范围及其后续筛选完整，不代表全市场全集完整。"
        return view

    def compute(self, spec):
        source = self.get(spec.evidence_id)
        if source["result_state"] not in {"ok", "empty", "partial"}:
            raise ValueError("Evidence is not usable")
        rows = deepcopy(source["rows"])
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("This evidence is not a row collection")
        inputs = [spec.evidence_id]
        parents = [source]
        rank_scope = None
        if spec.operation == "select" and self.scope_of(source) == CURRENT_SCOPE:
            rank_scope = self.complete_rank_scope(spec.evidence_id)
            prefix = self._ranked_prefixes.get(spec.evidence_id)
            if (rank_scope is None and prefix and not source.get("data_missing")
                    and not spec.filters and spec.sort_by == prefix["sort_by"]
                    and spec.descending == prefix["descending"]
                    and spec.offset + spec.limit <= len(rows)):
                rank_scope = {"source_evidence_id": spec.evidence_id, **prefix,
                              "offset": spec.offset, "limit": spec.limit}
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
        historical = any(self.scope_of(parent) != CURRENT_SCOPE for parent in parents)
        self.records[key]["evidence_scope"] = HISTORY_SCOPE if historical else CURRENT_SCOPE
        self.records[key]["historical_reference"] = historical
        if rank_scope and not historical:
            self._rank_scopes[key] = rank_scope
            self.records[key]["complete_rank_scope"] = deepcopy(rank_scope)
        if not historical:
            self._selections[key] = {"source": spec.evidence_id, "operation": spec.operation,
                                     "offset": spec.offset, "limit": spec.limit, "total": total,
                                     "sort_by": spec.sort_by, "descending": spec.descending,
                                     "filtered": bool(spec.filters)}
        return key
