from copy import deepcopy
import json
import socket
import sqlite3

import pytest

from app.agent_eval.basic70 import DEFAULT_RECIPE, build_basic70, check_values, preflight_candidate
from app.agent_eval.loader import load_suite
from app.agents.tools import TOOL_SCHEMAS
from test_agent_eval_checks import no_external_calls


CASES = json.loads(DEFAULT_RECIPE.read_text(encoding="utf-8"))["cases"]


@pytest.fixture(autouse=True)
def no_business_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("candidate preflight must not access network or database")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)


@pytest.mark.parametrize("item", CASES, ids=[c["id"] for c in CASES])
def test_each_candidate_contract_and_gateway_replay(item):
    case, world, report = preflight_candidate(item)
    assert case.status == "candidate"
    assert report["fixture_valid"] and report["model_calls"] == 0
    assert not report["answer_quality_verified"]
    assert all(r.origin == "synthetic" for r in world.recordings)
    assert item["reference_answer"] != item["negative_answer"]


def test_expansion_exactly_fills_tool_gaps():
    existing = {"market_summary", "limit_up_events", "market_event_pool"}
    added = {c["tool"] for c in CASES}
    assert len(CASES) == 30
    assert len(added) == 23
    assert added | existing == {s.name for s in TOOL_SCHEMAS}


def test_reviewed_fields_follow_production_models_and_versioning():
    from app.models import DailyBoardPromotionStat, FirstBoardCriticResponse, WebSearchFacts
    from app.agent_eval.basic70 import candidate_assets

    for item in CASES:
        case, world, records = candidate_assets(item)
        assert case.case_version == case.world.version == world.world_version == item.get("version", 1)
        for record in records:
            payload = record["payload"]
            if record["tool"] == "web_search":
                WebSearchFacts.model_validate(payload)
                assert "items" not in payload
            elif record["tool"] == "first_board_critic":
                FirstBoardCriticResponse.model_validate(payload)
            elif record["tool"] == "daily_board_promotion":
                for row in payload:
                    stat = DailyBoardPromotionStat.model_validate(row)
                    assert stat.promoted_count == len(stat.promoted_stocks)
            elif record["tool"] == "first_board_ratings":
                from app.models import FirstBoardRatingsResponse
                from pydantic import TypeAdapter
                field = FirstBoardRatingsResponse.model_fields["snapshot_source"]
                TypeAdapter(field.annotation).validate_python(payload["snapshot_source"])
                assert "prediction_source" not in payload
                assert payload["candidates"][0]["facts"]["symbol"] == payload["top_candidates"][0]["symbol"]
            elif record["tool"] == "post_limit_path":
                from app.services.post_limit import _anchor_fact
                anchor = payload["anchor"]
                assert _anchor_fact({**anchor, "trade_date": anchor["anchor_date"]}) == anchor


def test_build_loadable_assets_and_do_not_promote(tmp_path):
    output = tmp_path / "bundle"
    report = build_basic70(output)
    assert report["new_offline_candidates"] == 30
    assert report["new_active_golden"] == report["new_live"] == 0
    for entry in report["cases"]:
        cases, worlds = load_suite([output / entry["case"]], [output / entry["world"]])
        assert cases[0].world.id == worlds[0].world_id
    with pytest.raises(FileExistsError):
        build_basic70(output)


def test_expected_values_are_not_silently_regenerated():
    changed = deepcopy(CASES[0])
    changed["payload"][0]["sample_size"] = 99
    with pytest.raises(ValueError, match="contradicts"):
        preflight_candidate(changed)
    with pytest.raises(KeyError):
        check_values({}, [{"path": ["missing"], "equals": None}])
    with pytest.raises(ValueError):
        check_values({"count": True}, [{"path": ["count"], "equals": 1}])


@pytest.mark.parametrize("case_id,version", [
    ("OFF-B002", 2), ("OFF-B026", 2), ("OFF-B028", 2), ("OFF-B030", 4),
])
def test_dev10_revision_keeps_rubric_and_examples_in_candidate(case_id, version):
    item = next(item for item in CASES if item["id"] == case_id)
    case, world, report = preflight_candidate(item)
    expected = case.assertions[0].expected
    assert case.case_version == world.world_version == version
    assert case.status == "candidate" and not report["answer_quality_verified"]
    assert expected["grading"] == item["grading"]
    assert expected["reference_answer"] == item["reference_answer"]
    assert expected["negative_answer"] == item["negative_answer"]
    assert "任务遗漏例" in expected["negative_answer"]
    assert "task_completion" in expected["grading"]
    assert "grounding" in expected["grading"]


def test_dev10_kline_checks_cover_each_requested_close_and_actual_latest_price():
    items = {item["id"]: item for item in CASES}
    full = {tuple(c["path"]): c["equals"] for c in items["OFF-B002"]["checks"]}
    assert [full[("bars", i, "trade_date")] for i in range(5)] == [
        "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11",
    ]
    assert [full[("bars", i, "close")] for i in range(5)] == [10, 10.25, 10.5, 10.75, 11]
    assert full[("return_5d_pct",)] is None
    stale = {tuple(c["path"]): c["equals"] for c in items["OFF-B026"]["checks"]}
    assert stale[("latest_close",)] == 10.75
    assert stale[("data_as_of",)] == "2026-09-10"
    assert stale[("data_fresh",)] is False


def test_dev10_output_fields_are_distinct_from_background_score():
    item = next(item for item in CASES if item["id"] == "OFF-B028")
    assert item["checks"] == [
        {"path": ["items", 0, "symbol"], "equals": "600001"},
        {"path": ["items", 0, "name"], "equals": "样例甲"},
    ]
    assert item["payload"]["items"][0]["score"] == 70
    assert item["reference_answer"] == "600001 样例甲"


@pytest.mark.parametrize("case_id,path,bad_value", [
    ("OFF-B002", ["bars", 2, "close"], 10.60),
    ("OFF-B002", ["bars", 2, "trade_date"], "2026-09-12"),
    ("OFF-B026", ["latest_close"], 10.80),
    ("OFF-B028", ["items", 0, "name"], "样例乙"),
    ("OFF-B030", ["top_candidates", 0, "confidence"], 0.7),
])
def test_dev10_new_checks_reject_wrong_fixture_values(case_id, path, bad_value):
    item = deepcopy(next(item for item in CASES if item["id"] == case_id))
    target = item["payload"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad_value
    with pytest.raises(ValueError, match="contradicts"):
        preflight_candidate(item)


def test_dev10_comparison_alternative_route_supplies_same_facts_without_ratings():
    from app.agent_eval.frozen_registry import FrozenAgentToolRegistry
    from app.agents.react_runtime.evidence import EvidenceStore
    from app.agents.react_runtime.tools import ToolGateway

    item = next(item for item in CASES if item["id"] == "OFF-B030")
    case, world, _ = preflight_candidate(item)
    registry = FrozenAgentToolRegistry(world)
    gateway = ToolGateway(registry, EvidenceStore())
    with registry.anchored():
        _, filtered, state = gateway.execute("first_board_filter", gateway.validate({
            "name": "first_board_filter", "args": {"query": "样例甲", "trade_date": "2026-09-11"}}))
        assert state == "ok"
        entity = filtered["items"][0]
        assert (entity["symbol"], entity["score"], entity["confidence"]) == ("600001", 70, 0.6)
        _, critic, state = gateway.execute("first_board_critic", gateway.validate({
            "name": "first_board_critic", "args": {"symbol": entity["symbol"], "trade_date": "2026-09-11"}}))
    assert state == "ok"
    assert critic["symbol"] == entity["symbol"]
    assert critic["trade_date"] == filtered["trade_date"]
    assert (critic["original_confidence"], critic["suggested_confidence"]) == (0.6, 0.4)
    assert "也允许first_board_filter＋first_board_critic" in case.assertions[0].expected["grading"]
    # This checks fixture/rubric support, not an LLM judge's acceptance of that route.


def test_unrevised_candidates_keep_default_grading():
    from app.agent_eval.basic70 import candidate_assets
    for item in CASES:
        if "grading" not in item:
            case, _, _ = candidate_assets(item)
            assert case.assertions[0].expected["grading"] == (
                "Meaning and evidence support, not literal answer matching.")


def test_production_list_capture_roundtrips_without_object_coercion():
    from datetime import datetime
    from app.agent_eval.local_promotion import LocalPromotionRegistry
    from app.agent_eval.models import CalendarSpec
    from app.agent_eval.recorder import capture_tool, verify_replay

    anchor = datetime.fromisoformat("2026-09-11T18:00:00+08:00")
    artifact = capture_tool(LocalPromotionRegistry([], []), tool="daily_board_promotion",
        arguments={"days": 1, "end_date": "2026-09-11"}, anchor_datetime=anchor,
        recording_id="list-roundtrip", provenance="synthetic empty production invocation",
        source_manifest={"synthetic": True})
    calendar = CalendarSpec(id="one", version=1, start_date=anchor.date(),
                            end_date=anchor.date(), trading_dates=[anchor.date()])
    assert artifact.body.recording.observation.payload == []
    assert verify_replay(artifact, calendar=calendar, latest_local_trade_date=anchor.date())["passed"]


@pytest.mark.parametrize("item", CASES[:23], ids=[c["id"] for c in CASES[:23]])
def test_scripted_agent_consumes_each_new_tool_observation(item):
    from uuid import uuid4
    from langchain_core.messages import AIMessage
    from app.agents.react_runtime import runtime
    from app.agent_eval.frozen_registry import FrozenAgentToolRegistry
    from app.models import AgentChatRequest

    _, world, _ = preflight_candidate(item)
    class ScriptedProvider:
        calls = 0

        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content="", tool_calls=[{
                    "name": item["tool"], "args": item["arguments"], "id": "query"}])
            return AIMessage(content="", tool_calls=[{"name": "finish", "id": "finish",
                "args": {"status": item["terminal"], "answer": item["reference_answer"],
                         "missing": ["数据缺失"] if item["terminal"] == "partial" else []}}])
    registry = FrozenAgentToolRegistry(world)
    with registry.anchored():
        response = runtime.run(AgentChatRequest(session_id=str(uuid4()), message_id=str(uuid4()),
                                                message=item["question"]), registry, ScriptedProvider())
    execution = next(t.output for t in response.tool_results if t.name == "react_execution")
    records = list(execution["evidence"].values())
    assert any(r["tool"] == item["tool"] for r in records)
    assert response.stop_reason != "provider_error"
