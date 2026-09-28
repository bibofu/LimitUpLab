"""Independent checks over delivered answers and recorded evidence; no model calls."""

from collections import Counter
from datetime import date, datetime
from html import unescape
from math import isfinite

from app.models import AgentChatResponse
from evals.golden.contracts import Check, Expectation

COLLECTIONS = ("events", "top_candidates", "items", "stocks", "top_sectors", "bars", "candidates")
CONTROLS = {"finish", "update_task", "read_evidence", "compute_result"}
LABELS = {
    "symbol": {"代码", "股票代码", "证券代码", "stock code", "code", "ticker"},
    "name": {"名称", "股票名称", "证券名称", "股票", "stock name"},
    "trade_date": {"日期", "交易日期", "交易日", "date", "trade date"},
    "pct_change": {"涨跌幅", "涨跌幅(%)", "涨跌幅（%）", "涨跌幅%", "change (%)"},
    "change_pct": {"涨跌幅", "涨跌幅(%)", "涨跌幅（%）", "涨跌幅%", "change (%)"},
    "close": {"收盘价", "收盘", "收盘价(元)", "收盘价（元）", "close price"},
    "amount": {"成交额", "成交额(元)", "成交额（元）", "turnover"},
    "board_height": {"连板数", "连板高度", "板数", "连板", "板高"},
    "turnover_rate": {"换手率", "换手率(%)", "换手率（%）"},
    "break_count": {"炸板次数", "开板次数"},
    "rank": {"排名", "名次"}, "score": {"评分", "分数", "得分"},
    "sector": {"板块", "行业", "所属板块"}, "value": {"数值", "值", "数量", "统计值"},
    "group": {"分组", "组别"}, "metric": {"指标", "统计指标"},
    "range_days": {"区间天数", "统计区间", "统计天数"},
    "net_buy_amount": {"净买额", "净买额(元)", "净买额（元）"},
    "buy_amount": {"买入额", "买入额(元)", "买入额（元）"},
    "sell_amount": {"卖出额", "卖出额(元)", "卖出额（元）"},
}


def _cells(line):
    line = line.strip()
    if not (line.startswith("|") and line.endswith("|")):
        return None
    return [unescape(value.strip()) for value in line[1:-1].split("|")]


def _table(answer):
    """Parse only the server's pipe-table grammar, without searching prose numbers."""
    lines, tables, consumed = answer.splitlines(), [], set()
    index = 0
    while index + 1 < len(lines):
        header, divider = _cells(lines[index]), _cells(lines[index + 1])
        if not header or not divider or not all(
            len(part.strip(":")) >= 3 and set(part.strip(":")) == {"-"} for part in divider
        ):
            index += 1
            continue
        start, rows = index, []
        index += 2
        while index < len(lines) and _cells(lines[index]) is not None:
            rows.append(_cells(lines[index]))
            index += 1
        tables.append((header, rows, len(header) == len(divider) and all(len(r) == len(header) for r in rows)))
        consumed.update(range(start, index))
    outside = "\n".join(line for i, line in enumerate(lines) if i not in consumed).strip()
    return tables, outside


def _scalar(value):
    if value is None:
        return "缺失"
    if type(value) not in (str, bool, int, float) or isinstance(value, float) and not isfinite(value):
        raise ValueError("Non-scalar or non-finite table cell")
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def _rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return next((payload[k] for k in COLLECTIONS if isinstance(payload.get(k), list)), [payload] if payload else [])
    raise ValueError("Unsupported evidence payload")


def _label(field, label):
    normalized = label.strip().casefold()
    if normalized in {field.casefold(), field.replace("_", " ").casefold()} | LABELS.get(field, set()):
        return True
    # Known labels belonging to another field are a mismatch; unknown labels need review.
    if any(normalized in values or normalized == key for key, values in LABELS.items()):
        return False
    return None


def _recompute(record, records):
    """Small independent reference implementation for recorded compute operations."""
    spec = record["arguments"]
    parents = [spec["evidence_id"]]
    rows = list(records[parents[0]]["rows"])
    operation, key = spec.get("operation", "select"), spec.get("key", "symbol")
    if operation in {"intersection", "difference", "union"}:
        parents.append(spec["other_id"])
        left, right = {}, {}
        for target, source in ((left, rows), (right, records[parents[-1]]["rows"])):
            for row in source:
                target.setdefault(row[key], row)
        rows = ([row for identity, row in left.items() if (identity in right) == (operation == "intersection")]
                if operation != "union" else list(left.values()) + [r for k, r in right.items() if k not in left])
    elif operation == "distinct":
        distinct = {}
        for row in rows:
            distinct.setdefault(row[key], row)
        rows = list(distinct.values())
    elif operation not in {"select", "aggregate"}:
        raise ValueError("Unsupported compute operation")
    for predicate in spec.get("filters", []):
        def matches(row):
            value, target = row[predicate["field"]], predicate["value"]
            operator = predicate["operator"]
            if operator == "eq": return value == target
            if operator == "ne": return value != target
            if operator == "in": return value in target
            if operator == "gt": return value > target
            if operator == "ge": return value >= target
            if operator == "lt": return value < target
            if operator == "le": return value <= target
            raise ValueError("Unsupported predicate")
        rows = [row for row in rows if matches(row)]
    if operation == "aggregate":
        grouping, metric, aggregate = spec.get("group_by"), spec.get("metric"), spec.get("aggregate", "count")
        groups = {"all": []} if not rows and not grouping and aggregate == "count" else {}
        for row in rows:
            groups.setdefault(row[grouping] if grouping else "all", []).append(row)
        rows = []
        for group, members in groups.items():
            numbers = [member[metric] for member in members] if aggregate != "count" else []
            value = len(members) if aggregate == "count" else {
                "sum": lambda: sum(numbers), "mean": lambda: sum(numbers) / len(numbers),
                "min": lambda: min(numbers), "max": lambda: max(numbers),
            }[aggregate]()
            rows.append({"group": group, "metric": metric or "count", "value": value})
    if spec.get("sort_by"):
        rows = sorted(rows, key=lambda row: row[spec["sort_by"]], reverse=spec.get("descending", True))
    start = spec.get("offset", 0)
    return rows[start:start + spec.get("limit", 20)], parents


def _dates(record, records, seen=None):
    seen = set() if seen is None else seen
    key = record["evidence_id"]
    if key in seen:
        raise ValueError("Cyclic evidence lineage")
    seen = seen | {key}
    if record.get("tool") == "compute_result":
        parents = record["payload"]["source_evidence_ids"]
        if not parents:
            raise ValueError("Missing compute parents")
        return set().union(*(_dates(records[p], records, seen) for p in parents))
    payload = record.get("payload")
    # Request arguments and retrieval time do not prove the date of returned facts.
    metadata = payload if isinstance(payload, dict) else {}
    multiple_days = type(metadata.get("recent_trade_days")) is int and metadata["recent_trade_days"] > 1
    if not multiple_days:
        authoritative = []
        for field in ("data_as_of", "as_of_date", "captured_at", "fetched_at", "end_date", "trade_date", "data_date", "date"):
            if metadata.get(field) is not None:
                value = str(metadata[field])
                authoritative.append((datetime.fromisoformat(value).date() if "T" in value or " " in value
                                      else date.fromisoformat(value)).isoformat())
        if authoritative:
            return set(authoritative)
    # K-line bars are a measurement window, not separate as-of claims.
    if record.get("tool") == "stock_kline":
        return set()
    values = [row for row in record.get("rows", []) if isinstance(row, dict)]
    dates = set()
    for value in values:
        for field in ("trade_date", "date", "as_of_date", "data_date", "end_date"):
            if value.get(field) is not None:
                dates.add(date.fromisoformat(str(value[field])).isoformat())
    return dates


def _stream(events):
    drafts, current, errors = [], "", []
    for item in events:
        event, payload = item.get("event"), item.get("payload", {})
        if event in {"answer_start", "answer_reset"}:
            if current:
                drafts.append(current)
            current = ""
        elif event == "answer_delta":
            delta, offset = payload.get("delta"), payload.get("offset")
            if not isinstance(delta, str) or type(offset) is not int or offset != len(current):
                errors.append("Invalid or discontinuous answer_delta")
                if isinstance(delta, str) and delta:
                    drafts.append(delta)
                continue
            current += delta
    if current:
        drafts.append(current)
    return drafts, errors


def visible_drafts(events: list[dict] | None) -> list[str]:
    """Retain every visible revision, including text withdrawn by answer_reset."""
    return _stream(events or [])[0]


def grade_turn(expect: Expectation, response: AgentChatResponse, events: list[dict] | None = None,
               judgements: list[dict] | None = None) -> list[Check]:
    checks = []
    def add(name, passed, detail="", expected=None, actual=None):
        checks.append(Check(name=name, passed=passed, detail=detail, expected=expected, actual=actual))
    add("status", response.task_status in expect.statuses, expected=expect.statuses, actual=response.task_status)
    executions = [t.output for t in response.tool_results if t.name == "react_execution"]
    execution = executions[-1] if executions else {}
    records = execution.get("evidence", {})
    if not isinstance(records, dict):
        records = {}
    calls = [call for trace in response.tool_results if trace.name == "react_decision"
             for call in trace.output.get("tool_calls", []) if isinstance(call, dict)]
    finishes = [call.get("args") for call in calls if call.get("name") == "finish" and isinstance(call.get("args"), dict)]
    final = finishes[-1] if finishes and response.task_status not in {"error", "cancelled"} else None
    business = [t for t in response.tool_results if not t.name.startswith("react_")]
    attempted = {call.get("name") for call in calls} | set(response.tool_calls) | {t.name for t in business}
    add("forbidden_tools", not (attempted & set(expect.forbidden_tools)), actual=sorted(attempted & set(expect.forbidden_tools)))
    counts = [len(business), len(response.tool_calls)]
    if type(execution.get("tool_calls")) is int:
        counts.append(execution["tool_calls"])
    add("tool_budget", max(counts) <= expect.max_tool_calls, expected=expect.max_tool_calls, actual=max(counts))
    if final is not None:
        add("finish_status", final.get("status") == response.task_status, actual=final.get("status"))
        missing = final.get("missing", [])
        valid_missing = isinstance(missing, list) and all(isinstance(item, str) and item.strip() for item in missing)
        add("missing_consistency", valid_missing and (not missing if response.task_status == "complete" else bool(missing) if response.task_status == "partial" else True), actual=missing)
    elif response.task_status not in {"error", "cancelled"} and response.stop_reason != "input_policy":
        add("finish_submission", None, "Cannot locate an authoritative structured finish submission")
    table = final.get("table") if final else None
    cited = final.get("evidence_ids", []) if final else []
    if not isinstance(cited, list) or any(not isinstance(key, str) for key in cited):
        add("citations", False, "Malformed evidence references")
        cited = []
    cited = list(dict.fromkeys(cited + ([table.get("evidence_id")] if isinstance(table, dict) else [])))
    if expect.require_evidence:
        add("evidence_required", bool(cited), actual=cited)
    inspected = set()
    def inspect(key, visiting=None):
        visiting = set() if visiting is None else visiting
        if key in visiting:
            add("evidence_lineage", False, "Cyclic evidence lineage", actual=key)
            return
        if key in inspected:
            return
        inspected.add(key)
        record = records.get(key)
        if not isinstance(record, dict):
            add("evidence_exists", False, "Cited evidence is absent", actual=key)
            return
        scope = record.get("evidence_scope")
        add("evidence_scope", False if record.get("historical_reference") or scope not in {None, "current_run"} else True if scope == "current_run" else None, actual=key)
        add("evidence_identity", record.get("evidence_id") == key, actual=key)
        states = {"ok", "partial", "empty"}
        if not expect.require_evidence and not expect.columns and table is None and response.task_status in {"error", "partial"}:
            states.add("error")  # A failure report may cite the failed call, never deliver it as market facts.
        add("evidence_usable", record.get("result_state") in states, actual=key)
        try:
            add("evidence_payload_rows", record.get("rows") == _rows(record.get("payload")), actual=key)
            if record.get("tool") == "compute_result":
                expected_rows, parents = _recompute(record, records)
                add("computed_rows", record["rows"] == expected_rows, actual=key)
                add("compute_parents", record["payload"].get("source_evidence_ids") == parents, actual=key)
                add("compute_operation", record["payload"].get("operation") == record["arguments"], actual=key)
                for parent in parents:
                    inspect(parent, visiting | {key})
            else:
                originals = [t.output for t in business if t.name == record.get("tool") and t.input == record.get("arguments")]
                payload = record["payload"]
                expected_payload = {"items": payload} if isinstance(payload, list) else payload
                add("original_tool_payload", any(value == expected_payload for value in originals) if originals else None,
                    "Compare evidence against the recorded business-tool output", actual=key)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            add("evidence_structure", False, "Malformed or unverifiable evidence structure", actual=key)
    for key in cited:
        inspect(key)
    if response.task_status == "empty" and cited:
        states = [records.get(key, {}).get("result_state") for key in cited]
        add("empty_evidence", "empty" in states and all(state in {"ok", "empty"} for state in states), actual=states)
    if expect.evidence_dates:
        try:
            actual_dates = set().union(*(_dates(records[key], records) for key in cited)) if cited else set()
            add("evidence_dates", actual_dates == set(expect.evidence_dates) if actual_dates else None,
                "Dates must come from returned payloads or compute ancestors", expect.evidence_dates, sorted(actual_dates))
        except (KeyError, TypeError, ValueError):
            add("evidence_dates", None, "Cannot establish evidence dates from valid lineage")
    parsed, outside = _table(response.answer)
    if expect.columns or table is not None:
        add("table_count", len(parsed) == 1, actual=len(parsed))
        add("table_declaration", isinstance(table, dict))
        if isinstance(table, dict) and len(parsed) == 1:
            header, rows, valid = parsed[0]
            columns = table.get("columns", [])
            if not isinstance(columns, list) or any(not isinstance(c, dict) for c in columns):
                columns = []
            fields, labels = [c.get("field") for c in columns], [c.get("label") for c in columns]
            add("table_shape", valid and bool(fields) and all(isinstance(f, str) for f in fields)
                and len(fields) == len(set(fields)))
            if expect.columns:
                add("table_fields", fields == expect.columns, expected=expect.columns, actual=fields)
            add("table_headers", header == labels, expected=labels, actual=header)
            for field, label in zip(fields, labels):
                add("column_meaning", _label(field, label) if isinstance(field, str) and isinstance(label, str) else False,
                    "Column label must describe its declared evidence field", field, label)
            add("duplicate_rows", len(rows) == len({tuple(row) for row in rows}), actual=rows)
            record = records.get(table.get("evidence_id"), {})
            try:
                expected_rows = [[_scalar(row[field]) for field in fields] for row in record["rows"]]
                add("table_evidence_values", rows == expected_rows, expected=expected_rows, actual=rows)
            except (KeyError, TypeError, ValueError):
                add("table_evidence_values", False, "Declared fields unavailable as scalar evidence values")
            if response.task_status == "complete" and record.get("source_truncated"):
                add("complete_source", None if record.get("complete_rank_scope") else False,
                    "Truncated evidence requires independently reviewed rank-scope proof")
            if expect.rows is not None:
                match = rows == expect.rows if expect.ordered else Counter(map(tuple, rows)) == Counter(map(tuple, expect.rows))
                add("expected_rows", match, expected=expect.rows, actual=rows)
    if expect.table_only:
        add("table_only", len(parsed) == 1 and not outside, actual=outside)
    add("no_placeholder", "{{evidence_table}}" not in response.answer)
    if events is not None:
        drafts, errors = _stream(events)
        add("stream_protocol", not errors, "; ".join(errors))
        add("visible_draft_record", True, "Withdrawn revisions remain available to semantic judging", actual=drafts)
        if expect.table_only:
            for draft in drafts:
                draft_tables, draft_outside = _table(draft)
                allowed = not draft_outside if len(draft_tables) == 1 else None if draft.lstrip().startswith("|") else False
                add("visible_table_only", allowed, "Already displayed text counts even after withdrawal", actual=draft)
    judgements = judgements or []
    valid = [j for j in judgements if isinstance(j, dict) and type(j.get("index")) is int
             and type(j.get("passed")) is bool and isinstance(j.get("reason"), str) and j["reason"].strip()]
    indexes = [j["index"] for j in valid]
    if expect.semantic_checks:
        coverage = len(valid) == len(judgements) and sorted(indexes) == list(range(len(expect.semantic_checks)))
        add("semantic_coverage", True if coverage else None, "Each semantic criterion requires one indexed judgement")
    for index, criterion in enumerate(expect.semantic_checks):
        matches = [j for j in valid if j["index"] == index]
        judgement = matches[0] if len(matches) == 1 else None
        rejected = [j["reason"] for j in matches if not j["passed"]]
        add(f"semantic_{index}", False if rejected else judgement["passed"] if judgement else None,
            "; ".join(rejected) if rejected else judgement["reason"] if judgement else "No unique valid judgement supplied", expected=criterion)
    return checks
