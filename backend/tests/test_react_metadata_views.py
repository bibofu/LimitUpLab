"""Bounded summaries avoid exploratory paging without weakening evidence scope."""

from datetime import date

import pytest

from app.agents.react_runtime.contracts import Compute, Finish
from app.agents.react_runtime.evidence import EvidenceStore, HISTORY_SCOPE, payload_of
from app.agents.react_runtime.rendering import render_answer
from app.agents.tools import AgentToolRegistry, ToolResult
from app.services.sample_data import SAMPLE_EVENTS


def event(symbol, **changes):
    return SAMPLE_EVENTS[0].model_copy(update={
        "symbol": symbol, "name": "样本" + symbol, "trade_date": date(2026, 10, 21),
        "industry": "行业A", "concept": "概念1+概念2", "closed_limit": True,
        "board_height": 1, "break_count": 0, **changes,
    })


def query(events, **arguments):
    registry = AgentToolRegistry.__new__(AgentToolRegistry)
    registry.events = events
    result = registry.limit_up_events(trade_date=date(2026, 10, 21), **arguments)
    store = EvidenceStore()
    key = store.add(tool=result.name, payload=payload_of(result), state="ok", arguments=result.input)
    return store, key


def grouped_events():
    return [event(f"{group * 10 + member:06}", industry=f"行业{group}")
            for group, count in enumerate((5, 4, 3, 3, 2, 2, 1), 1)
            for member in range(count)]


def test_first_preview_preserves_top_five_and_exposes_bounded_tie_context():
    store, key = query(grouped_events(), group_by="industry", limit=100)
    view = store.view(key)
    groups = view["available_views"]["sector_summary"]
    assert len(groups["preview"]) == 5 and groups["row_count"] == 7
    assert groups["preview_truncated"] and not groups["source_truncated"]
    assert [row["unique_stock_count"] for row in groups["preview"]] == [5, 4, 3, 3, 2]
    assert groups["preview"][4]["sector_name"] == "行业5"
    assert groups["metadata"]["preview_boundary"]["tied_groups_in_returned_collection"] == 2
    first = groups["preview"][0]
    assert first["stocks_count"] == 5 and first["representative_count"] == 3
    assert first["representative_symbols"] == "000010、000011、000012"
    assert first["representatives_truncated"]
    assert "sector_summary" not in view["metadata"]
    remaining = store.view(key, offset=5, limit=30, view="sector_summary")
    assert [row["sector_name"] for row in remaining["rows"]] == ["行业6", "行业7"]
    assert remaining["evidence_id"] == groups["evidence_id"]
    assert remaining["sources"] == [key]


def test_group_evidence_is_directly_selectable_and_renderable_without_rereading_events():
    store, key = query(grouped_events(), group_by="industry", limit=100)
    groups = store.view(key)["available_views"]["sector_summary"]["evidence_id"]
    selected = store.compute(Compute(evidence_id=groups, sort_by="rank", descending=False, limit=5))
    rows = store.get(selected)["rows"]
    assert [row["sector_name"] for row in rows] == [f"行业{number}" for number in range(1, 6)]
    final = Finish(status="complete", answer="{{evidence_table}}", table={
        "evidence_id": selected, "columns": [
            {"field": "sector_name", "label": "行业"},
            {"field": "unique_stock_count", "label": "家数"},
            {"field": "representative_names", "label": "代表股票"},
        ]})
    rendered = render_answer(final, store)
    assert len(rendered.splitlines()) == 7 and "行业6" not in rendered
    assert store.get(selected)["sources"] == [groups]


def test_complete_group_collection_does_not_inherit_local_event_pagination():
    store, key = query([event(f"60000{i}") for i in range(3)], group_by="industry", limit=1)
    parent = store.get(key)
    assert parent["result_state"] == "partial" and parent["source_truncated"]
    groups = store.view(key, view="sector_summary")
    assert groups["row_count"] == 1
    assert groups["result_state"] == "ok" and not groups["source_truncated"]
    assert groups["rows"][0]["unique_stock_count"] == 3
    assert groups["metadata"]["grouping"]["population_event_count"] == 3
    final = Finish(status="complete", answer="{{evidence_table}}", table={
        "evidence_id": groups["evidence_id"], "columns": [{"field": "sector_name", "label": "行业"}]})
    assert "行业A" in render_answer(final, store)
    assert parent["source_truncated"], "The independent group proof never upgrades the primary event list"


@pytest.mark.parametrize("change", [
    "missing", "source_errors", "stale", "source_truncated", "truncated", "partial",
    "old_record", "group_count", "returned_group_count", "groups_truncated", "population", "arguments",
])
def test_independent_group_proof_cannot_erase_upstream_or_unknown_gaps(change):
    store, key = query([event(f"60000{i}") for i in range(3)], group_by="industry", limit=1)
    source = store.get(key)
    payload = source["payload"]
    if change == "missing":
        source["data_missing"] = payload["data_missing"] = ["upstream gap"]
    if change == "source_errors": payload["source_errors"] = ["upstream failure"]
    if change == "stale": payload["data_fresh"] = False
    if change == "source_truncated": payload["source_truncated"] = True
    if change == "truncated": payload["truncated"] = True
    if change == "partial": source["source_result_state"] = "partial"
    if change == "old_record": source.pop("source_result_state")
    if change == "group_count": payload["sector_summary_contract"]["group_count"] = 2
    if change == "returned_group_count": payload["sector_summary_contract"]["returned_group_count"] = 2
    if change == "groups_truncated": payload["sector_summary_contract"].pop("groups_truncated")
    if change == "population": payload["sector_summary_contract"]["population_event_count"] = 2
    if change == "arguments": source["arguments"]["group_by"] = "concept"
    groups = store.view(key, view="sector_summary")
    assert groups["result_state"] == "partial" and groups["source_truncated"]
    if change == "missing": assert groups["data_missing"] == ["upstream gap"]
    if change == "source_errors": assert groups["metadata"]["source_errors"] == ["upstream failure"]


def test_complete_groups_keep_unclassified_gap_even_when_events_are_paginated():
    store, key = query([event("600001"), event("600002", industry="")], group_by="industry", limit=1)
    groups = store.view(key, view="sector_summary")
    assert groups["result_state"] == "partial" and groups["source_truncated"]
    assert groups["data_missing"] == ["sector_summary: 1 events lack industry classification"]
    assert groups["metadata"]["grouping"]["unclassified_event_count"] == 1
    selected = store.compute(Compute(evidence_id=groups["evidence_id"], sort_by="rank", descending=False, limit=1))
    assert store.complete_rank_scope(selected) is None
    final = Finish(status="complete", answer="{{evidence_table}}", table={
        "evidence_id": selected, "columns": [{"field": "sector_name", "label": "行业"}]})
    with pytest.raises(ValueError, match="Truncated"):
        render_answer(final, store)


def test_independent_group_proof_never_promotes_history_to_current_evidence():
    store, key = query([event(f"60000{i}") for i in range(3)], group_by="industry", limit=1)
    store.restore_history("ev_previous", store.get(key))
    groups = store.view("ev_previous", view="sector_summary")
    assert groups["evidence_scope"] == HISTORY_SCOPE and groups["historical_reference"]
    final = Finish(status="complete", answer="{{evidence_table}}", table={
        "evidence_id": groups["evidence_id"], "columns": [{"field": "sector_name", "label": "行业"}]})
    with pytest.raises(ValueError, match="current-run"):
        render_answer(final, store)


def test_bounded_group_prefix_remains_partial_until_its_ranking_is_explicitly_proven():
    store, key = query(grouped_events(), group_by="industry", limit=5)
    groups = store.view(key, view="sector_summary")
    assert groups["row_count"] == 5 and groups["source_truncated"]
    assert groups["metadata"]["grouping"]["group_count"] == 7
    selected = store.compute(Compute(evidence_id=groups["evidence_id"], sort_by="rank", descending=False, limit=5))
    assert store.complete_rank_scope(selected) is None


def test_named_view_keeps_source_truncation_missing_and_historical_scope():
    store, key = query(grouped_events(), group_by="industry", limit=3)
    source = store.get(key)
    source["data_missing"] = ["unavailable page"]
    source["payload"]["data_missing"] = ["unavailable page"]
    store.restore_history("ev_old", source)
    view = store.view("ev_old", view="sector_summary")
    assert view["result_state"] == "partial" and view["source_truncated"]
    assert view["data_missing"] == ["unavailable page"]
    assert view["evidence_scope"] == HISTORY_SCOPE and view["historical_reference"]
    selected = store.compute(Compute(evidence_id=view["evidence_id"], limit=1))
    assert store.view(selected)["source_truncated"]
    assert store.view(selected)["evidence_scope"] == HISTORY_SCOPE


def test_old_group_metadata_without_completeness_contract_stays_partial():
    store, key = query(grouped_events(), group_by="industry", limit=100)
    store.get(key)["payload"].pop("sector_summary_contract")
    view = store.view(key, view="sector_summary")
    assert view["source_truncated"] and view["result_state"] == "partial"


def test_reading_named_view_is_idempotent_and_cannot_target_arbitrary_metadata():
    store, key = query(grouped_events(), group_by="industry", limit=100)
    first = store.view(key, view="sector_summary")
    assert store.view(key, view="sector_summary")["evidence_id"] == first["evidence_id"]
    assert len(store.records) == 2
    with pytest.raises(ValueError, match="Unknown evidence view"):
        store.view(key, view="arguments")
    with pytest.raises(ValueError, match="1<=limit<=30"):
        store.view(key, limit=31, view="sector_summary")


def test_group_coverage_counts_events_and_unique_stocks_without_splitting_industries():
    rows = [event("600001", industry="A+行业"),
            event("600001", industry="A+行业", trade_date=date(2026, 10, 20)),
            event("600002", industry="")]
    store, key = query(rows, group_by="industry", recent_trade_days=2, limit=100)
    view = store.view(key, view="sector_summary")
    assert view["rows"][0]["sector_name"] == "A+行业"
    assert view["rows"][0]["unique_stock_count"] == 1
    assert view["rows"][0]["limit_up_event_count"] == 2
    assert view["metadata"]["grouping"]["unclassified_event_count"] == 1


def test_state_counts_do_not_assume_unsealed_records_all_have_an_intraday_open():
    rows = [event("600001"), event("600002", break_count=2, board_height=2),
            event("600003", break_count=3, closed_limit=False),
            event("600004", closed_limit=False)]
    store, key = query(rows, event_status="all", limit=1)
    counts = store.view(key)["metadata"]["state_counts"]
    assert counts["population_event_count"] == 4 and counts["closed_count"] == 2
    assert counts["first_board_count"] == counts["continued_board_count"] == 1
    assert counts["intraday_opened_count"] == counts["unsealed_count"] == 2
    assert counts["resealed_count"] == counts["opened_unsealed_count"] == 1
    assert counts["unsealed_without_recorded_open_count"] == 1
    assert not counts["relations"]["all_unsealed_have_recorded_open"]
    assert store.view(key)["source_truncated"], "A complete count does not make a truncated event list complete"
    registry = AgentToolRegistry.__new__(AgentToolRegistry)
    registry.events = rows
    summary = registry.market_summary().trace_output
    assert summary["state_counts"]["resealed_count"] == 1


def test_five_interval_window_is_available_with_bound_dates_prices_and_unverified_calendar():
    bars = [{"trade_date": f"2026-10-{day:02}", "close": 10 + index,
             "source": "hithink-finance.market.history.none"}
            for index, day in enumerate((8, 9, 12, 13, 14, 15, 16, 19, 20, 21))]
    result = ToolResult(name="stock_kline", input={}, output={"symbol": "600001", "bars": bars}, summary="test")
    store = EvidenceStore()
    key = store.add(tool=result.name, payload=payload_of(result), state="ok", arguments={})
    view = store.view(key)
    window = view["metadata"]["return_windows"][0]
    assert (window["start_date"], window["start_close"]) == ("2026-10-14", 14)
    assert (window["end_date"], window["end_close"]) == ("2026-10-21", 19)
    assert window["observed_intervals"] == 5 and window["observations"] == 6
    assert not window["calendar_verified"] and window["adjustment"] == "unadjusted"
    assert view["metadata"]["calendar_verification"]["status"] == "not_verified"
    named = view["available_views"]["return_windows"]
    assert named["preview"][0]["symbol"] == "600001"
    assert named["preview"][0]["start_date"] == window["start_date"]
    assert store.get(key)["rows"] == bars


@pytest.mark.parametrize("state,bars", [
    ("empty", []),
    ("ok", [{"trade_date": "2026-10-21", "close": 10, "source": "fixture"}]),
    ("partial", [{"trade_date": "2026-10-21", "close": None, "source": "fixture"}]),
])
def test_unavailable_kline_windows_do_not_create_empty_current_evidence(state, bars):
    missing = ["close unavailable"] if state == "partial" else []
    result = ToolResult(name="stock_kline", input={}, summary="test", result_status=state,
                        output={"symbol": "600001", "bars": bars, "data_missing": missing})
    store = EvidenceStore()
    key = store.add(tool=result.name, payload=payload_of(result), state=state, arguments={})
    view = store.view(key)
    assert view["metadata"]["return_windows"] == []
    assert "available_views" not in view
    assert len(store.current_records()) == 1
    assert store.get(key)["rows"] == bars and store.get(key)["result_state"] == state
    assert store.get(key)["data_missing"] == missing
    with pytest.raises(ValueError, match="Unknown evidence view"):
        store.view(key, view="return_windows")


def test_error_payload_cannot_publish_named_fact_views():
    store = EvidenceStore()
    key = store.add(tool="limit_up_events", state="error", arguments={}, payload={
        "group_by": "industry", "sector_summary": [{"sector_name": "untrusted", "stocks": []}]})
    assert "available_views" not in store.view(key)
