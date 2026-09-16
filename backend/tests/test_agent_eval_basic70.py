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
