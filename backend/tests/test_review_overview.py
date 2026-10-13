"""Offline facts, generation and HTTP regressions for the on-demand overview."""

import json
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.agents.review_digest_profiles import build_digest_group
from app.agents.review_overview import build_overview_facts, focused_overview_facts, generate_overview
from app.review_digest_models import DigestStock
from app.routers.review_overview import router
from app.security import AnonymousVisitorMiddleware, SESSION_COOKIE_NAME
from app.services.llm_provider import DisabledLLMProvider, LLMProvider, LLMResult


DAYS = (date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30))
TEXT = ("本轮市场涨停延续有所分化，首板与连板须分别核对有效样本。候选晋级组可查看首板封板时间与炸板分布，特征分别统计。市场三板以上股票应回看首板与二板过程，缺失数据不作推断。" * 6)[:350]


def fixtures():
    def event(day, symbol, height, closed=True):
        return SimpleNamespace(trade_date=day, symbol=symbol, name="样本" + symbol,
                               board_height=height, closed_limit=closed,
                               first_limit_time=time(9, 45), break_count=0, turnover_rate=8,
                               industry="测试行业", concept="测试题材")
    events = [event(DAYS[0], "a", 1), event(DAYS[0], "b", 1), event(DAYS[0], "c", 2),
              event(DAYS[1], "a", 2), event(DAYS[1], "b", 1, False), event(DAYS[1], "c", 3),
              event(DAYS[1], "d", 1), event(DAYS[2], "a", 3), event(DAYS[2], "c", 4),
              event(DAYS[2], "d", 1, False)]
    picks = [SimpleNamespace(trade_date=day, symbol=symbol, name="候选" + symbol, score=90,
                             time_cohort="historical_backtest", prediction_source="historical_backtest")
             for day, symbol in [(DAYS[0], "a"), (DAYS[0], "b"), (DAYS[1], "d")]]
    repo = Mock()
    repo.list_predictions_between.return_value = [SimpleNamespace(trade_date=pick.trade_date, symbol=pick.symbol,
        facts_json={"first_limit_time": "09:45", "break_count": 0, "turnover_rate": 8,
                    "industry": "测试行业", "concept": "测试题材"}) for pick in picks]
    # 'b' has a strong cutoff return but did not actually advance to board two.
    repo.list_daily_bars_for_symbols.return_value = [SimpleNamespace(symbol=symbol, trade_date=day, open=10,
        close=12 if symbol == "b" and day == DAYS[2] else 10) for symbol in ("a", "b", "d") for day in DAYS]
    leader = DigestStock(symbol="a", name="高标a", first_board_date=DAYS[0], max_board_height=3,
        first_limit_time="09:45", break_count=0, second_board_date=DAYS[1], second_limit_time="10:00",
        second_break_count=1, second_turnover_rate=10, second_board_shape="有价格波动")
    report = SimpleNamespace(end_date=DAYS[-1], reviewed_picks=picks, warnings=[],
        review_digest=SimpleNamespace(leaders=build_digest_group("leaders", [leader]), market_dates=list(DAYS), notes=[]))
    return report, events, repo


def facts(report=None, events=None, repo=None, days=DAYS):
    original = fixtures()
    return build_overview_facts(report=report or original[0], events=events if events is not None else original[1],
                                repository=repo or original[2], trade_dates=days)


def test_rates_are_weighted_and_success_profiles_use_actual_promotion_not_price_returns():
    data = facts()
    assert data["market"]["first_to_second"] == {"promoted": 1, "total": 3, "percent": 33.3}
    assert data["market"]["continued_board_promotion"] == {"promoted": 3, "total": 3, "percent": 100.0}
    assert data["market"]["all_limit_up_promotion"] == {"promoted": 4, "total": 6, "percent": 66.7}
    assert data["candidates"]["first_to_second"] == {"promoted": 1, "total": 3, "percent": 33.3}
    assert [item["symbol"] for item in data["candidates"]["examples"]] == ["a"]
    seal = next(item for item in data["candidates"]["success_profiles"] if item["key"] == "first_limit_time")
    assert seal["total_count"] == 1 and seal["baseline_total_count"] == 3
    assert data["candidates"]["source_counts"] == {"historical_backtest": 3}
    assert data["leaders"]["examples"][0]["second_board_date"] == str(DAYS[1])
    assert data["market"]["base_trade_dates"] == [str(DAYS[0]), str(DAYS[1])]
    assert data["market"]["outcome_dates"] == [str(DAYS[1]), str(DAYS[2])]
    assert data["market"]["rate_period"] == "multi_day"
    assert data["candidates"]["comparison_uses_same_window"] is True
    assert data["candidates"]["eligible_first_board_dates"] == [str(DAYS[0]), str(DAYS[1])]
    assert data["leaders"]["observation_dates"] == list(map(str, DAYS))


def test_missing_session_is_not_bridged_or_treated_as_failed_candidates():
    report, events, repo = fixtures()
    data = facts(report, [event for event in events if event.trade_date != DAYS[1]], repo)
    assert data["market"]["first_to_second"]["percent"] is None
    assert data["candidates"]["first_to_second"]["total"] == 0
    assert len(data["candidates"]["data_missing"]) == 3
    assert data["notes"]


def test_conflicting_event_days_are_excluded_and_no_calendar_produces_unknown_rates():
    report, events, repo = fixtures()
    conflict = SimpleNamespace(**{**vars(events[0]), "board_height": 2})
    data = facts(report, events + [conflict], repo)
    assert data["candidates"]["first_to_second"] == {"promoted": 0, "total": 1, "percent": 0.0}
    assert any("冲突" in note for note in data["notes"])
    no_calendar = facts(days=())
    assert no_calendar["market"]["continued_board_promotion"]["percent"] is None
    assert no_calendar["candidates"]["first_to_second"]["percent"] is None


class Provider(LLMProvider):
    def __init__(self, text=TEXT, accepted=True):
        self.text, self.accepted, self.prompts, self.checks = text, accepted, [], []

    def stream_generate(self, system, user, on_delta):
        self.prompts.append((system, json.loads(user)))
        on_delta(self.text[:20])
        on_delta(self.text[20:])
        return LLMResult(content=self.text, model="offline-overview", provider="test")

    def generate_messages(self, messages, tools, **kwargs):
        self.checks.append(json.loads(messages[-1].content))
        return AIMessage(content="", tool_calls=[{"id": "check", "name": "submit_overview_check",
            "args": {"accepted": self.accepted, "reason": "离线核验",
                     "candidate_dimensions": ["first_limit_time", "break_count"],
                     "covered_scopes": ["market", "candidates", "leaders"],
                     "comparison_window_claim": "same", "market_rate_time_claim": "multi_day"}}])


def test_deltas_arrive_before_final_review_and_each_request_has_a_new_writing_edition():
    provider, events = Provider(), []
    for _ in range(2):
        generate_overview(facts=facts(), provider=provider, emit=lambda name, data: events.append((name, data)))
    assert [name for name, _ in events[:4]] == ["delta", "delta", "checking", "completed"]
    assert events[3][1]["text"] == TEXT
    assert provider.prompts[0][1]["edition"] != provider.prompts[1][1]["edition"]
    assert provider.checks[0]["facts"] == provider.prompts[0][1]["facts"]


@pytest.mark.parametrize("text,accepted", [("太短", True), ("长" * 401, True), (TEXT, False)])
def test_bad_length_or_failed_semantic_check_never_publishes_a_final_result(text, accepted):
    events = []
    with pytest.raises(ValueError):
        generate_overview(facts=facts(), provider=Provider(text, accepted),
                          emit=lambda name, data: events.append(name))
    assert "completed" not in events


def test_stopped_request_does_not_call_the_model():
    provider = Provider()
    with pytest.raises(RuntimeError):
        generate_overview(facts=facts(), provider=provider, emit=Mock(), stopped=lambda: True)
    assert not provider.prompts


def test_length_rewrite_is_bounded_withdraws_previous_draft_and_keeps_facts():
    class RewriteProvider(Provider):
        def stream_generate(self, system, user, on_delta):
            self.text = "短" * 280 if not self.prompts else TEXT
            return super().stream_generate(system, user, on_delta)
    provider, events = RewriteProvider(), []
    data = facts()
    generate_overview(facts=data, provider=provider, emit=lambda name, payload: events.append((name, payload)))
    assert [name for name, _ in events] == ["delta", "delta", "reset", "delta", "delta", "checking", "completed"]
    assert provider.prompts[1][1]["previous_character_count"] == 280
    assert provider.prompts[1][1]["facts"] == provider.prompts[0][1]["facts"]
    assert events[-1][1]["text"] == TEXT
    invalid = Provider("短")
    with pytest.raises(ValueError):
        generate_overview(facts=data, provider=invalid, emit=Mock())
    assert len(invalid.prompts) == 2 and not invalid.checks


def test_three_candidate_dimensions_are_rejected_even_if_reviewer_says_accepted():
    class ThreeDimensions(Provider):
        def generate_messages(self, messages, tools, **kwargs):
            response = super().generate_messages(messages, tools, **kwargs)
            response.tool_calls[0]["args"]["candidate_dimensions"] = ["position_label", "first_limit_time", "break_count"]
            return response
    events = []
    with pytest.raises(ValueError):
        generate_overview(facts=facts(), provider=ThreeDimensions(), emit=lambda name, data: events.append(name))
    assert "completed" not in events


def test_semantic_rewrite_uses_the_review_reason_and_still_requires_a_second_check():
    class SemanticRewrite(Provider):
        def generate_messages(self, messages, tools, **kwargs):
            response = super().generate_messages(messages, tools, **kwargs)
            if len(self.checks) == 1:
                response.tool_calls[0]["args"].update(accepted=False, reason="候选特征超过两个",
                    candidate_dimensions=["position_label", "first_limit_time", "break_count"])
            return response
    provider, events = SemanticRewrite(), []
    generate_overview(facts=facts(), provider=provider, emit=lambda name, data: events.append(name))
    assert events.count("reset") == 1 and events.count("checking") == 2
    assert events[-1] == "completed"
    assert provider.prompts[1][1]["required_correction"]["reason"] == "候选特征超过两个"
    assert len(provider.prompts) == 2 and len(provider.checks) == 2


def test_focus_keeps_only_two_supported_dimensions_without_mutating_rates_or_original_evidence():
    data = facts()
    original = json.dumps(data, sort_keys=True)
    focused = focused_overview_facts(data)
    assert len(focused["candidates"]["success_profiles"]) == 2
    allowed = {profile["key"] for profile in focused["candidates"]["success_profiles"]}
    assert allowed == {"first_limit_time", "break_count"}
    assert "turnover_rate" not in focused["candidates"]["examples"][0]
    assert focused["candidates"]["first_to_second"] == data["candidates"]["first_to_second"]
    assert focused["market"] == data["market"] and focused["leaders"] == data["leaders"]
    assert json.dumps(data, sort_keys=True) == original
    assert focused["candidates"]["examples"][0]["profile_matches"] == ["first_limit_time", "break_count"]


def test_missing_scope_is_rejected_even_if_facts_check_accepts_the_remaining_text():
    class MissingScope(Provider):
        def generate_messages(self, messages, tools, **kwargs):
            response = super().generate_messages(messages, tools, **kwargs)
            response.tool_calls[0]["args"]["covered_scopes"] = ["market", "candidates"]
            return response
    events = []
    with pytest.raises(ValueError):
        generate_overview(facts=facts(), provider=MissingScope(), emit=lambda name, data: events.append(name))
    assert "completed" not in events


@pytest.mark.parametrize("field,value", [("comparison_window_claim", "different"), ("market_rate_time_claim", "single_day")])
def test_wrong_window_claim_is_rejected_even_when_semantic_check_accepts(field, value):
    class WrongWindow(Provider):
        def generate_messages(self, messages, tools, **kwargs):
            response = super().generate_messages(messages, tools, **kwargs)
            response.tool_calls[0]["args"][field] = value
            return response
    events = []
    with pytest.raises(ValueError):
        generate_overview(facts=facts(), provider=WrongWindow(), emit=lambda name, data: events.append(name))
    assert "completed" not in events


def app():
    instance = FastAPI()
    instance.add_middleware(AnonymousVisitorMiddleware)
    instance.include_router(router, prefix="/api/agents")
    return instance


def test_real_sse_route_uses_read_only_facts_tracks_usage_and_issues_owner_cookie():
    report, events, repo = fixtures()
    lease, usage = Mock(), Mock()
    with (
        patch("app.routers.review_overview.get_llm_provider", return_value=Provider()),
        patch("app.routers.review_overview.get_limit_up_repository", return_value=SimpleNamespace(list_events=lambda: events)),
        patch("app.routers.review_overview.SQLiteFirstBoardRepository", return_value=repo),
        patch("app.routers.review_overview.load_promotion_calendar", return_value=SimpleNamespace(trade_dates=DAYS, warnings=())),
        patch("app.routers.review_overview.build_review_agent_report", return_value=report) as build,
        patch("app.routers.agents._begin_agent_request", return_value=(lease, usage, Mock())),
        patch("app.routers.agents._finish_agent_request") as finish,
        TestClient(app()) as client,
    ):
        response = client.post("/api/agents/review-overview/stream", json={"end_date": str(DAYS[-1])})
    assert response.status_code == 200 and response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-store"
    assert SESSION_COOKIE_NAME in response.cookies
    assert "event: delta" in response.text and "event: completed" in response.text
    assert isinstance(build.call_args.kwargs["provider"], DisabledLLMProvider)
    assert build.call_args.kwargs["end_date"] == DAYS[-1]
    finish.assert_called_once()
    lease.release.assert_called_once()
    repo.save_prediction.assert_not_called()


def test_route_failure_withdraws_draft_and_releases_usage_slot():
    lease, usage = Mock(), Mock()
    with (
        patch("app.routers.review_overview.get_llm_provider", return_value=Provider()),
        patch("app.routers.review_overview.get_limit_up_repository", side_effect=RuntimeError("private upstream detail")),
        patch("app.routers.agents._begin_agent_request", return_value=(lease, usage, Mock())),
        patch("app.routers.agents._finish_agent_request") as finish,
        TestClient(app()) as client,
    ):
        response = client.post("/api/agents/review-overview/stream", json={"end_date": str(DAYS[-1])})
    assert "event: error" in response.text and "private upstream detail" not in response.text
    assert finish.call_args.kwargs["error"] is not None
    lease.release.assert_called_once()


def test_unavailable_model_invalid_contract_and_rate_rejection_start_no_worker():
    with patch("app.routers.review_overview.get_llm_provider", return_value=DisabledLLMProvider()), TestClient(app()) as client:
        assert client.post("/api/agents/review-overview/stream", json={"end_date": str(DAYS[-1])}).status_code == 503
        assert client.post("/api/agents/review-overview/stream", json={"end_date": str(DAYS[-1]), "facts": {}}).status_code == 422
    with (
        patch("app.routers.review_overview.get_llm_provider", return_value=Provider()),
        patch("app.routers.agents._begin_agent_request", side_effect=HTTPException(429, "请稍后重试")),
        patch("app.routers.review_overview.Thread") as thread,
        TestClient(app()) as client,
    ):
        assert client.post("/api/agents/review-overview/stream", json={"end_date": str(DAYS[-1])}).status_code == 429
        thread.assert_not_called()


def test_worker_start_failure_releases_slot_and_records_failure():
    lease, usage = Mock(), Mock()
    with (
        patch("app.routers.review_overview.get_llm_provider", return_value=Provider()),
        patch("app.routers.review_overview.Thread", return_value=SimpleNamespace(start=Mock(side_effect=RuntimeError("start failed")))),
        patch("app.routers.agents._begin_agent_request", return_value=(lease, usage, Mock())),
        patch("app.routers.agents._finish_agent_request") as finish,
        TestClient(app()) as client,
    ):
        response = client.post("/api/agents/review-overview/stream", json={"end_date": str(DAYS[-1])})
    assert response.status_code == 503
    lease.release.assert_called_once()
    assert finish.call_args.kwargs["error"] is not None
