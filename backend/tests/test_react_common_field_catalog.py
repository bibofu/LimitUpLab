"""Common display semantics must survive the review-to-evidence delivery boundary."""

from dataclasses import asdict
from datetime import date
import json

import pytest
from langchain_core.messages import AIMessage

from app.agents.react_runtime.contracts import Compute, Finish
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import validate_delivery
from app.collectors.hithink_finance_collector import HithinkHotStockFact
from app.collectors.market_index_collector import _trend_from_rows
from app.models import StockKLineBar
from app.services.prompt_security import review_input


class Reviewer:
    def __init__(self, fields):
        self.fields = fields
        self.payload = self.schema = None

    def generate_messages(self, messages, tools, **kwargs):
        self.payload = json.loads(messages[-1].content)
        self.schema = tools[0]["function"]["parameters"]
        return AIMessage(content="", tool_calls=[{
            "id": "common-fields", "name": "submit_input_security_review", "args": {
                "decision": "allow", "signals": [], "reason": "scripted semantic review",
                "request_kind": "research", "context_mode": "standalone",
                "current_task": self.payload["user_message"], "date_scope": {"mode": "unspecified"},
                "output_contract": {"mode": "table_only", "table_required": True,
                    "field_requests": [{"requested": label, "field": field} for field, label in self.fields]},
            },
        }])


def deliver(fields, store, key, *, question):
    reviewer = Reviewer(fields)
    task = review_input(reviewer, message=question, timeout_seconds=5)
    assert task.output_contract.fields == [field for field, _ in fields]
    assert task.output_contract.field_resolution.status == "resolved"
    field_schema = reviewer.schema["$defs"]["ReviewFieldRequest"]["properties"]["field"]
    allowed = next(item["enum"] for item in field_schema["anyOf"] if "enum" in item)
    assert set(task.output_contract.fields) <= set(allowed)
    for field, _ in fields:
        assert field in reviewer.payload["display_field_catalog"]
    final = Finish(status="complete", answer="{{evidence_table}}", evidence_ids=[key], table={
        "evidence_id": key, "columns": [{"field": field, "label": label} for field, label in fields]})
    validate_delivery(final, task, store)
    return render_answer(final, store)


def hot_store():
    rows = [asdict(HithinkHotStockFact(
        symbol=f"60000{rank}", thscode=f"60000{rank}.SH", name=f"样本{rank}",
        rank=rank, heat=1000 - rank, rank_change=rank - 4,
        rank_trend="up" if rank > 4 else "flat" if rank == 4 else "down",
    )) for rank in range(1, 11)]
    store = EvidenceStore()
    key = store.add(tool="hot_stock_ranking", state="ok", arguments={"limit": 10},
                    payload={"items": rows, "count": 10, "complete": True})
    return store, key


@pytest.mark.parametrize("operation", ["top", "window", "intersection"])
def test_popularity_fields_retain_provider_rank_after_collection_operations(operation):
    store, key = hot_store()
    if operation == "window":
        key = store.compute(Compute(evidence_id=key, offset=3, limit=3, sort_by="rank", descending=False))
    elif operation == "intersection":
        other = store.add(tool="limit_up_events", state="ok", arguments={}, payload={
            "events": [{"symbol": "600002"}, {"symbol": "600006"}], "matched_count": 2})
        key = store.compute(Compute(evidence_id=key, operation="intersection", other_id=other))
    fields = [("rank", "人气排名"), ("symbol", "代码"), ("name", "名称"),
              ("rank_change", "排名变化"), ("rank_trend", "排名趋势")]
    answer = deliver(fields, store, key, question="列出人气榜及排名变化和趋势，保留原排名。")
    rows = store.get(key)["rows"]
    assert len(answer.splitlines()) == len(rows) + 2
    assert [row["rank"] for row in rows] == ({"top": list(range(1, 11)),
        "window": [4, 5, 6], "intersection": [2, 6]}[operation])
    assert "| 6 | 600006 | 样本6 | 2 | 上升 |" in answer


def test_index_daily_change_is_not_window_return_and_both_bind_explicitly():
    item = _trend_from_rows(name="样本指数", display_symbol="000001", requested_end_date=date(2026, 9, 18),
        days=3, source="synthetic-index", rows=[{"date": "2026-09-16", "close": 100},
        {"date": "2026-09-17", "close": 110}, {"date": "2026-09-18", "close": 99}])
    assert item.change_pct == -10 and item.return_pct == -1
    store = EvidenceStore()
    key = store.add(tool="market_index_trend", state="ok", arguments={},
                    payload={"indices": [item.model_dump(mode="json")]})
    answer = deliver([("name", "指数"), ("end_close", "收盘点位"), ("change_pct", "当日涨跌幅"),
        ("return_pct", "区间涨跌幅")], store, key, question="列出指数今天收盘点位、当日涨跌幅及近3日区间涨跌幅。")
    assert "| 样本指数 | 99.0 | -10.0 | -1.0 |" in answer


def test_stock_daily_price_and_nullable_percentage_bind_without_inventing_values():
    bar = StockKLineBar(trade_date=date(2026, 9, 18), open=9, high=10, low=8, close=9.5,
                       volume=100, amount=95000, source="synthetic-kline")
    store = EvidenceStore()
    key = store.add(tool="stock_kline", state="partial", arguments={}, payload={
        "bars": [bar.model_dump(mode="json")], "data_missing": ["previous close unavailable"]})
    answer = deliver([("trade_date", "日期"), ("close", "收盘价"), ("change_pct", "涨跌幅"),
        ("amount", "成交额")], store, key, question="逐日列出日期、收盘价、涨跌幅和成交额。")
    assert "| 2026-09-18 | 9.5 | 缺失 | 95000.0 |" in answer


def test_catalog_entry_does_not_make_an_absent_evidence_column_available():
    store, key = hot_store()
    with pytest.raises(ValueError, match="Table field missing"):
        deliver([("close", "收盘价")], store, key, question="只列收盘价。")


def test_unknown_requested_metric_is_still_blocked_after_catalog_expansion():
    task = review_input(Reviewer([("name", "名称"), (None, "未定义的自创收益")]),
                        message="只列名称和未定义的自创收益。", timeout_seconds=5)
    assert task.output_contract.field_resolution.status == "unresolved"
    with pytest.raises(ValueError, match="fields remain unresolved"):
        validate_delivery(Finish(status="complete", answer="完成"), task)
