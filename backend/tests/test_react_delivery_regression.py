"""Data-backed delivery remains usable without the removed completion gates."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.react_runtime import runtime
from app.agents.react_runtime.contracts import Finish
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import TaskInterpretation, validate_delivery
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest


DATE = "2026-09-18"
ROWS = [{"symbol": f"60010{index}", "name": f"合成样本{index}",
         "trade_date": DATE, "amount": float(60 - index * 10)} for index in range(6)]
COLUMNS = [{"field": "symbol", "label": "代码"}, {"field": "name", "label": "名称"}]


def native(name, args, identifier):
    return {"name": name, "args": args, "id": identifier}


def reply(*calls):
    return AIMessage(content="", tool_calls=list(calls))


class Script:
    output_contract = {"mode": "prose_only", "table_required": False}

    def generate_messages(self, messages, tools, **kwargs):
        name = tools[0]["function"]["name"]
        if name == "submit_input_security_review":
            return reply(native(name, {
                "decision": "allow", "signals": [], "reason": "offline interpretation",
                "request_kind": "research", "context_mode": "standalone",
                "date_scope": {"mode": "dates", "dates": [DATE]},
                "current_task": json.loads(messages[-1].content)["user_message"],
                "output_contract": self.output_contract,
            }, "input-review"))
        if name == "submit_compliance_review":
            return reply(native(name, {"decision": "allow", "violations": [],
                                      "reason": "offline research answer"}, "output-review"))
        results = {message.tool_call_id: json.loads(message.content) for message in messages
                   if isinstance(message, ToolMessage)}
        return self.decide(results)


def execute(model, message):
    queries = []

    def query(**kwargs):
        queries.append(kwargs)
        payload = {"events": deepcopy(ROWS), "trade_date": DATE, "matched_count": len(ROWS),
                   "returned_count": len(ROWS), "source": "synthetic-delivery-fixture"}
        return ToolResult(name="limit_up_events", input=kwargs, output=payload, trace_output=payload,
                          summary="synthetic six-row collection", result_status="ok")

    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS,
                               is_enabled=lambda _: True, limit_up_events=query)
    response = runtime.run(AgentChatRequest(session_id="delivery-regression", message=message),
                           registry, model)
    assert response.task_status == "complete" and response.stop_reason == "answered"
    assert len(queries) == 1
    checks = [trace.output for trace in response.tool_results if trace.name == "react_answer_check"]
    assert len(checks) == 1 and checks[0]["passed"] is True
    return response


def test_multiple_prose_metrics_complete_without_updating_an_advisory_checklist():
    class Model(Script):
        def decide(self, results):
            if "query" not in results:
                return reply(native("limit_up_events", {"trade_date": DATE, "limit": 100}, "query"),
                    native("update_task", {"requirements": [{"id": "summary", "description": "数量及成交额",
                        "source_text": "给出数量和成交额合计", "status": "pending", "evidence_ids": []}]}, "note"))
            source = results["query"]["evidence_id"]
            if "sum" not in results:
                return reply(native("compute_result", {"evidence_id": source, "operation": "aggregate",
                    "aggregate": "sum", "metric": "amount"}, "sum"))
            assert results["sum"]["rows"][0]["value"] == 210
            return reply(native("finish", {"status": "complete", "answer": "查询日涨停6只，成交额合计210元。",
                "evidence_ids": [source, results["sum"]["evidence_id"]], "missing": []}, "finish"))

    response = execute(Model(), f"{DATE}的涨停数量和成交额合计是多少？不用表格。")
    assert "6只" in response.answer and "210元" in response.answer
    state = next(trace.output for trace in response.tool_results if trace.name == "react_execution")
    assert state["requirements"][0]["status"] == "pending"


def test_table_and_its_total_complete_as_separate_deliverables():
    class Model(Script):
        output_contract = {"mode": "freeform", "table_required": True,
            "field_requests": [{"requested": "代码", "field": "symbol"},
                               {"requested": "名称", "field": "name"}]}

        def decide(self, results):
            if "query" not in results:
                return reply(native("limit_up_events", {"trade_date": DATE, "limit": 100}, "query"))
            source = results["query"]["evidence_id"]
            return reply(native("finish", {"status": "complete", "answer": "{{evidence_table}}\n\n合计6只。",
                "table": {"evidence_id": source, "columns": COLUMNS},
                "evidence_ids": [source], "missing": []}, "finish"))

    response = execute(Model(), f"列出{DATE}涨停名单，表内只列代码名称，并在表外给出总数量。")
    assert response.answer.startswith("| 代码 | 名称 |")
    assert response.answer.count("| 60010") == 6 and response.answer.endswith("合计6只。")


def test_correct_rank_members_can_complete_without_selection_proof():
    class Model(Script):
        output_contract = {"mode": "table_only", "table_required": True,
            "field_requests": [{"requested": "代码", "field": "symbol"},
                               {"requested": "名称", "field": "name"}],
            "row_selection": {"requested": "成交额第4至6名", "offset": 3, "limit": 3}}

        def decide(self, results):
            if "query" not in results:
                return reply(native("limit_up_events", {"trade_date": DATE, "limit": 100}, "query"))
            source = results["query"]["evidence_id"]
            if "selected" not in results:
                return reply(native("compute_result", {"evidence_id": source,
                    "filters": [{"field": "amount", "operator": "le", "value": 30}],
                    "sort_by": "amount", "descending": True, "limit": 3}, "selected"))
            selected = results["selected"]["evidence_id"]
            return reply(native("finish", {"status": "complete", "answer": "{{evidence_table}}",
                "table": {"evidence_id": selected, "columns": COLUMNS},
                "evidence_ids": [selected], "missing": []}, "finish"))

    response = execute(Model(), f"{DATE}涨停股票按成交额降序取第4至6名，只列代码名称。")
    assert response.answer.splitlines()[2:] == [f'| {row["symbol"]} | {row["name"]} |' for row in ROWS[3:]]


def test_unresolved_legacy_proposal_does_not_block_a_valid_evidence_table():
    task = TaskInterpretation(output_contract={"mode": "table_only", "table_required": True,
                                              "fields": ["symbol", "obsolete_proposal"]})
    store = EvidenceStore()
    key = store.add(tool="fixture", payload={"items": ROWS}, state="ok", arguments={})
    final = Finish(status="complete", answer="{{evidence_table}}", evidence_ids=[key],
                   table={"evidence_id": key, "columns": COLUMNS})
    assert task.output_contract.field_resolution.status == "unresolved"
    validate_delivery(final, task, store)
    assert "600100" in render_answer(final, store)


@pytest.mark.parametrize("invalid", ["reference", "column"])
def test_delivery_still_rejects_unknown_evidence_and_absent_row_fields(invalid):
    store = EvidenceStore()
    key = store.add(tool="fixture", payload={"items": [{"symbol": "600001"}]}, state="ok", arguments={})
    final = Finish(status="complete", answer="{{evidence_table}}", evidence_ids=[key], table={
        "evidence_id": "ev_missing" if invalid == "reference" else key,
        "columns": [{"field": "symbol" if invalid == "reference" else "amount", "label": "数据"}]})
    with pytest.raises(ValueError, match="Unknown evidence_id|Table field missing"):
        render_answer(final, store)
