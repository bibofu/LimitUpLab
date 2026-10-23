"""Bounded two-period metric projection; every value retains its source recipe."""

from math import isfinite

from app.agents.react_runtime.display_fields import FIELD_UNITS, evidence_dates, evidence_roots


def _unit(spec):
    if spec.operation == "count":
        return "只" if spec.field == "symbol" else "项" if spec.field else "条"
    if spec.operation == "scalar" and spec.field in {"matched_count", "returned_count", "total_count"}:
        return "条"
    if spec.field == "unique_stock_count":
        return "只"
    return FIELD_UNITS.get(spec.field)


def _scope(record, store):
    if record["tool"] in {"compute_result", "evidence_view", "compare_metrics"}:
        return {"tool": record["tool"], "recipe": {key: value for key, value in record.get("arguments", {}).items()
                if key not in {"evidence_id", "other_id"}},
                "parents": [_scope(store.get(key), store) for key in record.get("sources", []) if key in store.records]}
    scopes = []
    for root in evidence_roots(record, store):
        args = {key: value for key, value in root.get("arguments", {}).items() if key != "trade_date"}
        payload = root.get("payload", {})
        dimensions = {key: payload[key] for key in (
            "market", "event_status", "event_type", "query", "group_by", "recent_trade_days", "result_mode"
        ) if isinstance(payload, dict) and key in payload}
        scopes.append({"tool": root["tool"], "arguments": args, "dimensions": dimensions})
    return scopes


def _filtered(rows, filters):
    for predicate in filters:
        if any(not isinstance(row, dict) or row.get(predicate.field) is None for row in rows):
            raise ValueError(f"Metric filter field missing: {predicate.field}")
        def matches(row):
            value, target = row[predicate.field], predicate.value
            if predicate.operator == "eq": return value == target
            if predicate.operator == "ne": return value != target
            if predicate.operator == "in":
                if not isinstance(target, list):
                    raise ValueError("Membership filter requires an array")
                return value in target
            if predicate.operator == "gt": return value > target
            if predicate.operator == "ge": return value >= target
            if predicate.operator == "lt": return value < target
            return value <= target
        rows = [row for row in rows if matches(row)]
    return rows


def _value(spec, record):
    if (record["result_state"] not in {"ok", "empty"} or record.get("source_truncated")
            or record.get("data_missing")):
        return None, "来源缺失、失败或不完整，不能据此计算完整指标"
    if spec.operation == "scalar":
        payload = record.get("payload", {})
        value = payload.get(spec.field) if isinstance(payload, dict) else None
        if type(value) not in (int, float) or not isfinite(value):
            return None, "来源未提供有效数值元数据"
        return value, None
    if isinstance(record.get("payload"), dict) and record["payload"].get("result_mode") == "count":
        raise ValueError("Count-mode evidence requires scalar matched_count, not row aggregation")
    rows = record["rows"]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Metric aggregation requires object rows")
    rows = _filtered(rows, spec.filters)
    if spec.operation == "count":
        if not spec.field:
            return len(rows), None
        identities = [row.get(spec.field) for row in rows]
        if any(type(value) not in (str, int) or value == "" for value in identities):
            return None, "计数所需的唯一标识缺失"
        return len(set(identities)), None
    numbers = [row.get(spec.field) for row in rows]
    if not numbers or any(type(value) not in (int, float) or not isfinite(value) for value in numbers):
        return None, "聚合所需数值缺失，不能将空集合视为零"
    return {
        "sum": lambda: sum(numbers), "mean": lambda: sum(numbers) / len(numbers),
        "min": lambda: min(numbers), "max": lambda: max(numbers),
    }[spec.operation](), None


def compare_metrics(store, spec):
    """Create one auditable metric table; labels never supply numeric facts."""
    dates = [day.isoformat() for day in spec.dates]
    rows, lineage, sources, missing = [], [], [], []
    for metric in spec.metrics:
        before, after = metric.before, metric.after
        if before.model_dump(exclude={"evidence_id"}) != after.model_dump(exclude={"evidence_id"}):
            raise ValueError("A metric comparison must use the same operation, field and filters in both periods")
        records = [store.get(value.evidence_id) for value in (before, after)]
        if any(record["tool"] in {"compute_result", "compare_metrics"} for record in records):
            raise ValueError("Metrics require original business evidence; declare filters in the metric recipe, not sliced compute evidence")
        if any(record["tool"] == "evidence_view" and record.get("arguments", {}).get("view") != "sector_summary"
               for record in records):
            raise ValueError("This evidence view has no complete metric aggregation contract")
        if any(store.scope_of(record) != "current_run" for record in records):
            raise ValueError("Metric comparison requires current-run evidence")
        for record, expected in zip(records, dates):
            actual = evidence_dates(record, store)
            if actual != [expected]:
                raise ValueError(f"Metric evidence date must be exactly {expected}; got {actual}")
        if _scope(records[0], store) != _scope(records[1], store):
            raise ValueError("Metric comparison source or query scope differs between periods")
        values, gaps = zip(*(_value(value, record) for value, record in zip((before, after), records)))
        if any(value is not None and not isfinite(value) for value in values):
            raise ValueError("Metric result is not finite")
        change = values[1] - values[0] if all(value is not None for value in values) else None
        if change is not None and not isfinite(change):
            raise ValueError("Metric change is not finite")
        gap = "；".join(f"{day}：{error}" for day, error in zip(dates, gaps) if error)
        if gap:
            missing.append(f"{metric.label}：{gap}")
        rows.append({"metric": metric.label, "before": values[0], "after": values[1],
                     "change": change, "unit": _unit(before),
                     "change_unit": "个百分点" if _unit(before) == "%" else _unit(before), "missing": gap or None})
        references = [value.evidence_id for value in (before, after)]
        sources.extend(key for key in references if key not in sources)
        lineage.append({"metric": metric.label, "before": before.model_dump(mode="json"),
                        "after": after.model_dump(mode="json"), "change": "after - before"})
    return store.add(
        tool="compare_metrics", state="partial" if missing else "ok", sources=sources,
        arguments=spec.model_dump(mode="json"), payload={
            "items": rows, "matched_count": len(rows), "returned_count": len(rows),
            "table_kind": "metric_comparison", "comparison": {"dates": dates,
                "metrics": [item.label for item in spec.metrics]},
            "metric_lineage": lineage, "data_missing": missing,
            "source_truncated": False,
        },
    )
