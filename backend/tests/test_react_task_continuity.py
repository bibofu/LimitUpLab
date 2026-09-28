"""Offline protocol regressions, not a measurement of real-model accuracy.

No golden fixtures, market database or API is used. Scripted semantic results
exercise plumbing and gates, not the quality of intent classification.
"""

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.react_runtime import runtime as runtime_module
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.context import HISTORY_CHAR_BUDGET, prepare_task_context
from app.agents.react_runtime.contracts import Finish
from app.agents.react_runtime.runtime import Run, run
from app.agents.react_runtime.task_contract import OutputContract, TaskInterpretation
from app.agents.react_runtime.task_contract import validate_delivery
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest, ChatSessionMemory, ChatSessionMessage
from app.services.prompt_security import PromptInjectionAssessment


@pytest.fixture(autouse=True)
def allow_compliance(monkeypatch):
    monkeypatch.setattr(runtime_module, "review_answer", lambda *args, **kwargs:
        ComplianceReview(decision="allow", violations=[], reason="offline protocol fixture"))


def call(name, arguments, key):
    return AIMessage(content="", tool_calls=[{"name": name, "args": arguments, "id": key}])


def registry(**methods):
    return SimpleNamespace(events=[], profile="test", schemas=lambda: TOOL_SCHEMAS,
                           is_enabled=lambda _: True, **methods)


def previous_turn(task, *, session="continuity", clarification="请补充查询对象。", fields=None):
    metadata = {"task_status": "clarify", "tool_results": []}
    if fields is not None:
        metadata = {"task_status": "complete", "tool_results": [
            {"name": "limit_up_events", "status": "success", "input": {
                "trade_date": "2026-08-12", "board_height": 1, "limit": 2},
             "output": {"old_fact": "DO_NOT_REUSE_HISTORICAL_VALUE_48217"}},
            {"name": "react_decision", "status": "success", "output": {"tool_calls": [
                {"name": "finish", "args": {"table": {"evidence_id": "old-evidence",
                    "columns": [{"field": field, "label": field} for field in fields]}}}]}},
        ]}
    return [ChatSessionMessage(message_id="old-user", session_id=session, role="user",
                content=task, created_at=datetime(2026, 8, 12, tzinfo=timezone.utc)),
            ChatSessionMessage(message_id="old-assistant", session_id=session, role="assistant",
                content=clarification, metadata=metadata,
                created_at=datetime(2026, 8, 12, tzinfo=timezone.utc))]


class PromptProbe:
    def __init__(self, context_mode="follow_up"):
        self.context_mode, self.security_prompts, self.agent_prompts = context_mode, [], []

    def generate_messages(self, messages, tools, **kwargs):
        prompt = [(message.type, message.content) for message in messages]
        if any(tool["function"]["name"] == "submit_input_security_review" for tool in tools):
            self.security_prompts.append(prompt)
            return call("submit_input_security_review", {
                "decision": "allow", "signals": [], "reason": "offline protocol fixture",
                "request_kind": "research", "context_mode": self.context_mode}, "review")
        self.agent_prompts.append(prompt)
        return call("finish", {"status": "clarify", "answer": "请补充查询条件。"}, "finish")


def probe(history, reply, *, context_mode="follow_up", memory=None):
    model = PromptProbe(context_mode)
    result = run(AgentChatRequest(session_id="continuity", message=reply), registry(), model,
                 history=history, memory=memory)
    assert result.stop_reason == "answered"
    assert len(model.security_prompts) == len(model.agent_prompts) == 1
    return model


@pytest.mark.parametrize("tasks,reply,clarification", [
    (("查这只股票的10日收益率。", "查这只股票的5日收益率。"),
     "回归甲。", "请补充股票名称或代码。"),
    (("找出两天均属于主板首板的股票。", "找出第二天新增的主板首板股票。"),
     "2026-08-12和2026-08-13。", "请补充需要比较的两个日期。"),
])
def test_security_review_receives_pending_task_when_user_only_fills_slot(tasks, reply, clarification):
    left, right = [probe(previous_turn(task, clarification=clarification), reply) for task in tasks]
    assert left.security_prompts != right.security_prompts, (
        "The same slot-only reply must be classified with its different pending task")


@pytest.mark.parametrize("reply", [
    "日期改成2026-08-13，其他不变。", "板数改成二板，其他不变。", "数量改成3只，其他不变。",
])
def test_parameter_edit_keeps_user_output_fields_available_to_semantic_review(reply):
    histories = [previous_turn("列出主板首板，只输出" + text + "。", fields=fields)
                 for text, fields in [("代码名称", ["symbol", "name"]), ("代码", ["symbol"])]]
    left, right = [probe(history, reply) for history in histories]
    assert left.security_prompts != right.security_prompts, (
        "Business query arguments are identical but the inherited delivery fields differ")


@pytest.mark.parametrize("task,reply", [
    ("查询回归甲截至2026-08-12的10日收益率。", "回归甲。"),
    ("列出2026-08-12和2026-08-13都属于主板首板的股票。", "这两个日期。"),
])
def test_resolved_current_task_is_available_to_react_without_historical_answers(monkeypatch, task, reply):
    monkeypatch.setattr(runtime_module, "review_input", lambda *args, **kwargs: PromptInjectionAssessment(
        decision="allow", reason="offline resolved task", request_kind="research",
        context_mode="follow_up", current_task=task))
    model = PromptProbe()
    run(AgentChatRequest(session_id="continuity", message=reply), registry(), model)
    prompt = json.dumps(model.agent_prompts, ensure_ascii=False)
    assert task in prompt, "ReAct must receive the reviewed effective task, not only the slot reply"


def test_foreign_session_pending_task_is_excluded_from_review_and_react():
    history = previous_turn("FOREIGN_SESSION_PENDING_TASK", session="another-session")
    foreign, absent = probe(history, "继续。"), probe([], "继续。")
    assert foreign.security_prompts == absent.security_prompts
    assert foreign.agent_prompts == absent.agent_prompts


def test_standalone_task_does_not_receive_unrelated_pending_task():
    left = probe(previous_turn("查询上一个未完成对象。"), "查询市场概况。", context_mode="standalone")
    right = probe(previous_turn("解释另一个行业。"), "查询市场概况。", context_mode="standalone")
    assert left.agent_prompts == right.agent_prompts


def test_follow_up_context_never_reuses_historical_market_fact_or_evidence_id():
    model = probe(previous_turn("列出代码名称。", fields=["symbol", "name"]), "数量改成3只。")
    prompts = json.dumps(model.security_prompts + model.agent_prompts, ensure_ascii=False)
    assert "DO_NOT_REUSE_HISTORICAL_VALUE_48217" not in prompts
    assert "old-evidence" not in prompts


TABLE = "| 代码 | 名称 |\n| --- | --- |\n| 600123 | 回归甲 |"


def run_table_protocol(monkeypatch, *, mode="table_only", damage=None, partial=False):
    """A scripted malformed first answer must use the real runtime repair path."""
    monkeypatch.setattr(runtime_module, "review_input", lambda *args, **kwargs: PromptInjectionAssessment(
        decision="allow", reason="offline output contract", request_kind="research",
        current_task="列出代码名称。", output_contract=OutputContract(
            mode=mode, fields=["symbol", "name"], table_required=True)))
    events, streamed_before_return = [], []

    class Model:
        answers = 0

        def generate_messages(self, messages, tools, **kwargs):
            observations = [json.loads(message.content) for message in messages
                            if isinstance(message, ToolMessage) and message.tool_call_id == "pool"]
            if not observations:
                return call("market_event_pool", {"event_type": "limit_up"}, "pool")
            self.answers += 1
            evidence_id = observations[-1]["evidence_id"]
            answer = "{{evidence_table}}"
            if partial:
                answer += "\n\n另一个来源未返回，其余名单尚未核实。"
            elif mode == "freeform":
                answer = "研究名单如下。\n\n" + answer
            columns = [{"field": "symbol", "label": "代码"}, {"field": "name", "label": "名称"}]
            payload = {"status": "partial" if partial else "complete", "answer": answer,
                "evidence_ids": [evidence_id], "missing": ["另一来源名单"] if partial else [],
                "table": {"evidence_id": evidence_id, "columns": columns}}
            if self.answers == 1:
                if damage == "prose":
                    payload["answer"] = "不应发布的额外标题\n\n" + answer
                elif damage == "column":
                    columns.append({"field": "board_height", "label": "板数"})
                elif damage == "no_table":
                    payload.pop("table")
                    payload["answer"] = "已经查到名单。"
            before = len(events)
            if kwargs.get("on_chunk"):
                # answer arrives before the rest of the function-call JSON, as in a real stream.
                chunk = '{"answer":' + json.dumps(payload["answer"], ensure_ascii=False) + ','
                kwargs["on_chunk"](SimpleNamespace(tool_call_chunks=[
                    {"index": 0, "name": "finish", "args": chunk}]))
            streamed_before_return.extend(events[before:])
            return call("finish", payload, f"finish-{self.answers}")

    def pool(**kwargs):
        return ToolResult(name="market_event_pool", input=kwargs, summary="synthetic protocol row",
            output={"items": [{"symbol": "600123", "name": "回归甲", "board_height": 2}],
                    "matched_count": 1, "returned_count": 1}, result_status="ok")

    model = Model()
    response = run(AgentChatRequest(session_id="continuity", message="只列代码名称。"),
                   registry(market_event_pool=pool), model,
                   answer_event=lambda kind, payload: events.append((kind, payload)))
    return response, model, events, streamed_before_return


@pytest.mark.parametrize("damage,mode", [
    ("prose", "table_only"), ("column", "table_only"),
    ("column", "freeform"), ("no_table", "table_only"),
])
def test_output_contract_violation_uses_one_repair_instead_of_accepting_or_silently_trimming(
        monkeypatch, damage, mode):
    response, model, _, _ = run_table_protocol(monkeypatch, damage=damage, mode=mode)
    assert model.answers == 2, "The malformed first draft must trigger the existing repair round"
    checks = [trace.output["passed"] for trace in response.tool_results if trace.name == "react_answer_check"]
    assert checks == [False, True]
    assert response.stop_reason == "answered" and response.task_status == "complete"
    assert response.answer == (("研究名单如下。\n\n" if mode == "freeform" else "") + TABLE)


def test_table_only_invalid_prose_is_never_published_even_if_later_reset(monkeypatch):
    _, _, events, before_return = run_table_protocol(monkeypatch, damage="prose")
    assert before_return == [], "Constrained answers must wait for contract validation"
    published = "".join(payload.get("delta", "") for kind, payload in events if kind == "answer_delta")
    assert "不应发布的额外标题" not in published


def test_valid_table_only_answer_is_rendered_once(monkeypatch):
    response, model, events, before_return = run_table_protocol(monkeypatch)
    assert model.answers == 1 and response.answer == TABLE
    assert before_return == []
    assert "".join(payload["delta"] for kind, payload in events if kind == "answer_delta") == TABLE


def test_table_only_does_not_hide_real_partial_delivery_gap(monkeypatch):
    response, model, _, _ = run_table_protocol(monkeypatch, partial=True)
    assert model.answers == 1 and response.task_status == "partial"
    assert response.answer == TABLE + "\n\n另一个来源未返回，其余名单尚未核实。"


def test_freeform_keeps_realtime_prose_and_still_limits_table_fields(monkeypatch):
    response, model, _, before_return = run_table_protocol(monkeypatch, mode="freeform")
    assert model.answers == 1 and response.answer == "研究名单如下。\n\n" + TABLE
    assert any(kind == "answer_delta" and payload["delta"] == "研究名单如下。\n\n"
               for kind, payload in before_return), "Preserve the BC-091 ordinary prose stream"


def memory(session="continuity"):
    now = datetime.now(timezone.utc)
    return ChatSessionMemory(session_id=session, owner_id="test-owner", memory_version="test",
        constraints=["以后名单只显示代码。"], summary="HISTORICAL_MARKET_SUMMARY_88412",
        research_goal="OLD_PENDING_RESEARCH", stock_symbols=["000987"],
        created_at=now, updated_at=now)


def test_only_same_session_structured_memory_reaches_semantic_review():
    current = probe([], "列出当前名单。", memory=memory())
    foreign = probe([], "列出当前名单。", memory=memory("another-session"))
    absent = probe([], "列出当前名单。")
    payload = json.loads(current.security_prompts[0][-1][1])
    assert payload["task_context"]["preferences"] == ["以后名单只显示代码。"]
    assert foreign.security_prompts == absent.security_prompts
    assert foreign.agent_prompts == absent.agent_prompts
    reference = payload["task_context"]["memory_reference"]
    assert reference["research_goal"] == "OLD_PENDING_RESEARCH" and reference["stock_symbols"] == ["000987"]
    assert "HISTORICAL_MARKET_SUMMARY_88412" not in json.dumps(current.security_prompts)
    assert "OLD_PENDING_RESEARCH" not in json.dumps(current.agent_prompts)
    assert "000987" not in json.dumps(current.agent_prompts)


def test_review_receives_trusted_anchor_and_separate_page_defaults(monkeypatch):
    monkeypatch.setattr(runtime_module, "current_query_reference_date", lambda: date(2026, 8, 14))
    model = PromptProbe()
    run(AgentChatRequest(session_id="continuity", message="查昨天的数据。",
                        trade_date=date(2026, 8, 11), symbol="600123"), registry(), model)
    payload = json.loads(model.security_prompts[0][-1][1])
    assert payload["trusted_defaults"] == {"anchor_date": "2026-08-14",
        "page": {"trade_date": "2026-08-11", "symbol": "600123"}}


def test_preference_confirmation_does_not_require_a_table_or_market_query(monkeypatch):
    monkeypatch.setattr(runtime_module, "review_input", lambda *args, **kwargs: PromptInjectionAssessment(
        decision="allow", reason="only confirms future preference", request_kind="conversation",
        current_task="确认以后名单只列代码。", output_contract=OutputContract(
            mode="table_only", fields=["symbol"], table_required=False)))
    class Model:
        def generate_messages(self, *args, **kwargs):
            return call("finish", {"status": "complete", "answer": "以后名单只列代码。"}, "finish")
    result = run(AgentChatRequest(session_id="continuity", message="以后名单只列代码。"), registry(), Model())
    assert result.task_status == "complete" and result.tool_calls == []
    task = next(trace.output for trace in result.tool_results if trace.name == "react_task_context")
    assert task["output_contract"]["fields"] == ["symbol"]
    assert task["output_contract"]["mode"] == "table_only" and not task["output_contract"]["table_required"]


def test_pending_user_slot_cannot_be_guessed_from_available_dates_or_symbols(monkeypatch):
    monkeypatch.setattr(runtime_module, "review_input", lambda *args, **kwargs: PromptInjectionAssessment(
        decision="allow", reason="dates are missing", request_kind="research", context_mode="follow_up",
        current_task="比较用户指定两天的交集。", pending_slots=["需要比较的两个日期"]))
    class Model:
        calls = 0
        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            assert [tool["function"]["name"] for tool in tools] == ["finish"]
            if self.calls == 1:
                return call("market_event_pool", {"event_type": "limit_up", "trade_date": "2026-08-12"}, "guess")
            assert "Required user input is unresolved" in messages[-1].content
            return call("finish", {"status": "clarify", "answer": "请提供两个比较日期。"}, "finish")
    source = registry()
    source.events = [SimpleNamespace(trade_date=date(2026, 8, 12))]
    result = run(AgentChatRequest(session_id="continuity", message="比较那两天。"), source, Model())
    assert result.task_status == "clarify" and result.tool_calls == []


def test_task_trace_and_bounded_context_preserve_contract_without_old_answers():
    request = AgentChatRequest(session_id="continuity", message="日期改一下。")
    history = previous_turn("名单只列代码名称。", fields=["symbol", "name"])
    task = TaskInterpretation(current_task="名单只列代码名称。",
        output_contract=OutputContract(mode="table_only", fields=["symbol", "name"], table_required=True))
    history[-1].metadata["tool_results"].append({"name": "react_task_context", "output": task.model_dump()})
    context = prepare_task_context(request, history)
    assert context["previous_task"] == task.model_dump()
    assert "DO_NOT_REUSE" not in json.dumps(context)
    for index in range(20):
        history.insert(0, history[0].model_copy(update={"message_id": str(index), "content": "旧" * 13000}))
    oversized = prepare_task_context(request, history, memory())
    assert len(json.dumps(oversized, ensure_ascii=False)) <= HISTORY_CHAR_BUDGET
    assert oversized["omitted_oversized_context"]
    assert oversized["previous_task"] == task.model_dump()


def test_checkpoint_restores_task_contract_and_date_without_stringifying_models():
    request = AgentChatRequest(session_id="continuity", message="延续当前研究。")
    original = Run(request, registry(), object(), [], None)
    original.task_context = TaskInterpretation(current_task="只输出代码。", pending_slots=["目标日期"],
        output_contract=OutputContract(mode="table_only", fields=["symbol"], table_required=True))
    original.requires_current_evidence = False
    original.gateway.required_date = date(2026, 8, 12)
    snapshots = []
    original.control = SimpleNamespace(save=snapshots.append)
    original.save({"messages": [], "done": False}, "agent")
    assert isinstance(snapshots[0]["runtime"]["task_context"], dict)
    restored = Run(request, registry(), object(), [], None)
    restored.control = SimpleNamespace(row={"checkpoint_json": json.dumps(snapshots[0])})
    assert restored.restore()["resume_node"] == "agent"
    assert restored.task_context == original.task_context
    assert restored.gateway.required_date == date(2026, 8, 12)
    assert restored.requires_current_evidence is False


@pytest.mark.parametrize("fields", [["name", "symbol"], ["symbol"], ["symbol", "name", "score"]])
def test_explicit_field_order_and_membership_are_both_enforced(fields):
    final = Finish(status="complete", answer="{{evidence_table}}", table={"evidence_id": "test",
        "columns": [{"field": field, "label": field} for field in fields]})
    with pytest.raises(ValueError, match="Table fields"):
        validate_delivery(final, TaskInterpretation(output_contract=OutputContract(fields=["symbol", "name"])))


def test_unspecified_raw_field_names_do_not_reject_valid_tool_columns():
    final = Finish(status="complete", answer="{{evidence_table}}", table={"evidence_id": "test",
        "columns": [{"field": "pct_change", "label": "涨跌幅"}]})
    validate_delivery(final, TaskInterpretation(current_task="只列涨跌幅。",
        output_contract=OutputContract(mode="table_only", table_required=True)))


@pytest.mark.parametrize("scope", ["follow_up", "standalone"])
def test_compressed_memory_can_resolve_follow_up_but_never_enters_react_directly(scope):
    saved = memory()
    saved.constraints = ["用简洁中文回答，仅使用本地来源。"]
    saved.research_goal = "跟踪最近10个交易日表现"
    class Model(PromptProbe):
        def generate_messages(self, messages, tools, **kwargs):
            if tools[0]["function"]["name"] == "submit_input_security_review":
                payload = json.loads(messages[-1].content)
                assert payload["task_context"]["memory_reference"]["stock_symbols"] == ["000987"]
                assert payload["task_context"]["memory_reference"]["research_goal"] == saved.research_goal
                assert payload["task_context"]["preferences"] == saved.constraints
                task = "查询000987最近10个交易日表现。" if scope == "follow_up" else "查询新的市场概况。"
                return call("submit_input_security_review", {"decision": "allow", "signals": [],
                    "reason": "scripted interpretation", "request_kind": "research", "context_mode": scope,
                    "current_task": task + "用简洁中文回答，仅使用本地来源。"}, "review")
            return super().generate_messages(messages, tools, **kwargs)
    model = Model(scope)
    run(AgentChatRequest(session_id="continuity", message="它呢？" if scope == "follow_up" else "新的市场概况。"),
        registry(), model, memory=saved)
    text = json.dumps(model.agent_prompts, ensure_ascii=False)
    assert ("000987" in text) == (scope == "follow_up")
    assert ("最近10个交易日表现" in text) == (scope == "follow_up")
    assert "用简洁中文回答，仅使用本地来源。" in text
    assert "HISTORICAL_MARKET_SUMMARY_88412" not in text


@pytest.mark.parametrize("symbol", [None, "600123"])
def test_page_stock_is_available_for_semantic_reference_resolution(symbol):
    model = PromptProbe()
    run(AgentChatRequest(session_id="continuity", message="这只股票近10日怎么样？", symbol=symbol),
        registry(), model)
    payload = json.loads(model.security_prompts[0][-1][1])
    assert payload["trusted_defaults"]["page"]["symbol"] == symbol
    assert payload["user_message"] == "这只股票近10日怎么样？"
    instruction = model.security_prompts[0][0][1]
    assert "没有唯一标的才澄清" in instruction and "单个页面默认日期不能臆造" in instruction
