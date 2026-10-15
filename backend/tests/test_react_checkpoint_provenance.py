"""Actual Run checkpoint recovery must preserve the evidence needed by its gate."""

import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.react_runtime.contracts import Compute, Finish
from app.agents.react_runtime.date_scope import DateScope
from app.agents.react_runtime.runtime import Run
from app.agents.react_runtime.task_contract import TaskInterpretation, update_requirements
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest


def test_saved_run_delivers_proven_rank_window_without_requerying_after_restore():
    dates = ["2026-09-18", "2026-09-21"]
    queries, reviews = [], []

    def events(**kwargs):
        queries.append(dict(kwargs))
        payload = {
            "events": [{"symbol": f"60000{index}", "name": f"合成样本{index}",
                        "trade_date": kwargs["trade_date"], "amount": float(100 - index)}
                       for index in range(8)],
            "trade_date": kwargs["trade_date"], "recent_trade_days": 1,
            "sort_by": "amount", "sort_order": "desc", "returned_count": 8,
            "matched_count": 10, "source": "synthetic-checkpoint-fixture",
        }
        return ToolResult(name="limit_up_events", input=kwargs, output=payload,
                          trace_output=payload, summary="synthetic ranked prefix", result_status="ok")

    class Provider:
        def generate_messages(self, messages, tools, **kwargs):
            assert tools[0]["function"]["name"] == "submit_compliance_review"
            reviews.append(json.loads(messages[-1].content))
            return AIMessage(content="", tool_calls=[{
                "name": "submit_compliance_review", "id": "review", "args": {
                    "decision": "allow", "violations": [], "reason": "offline research-only fixture"}}])

    class Checkpoint:
        row = {}

        def save(self, payload):
            self.row = {"checkpoint_json": json.dumps(payload, ensure_ascii=False)}

        def cancelled(self):
            return False

    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS,
                               is_enabled=lambda _: True, limit_up_events=events)
    request = AgentChatRequest(session_id="checkpoint-provenance", message=(
        "查询2026-09-18匹配数量，以及2026-09-21成交额降序第4至6名，名单只列代码和名称。"))
    provider = Provider()
    original = Run(request, registry, provider, [], None)
    original.gateway.date_scope = DateScope(mode="dates", dates=dates)
    original.task_context = TaskInterpretation(current_task=request.message, output_contract={
        "mode": "freeform", "fields": ["symbol", "name"], "table_required": True,
        "row_selection": {"requested": "成交额降序第4至6名", "offset": 3, "limit": 3,
                          "sort_by": "amount", "descending": True}})
    calls = [{"name": "limit_up_events", "id": day, "args": {
        "trade_date": day, "sort_by": "amount", "sort_order": "desc", "limit": 8}}
        for day in dates]
    state = {"messages": [HumanMessage(content=request.message), AIMessage(content="", tool_calls=calls)],
             "pending": calls}
    observations = original.tools_node(original.policy(state))
    state.update(original.observe({**state, **observations}))
    keys = {call["id"]: result["evidence_id"] for call, result in observations["observations"]}
    selected = original.evidence.compute(Compute(
        evidence_id=keys[dates[1]], sort_by="amount", descending=True, offset=3, limit=3))
    original.requirements = update_requirements([], [
        {"id": "count", "description": "回答9月18日匹配数量", "source_text": "查询2026-09-18匹配数量",
         "status": "satisfied", "evidence_ids": [keys[dates[0]]]},
        {"id": "window", "description": "交付9月21日指定排名名单", "source_text": "2026-09-21成交额降序第4至6名",
         "status": "satisfied", "evidence_ids": [selected]},
    ], original.evidence)
    final = Finish(status="complete", answer="合成数据中，2026-09-18匹配数量为10。\n\n{{evidence_table}}",
                   evidence_ids=[keys[dates[0]], selected], table={"evidence_id": selected, "columns": [
                       {"field": "symbol", "label": "代码"}, {"field": "name", "label": "名称"}]})
    state.update(finish=final.model_dump(mode="json"), done=False)
    checkpoint = Checkpoint()
    original.control = checkpoint
    original.save(state, "gate")

    restored = Run(request, registry, provider, [], None)
    restored.control = checkpoint
    resumed = restored.restore()
    assert resumed["resume_node"] == "gate"
    assert restored.gateway.date_scope == original.gateway.date_scope
    assert restored.gateway.required_date is None
    assert restored.task_context == original.task_context
    assert restored.requirements == original.requirements
    assert restored.evidence.complete_rank_scope(selected) == original.evidence.complete_rank_scope(selected)
    assert restored.evidence.row_selection_matches(selected, offset=3, limit=3,
                                                   sort_by="amount", descending=True)
    assert restored.gate(resumed) == {"done": True}
    assert restored.status == "complete" and restored.reason == "answered"
    assert restored.answer.splitlines()[-3:] == [
        "| 600003 | 合成样本3 |", "| 600004 | 合成样本4 |", "| 600005 | 合成样本5 |"]
    assert sorted(query["trade_date"] for query in queries) == dates
    assert restored.tools == original.tools == 2
    assert len(reviews) == 1 and reviews[0]["candidate_answer"] == restored.answer
    assert restored.traces[-1].name == "react_answer_check"
    assert restored.traces[-1].output["passed"] is True
