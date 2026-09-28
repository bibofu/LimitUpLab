"""Mutation tests for the evaluator itself; these are not Agent accuracy scores."""

from copy import deepcopy

import pytest

from app.models import AgentChatResponse, AgentToolTrace
from evals.golden.contracts import Expectation, verdict
from evals.golden.grading import grade_turn, visible_drafts

DAY, PRIOR = "2026-09-22", "2026-09-21"
FIELDS, HEADERS = ["symbol", "name", "pct_change"], ["代码", "名称", "涨跌幅"]
ROWS = [{"symbol": "600101", "name": "样本甲", "pct_change": 1.2, "trade_date": DAY},
        {"symbol": "000202", "name": "样本乙", "pct_change": 2.3, "trade_date": DAY}]
ORACLE = [["600101", "样本甲", "1.2"], ["000202", "样本乙", "2.3"]]


def markdown(rows, fields=FIELDS, headers=HEADERS):
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
                     + ["| " + " | ".join(str(row[field]) for field in fields) + " |" for row in rows])


def example(rows=None, *, tool="limit_up_events", payload=None):
    rows = deepcopy(ROWS if rows is None else rows)
    payload = deepcopy(payload if payload is not None else {"trade_date": DAY, "events": rows})
    record = {"evidence_id": "ev_current", "tool": tool, "rows": deepcopy(rows), "payload": deepcopy(payload),
              "arguments": {"trade_date": DAY}, "evidence_scope": "current_run", "historical_reference": False,
              "source_truncated": False, "data_missing": [], "result_state": "ok" if rows else "empty"}
    final = {"status": "complete", "answer": "{{evidence_table}}", "missing": [], "evidence_ids": ["ev_current"],
             "table": {"evidence_id": "ev_current", "columns": [{"field": f, "label": h} for f, h in zip(FIELDS, HEADERS)]}}
    traces = [AgentToolTrace(name=tool, input={"trade_date": DAY}, output=deepcopy(payload), summary="fixture"),
              AgentToolTrace(name="react_decision", output={"tool_calls": [{"name": "finish", "args": final}]}, summary="fixture"),
              AgentToolTrace(name="react_execution", output={"tool_calls": 1, "evidence": {"ev_current": record}}, summary="fixture")]
    response = AgentChatResponse(session_id="synthetic", intent="test", answer=markdown(rows), task_status="complete",
                                 stop_reason="answered", tool_calls=[tool], tool_results=traces, generated_by="fixture")
    expect = Expectation(columns=FIELDS, rows=ORACLE, table_only=True, evidence_dates=[DAY])
    return expect, response


def parts(response):
    return (response.tool_results[-2].output["tool_calls"][0]["args"],
            response.tool_results[-1].output["evidence"], response.tool_results[0])


def outcome(expect, response, **kwargs):
    return verdict(grade_turn(expect, response, **kwargs))


def test_correct_original_values_and_english_headers_pass():
    expect, response = example()
    assert outcome(expect, response) == "pass"
    final, _, _ = parts(response)
    for column in final["table"]["columns"]:
        column["label"] = column["field"]
    response.answer = markdown(ROWS, headers=FIELDS)
    assert outcome(expect, response) == "pass"


@pytest.mark.parametrize("mutation", ["number", "missing_row", "extra_row", "duplicate_row", "prose", "wrong_header",
    "wrong_field", "wrong_status", "complete_missing", "historical", "missing_citation", "record_rows", "record_payload",
    "raw_payload", "wrong_date", "forbidden_tool", "tool_budget", "truncation"])
def test_mutations_cannot_pass(mutation):
    expect, response = example()
    final, records, raw = parts(response)
    record = records["ev_current"]
    if mutation == "number": response.answer = response.answer.replace("1.2", "9.9")
    elif mutation == "missing_row": response.answer = markdown(ROWS[:1])
    elif mutation == "extra_row": response.answer = markdown(ROWS + [{"symbol": "600999", "name": "补造", "pct_change": 3.4}])
    elif mutation == "duplicate_row": response.answer = markdown(ROWS + ROWS[:1])
    elif mutation == "prose": response.answer = "多余说明\n" + response.answer
    elif mutation == "wrong_header": response.answer = response.answer.replace("涨跌幅", "成交额")
    elif mutation == "wrong_field": final["table"]["columns"][2]["field"] = "amount"
    elif mutation == "wrong_status": response.task_status = "partial"
    elif mutation == "complete_missing": final["missing"] = ["缺少一项明确要求"]
    elif mutation == "historical": record["evidence_scope"] = "conversation_history"
    elif mutation == "missing_citation": final["evidence_ids"].append("ev_nonexistent")
    elif mutation == "record_rows": record["rows"][0]["pct_change"] = 9.9
    elif mutation == "record_payload":
        record["rows"][0]["pct_change"] = record["payload"]["events"][0]["pct_change"] = 9.9
        response.answer = response.answer.replace("1.2", "9.9")
    elif mutation == "raw_payload": raw.output["events"][0]["pct_change"] = 9.9
    elif mutation == "wrong_date":
        record["payload"]["trade_date"] = raw.output["trade_date"] = PRIOR
    elif mutation == "forbidden_tool": expect.forbidden_tools = ["limit_up_events"]
    elif mutation == "tool_budget": expect.max_tool_calls = 0
    elif mutation == "truncation": record["source_truncated"] = True
    assert outcome(expect, response) == "fail", grade_turn(expect, response)


def test_order_is_checked_only_when_requested_but_rendered_rows_always_follow_evidence():
    expect, response = example(list(reversed(ROWS)))
    assert outcome(expect, response) == "pass"
    expect.ordered = True
    assert outcome(expect, response) == "fail"
    expect.ordered = False
    response.answer = markdown(ROWS)
    assert outcome(expect, response) == "fail"


def test_unknown_header_and_missing_provenance_require_review():
    expect, response = example()
    final, _, _ = parts(response)
    final["table"]["columns"][2]["label"] = "神秘指标"
    response.answer = response.answer.replace("涨跌幅", "神秘指标")
    assert outcome(expect, response) == "review"
    expect, response = example()
    response.tool_results.pop(0)
    assert outcome(expect, response) == "review"


def computed_example():
    expect, response = example()
    final, records, _ = parts(response)
    arguments = {"evidence_id": "ev_current", "operation": "select", "sort_by": "pct_change", "descending": False, "limit": 2}
    payload = {"items": deepcopy(ROWS), "operation": deepcopy(arguments), "source_evidence_ids": ["ev_current"]}
    records["ev_derived"] = {**deepcopy(records["ev_current"]), "evidence_id": "ev_derived", "tool": "compute_result",
                             "arguments": arguments, "payload": payload}
    final["evidence_ids"] = ["ev_derived"]
    final["table"]["evidence_id"] = "ev_derived"
    return expect, response


def test_compute_lineage_rechecks_values_scope_and_dates():
    expect, response = computed_example()
    assert outcome(expect, response) == "pass"
    _, records, _ = parts(response)
    records["ev_derived"]["rows"][0]["pct_change"] = 9.9
    records["ev_derived"]["payload"]["items"][0]["pct_change"] = 9.9
    response.answer = response.answer.replace("1.2", "9.9")
    checks = grade_turn(expect, response)
    assert any(c.name == "computed_rows" and c.passed is False for c in checks)
    expect, response = computed_example()
    _, records, _ = parts(response)
    records["ev_current"]["evidence_scope"] = "conversation_history"
    assert outcome(expect, response) == "fail"


def test_cross_date_parent_union_preserves_both_source_dates():
    expect, response = computed_example()
    _, records, raw = parts(response)
    prior = deepcopy(records["ev_current"])
    prior.update(evidence_id="ev_prior", arguments={"trade_date": PRIOR})
    prior["payload"]["trade_date"] = PRIOR
    for row in prior["rows"] + prior["payload"]["events"]:
        row["trade_date"] = PRIOR
    records["ev_prior"] = prior
    response.tool_results.insert(0, AgentToolTrace(name="limit_up_events", input=prior["arguments"], output=deepcopy(prior["payload"]), summary="prior"))
    derived = records["ev_derived"]
    derived["arguments"].update(operation="intersection", other_id="ev_prior")
    derived["payload"]["operation"] = deepcopy(derived["arguments"])
    derived["payload"]["source_evidence_ids"].append("ev_prior")
    expect.evidence_dates = [PRIOR, DAY]
    assert outcome(expect, response) == "pass"


@pytest.mark.parametrize("field", ["data_as_of", "as_of_date", "end_date", "trade_date"])
def test_kline_as_of_is_not_confused_with_bar_window(field):
    rows = deepcopy(ROWS)
    rows[0]["trade_date"] = PRIOR
    expect, response = example(rows, tool="stock_kline", payload={field: DAY, "bars": rows})
    assert outcome(expect, response) == "pass"
    _, records, raw = parts(response)
    records["ev_current"]["payload"][field] = raw.output[field] = PRIOR
    assert outcome(expect, response) == "fail"


def test_argument_date_and_retrieval_time_cannot_replace_missing_payload_as_of():
    expect, response = example(tool="stock_kline", payload={"bars": ROWS, "requested_end_date": DAY})
    _, records, _ = parts(response)
    records["ev_current"]["retrieved_at"] = DAY + "T12:00:00Z"
    assert outcome(expect, response) == "review"


def test_multi_day_collection_uses_actual_row_dates():
    rows = deepcopy(ROWS)
    rows[0]["trade_date"] = PRIOR
    expect, response = example(rows, payload={"trade_date": DAY, "recent_trade_days": 2, "events": rows})
    expect.evidence_dates = [PRIOR, DAY]
    assert outcome(expect, response) == "pass"


@pytest.mark.parametrize("tool,field", [("hot_stock_ranking", "captured_at"), ("stock_news", "fetched_at")])
def test_snapshot_date_is_distinct_from_publication_date(tool, field):
    rows = [{**row, "published_at": PRIOR + "T12:00:00+08:00"} for row in ROWS]
    expect, response = example(rows, tool=tool, payload={field: DAY + "T18:00:00+08:00", "items": rows})
    assert outcome(expect, response) == "pass"
    _, records, raw = parts(response)
    records["ev_current"]["payload"][field] = raw.output[field] = PRIOR + "T18:00:00+08:00"
    assert outcome(expect, response) == "fail"


def test_conflicting_authoritative_dates_do_not_pass():
    expect, response = example(tool="stock_kline", payload={"data_as_of": DAY, "end_date": PRIOR, "bars": ROWS})
    assert outcome(expect, response) == "fail"


def test_partial_failure_report_can_cite_error_without_delivering_facts():
    expect, response = example()
    final, records, _ = parts(response)
    response.task_status = final["status"] = "partial"
    final.update(table=None, answer="数据源查询失败，无法列出名单。", missing=["涨停名单"])
    response.answer = final["answer"]
    records["ev_current"]["result_state"] = "error"
    expect = Expectation(statuses=["partial"], require_evidence=False)
    assert outcome(expect, response) == "pass"
    expect.require_evidence = True
    assert outcome(expect, response) == "fail"


def test_partial_and_empty_status_require_consistent_missing_and_evidence():
    expect, response = example()
    final, _, _ = parts(response)
    expect.statuses = ["partial"]
    response.task_status = final["status"] = "partial"
    assert outcome(expect, response) == "fail"
    final["missing"] = ["用户要求的另一项数据暂缺"]
    assert outcome(expect, response) == "pass"
    expect, response = example([])
    final, _, _ = parts(response)
    expect.statuses, expect.rows = ["empty"], []
    response.task_status = final["status"] = "empty"
    assert outcome(expect, response) == "pass"


def test_unjudged_semantics_and_incomplete_duplicate_judgements_never_pass():
    expect, response = example()
    expect.semantic_checks = ["首项涨跌幅为1.2%", "没有投资建议"]
    assert outcome(expect, response) == "review"
    first = {"index": 0, "passed": True, "reason": "已经核对首项为1.2%"}
    second = {"index": 1, "passed": True, "reason": "只展示事实"}
    assert outcome(expect, response, judgements=[first]) == "review"
    assert outcome(expect, response, judgements=[first, first, second]) == "review"
    checks = grade_turn(expect, response, judgements=[first, second])
    assert verdict(checks) == "pass"
    assert next(c for c in checks if c.name == "semantic_0").detail == first["reason"]
    assert outcome(expect, response, judgements=[first, {**first, "passed": False}, second]) == "fail"


def test_visible_withdrawn_drafts_are_retained_and_table_only_violation_fails():
    expect, response = example()
    draft = "此前曾展示的多余文本"
    events = [{"event": "answer_start", "payload": {"revision": 1}},
              {"event": "answer_delta", "payload": {"offset": 0, "delta": draft[:5]}},
              {"event": "answer_delta", "payload": {"offset": 5, "delta": draft[5:]}},
              {"event": "answer_reset", "payload": {}},
              {"event": "answer_start", "payload": {"revision": 2}},
              {"event": "answer_delta", "payload": {"offset": 0, "delta": response.answer}}]
    assert visible_drafts(events) == [draft, response.answer]
    assert outcome(expect, response, events=events) == "fail"
    assert outcome(expect, response, events=events[4:]) == "pass"
    events[-1]["payload"]["offset"] = 8
    assert outcome(expect, response, events=events[4:]) == "fail"


@pytest.mark.parametrize("change,expected", [("extra", True), ("wrong", False), ("omit", False), ("missing_field", None)])
def test_required_values_are_separate_from_delivered_column_format(change, expected):
    expect, response = example()
    final, records, raw = parts(response)
    fields = FIELDS + ["trade_date"]
    headers = HEADERS + ["日期"]
    if change == "missing_field":
        fields, headers = ["symbol", "name"], ["代码", "名称"]
    final["table"]["columns"] = [{"field": f, "label": h} for f, h in zip(fields, headers)]
    rows = deepcopy(ROWS)
    if change == "wrong": rows[0]["pct_change"] = 9.9
    if change == "omit": rows = rows[:1]
    response.answer = markdown(rows, fields, headers)
    checks = grade_turn(expect, response)
    assert next(c for c in checks if c.name == "expected_values").passed is expected
    assert next(c for c in checks if c.name == "table_fields").passed is False


def test_unknown_semantic_decision_preserves_its_reason():
    expect, response = example()
    expect.semantic_checks = ["来源描述等价"]
    checks = grade_turn(expect, response, judgements=[{"index": 0, "passed": None, "reason": "无法确认来源归属"}])
    assert verdict(checks) == "review"
    semantic = next(c for c in checks if c.name == "semantic_0")
    assert semantic.passed is None and semantic.detail == "无法确认来源归属"
