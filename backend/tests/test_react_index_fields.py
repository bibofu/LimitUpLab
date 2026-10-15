"""Offline index-field binding from the real review wire through table delivery."""

from datetime import date
import json

import pytest
from langchain_core.messages import AIMessage

from app.agents import tools as business_tools
from app.agents.react_runtime.contracts import Compute, Finish
from app.agents.react_runtime.display_fields import DISPLAY_FIELD_CATALOG
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import validate_delivery
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import AgentToolRegistry
from app.models import MarketIndexTrendFacts, MarketIndexTrendItem
from app.services.prompt_security import review_input


DATE = date(2026, 9, 18)
LABELS = {
    "name": "指数名称", "symbol": "指数代码", "return_pct": "区间涨幅（%）",
    "max_drawdown_pct": "最大回撤（%）", "start_date": "起始日期", "end_date": "截止日期",
    "start_close": "起始点位", "end_close": "截止点位", "positive_days": "上涨天数",
    "negative_days": "下跌天数", "source": "数据来源",
}


class Reviewer:
    def __init__(self, mappings):
        self.mappings = mappings
        self.schema = self.payload = None

    def generate_messages(self, messages, tools, **kwargs):
        self.schema = tools[0]["function"]["parameters"]
        self.payload = json.loads(messages[-1].content)
        return AIMessage(content="", tool_calls=[{
            "id": "index-review", "name": "submit_input_security_review", "args": {
                "decision": "allow", "signals": [], "reason": "scripted index-field semantics",
                "request_kind": "research", "context_mode": "standalone",
                "current_task": self.payload["user_message"],
                "date_scope": {"mode": "dates", "dates": [DATE.isoformat()]},
                "output_contract": {"mode": "table_only", "table_required": True,
                                    "field_requests": self.mappings},
            },
        }])


def field_wire(schema):
    def resolve(node):
        while "$ref" in node:
            node = schema["$defs"][node["$ref"].split("/")[-1]]
        return node
    output = resolve(schema["properties"]["output_contract"])
    return resolve(output["properties"]["field_requests"]["items"])["properties"]["field"]


def index_evidence(monkeypatch):
    items = [MarketIndexTrendItem(
        symbol=symbol, name=name, start_date=date(2026, 9, 14), end_date=DATE,
        start_close=100, end_close=100 + gain, return_pct=gain, max_drawdown_pct=-1.0,
        positive_days=3, negative_days=1, points=[], source="synthetic-index-source",
    ) for symbol, name, gain in [("000001", "上证样本", 1.0), ("399001", "深成样本", 3.0),
                                 ("399006", "创业样本", 2.0)]]
    facts = MarketIndexTrendFacts(requested_days=5, requested_end_date=DATE,
                                 data_as_of=DATE, data_fresh=True, indices=items)
    calls = []

    def collect(**kwargs):
        calls.append(kwargs)
        return facts

    monkeypatch.setattr(business_tools, "collect_market_index_trends", collect)
    registry = AgentToolRegistry.__new__(AgentToolRegistry)
    registry.events, registry.profile = [], "extended"
    store = EvidenceStore()
    gateway = ToolGateway(registry, store)
    args = gateway.validate({"name": "market_index_trend", "args": {"days": 5, "end_date": DATE.isoformat()}})
    result, payload, state = gateway.execute("market_index_trend", args)
    key = store.add(tool=result.name, payload=payload, state=state, arguments=result.input)
    assert calls == [{"days": 5, "end_date": DATE}]
    return store, key


@pytest.mark.parametrize("fields", [
    ["name", "return_pct"],
    ["symbol", "name", "return_pct", "max_drawdown_pct"],
    ["name", "start_date", "end_date", "start_close", "end_close", "return_pct",
     "max_drawdown_pct", "positive_days", "negative_days", "source"],
])
def test_index_fields_bind_in_review_enum_and_deliver_real_tool_rows(monkeypatch, fields):
    reviewer = Reviewer([{"requested": LABELS[field], "field": field} for field in fields])
    task = review_input(reviewer,
        message="截至2026-09-18，比较三大指数近5个交易日区间表现，按区间涨幅降序，只列" + "、".join(LABELS[field] for field in fields),
        anchor_date=DATE, timeout_seconds=5)
    allowed = next(choice["enum"] for choice in field_wire(reviewer.schema)["anyOf"] if "enum" in choice)
    assert set(fields) <= set(allowed)
    assert task.output_contract.fields == fields
    assert task.output_contract.field_resolution.status == "resolved"
    for field in fields:
        entry = reviewer.payload["display_field_catalog"][field]
        assert "market_index_trend" in entry["tools"]
        assert "指数" in entry["meaning"]

    store, root = index_evidence(monkeypatch)
    selected = store.compute(Compute(evidence_id=root, sort_by="return_pct", descending=True, limit=3))
    final = Finish(status="complete", answer="{{evidence_table}}", evidence_ids=[selected], table={
        "evidence_id": selected, "columns": [{"field": field, "label": LABELS[field]} for field in fields]})
    validate_delivery(final, task, store)
    answer = render_answer(final, store)
    assert answer.splitlines()[0] == "| " + " | ".join(LABELS[field] for field in fields) + " |"
    assert len(answer.splitlines()) == 5
    assert answer.index("深成样本") < answer.index("创业样本") < answer.index("上证样本")
    assert [row["return_pct"] for row in store.get(selected)["rows"]] == [3.0, 2.0, 1.0]


@pytest.mark.parametrize("requested", ["涨跌幅", "未明确口径的收益", "未知自定义指标"])
def test_ambiguous_or_unknown_metric_stays_unbound_instead_of_becoming_index_return(requested):
    reviewer = Reviewer([{"requested": "指数名称", "field": "name"},
                         {"requested": requested, "field": None}])
    task = review_input(reviewer, message="只列名称和" + requested, anchor_date=DATE, timeout_seconds=5)
    assert any(choice.get("type") == "null" for choice in field_wire(reviewer.schema)["anyOf"])
    assert task.output_contract.field_requests[1].field is None
    assert task.output_contract.field_resolution.status == "unresolved"
    with pytest.raises(ValueError, match="fields remain unresolved"):
        validate_delivery(Finish(status="complete", answer="{{evidence_table}}", table={
            "evidence_id": "unused", "columns": [{"field": "name", "label": "名称"}]}), task)


def test_index_catalog_only_declares_actual_scalar_fields_and_keeps_return_scope_explicit():
    properties = MarketIndexTrendItem.model_json_schema()["properties"]
    for field, entry in DISPLAY_FIELD_CATALOG.items():
        if "market_index_trend" in entry["tools"]:
            types = {item.get("type") for item in properties[field].get("anyOf", [properties[field]])}
            assert types <= {"string", "number", "integer", "boolean", "null"}
    assert "points" not in DISPLAY_FIELD_CATALOG
    assert "不是窗口区间涨跌幅" in DISPLAY_FIELD_CATALOG["change_pct"]["meaning"]
    assert "不是单日涨跌幅" in DISPLAY_FIELD_CATALOG["return_pct"]["meaning"]
