"""Offline regressions for grounded four-part review commentary."""

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.agents.review_agent import REVIEW_AGENT_VERSION, build_review_agent_report
from app.agents.review_narrative import ReviewNarrative, apply_review_narrative, authoritative_review_facts
from app.models import AgentEvaluationItem, AgentPrediction, DailyReviewSnapshot, ReviewAgentReportResponse, StockDailyBar
from app.repositories import SQLiteFirstBoardRepository
from app.review_digest_models import DigestObservation
from app.routers.agents import router
from app.services.llm_provider import DisabledLLMProvider, LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


BASE, END = date(2026, 9, 29), date(2026, 9, 30)
VALID = {
    "sections": [
        {"scope": scope, "summary": "当前样本数量有限，需要结合原始事实继续观察。", "observation_ids": []}
        for scope in ("excellent", "weak", "leaders")
    ],
    "confidence": 0.6,
}


class ReviewProvider(LLMProvider):
    def __init__(self, payload=VALID, *, compliance="allow"):
        self.payload, self.compliance = payload, compliance
        self.calls, self.checks = [], []

    def generate(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        if "planner" in system_prompt.lower():
            content = '{"tool_calls":[{"name":"daily_high_score_picks"}]}'
        elif isinstance(self.payload, Exception):
            raise self.payload
        else:
            content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return LLMResult(content=content, model="offline-review-model", provider="fake")

    def generate_messages(self, messages, tools, **kwargs):
        self.checks.append(messages)
        if isinstance(self.compliance, Exception):
            raise self.compliance
        return AIMessage(content="", tool_calls=[{
            "id": "compliance", "name": "submit_compliance_review",
            "args": {"decision": self.compliance, "violations": [] if self.compliance == "allow" else ["trade_instruction"],
                     "reason": "离线审查结果"},
        }])


def repository():
    repo = Mock(spec=SQLiteFirstBoardRepository)
    repo.list_enrichment_for_date.return_value = []
    repo.list_predictions_between.return_value = [AgentPrediction(
        prediction_id=f"test-{index}", trade_date=BASE, symbol=f"00000{index + 1}",
        name="研究样本", score=90, rating="A", confidence=0.8,
        scoring_version="test", prediction_source="historical_backtest", data_as_of=BASE,
        facts_json={}, reasons=[], risks=[], created_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    ) for index in range(3)]
    def post_bars(symbol, base_date, **kwargs):
        close = {"000001": 11, "000002": 9, "000003": 10}[symbol]
        return [StockDailyBar(
            symbol=symbol, trade_date=day, open=10, high=12, low=8, close=price,
            volume=100, amount=1000, source="test", created_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
        ) for day, price in [(BASE, 10), (END, close)]]
    repo.list_post_bars.side_effect = post_bars
    repo.list_daily_bars_for_symbols.side_effect = lambda symbols, **_: [bar for symbol in symbols for bar in post_bars(symbol, BASE)]
    return repo


def events():
    return [SAMPLE_EVENTS[2].model_copy(update={
        "trade_date": day, "symbol": symbol, "board_height": height, "closed_limit": True,
    }) for day, symbol, height in [
        (BASE, "000001", 1), (BASE, "000002", 1), (BASE, "000003", 1), (END, "000001", 2),
    ]]


def build(provider):
    evaluations = [AgentEvaluationItem(
        prediction_id=f"test-{index}", trade_date=BASE, symbol=f"00000{index + 1}",
        name="研究样本", score=90, rating="A", confidence=0.8,
        prediction_source="historical_backtest", data_as_of=BASE,
        time_cohort="historical_backtest", evaluation_label=label,
        outcome_ready=True, promoted_to_second_board=index == 0,
        lesson="结构化历史事实", scoring_suggestion="继续补齐观察数据",
    ) for index, label in enumerate(["success", "miss", "partial"])]
    with (
        patch("app.agents.review_agent.build_agent_evaluation", return_value=SimpleNamespace(evaluations=evaluations, prediction_count=600)),
        patch("app.agents.review_agent.build_top10_outcome_completeness", return_value=SimpleNamespace(warnings=["三日走势尚未完整缓存"])),
    ):
        return build_review_agent_report(
            events=events(), repository=repository(), start_date=BASE, end_date=END,
            min_score=0, provider=provider, trade_dates=(BASE, END),
        )


def test_llm_gets_single_authoritative_digest_even_when_planner_omits_tools():
    provider = ReviewProvider({**VALID, "review_digest": {"overview": {"candidate_count": 999}}})
    report = build(provider)
    assert report.generation_mode == "llm"
    assert report.llm_model == "offline-review-model"
    assert len(provider.calls) == 2 and len(provider.checks) == 1
    facts = json.loads(provider.calls[-1][1])["authoritative_review"]
    assert facts["forward_validation"]["eligible_sample_count"] == 0
    assert facts["sampling_limits"]["candidates_truncated"] is True
    digest = report.review_digest
    assert digest.overview.candidate_count == 3
    assert (digest.overview.excellent_count, digest.overview.weak_count, digest.overview.ordinary_count) == (1, 1, 1)
    assert digest.overview.headline == facts["review_digest"]["overview"]["headline"]
    assert "stocks" not in facts["review_digest"]["excellent"]
    assert [section["scope"] for section in json.loads(provider.calls[-1][1])["required_json_shape"]["sections"]] == ["excellent", "weak", "leaders"]
    assert report.feature_research is None and not report.summary_insights
    assert report.top_pick_promotion_rate == 0.3333


@pytest.mark.parametrize("payload", [
    {}, {"sections": []}, {**VALID, "sections": [VALID["sections"][0]] * 3},
    {**VALID, "sections": [{**item, "summary": "空" * 101} for item in VALID["sections"]]},
    {**VALID, "sections": [{**item, "observation_ids": ["invented"]} for item in VALID["sections"]]},
    {**VALID, "confidence": 2}, {**VALID, "confidence": float("nan")}, {**VALID, "confidence": True}, "not json",
])
def test_invalid_narrative_falls_back_without_touching_evidence(payload):
    provider = ReviewProvider(payload)
    report = build(provider)
    assert report.generation_mode == "deterministic"
    assert "未返回有效且有解释内容" in report.generation_note
    assert report.review_digest.overview.candidate_count == 3
    assert not provider.checks


def test_narrative_references_cannot_change_facts_or_cross_groups():
    digest = build(DisabledLLMProvider()).review_digest
    observation = DigestObservation(id="excellent:position:低位", dimension="position_label", text="固定事实证据", support_count=3, sample_size=4)
    digest.excellent.observations = [observation]
    digest.excellent.selected_observation_ids = [observation.id]
    before = digest.model_dump_json()
    payload = json.loads(json.dumps(VALID))
    payload["sections"][0]["observation_ids"] = [observation.id]
    updated = apply_review_narrative(digest, ReviewNarrative.model_validate(payload))
    assert updated.excellent.summary == VALID["sections"][0]["summary"]
    assert updated.excellent.observations == digest.excellent.observations
    assert updated.overview == digest.overview and digest.model_dump_json() == before
    payload["sections"][1]["observation_ids"] = [observation.id]
    with pytest.raises(ValueError):
        apply_review_narrative(digest, ReviewNarrative.model_validate(payload))


@pytest.mark.parametrize("compliance,expected", [("reject", "未通过"), (RuntimeError("unavailable"), "未完成"), ("malformed", "未完成")])
def test_compliance_failure_discards_model_text(compliance, expected):
    provider = ReviewProvider(compliance=compliance)
    report = build(provider)
    assert report.generation_mode == "deterministic"
    assert "研究边界检查" in report.generation_note and expected in report.generation_note
    assert report.review_digest.overview.candidate_count == 3


def test_disabled_and_unavailable_providers_keep_readable_facts():
    disabled = DisabledLLMProvider()
    with patch.object(disabled, "generate", side_effect=AssertionError("must not call")) as generate:
        report = build(disabled)
    generate.assert_not_called()
    assert report.generation_mode == "deterministic" and "未启用" in report.generation_note
    failed = build(ReviewProvider(RuntimeError("private upstream diagnostic")))
    assert failed.generation_mode == "deterministic" and "请求失败" in failed.generation_note
    assert "private upstream" not in failed.model_dump_json()


@pytest.mark.parametrize("mode", ["legacy", "deterministic", "llm"])
@pytest.mark.parametrize("use_llm", [False, True])
@pytest.mark.parametrize("refresh_facts", [False, True])
def test_api_rebuild_does_not_mutate_daily_snapshot(mode, use_llm, refresh_facts):
    stored = build(DisabledLLMProvider()).model_copy(update={"generation_mode": mode})
    original = DailyReviewSnapshot(as_of_date=END, start_date=BASE, report=stored,
        generated_by="daily-review-snapshot-v1", generated_at=datetime(2026, 9, 30, tzinfo=timezone.utc))
    before = original.model_dump_json()
    snapshots, market = Mock(), Mock()
    snapshots.get_snapshot.return_value, market.list_events.return_value = original, events()
    generated = stored.model_copy(update={"generation_mode": "llm" if use_llm else "deterministic"})
    app = FastAPI()
    app.include_router(router, prefix="/agents")
    with (
        patch("app.routers.agents.get_limit_up_repository", return_value=market),
        patch("app.routers.agents.SQLiteFirstBoardRepository", return_value=repository()),
        patch("app.routers.agents.SQLiteReviewSnapshotRepository", return_value=snapshots),
        patch("app.routers.agents.build_review_agent_report", return_value=generated) as generate,
        TestClient(app) as client,
    ):
        response = client.get("/agents/review-report", params={"use_llm": str(use_llm).lower(), "refresh_facts": str(refresh_facts).lower()})
    assert response.status_code == 200 and original.model_dump_json() == before
    snapshots.save_snapshot.assert_not_called()
    if use_llm or refresh_facts:
        generate.assert_called_once()
        snapshots.get_snapshot.assert_not_called()
    else:
        generate.assert_not_called()


def test_old_saved_reports_remain_readable_without_new_digest():
    payload = build(DisabledLLMProvider()).model_dump(mode="json", exclude={"review_digest", "generation_mode"})
    restored = ReviewAgentReportResponse.model_validate(payload)
    assert restored.review_digest is None and restored.generation_mode == "legacy"


@pytest.mark.parametrize("cohort,expected", [("close_baseline", 0), ("premarket_final", 3)])
def test_live_source_alone_does_not_prove_forward_eligibility(cohort, expected):
    report = build(DisabledLLMProvider())
    picks = [pick.model_copy(update={"prediction_source": "live", "time_cohort": cohort}) for pick in report.reviewed_picks]
    facts = authoritative_review_facts(report, picks, {}, {})
    assert facts["forward_validation"]["eligible_sample_count"] == expected
