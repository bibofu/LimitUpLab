"""Scripted full-graph checks for truthful completion and rank-window repair."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.react_runtime import runtime
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest


DATE = "2026-09-18"
ROWS = [{"symbol": f"60010{index}", "name": f"合成样本{index}",
         "trade_date": DATE, "amount": float(60 - index * 10)} for index in range(6)]
OBLIGATION = {"id": "events", "description": "查询指定日期的涨停事件数量",
              "source_text": "查询2026-09-18的涨停事件数量", "status": "pending", "evidence_ids": []}


def native(name, args, identifier):
    return {"name": name, "args": args, "id": identifier}


def reply(*calls):
    return AIMessage(content="", tool_calls=list(calls))


def observed(messages):
    return {message.tool_call_id: json.loads(message.content) for message in messages
            if isinstance(message, ToolMessage)}


class Script:
    """Run real input/output review adapters with isolated structured decisions."""

    output_contract = {"mode": "prose_only", "table_required": False}

    def __init__(self):
        self.rounds = 0

    def generate_messages(self, messages, tools, **kwargs):
        name = tools[0]["function"]["name"]
        if name == "submit_input_security_review":
            return reply(native(name, {
                "decision": "allow", "signals": [], "reason": "offline scripted interpretation",
                "request_kind": "research", "context_mode": "standalone",
                "date_scope": {"mode": "dates", "dates": [DATE]},
                "current_task": json.loads(messages[-1].content)["user_message"],
                "output_contract": self.output_contract,
            }, "input-review"))
        if name == "submit_compliance_review":
            return reply(native(name, {"decision": "allow", "violations": [],
                                      "reason": "offline research-only answer"}, "output-review"))
        self.rounds += 1
        return self.decide(messages, observed(messages))


def execute(model, *, message=OBLIGATION["source_text"], events=None):
    queries = []

    def query(**kwargs):
        queries.append(kwargs)
        payload = {"events": deepcopy(ROWS), "trade_date": DATE, "matched_count": len(ROWS),
                   "returned_count": len(ROWS), "source": "synthetic-completion-fixture"}
        return ToolResult(name="limit_up_events", input=kwargs, output=payload, trace_output=payload,
                          summary="synthetic six-row collection", result_status="ok")

    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS,
                               is_enabled=lambda _: True, limit_up_events=query)
    response = runtime.run(AgentChatRequest(session_id="completion-integration", message=message),
                           registry, model,
                           answer_event=(lambda name, payload: events.append((name, payload)))
                           if events is not None else None)
    return response, queries


def query_and_record_requirement():
    return reply(native("limit_up_events", {"trade_date": DATE, "limit": 100}, "query"),
                 native("update_task", {"requirements": [deepcopy(OBLIGATION)]}, "register"))


def satisfied(key):
    return {**OBLIGATION, "status": "satisfied", "evidence_ids": [key]}


def completed(key, identifier):
    return reply(native("finish", {"status": "complete", "answer": "指定日期共查询到6条涨停事件。",
                                   "evidence_ids": [key], "missing": []}, identifier))


def execution(response):
    return next(trace.output for trace in response.tool_results if trace.name == "react_execution")


def test_pending_recorded_requirement_blocks_complete_then_can_be_satisfied():
    class Model(Script):
        def decide(self, messages, results):
            if self.rounds == 1:
                return query_and_record_requirement()
            key = results["query"]["evidence_id"]
            if self.rounds == 2:
                assert results["register"]["requirements"][0]["status"] == "pending"
                return completed(key, "premature-finish")
            if self.rounds == 3:
                assert "recorded requirements remain pending" in messages[-1].content
                return reply(native("update_task", {"requirements": [satisfied(key)]}, "satisfy"))
            assert results["satisfy"]["requirements"][0]["status"] == "satisfied"
            return completed(key, "valid-finish")

    model = Model()
    response, queries = execute(model)
    assert response.task_status == "complete" and response.stop_reason == "answered"
    assert model.rounds == 4 and len(queries) == 1
    checks = [trace.output for trace in response.tool_results if trace.name == "react_answer_check"]
    assert [check["passed"] for check in checks] == [False, True]
    state = execution(response)
    assert state["requirements"][0]["status"] == "satisfied"
    assert state["requirements"][0]["evidence_ids"] == [next(iter(state["evidence"]))]


@pytest.mark.parametrize("mutation,feedback", [
    ("missing-evidence", "requires current usable evidence"),
    ("unknown-evidence", "Unknown evidence_id"),
    ("description", "Cannot rewrite"),
    ("source_text", "Cannot rewrite"),
])
def test_invalid_requirement_update_is_rejected_without_mutation_and_then_repaired(mutation, feedback):
    class Model(Script):
        def decide(self, messages, results):
            if self.rounds == 1:
                return query_and_record_requirement()
            key = results["query"]["evidence_id"]
            if self.rounds == 2:
                proposed = satisfied(key)
                if mutation == "missing-evidence":
                    proposed["evidence_ids"] = []
                elif mutation == "unknown-evidence":
                    proposed["evidence_ids"] = ["ev_fabricated"]
                else:
                    proposed[mutation] = "换成另一项未要求的任务"
                return reply(native("update_task", {"requirements": [proposed]}, "invalid-update"))
            if self.rounds == 3:
                assert results["invalid-update"]["execution_status"] == "rejected"
                assert feedback in results["invalid-update"]["error"]
                return reply(native("update_task", {"requirements": [satisfied(key)]}, "repair-update"))
            assert results["repair-update"]["requirements"] == [satisfied(key)]
            return completed(key, "finish")

    model = Model()
    response, queries = execute(model)
    assert response.task_status == "complete" and response.stop_reason == "answered"
    assert model.rounds == 4 and len(queries) == 1
    requirement = execution(response)["requirements"][0]
    assert (requirement["description"], requirement["source_text"]) == (
        OBLIGATION["description"], OBLIGATION["source_text"])
    assert requirement["status"] == "satisfied"


def test_equal_row_count_with_wrong_rank_offset_is_rejected_then_recomputed():
    class Model(Script):
        output_contract = {"mode": "table_only", "table_required": True,
            "field_requests": [{"requested": "代码", "field": "symbol"},
                               {"requested": "名称", "field": "name"}],
            "row_selection": {"requested": "成交额降序第4至6名", "offset": 3, "limit": 3,
                              "sort_by": "amount", "descending": True}}

        def decide(self, messages, results):
            if self.rounds == 1:
                return reply(native("limit_up_events", {"trade_date": DATE, "limit": 100}, "query"))
            source = results["query"]["evidence_id"]
            if self.rounds in {2, 4}:
                offset = 0 if self.rounds == 2 else 3
                if offset:
                    assert "does not prove the requested row window" in messages[-1].content
                return reply(native("compute_result", {"evidence_id": source, "sort_by": "amount",
                    "descending": True, "offset": offset, "limit": 3}, "wrong" if offset == 0 else "correct"))
            selected = results["wrong" if self.rounds == 3 else "correct"]
            assert selected["row_count"] == 3
            return reply(native("finish", {"status": "complete", "answer": "{{evidence_table}}",
                "table": {"evidence_id": selected["evidence_id"], "columns": [
                    {"field": "symbol", "label": "代码"}, {"field": "name", "label": "名称"}]},
                "evidence_ids": [selected["evidence_id"]], "missing": []}, f"finish-{self.rounds}"))

    model, events = Model(), []
    response, queries = execute(model, message="2026-09-18涨停股票按成交额降序取第4至6名，只列代码名称。", events=events)
    assert response.task_status == "complete" and response.stop_reason == "answered"
    assert model.rounds == 5 and len(queries) == 1
    checks = [trace.output for trace in response.tool_results if trace.name == "react_answer_check"]
    assert [check["passed"] for check in checks] == [False, True]
    assert response.answer.splitlines()[2:] == [f'| {row["symbol"]} | {row["name"]} |' for row in ROWS[3:]]
    assert next(record for record in execution(response)["evidence"].values()
                if record["tool"] == "limit_up_events")["rows"] == ROWS
    assert events == [], "Invalid and repaired ranking windows remain private until persistence"
    assert all(row["symbol"] not in response.answer for row in ROWS[:3])
