"""Overview facts must remain scoped, auditable, and usable without extra calls."""

from copy import deepcopy
from dataclasses import asdict
from datetime import date
from types import SimpleNamespace

import pytest

from app.agents.dragon_tiger_summary import build_dragon_tiger_overview
from app.agents.react_runtime.contracts import Compute, Finish
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import TaskInterpretation, validate_delivery
from app.agents.tools import AgentToolRegistry
from app.collectors.hithink_finance_collector import HithinkDragonTigerFact, HithinkDragonTigerSnapshot


def row(symbol="600001", days=1, net=100, org=None, hot=None):
    return {"symbol": symbol, "name": "测试" + symbol, "range_days": days,
            "net_buy_amount": net, "organization_net_buy_amount": org,
            "hot_money_net_buy_amount": hot}


def payload(items, **overrides):
    return {"trade_date": "2026-10-16", "source": "fixture", "board_type": "all",
            "stock_count": 99, "matched_count": len(items), "source_truncated": False,
            "items": items, **overrides}


def test_windows_and_disclosure_records_are_not_collapsed_or_mixed():
    original = payload([row(net=200), row(net=-100), row(days=3, net=900),
                        row("000002", days=None, net=50)])
    before = deepcopy(original)
    overview = build_dragon_tiger_overview(original)
    assert original == before
    assert overview["record_count"] == 4 and overview["distinct_stock_count"] == 2
    assert overview["trade_date"] == "2026-10-16" and overview["source"] == "fixture"
    single = overview["windows"]["1"]
    assert single["record_count"] == 2 and single["distinct_stock_count"] == 1
    flow = single["flows"]["net_buy_amount"]
    assert flow["positive_count"] == 1 and flow["negative_count"] == 1
    assert flow["sum_known_amount"] == 100
    assert flow["net_buy_leaders"]["records"][0]["amount"] == 200
    assert flow["net_sell_leaders"]["records"][0]["row_index"] == 1
    assert overview["windows"]["3"]["flows"]["net_buy_amount"]["net_buy_leaders"]["records"][0]["amount"] == 900
    assert overview["windows"]["unknown"]["range_days"] is None


def test_zero_missing_and_invalid_amounts_are_distinguished():
    overview = build_dragon_tiger_overview(payload([
        row(net=value) for value in [0, None, float("nan"), float("inf"), True, -30, 20]
    ]))
    flows = overview["windows"]["1"]["flows"]
    net = flows["net_buy_amount"]
    assert (net["available_count"], net["missing_count"], net["zero_count"]) == (3, 4, 1)
    assert net["net_buy_leaders"]["records"][0]["amount"] == 20
    assert net["net_sell_leaders"]["records"][0]["amount"] == -30
    institution = flows["organization_net_buy_amount"]
    assert institution["available_count"] == 0 and institution["missing_count"] == 7
    assert institution["zero_count"] == 0
    assert institution["sum_known_amount"] is None
    assert institution["net_buy_leaders"]["records"] == []


def test_more_net_selling_records_does_not_mean_a_negative_amount_sum():
    overview = build_dragon_tiger_overview(payload([
        row("600001", net=100), row("600002", net=-10), row("600003", net=-20),
        row("600001", days=3, net=-1000),
    ]))
    flow = overview["windows"]["1"]["flows"]["net_buy_amount"]
    assert flow["positive_count"] == 1 and flow["negative_count"] == 2
    assert flow["sum_known_amount"] == 70
    assert overview["windows"]["3"]["flows"]["net_buy_amount"]["sum_known_amount"] == -1000


def test_unknown_windows_preserve_records_without_claiming_comparable_amount_sum():
    original = payload([row(days=None, net=100, org=20), row(days=None, net=-30, hot=10)])
    overview = build_dragon_tiger_overview(original, query="测试")
    window = overview["windows"]["unknown"]
    assert window["record_count"] == 2
    assert window["flows"]["net_buy_amount"]["positive_count"] == 1
    assert window["flows"]["net_buy_amount"]["negative_count"] == 1
    assert all(flow["sum_known_amount"] is None for flow in window["flows"].values())
    original["overview"] = overview
    store = EvidenceStore()
    key = store.add(tool="dragon_tiger_list", payload=original, state="ok", arguments={})
    assert store.get(key)["rows"] == original["items"]


def test_top_five_direction_ties_and_record_provenance():
    items = [row(f"60000{i}", net=100) for i in range(6, 0, -1)]
    overview = build_dragon_tiger_overview(payload(items))
    net = overview["windows"]["1"]["flows"]["net_buy_amount"]
    assert net["net_sell_leaders"]["records"] == []
    assert net["net_buy_leaders"]["omitted_cutoff_ties"] == 1
    ranked = net["net_buy_leaders"]["records"]
    assert [item["symbol"] for item in ranked] == [f"60000{i}" for i in range(1, 6)]
    assert all(items[item["row_index"]]["symbol"] == item["symbol"] for item in ranked)


@pytest.mark.parametrize("bounds", [{"source_truncated": True}, {"matched_count": 10}])
def test_truncated_or_filtered_scope_never_inherits_upstream_stock_count(bounds):
    overview = build_dragon_tiger_overview(payload([row()], board_type="org", **bounds), query="600001")
    assert not overview["complete_matching_query"]
    assert overview["scope"] == "returned_records"
    assert overview["record_count"] == overview["distinct_stock_count"] == 1
    assert overview["query"] == "600001" and overview["board_type"] == "org"


def test_empty_result_is_not_missing_interval_or_zero_institution_data():
    overview = build_dragon_tiger_overview(payload([]))
    assert overview["complete_matching_query"]
    assert overview["record_count"] == overview["distinct_stock_count"] == 0
    assert overview["windows"] == {}


def test_full_overview_visible_while_original_records_remain_computable_and_renderable():
    original = payload([row(f"6000{i:02}", net=i * 100) for i in range(1, 13)])
    original["overview"] = build_dragon_tiger_overview(original)
    store = EvidenceStore()
    key = store.add(tool="dragon_tiger_list", payload=original, state="ok", arguments={})
    view = store.view(key)
    assert len(view["rows"]) == 8 and view["row_count"] == 12
    assert view["metadata"]["overview"] == original["overview"]
    assert len(view["metadata"]["overview"]["windows"]["1"]["flows"]["net_buy_amount"]["net_buy_leaders"]["records"]) == 5
    # An explicitly requested custom range still operates on ALL original rows.
    selected = store.compute(Compute(evidence_id=key, sort_by="net_buy_amount", offset=5, limit=3))
    assert [r["symbol"] for r in store.get(selected)["rows"]] == ["600007", "600006", "600005"]
    final = Finish(status="complete", answer="{{evidence_table}}", evidence_ids=[selected], table={
        "evidence_id": selected, "columns": [{"field": "symbol", "label": "代码"},
                                              {"field": "net_buy_amount", "label": "净买额"}]})
    validate_delivery(final, TaskInterpretation(output_contract={"table_required": True}), store)
    assert "| 600007 | 700 |" in render_answer(final, store)
    assert store.get(key)["rows"] == original["items"]


@pytest.mark.parametrize("snapshot_date", [date(2026, 10, 16), None])
def test_snapshot_date_can_be_rendered_without_losing_windows_or_inventing_dates(monkeypatch, snapshot_date):
    facts = [HithinkDragonTigerFact(
        symbol="600001", thscode="600001.SH", name="测试", change_pct=1,
        buy_amount=200, sell_amount=100, net_buy_amount=100, net_rate=None,
        organization_net_buy_amount=None, hot_money_net_buy_amount=None,
        hot_rank=None, range_days=days, limit_reason=None, concepts=[],
    ) for days in (1, 3)]
    snapshot = HithinkDragonTigerSnapshot(snapshot_date, "all", 1, facts)
    monkeypatch.setattr("app.agents.tools.collect_preferred_dragon_tiger_snapshot", lambda **_: snapshot)
    result = AgentToolRegistry.dragon_tiger_list(
        SimpleNamespace(hithink_collector=object()), trade_date=date(2026, 10, 16),
    )
    expected = snapshot_date.isoformat() if snapshot_date else None
    assert [item["trade_date"] for item in result.output["items"]] == [expected, expected]
    for actual, fact in zip(result.output["items"], facts):
        assert {key: value for key, value in actual.items() if key != "trade_date"} == asdict(fact)
    if snapshot_date:
        store = EvidenceStore()
        key = store.add(tool=result.name, payload=result.output, state="ok", arguments=result.input)
        final = Finish(status="complete", answer="{{evidence_table}}", evidence_ids=[key], table={
            "evidence_id": key, "columns": [{"field": "trade_date", "label": "榜单日期"},
                                              {"field": "range_days", "label": "区间天数"}]})
        contract = TaskInterpretation(output_contract={"table_required": True,
                                                      "fields": ["trade_date", "range_days"]})
        validate_delivery(final, contract, store)
        answer = render_answer(final, store, contract.output_contract)
        assert "| 2026-10-16 | 1 |" in answer and "| 2026-10-16 | 3 |" in answer
        assert "数据未成功获取" not in answer
