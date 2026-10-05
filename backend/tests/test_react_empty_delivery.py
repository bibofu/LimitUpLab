"""Task completion distinguishes a negative answer from an empty requested list."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.react_runtime import runtime
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.contracts import Finish
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest
from app.services.prompt_security import PromptInjectionAssessment


@pytest.mark.parametrize("question,answer,status", [
    ("样本甲在2026-09-22是否有首板评级记录？仅回答有无。",
     "样本甲在2026-09-22无首板评级记录。", "complete"),
    ("样本甲在2026-09-22共有几条首板评级记录？",
     "样本甲在2026-09-22的首板评级记录数为0。", "complete"),
    ("列出样本甲在2026-09-22的首板评级记录。",
     "该日期和对象没有匹配的首板评级记录。", "empty"),
])
def test_same_valid_empty_evidence_supports_different_user_deliverables(monkeypatch, question, answer, status):
    monkeypatch.setattr(runtime, "review_input", lambda *args, **kwargs: PromptInjectionAssessment(
        decision="allow", signals=[], reason="fixture", request_kind="research", current_task=question))
    monkeypatch.setattr(runtime, "review_answer", lambda *args, **kwargs: ComplianceReview(
        decision="allow", violations=[], reason="fixture"))

    def ratings(**kwargs):
        return ToolResult(name="first_board_ratings", input=kwargs, summary="有效定向空结果",
                          result_status="empty", output={"trade_date": "2026-09-22", "candidates": [],
                                                          "filtered_out": [], "universe_count": 10})

    class Model:
        def generate_messages(self, messages, tools, **kwargs):
            observations = [message for message in messages if isinstance(message, ToolMessage)]
            if not observations:
                tool, args = "first_board_ratings", {"trade_date": "2026-09-22", "symbols": ["600001"]}
            else:
                observation = json.loads(observations[-1].content)
                assert observation["result_state"] == "empty"
                tool, args = "finish", {"status": status, "answer": answer,
                                        "evidence_ids": [observation["evidence_id"]], "missing": []}
            return AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": tool}])

    registry = SimpleNamespace(events=[], profile="test", schemas=lambda: TOOL_SCHEMAS,
                               is_enabled=lambda _: True, first_board_ratings=ratings)
    response = runtime.run(AgentChatRequest(session_id="empty-deliverable", message=question), registry, Model())
    assert response.task_status == status and response.stop_reason == "answered"
    assert response.answer == answer
    checks = [trace.output for trace in response.tool_results if trace.name == "react_answer_check"]
    assert checks == [{"passed": True, "status": status, "missing": []}]


def test_finish_schema_exposes_task_status_semantics_to_native_models():
    from app.agents.react_runtime.evidence import EvidenceStore
    from app.agents.react_runtime.tools import ToolGateway

    registry = SimpleNamespace(schemas=lambda: [], is_enabled=lambda _: False)
    definition = next(item["function"] for item in ToolGateway(registry, EvidenceStore()).definitions()
                      if item["function"]["name"] == "finish")
    exposed = definition["parameters"]["properties"]["status"]
    assert exposed == Finish.model_json_schema()["properties"]["status"]
    assert "User-deliverable status" in exposed["description"]
    assert set(exposed["enum"]) == {"complete", "partial", "empty", "clarify", "refuse"}


@pytest.mark.parametrize("source_state,attempted_status,rejection", [
    ("error", "empty", "cannot finish as empty"),
    ("partial", "empty", "cannot finish as empty"),
    ("error", "complete", "cannot finish as complete using only error evidence"),
])
def test_unavailable_source_rejects_unsupported_absence_status(monkeypatch, source_state, attempted_status, rejection):
    monkeypatch.setattr(runtime, "review_input", lambda *args, **kwargs: PromptInjectionAssessment(
        decision="allow", signals=[], reason="fixture", request_kind="research"))
    monkeypatch.setattr(runtime, "review_answer", lambda *args, **kwargs: ComplianceReview(
        decision="allow", violations=[], reason="fixture"))

    def ratings(**kwargs):
        return ToolResult(name="first_board_ratings", input=kwargs, summary="来源不可用",
                          result_status=source_state, output={"trade_date": "2026-09-22", "candidates": [],
                                                             "data_missing": ["评级记录来源不可用"]})

    class Model:
        calls = 0

        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                tool, args = "first_board_ratings", {"trade_date": "2026-09-22", "symbols": ["600001"]}
            else:
                observation = json.loads(next(message.content for message in messages if isinstance(message, ToolMessage)))
                if self.calls == 2:
                    tool, args = "finish", {"status": attempted_status, "answer": "样本甲该日无首板评级记录。",
                                            "evidence_ids": [observation["evidence_id"]]}
                else:
                    assert rejection in messages[-1].content
                    tool, args = "finish", {"status": "partial", "answer": "评级来源不可用，暂时无法确定是否有记录。",
                                            "evidence_ids": [observation["evidence_id"]], "missing": ["核实是否有评级记录"]}
            return AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": str(self.calls)}])

    registry = SimpleNamespace(events=[], profile="test", schemas=lambda: TOOL_SCHEMAS,
                               is_enabled=lambda _: True, first_board_ratings=ratings)
    response = runtime.run(AgentChatRequest(session_id="unknown-existence", message="样本甲在2026-09-22是否有首板评级记录？"), registry, Model())
    assert response.task_status == "partial" and response.stop_reason == "answered"
    checks = [trace.output for trace in response.tool_results if trace.name == "react_answer_check"]
    assert checks[0]["passed"] is False and rejection in checks[0]["reason"]
    assert checks[1] == {"passed": True, "status": "partial", "missing": ["核实是否有评级记录"]}


def test_complete_count_is_not_blocked_by_unrelated_detail_gap_in_partial_evidence(monkeypatch):
    question = "截至2026-09-22收盘涨停共有多少只？只要总数。"
    monkeypatch.setattr(runtime, "review_input", lambda *args, **kwargs: PromptInjectionAssessment(
        decision="allow", signals=[], reason="fixture", request_kind="research", current_task=question))
    monkeypatch.setattr(runtime, "review_answer", lambda *args, **kwargs: ComplianceReview(
        decision="allow", violations=[], reason="fixture"))

    def summary(**kwargs):
        return ToolResult(name="market_summary", input=kwargs, summary="计数完整，行业明细缺失",
                          result_status="partial", output={"trade_date": "2026-09-22", "limit_up_count": 8,
                                                           "data_missing": ["未请求的行业分布明细不可用；涨停总数统计完整"]})

    class Model:
        def generate_messages(self, messages, tools, **kwargs):
            observations = [message for message in messages if isinstance(message, ToolMessage)]
            if not observations:
                tool, args = "market_summary", {}
            else:
                observation = json.loads(observations[-1].content)
                assert observation["result_state"] == "partial"
                tool, args = "finish", {"status": "complete", "answer": "截至2026-09-22收盘涨停共8只。",
                                        "evidence_ids": [observation["evidence_id"]], "missing": []}
            return AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": tool}])

    registry = SimpleNamespace(events=[], profile="test", schemas=lambda: TOOL_SCHEMAS,
                               is_enabled=lambda _: True, market_summary=summary)
    response = runtime.run(AgentChatRequest(session_id="complete-count", message=question), registry, Model())
    assert response.task_status == "complete" and response.stop_reason == "answered"
    checks = [trace.output for trace in response.tool_results if trace.name == "react_answer_check"]
    assert checks == [{"passed": True, "status": "complete", "missing": []}]
