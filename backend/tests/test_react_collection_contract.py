"""Offline tool-output to evidence/computation/table collection regressions."""

from datetime import date

import pytest

from app.agents import tools as business_tools
from app.agents.react_runtime.contracts import Compute, Finish
from app.agents.react_runtime.evidence import EvidenceStore, TOOL_COLLECTIONS, payload_of
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import AgentToolRegistry, TOOL_SCHEMAS, ToolResult
from app.models import MarketIndexTrendFacts, MarketIndexTrendItem


def store_result(result):
    store = EvidenceStore()
    key = store.add(tool=result.name, payload=payload_of(result), state=result.trace().result.status,
                    arguments=result.input)
    return store, key


def table(key, fields, status="complete"):
    return Finish(status=status, answer="{{evidence_table}}", evidence_ids=[key], table={
        "evidence_id": key,
        "columns": [{"field": field, "label": label} for field, label in fields],
    })


def test_real_index_tool_output_reaches_sort_aggregate_and_render(monkeypatch):
    cutoff = date(2026, 9, 18)
    items = [MarketIndexTrendItem(
        symbol=symbol, name=name, start_date=date(2026, 9, 14), end_date=cutoff,
        start_close=100, end_close=100 + change, return_pct=change,
        max_drawdown_pct=-1, positive_days=3, negative_days=1,
        points=[], source="synthetic-index-source",
    ) for symbol, name, change in [
        ("000001", "上证样本", 1.0), ("399001", "深成样本", 3.0), ("399006", "创业样本", 2.0),
    ]]
    facts = MarketIndexTrendFacts(requested_days=5, requested_end_date=cutoff,
                                 data_as_of=cutoff, data_fresh=True, indices=items)
    monkeypatch.setattr(business_tools, "collect_market_index_trends", lambda **_: facts)
    registry = AgentToolRegistry.__new__(AgentToolRegistry)
    registry.events, registry.profile = [], "extended"
    store = EvidenceStore()
    gateway = ToolGateway(registry, store)
    arguments = gateway.validate({"name": "market_index_trend", "args": {
        "days": 5, "end_date": cutoff.isoformat(),
    }})
    result, payload, state = gateway.execute("market_index_trend", arguments)
    key = store.add(tool=result.name, payload=payload, state=state, arguments=result.input)
    assert store.view(key)["row_count"] == 3
    assert "indices" not in store.view(key)["metadata"]
    assert store.view(key)["metadata"]["data_as_of"] == cutoff.isoformat()
    assert store.get(key)["rows"][0]["source"] == "synthetic-index-source"
    selected = store.compute(Compute(evidence_id=key, sort_by="return_pct", limit=3))
    answer = render_answer(table(selected, [("name", "名称"), ("return_pct", "区间涨幅")]), store)
    assert answer.splitlines()[2:] == ["| 深成样本 | 3.0 |", "| 创业样本 | 2.0 |", "| 上证样本 | 1.0 |"]
    aggregate = store.compute(Compute(evidence_id=key, operation="aggregate", metric="return_pct", aggregate="mean"))
    assert store.get(aggregate)["rows"] == [{"group": "all", "metric": "return_pct", "value": 2.0}]
    assert store.get(selected)["sources"] == [key]


@pytest.mark.parametrize("contract", [contract for contract in TOOL_SCHEMAS if contract.collection],
                         ids=lambda contract: contract.name)
def test_every_declared_collection_selects_its_rows_and_excludes_them_from_metadata(contract):
    rows = [{"name": "样本", "value": 5}]
    payload = {contract.collection: rows, "data_as_of": "2026-09-18", "source": "synthetic"}
    if contract.collection != "items":
        payload["items"] = [{"name": "无关集合", "value": 99}]
    store = EvidenceStore()
    key = store.add(tool=contract.name, payload=payload, state="ok", arguments={})
    assert store.get(key)["rows"] == rows
    assert contract.collection not in store.view(key)["metadata"]
    assert store.view(key)["metadata"]["data_as_of"] == "2026-09-18"
    selected = store.compute(Compute(evidence_id=key, sort_by="value", limit=1))
    assert render_answer(table(selected, [("name", "名称"), ("value", "数值")]), store).endswith("| 样本 | 5 |")


@pytest.mark.parametrize("collection_value", [None, {}, "[]", [None], ["not a row"]])
def test_malformed_declared_collection_is_rejected_even_with_another_valid_list(collection_value):
    payload = {"indices": collection_value, "items": [{"name": "unrelated"}]}
    store = EvidenceStore()
    with pytest.raises(ValueError, match="Invalid market_index_trend collection"):
        store.add(tool="market_index_trend", payload=payload, state="ok", arguments={})
    assert store.records == {}
    result = ToolResult(name="market_index_trend", input={}, output=payload,
                        trace_output=payload, summary="malformed synthetic output")
    with pytest.raises(ValueError, match="Invalid market_index_trend collection"):
        payload_of(result)


def test_missing_declared_collection_is_not_inferred_from_other_arrays():
    with pytest.raises(ValueError, match="indices must be an array"):
        EvidenceStore().add(tool="market_index_trend", payload={"items": []}, state="empty", arguments={})


@pytest.mark.parametrize("name", ["market_index_trend", "first_board_ratings", "stock_kline"])
def test_error_records_need_no_business_collection_and_never_supply_rows(name):
    result = ToolResult(name=name, input={}, output={"items": [{"name": "untrusted"}]},
                        summary="failure", status="error", error="synthetic failure")
    store, key = store_result(result)
    assert store.get(key)["rows"] == []
    assert store.get(key)["result_state"] == "error"
    with pytest.raises(ValueError, match="not usable"):
        store.compute(Compute(evidence_id=key))


def test_first_board_raw_candidate_facts_are_flattened_without_preview_replacement():
    result = ToolResult(name="first_board_ratings", input={}, summary="ratings", output={
        "trade_date": "2026-09-18", "universe_count": 8, "snapshot_source": "published",
        "candidates": [{"facts": {"symbol": "600001", "name": "样本", "break_count": 2}, "score": 80}],
    }, trace_output={"top_candidates": []})
    store, key = store_result(result)
    assert store.get(key)["rows"] == [{"symbol": "600001", "name": "样本", "break_count": 2, "score": 80}]
    assert "candidates" not in store.view(key)["metadata"]
    assert "top_candidates" not in store.view(key)["metadata"]
    assert store.view(key)["metadata"]["universe_count"] == 8
    assert store.view(key)["metadata"]["snapshot_source"] == "published"


@pytest.mark.parametrize("raw", [{}, {"candidates": None}, {"candidates": [None]},
                                  {"candidates": [{"facts": None}]}])
def test_malformed_raw_ratings_are_not_converted_into_empty_candidates(raw):
    with pytest.raises(ValueError, match="Invalid first_board_ratings collection"):
        payload_of(ToolResult(name="first_board_ratings", input={}, output=raw, summary="bad ratings"))


def test_raw_list_tool_keeps_trace_scope_and_missing_data():
    result = ToolResult(name="daily_board_promotion", input={}, summary="partial rows",
                        output=[{"trade_date": "2026-09-18", "probability": .5}],
                        trace_output={"end_date": "2026-09-18", "data_missing": ["previous trading day"],
                                      "items": []}, result_status="partial")
    store, key = store_result(result)
    assert len(store.get(key)["rows"]) == 1
    assert store.view(key)["metadata"]["end_date"] == "2026-09-18"
    derived = store.compute(Compute(evidence_id=key))
    assert store.get(derived)["result_state"] == "partial"
    assert store.get(derived)["data_missing"] == ["previous trading day"]


def test_indices_truncation_and_missing_flags_survive_computation():
    store = EvidenceStore()
    key = store.add(tool="market_index_trend", state="ok", arguments={}, payload={
        "indices": [{"name": "样本", "return_pct": 1}], "matched_count": 3,
        "data_missing": ["second page"], "data_fresh": False,
    })
    derived = store.compute(Compute(evidence_id=key, sort_by="return_pct", limit=1))
    assert store.get(derived)["result_state"] == "partial"
    assert store.get(derived)["source_truncated"]
    assert store.get(derived)["data_missing"] == ["second page"]
    with pytest.raises(ValueError, match="Truncated source"):
        render_answer(table(derived, [("name", "名称")]), store)
    assert "样本" in render_answer(table(derived, [("name", "名称")], "partial"), store)


def test_contracts_match_existing_typed_output_collections():
    assert TOOL_COLLECTIONS["sector_stock_ranking"] == "items"
    assert TOOL_COLLECTIONS["rating_evaluation"] == "evaluations"
    assert TOOL_COLLECTIONS["post_limit_path"] == "path"
    assert TOOL_COLLECTIONS["compute_result"] == "items"
