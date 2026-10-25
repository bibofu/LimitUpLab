"""New selections and historical references have distinct, reviewed contracts."""

import json

import pytest

from app.agents.context_builder import build_context
from app.agents.reference_intent import ReferenceIntent, validated_reference_intent
from app.agents.runner.security import review_input_security
from app.agents.structured_references import references_from_committed_result, resolve_reference
from test_runner_shadow import ScriptedProvider
from test_runner_production import input_review
from test_structured_references import scoped_response


def context(query, intent=None, page=None):
    refs = references_from_committed_result("run-1", scoped_response())
    return build_context(system_prompt="只使用本轮证据", query=query,
        conversation={"active_references": refs}, working_messages=[], page_context=page,
        reference_intent=intent)


@pytest.mark.parametrize("query", [
    "查询今天按评分排序的第40只股票", "查询2026-10-26的首板名单，按代码排序给第2只",
    "不看上一份名单，改为600003", "第二只换成600003，忽略页面",
])
def test_new_selection_is_not_bound_to_old_list_or_page(query):
    result = context(query, {"kind": "none"}, {"symbol": "600002"})
    assert result.clarification is None and result.resolved_reference is None
    assert result.diagnostics["reference_resolution"] == {"status": "none"}
    assert result.messages[-1].content == query


def test_first_query_rank_and_unreviewed_rank_do_not_require_prior_reference():
    query = "查询今天排名第40只股票"
    assert resolve_reference(query, [], intent={"kind": "none"}) == {"status": "none"}
    assert resolve_reference("第二只", []) == {"status": "none"}


def test_historical_ordinal_uses_display_order_and_conflicting_page_clarifies():
    intent = {"kind": "entities", "quote": "第二只", "ordinal": 2}
    assert context("第二只最近走势", intent).resolved_reference["symbols"] == ["600001"]
    assert context("第二只最近走势", intent, {"symbol": "600002"}).clarification


@pytest.mark.parametrize("query", ["它最近走势如何", "刚才那只的风险"])
def test_unresolved_singular_never_expands_to_the_entire_batch(query):
    assert context(query, {"kind": "entities", "quote": query}).clarification
    batch = context("刚才那批股票", {"kind": "entities", "quote": "刚才那批股票", "all_entities": True})
    assert batch.resolved_reference["symbols"] == ["000002", "600001"]


def test_conditions_requery_cannot_inherit_output_symbols_or_override_current_date():
    query = "继续按上次条件，日期改成2026-10-26，按评分降序给新名单第2只"
    result = context(query, {"kind": "conditions", "quote": "继续按上次条件"}, {"symbol": "600002"})
    resolution = result.resolved_reference
    assert not result.clarification
    assert resolution["kind"] == "conditions" and resolution["symbols"] == []
    assert "symbols" not in resolution["query_scope"]["arguments"]
    assert "source_symbols" not in resolution and "trade_date" not in resolution
    assert resolution["query_scope"]["arguments"]["board_height"] == 1
    assert result.messages[-1].content == query
    assert "本轮明确的新日期、股票、排序和筛选条件始终覆盖" in result.messages[0].content


@pytest.mark.parametrize("intent", [
    {}, {"kind": "unknown"}, {"kind": "entities"}, {"kind": "none", "ordinal": 2},
    {"kind": "conditions", "quote": "沿用条件", "ordinal": 2},
    {"kind": "entities", "quote": "第二只", "ordinal": "2"},
    {"kind": "entities", "quote": "第二只", "ordinal": 2, "all_entities": True},
    {"kind": "conditions", "quote": "沿用条件", "all_entities": True},
])
def test_intent_schema_rejects_missing_or_contradictory_meaning(intent):
    with pytest.raises(ValueError):
        ReferenceIntent.model_validate(intent)


def test_nonverbatim_reference_quote_is_rejected():
    with pytest.raises(ValueError, match="exact excerpt"):
        validated_reference_intent({"kind": "entities", "quote": "上一次的第二只", "ordinal": 2}, "查询今天第40只")


def test_required_intent_uses_existing_bounded_schema_repair_without_extra_normal_round():
    missing = input_review()
    missing.tool_calls[0]["args"].pop("reference_intent")
    provider = ScriptedProvider([missing, input_review()])
    assert review_input_security(provider, message="查询今天第40只").reference_intent.kind == "none"
    assert len(provider.calls) == 2
    provider = ScriptedProvider([input_review(reference_intent={"kind": "entities", "quote": "第二只", "ordinal": 2})])
    assert review_input_security(provider, message="第二只最近走势").reference_intent.ordinal == 2
    assert len(provider.calls) == 1


def test_bad_reference_quote_fails_after_existing_two_attempts():
    bad = input_review(reference_intent={"kind": "entities", "quote": "第二只", "ordinal": 2})
    with pytest.raises(ValueError, match="Input security review failed"):
        review_input_security(ScriptedProvider([bad, bad]), message="查询今天第40只")


@pytest.mark.parametrize("query,intent,expected", [
    ("查询2026-10-26按代码排序第40只", {"kind": "none"}, None),
    ("第二只最近走势", {"kind": "entities", "quote": "第二只", "ordinal": 2}, ["600001"]),
    ("继续按上次条件，查询今天第2只", {"kind": "conditions", "quote": "继续按上次条件"}, []),
])
def test_graph_review_intent_reaches_every_context_without_an_extra_model_round(world, query, intent, expected):
    from durable_runtime_fixtures import execution_config, run_async
    from test_durable_agent import list_answer, runner, runtime_for, tool_call

    async def scenario():
        async with runtime_for(world) as runtime:
            first_request, first_control, history = world.start()
            first, _ = runner(first_request, ScriptedProvider([input_review(), tool_call(), list_answer()]), history=history)
            world.deliver(first_request, first_control, await runtime.run(first, first_control))
            first_control.execution.release()
            request, control, history = world.start(query)
            provider = ScriptedProvider([input_review(reference_intent=intent), tool_call(), list_answer()])
            execution, calls = runner(request, provider, history=history)
            result = await runtime.run(execution, control)
            assert result.status == "answered" and len(calls) == 1 and len(provider.calls) == 3
            for messages in provider.calls[1:]:
                data = json.loads(messages[1].content.split("\n", 1)[1])
                resolution = data["resolved_reference"]
                assert resolution == {"status": "none"} if expected is None else resolution["symbols"] == expected
            state = await runtime.graph.aget_state(execution_config(control.row, control.execution))
            assert state.values["reference_intent"]["kind"] == intent["kind"]
    run_async(scenario())


def test_pending_pre_intent_checkpoint_is_reviewed_once_before_resuming(world):
    from durable_runtime_fixtures import execution_config, run_async
    from test_durable_agent import list_answer, runner, runtime_for, tool_call
    from app.agents.runtime_errors import RecoverableRunError

    async def scenario():
        request, control, history = world.start("查询今天第40只")
        first, first_calls = runner(request, ScriptedProvider([
            input_review(), tool_call(), ConnectionError("pause before answer")]), history=history)
        async with runtime_for(world) as runtime:
            with pytest.raises(RecoverableRunError):
                await runtime.run(first, control)
            config = execution_config(control.row, control.execution)
            await runtime.graph.aupdate_state(config, {"reference_intent": None})
            provider = ScriptedProvider([input_review(), list_answer()])
            resumed, later_calls = runner(request, provider, history=history)
            assert (await runtime.run(resumed, control)).status == "answered"
            assert len(provider.calls) == 2 and len(first_calls) == 1 and later_calls == []
            state = await runtime.graph.aget_state(config)
            assert state.values["reference_intent"]["kind"] == "none"
    run_async(scenario())


from test_durable_agent import world  # shared isolated PostgreSQL fixture
