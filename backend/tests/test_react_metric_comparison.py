from copy import deepcopy

import pytest

from app.agents.react_runtime.contracts import CompareMetrics, Compute, Finish
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import TaskInterpretation, validate_delivery


DATES = ["2026-10-20", "2026-10-21"]


def source(store, day, heights, **extra):
    rows = [{"symbol": str(index), "trade_date": day, "board_height": height}
            for index, height in enumerate(heights)]
    return store.add(tool="limit_up_events", state="ok", arguments={"trade_date": day},
        payload={"trade_date": day, "event_status": "closed", "events": rows,
                 "matched_count": len(rows), **extra})


def comparison(store, left, right, *, operation="count", field="symbol", filters=None, label="总数"):
    spec = CompareMetrics.model_validate({"dates": DATES, "metrics": [{"label": label,
        "before": {"evidence_id": left, "operation": operation, "field": field, "filters": filters or []},
        "after": {"evidence_id": right, "operation": operation, "field": field, "filters": filters or []}}]})
    return store.compare(spec)


def task(*, statistic="count", field="symbol", label="总数", filters=None):
    return TaskInterpretation.model_validate({"output_contract": {"mode": "table_only", "comparison": {
        "dates": DATES, "metrics": [{"label": label, "statistic": statistic, "field": field,
            "filters": filters or [], "scope": {"event_status": "closed"}}]}}})


def finish(key, **extra):
    return Finish.model_validate({"status": "complete", "delivery": "table", "table": {
        "evidence_id": key, "columns": [{"field": field, "label": label} for field, label in
            zip(("metric", "before", "after", "change"), ("指标", *DATES, "变化"))]}, **extra})


def test_comparison_builds_replayable_numeric_table_and_exact_date_columns():
    store = EvidenceStore()
    left, right = source(store, DATES[0], [1, 1, 4]), source(store, DATES[1], [1, 4, 4, 4])
    key = comparison(store, left, right)
    record = store.get(key)
    assert record["rows"] == [{"metric": "总数", "before": 3, "after": 4, "change": 1,
                               "unit": "只", "change_unit": "只", "missing": None}]
    assert record["sources"] == [left, right]
    assert record["payload"]["metric_lineage"][0]["before"]["evidence_id"] == left
    final, interpretation = finish(key), task()
    validate_delivery(final, interpretation, store)
    assert "| 总数 | 3 只 | 4 只 | 1 只 |" in render_answer(final, store, interpretation.output_contract)
    assert final.answer == ""  # Rendering does not rewrite submitted model text.


def test_counts_and_maxima_use_different_declared_recipes_not_metric_labels():
    store = EvidenceStore()
    left, right = source(store, DATES[0], [1, 4]), source(store, DATES[1], [1, 3])
    key = comparison(store, left, right, operation="max", field="board_height", label="高度")
    validate_delivery(finish(key), task(statistic="max", field="board_height", label="高度"), store)
    assert store.get(key)["rows"][0]["change"] == -1
    with pytest.raises(ValueError, match="statistic/filter"):
        validate_delivery(finish(key), task(label="高度"), store)


@pytest.mark.parametrize("operation,field", [("count", None), ("count", "symbol"), ("max", "board_height")])
def test_count_mode_never_becomes_zero_from_empty_rows(operation, field):
    store = EvidenceStore()
    keys = [source(store, day, [], result_mode="count", matched_count=count)
            for day, count in zip(DATES, (29, 19))]
    with pytest.raises(ValueError, match="Count-mode"):
        comparison(store, *keys, operation=operation, field=field)
    key = comparison(store, *keys, operation="scalar", field="matched_count")
    assert [store.get(key)["rows"][0][field] for field in ("before", "after", "change")] == [29, 19, -10]


def test_partial_source_is_a_missing_cell_not_zero_or_supported_total():
    store = EvidenceStore()
    left = source(store, DATES[0], [1], matched_count=50)
    right = source(store, DATES[1], [1, 2])
    key = comparison(store, left, right)
    row = store.get(key)["rows"][0]
    assert row["before"] is None and row["change"] is None and row["after"] == 2
    with pytest.raises(ValueError, match="Missing metric"):
        validate_delivery(finish(key), task(), store)
    final = finish(key, status="partial", missing=store.get(key)["data_missing"])
    validate_delivery(final, task(), store)


def test_empty_complete_count_is_zero_but_empty_max_is_missing():
    store = EvidenceStore()
    keys = [source(store, day, []) for day in DATES]
    count = store.get(comparison(store, *keys))["rows"][0]
    maximum = store.get(comparison(store, *keys, operation="max", field="board_height"))["rows"][0]
    assert count["before"] == count["after"] == count["change"] == 0
    assert maximum["before"] is None and maximum["change"] is None


@pytest.mark.parametrize("damage", ["date", "scope", "history"])
def test_comparison_rejects_unmatched_dates_scope_and_historical_values(damage):
    store = EvidenceStore()
    left, right = source(store, DATES[0], [1]), source(store, DATES[1], [1])
    if damage == "date":
        store.get(right)["payload"]["trade_date"] = DATES[0]
    elif damage == "scope":
        store.get(right)["payload"]["event_status"] = "opened"
    else:
        store.restore_history(right, store.get(right))
    with pytest.raises(ValueError):
        comparison(store, left, right)


def test_derived_filter_and_view_grain_are_part_of_comparison_scope():
    store = EvidenceStore()
    left, right = source(store, DATES[0], [1, 2]), source(store, DATES[1], [1, 2])
    first = store.compute(Compute(evidence_id=left, filters=[{"field": "board_height", "operator": "eq", "value": 1}]))
    multi = store.compute(Compute(evidence_id=right, filters=[{"field": "board_height", "operator": "eq", "value": 2}]))
    with pytest.raises(ValueError, match="original business evidence"):
        comparison(store, first, multi)
    view = store.add(tool="evidence_view", state="ok", arguments={"evidence_id": right, "view": "sector_summary"},
                     sources=[right], payload={"items": [{"sector_name": "行业", "count": 2}]})
    with pytest.raises(ValueError, match="scope differs"):
        comparison(store, left, view, field=None)


def test_identical_top_one_slices_cannot_masquerade_as_full_counts():
    store = EvidenceStore()
    left, right = source(store, DATES[0], [1, 2, 3]), source(store, DATES[1], [1, 2, 3, 4])
    selected = [store.compute(Compute(evidence_id=key, limit=1)) for key in (left, right)]
    with pytest.raises(ValueError, match="original business evidence"):
        comparison(store, *selected)
    filters = [{"field": "board_height", "operator": "eq", "value": 1}]
    key = comparison(store, left, right, filters=filters)
    validate_delivery(finish(key), task(filters=filters), store)
    with pytest.raises(ValueError, match="statistic/filter"):
        validate_delivery(finish(key), task(), store)


def test_entity_rows_or_relabelled_periods_cannot_satisfy_metric_delivery():
    store = EvidenceStore()
    left, right = source(store, DATES[0], [1]), source(store, DATES[1], [1])
    with pytest.raises(ValueError, match="metric identities"):
        validate_delivery(finish(left), task(), store)
    key = comparison(store, left, right)
    final = finish(key)
    final.table.columns[1].label = DATES[1]
    with pytest.raises(ValueError, match="column labels"):
        validate_delivery(final, task(), store)
    final = finish(key)
    invalid = task()
    invalid.output_contract.comparison.metrics[0].scope = {"event_status": "opened"}
    with pytest.raises(ValueError, match="source scope"):
        validate_delivery(final, invalid, store)


def test_new_compute_calls_reject_ignored_parameters_and_old_executed_recipe_stays_select():
    with pytest.raises(ValueError, match="operation=aggregate"):
        Compute(evidence_id="unused", aggregate="max", metric="board_height")
    with pytest.raises(ValueError, match="operation=aggregate"):
        Compute(evidence_id="unused", aggregate="count")
    store = EvidenceStore()
    key = source(store, DATES[0], [1, 4])
    old = {**Compute(evidence_id=key).model_dump(), "aggregate": "count"}
    original = deepcopy(old)
    replay = Compute.from_record(old)
    assert replay.operation == "select" and replay.aggregate is None and old == original
    assert len(store.get(store.compute(replay))["rows"]) == 2


def test_legacy_serialized_selection_remains_replayable_without_reinterpreting_count():
    from app.agents.react_runtime.partial_delivery import verified_selection_total
    from app.agents.react_runtime.task_contract import RowSelection

    store = EvidenceStore()
    root = source(store, DATES[0], [1, 2, 3])
    selected = store.compute(Compute(evidence_id=root, sort_by="symbol", limit=2))
    record = store.get(selected)
    record["arguments"]["aggregate"] = "count"
    record["payload"]["operation"]["aggregate"] = "count"
    assert verified_selection_total(store, record, RowSelection(requested="前2条", limit=2)) == 3


def test_percentage_difference_is_percentage_points():
    store = EvidenceStore()
    left, right = source(store, DATES[0], [1]), source(store, DATES[1], [1])
    store.get(left)["rows"][0]["turnover_rate"] = 1.0
    store.get(right)["rows"][0]["turnover_rate"] = 2.0
    key = comparison(store, left, right, operation="max", field="turnover_rate")
    answer = render_answer(finish(key), store)
    assert "1.0 % | 2.0 % | 1.0 个百分点" in answer


def test_runtime_comparison_control_and_typed_gate_replay_the_original_finish(monkeypatch):
    from types import SimpleNamespace
    from app.agents.react_runtime import runtime
    from app.agents.react_runtime.compliance import ComplianceReview
    from app.agents.tools import TOOL_SCHEMAS
    from app.models import AgentChatRequest
    from app.services.quality_replay_checks import compute_checks, rendering_check
    from app.services.quality_trace import component_fingerprints

    registry = SimpleNamespace(schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True)
    runner = runtime.Run(AgentChatRequest(session_id="typed-replay", message="比较两日总数"),
                         registry, object(), [], None)
    left = source(runner.evidence, DATES[0], [1, 2, 3])
    right = source(runner.evidence, DATES[1], [1, 2, 3, 4])
    spec = {"dates": DATES, "metrics": [{"label": "总数", "before": {
        "evidence_id": left, "operation": "count", "field": "symbol"}, "after": {
        "evidence_id": right, "operation": "count", "field": "symbol"}}]}
    result = runner.tools_node({"observations": [], "pending": [{"name": "compare_metrics", "args": spec, "id": "comparison"}]})
    key = result["observations"][0][1]["evidence_id"]
    runner.task_context = task()
    snapshots = []
    monkeypatch.setattr(runtime, "record_snapshot", lambda _control, stage, payload:
                        snapshots.append(payload()) if stage == "answer_rendering" else None)
    monkeypatch.setattr(runtime, "review_answer", lambda *args, **kwargs:
                        ComplianceReview(decision="allow", violations=[], reason="fixture"))
    assert runner.gate({"finish": finish(key).model_dump(mode="json")}) == {"done": True}
    assert runner.repairs == 0 and runner.status == "complete"
    raw = snapshots[0]["finish_input"]
    assert raw["delivery"] == "table" and raw["answer"] == ""
    assert Finish.model_validate(raw).answer == ""
    bundle = {"renderings": snapshots, "response": {"answer": runner.answer, "task_status": "complete"},
              "provenance": [component_fingerprints()]}
    assert rendering_check(bundle, runner.evidence.records).verdict == "pass"
    assert compute_checks(bundle, runner.evidence.records, [key])[0].verdict == "pass"
