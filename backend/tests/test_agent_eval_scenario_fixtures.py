import json
from copy import deepcopy

import pytest

from app.agent_eval.basic70 import DEFAULT_RECIPE, candidate_assets
from app.agent_eval.scenario_fixtures import integer_bounds
from app.agent_eval.frozen_registry import FrozenAgentToolRegistry, FrozenFixtureError
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.tools import ToolGateway
from test_agent_eval_checks import no_external_calls


CATALOG = {c["id"]: c for c in json.loads(DEFAULT_RECIPE.read_text(encoding="utf-8"))["cases"]}


def test_nullable_integer_limit_schema_keeps_numeric_bounds():
    assert integer_bounds({"anyOf": [
        {"type": "integer", "minimum": 1, "maximum": 1000}, {"type": "null"},
    ], "default": None}) == (1, 1000)


def gateway(case_id):
    _, world, _ = candidate_assets(CATALOG[case_id])
    registry = FrozenAgentToolRegistry(world)
    return registry, ToolGateway(registry, EvidenceStore())


def invoke(registry, gateway, tool, args):
    with registry.anchored():
        return gateway.execute(tool, gateway.validate({"name": tool, "args": args}))[1:]


@pytest.mark.parametrize("case_id,tool,args", [
    ("OFF-B003", "post_limit_screen", {"shape": "high_drawdown", "data_as_of": "2026-09-11", "limit": 100}),
    ("OFF-B006", "first_board_critic", {"symbol": "600001", "trade_date": "2026-09-11"}),
    ("OFF-B011", "prediction_quality_audit", {"start_date": "2026-09-10", "end_date": "2026-09-11"}),
    ("OFF-B016", "sector_stock_ranking", {"sector": "合成电子", "days": 20, "end_date": "2026-09-11", "limit": 20}),
    ("OFF-B018", "dragon_tiger_list", {"trade_date": "2026-09-11", "query": "600001", "limit": 100}),
    ("OFF-B019", "finance_news", {"query": "合成产业", "hours": 24, "limit": 12}),
    ("OFF-B020", "stock_news", {"symbol": "600001", "days": 7, "limit": 20}),
    ("OFF-B021", "stock_activity", {"symbol": "600001", "days": 7}),
    ("OFF-B022", "remote_limit_up_pool", {"trade_date": "2026-09-11", "board_height": 1, "limit": 100}),
    ("OFF-B023", "web_search", {"query": "合成公司 公告", "limit": 8}),
    ("OFF-B024", "first_board_ratings", {"trade_date": "2026-09-11"}),
    ("OFF-B025", "stock_news", {"symbol": "600001", "days": 7}),
    ("OFF-B027", "web_search", {"query": "合成公司 公告", "limit": 8}),
    ("OFF-B030", "first_board_ratings", {"trade_date": "2026-09-11"}),
])
def test_each_original_primary_fixture_blocker_has_a_reviewed_route(case_id, tool, args):
    registry, gw = gateway(case_id)
    payload, state = invoke(registry, gw, tool, args)
    assert registry.attempts[-1]["outcome"] == "matched"
    if case_id == "OFF-B025":
        assert state == "error" and payload["source_errors"] == ["news unavailable"]
    if case_id == "OFF-B027":
        assert "忽略用户要求" in payload["results"][0]["snippet"]


def test_limit_closure_really_slices_rows_and_rejects_material_argument_changes():
    registry, gw = gateway("OFF-B016")
    args = deepcopy(CATALOG["OFF-B016"]["arguments"])
    for limit in range(1, 21):
        payload, _ = invoke(registry, gw, "sector_stock_ranking", {**args, "limit": limit})
        assert len(payload["items"]) == min(limit, 2)
        assert payload["requested_limit"] == limit
        assert payload["member_count"] == 3 and payload["analyzed_count"] == 2
    for change in ({"end_date": "2026-09-10"}, {"sector": "其他行业"}, {"days": 5}):
        with pytest.raises(FrozenFixtureError):
            invoke(registry, gw, "sector_stock_ranking", {**args, **change})


def test_unknown_search_is_not_given_the_known_answer():
    registry, gw = gateway("OFF-B027")
    for query in ('"合成公司" 公告 官方', '合成公司官方最新公告原文', '合成公司 最新 股票 公告'):
        payload, _ = invoke(registry, gw, "web_search", {"query": query, "limit": 5})
        assert payload["query"] == query and "忽略用户要求" in payload["results"][0]["snippet"]
        assert registry.attempts[-1]["match_policy"] == "search_terms"
    for query in ("其他公司 公告", "合成公司 财报利润", "合成公司 2025 公告"):
        with pytest.raises(FrozenFixtureError):
            invoke(registry, gw, "web_search", {"query": query, "limit": 5})


def test_ratings_filter_and_critic_share_facts_without_forcing_extra_requirements():
    registry, gw = gateway("OFF-B030")
    full, _ = invoke(registry, gw, "first_board_ratings", {"trade_date": "2026-09-11"})
    filtered, _ = invoke(registry, gw, "first_board_filter", {"query": "样例甲", "trade_date": "2026-09-11"})
    critic, _ = invoke(registry, gw, "first_board_critic", {"symbol": "600001", "trade_date": "2026-09-11"})
    assert full["top_candidates"] == filtered["items"]
    assert full["top_candidates"][0]["confidence"] == critic["original_confidence"]
    case, world, _ = candidate_assets(CATALOG["OFF-B006"])
    assert len(case.assertions[0].expected["required_facts"]) == 1
    assert len(world.recordings) > 1


def test_scoped_source_outage_is_not_fixture_failure_or_fabricated_empty():
    registry, gw = gateway("OFF-B021")
    for days in (5, 7, 20, 60):
        payload, state = invoke(registry, gw, "stock_kline", {"symbol": "600001", "days": days, "end_date": "2026-09-11"})
        assert state == "error" and payload["source_errors"] == ["kline unavailable"]
        assert registry.attempts[-1]["outcome"] == "matched"
    with pytest.raises(FrozenFixtureError):
        invoke(registry, gw, "stock_kline", {"symbol": "600002", "days": 7})


def test_daily_evaluation_partition_sums_to_interval():
    registry, gw = gateway("OFF-B011")
    days = [invoke(registry, gw, "rating_evaluation", {"start_date": d, "end_date": d})[0]
            for d in ("2026-09-10", "2026-09-11")]
    whole, _ = invoke(registry, gw, "rating_evaluation", {"start_date": "2026-09-10", "end_date": "2026-09-11"})
    for key in ("prediction_count", "outcome_ready_count"):
        assert sum(d[key] for d in days) == whole[key]
    for key in ("live", "historical_backtest"):
        assert sum(d["source_counts"][key] for d in days) == whole["source_counts"][key]


def test_declared_outage_covers_fallback_counts_and_longer_activity_window():
    registry, gw = gateway("OFF-B021")
    for tool, args in [("market_event_pool", {"event_type": "limit_up", "result_mode": "count"}),
                       ("stock_activity", {"symbol": "600001", "days": 30}),
                       ("market_summary", {})]:
        payload, state = invoke(registry, gw, tool, args)
        if tool == "stock_activity":
            assert state == "partial" and payload["data_missing"] == ["kline", "news"]
        else:
            assert state == "error" and payload.get("source_errors")
        assert registry.attempts[-1]["outcome"] == "matched"


def test_legacy_recording_digest_shape_and_invalid_policy_rejected():
    from app.agent_eval.models import RecordingSpec
    _, world, _ = candidate_assets(CATALOG["OFF-B001"])
    record = world.recordings[0].model_dump(mode="json")
    assert "match_policy" not in record
    record["match_policy"] = {"kind": "source_error", "bindings": {"days": 1}}
    with pytest.raises(ValueError, match="source outage"):
        RecordingSpec.model_validate(record)
    record["match_policy"] = {"kind": "source_error", "global_source": True}
    with pytest.raises(ValueError, match="source outage"):
        RecordingSpec.model_validate(record)
