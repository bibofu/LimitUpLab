"""Offline regressions for explicitly requested review narratives."""

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.agents.review_agent import REVIEW_AGENT_VERSION, build_review_agent_report
from app.agents.review_narrative import authoritative_review_facts
from app.models import AgentEvaluationItem, AgentPrediction, DailyReviewSnapshot, ReviewAgentReportResponse, StockDailyBar
from app.repositories import SQLiteFirstBoardRepository
from app.routers.agents import router
from app.services.llm_provider import DisabledLLMProvider, LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


BASE, END = date(2026, 9, 29), date(2026, 9, 30)
VALID = {
    "main_findings": ["样本观察期较短，暂不足以判断评分体系长期有效。"],
    "successful_patterns": ["成功组需继续积累同口径样本，再检验题材结构的解释力。"],
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
            "args": {
                "decision": self.compliance,
                "violations": [] if self.compliance == "allow" else ["trade_instruction"],
                "reason": "离线审查结果",
            },
        }])


def repository():
    repo = Mock(spec=SQLiteFirstBoardRepository)
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
        patch("app.agents.review_agent.build_agent_evaluation", return_value=SimpleNamespace(
            evaluations=evaluations, prediction_count=600,
        )),
        patch("app.agents.review_agent.build_top10_outcome_completeness", return_value=SimpleNamespace(
            warnings=["三日走势尚未完整缓存"],
        )),
    ):
        return build_review_agent_report(
            events=events(), repository=repository(), start_date=BASE, end_date=END,
            min_score=0, provider=provider, trade_dates=(BASE, END),
        )


def test_llm_gets_authoritative_facts_even_when_planner_omits_comparison_tools():
    provider = ReviewProvider({**VALID, "sample_size": 999, "success_count": 999,
                               "top_pick_promotion_rate": 0.99})
    report = build(provider)
    assert report.generation_mode == "llm"
    assert report.llm_model == "offline-review-model"
    assert len(provider.calls) == 2 and len(provider.checks) == 1
    assert [trace.name for trace in report.tool_results] == ["daily_high_score_picks"]
    facts = json.loads(provider.calls[-1][1])["tool_facts"]["authoritative_review"]
    assert facts["evaluation_label_counts"] == {"success": 1, "miss": 1, "partial": 1}
    assert facts["prediction_source_counts"] == {"historical_backtest": 3}
    assert facts["forward_validation"]["eligible_sample_count"] == 0
    assert facts["forward_validation"]["cohort_counts"] == {"historical_backtest": 3}
    assert any("前向验证资格的样本 0 只" in line for line in report.main_findings)
    assert any("其余评价 1 只" in line for line in report.main_findings)
    assert facts["sampling_limits"]["candidates_truncated"] is True
    assert facts["sampling_limits"]["tool_detail_limit"] == 20
    assert facts["sampling_limits"]["response_pick_limit"] == 100
    assert facts["feature_comparison"]["main_findings"]
    assert facts["deterministic_report"]["promotion_comparisons"]
    assert "三日走势尚未完整缓存" in facts["deterministic_report"]["warnings"]
    assert "次日开盘至收盘" in facts["comparison_basis"]["report_counts"]
    assert "首板至复盘截至日内最新" in facts["comparison_basis"]["feature_groups"]
    assert (report.sample_size, report.success_count, report.failed_count, report.pending_count) == (3, 1, 1, 0)
    assert report.top_pick_promotion_rate == 0.3333
    assert report.main_findings[0].startswith("高分首板样本 3")
    assert VALID["main_findings"][0] in report.main_findings
    assert VALID["successful_patterns"][0] in report.successful_patterns


@pytest.mark.parametrize("payload", [
    {}, {"main_findings": []}, {**VALID, "main_findings": ["   "]},
    {**VALID, "main_findings": [42]}, {**VALID, "successful_patterns": []},
    {**VALID, "confidence": 2}, {**VALID, "confidence": float("nan")},
    {**VALID, "confidence": True}, "not json",
])
def test_invalid_or_empty_narrative_explicitly_falls_back(payload):
    provider = ReviewProvider(payload)
    report = build(provider)
    assert report.generation_mode == "deterministic"
    assert report.llm_model is None
    assert "未返回有效且有解释内容" in report.generation_note
    assert report.main_findings and report.sample_size == 3
    assert provider.checks == []


@pytest.mark.parametrize("compliance,expected", [
    ("reject", "未通过"), (RuntimeError("unavailable"), "未完成"), ("malformed", "未完成"),
])
def test_compliance_rejection_or_failure_discards_model_text(compliance, expected):
    provider = ReviewProvider({**VALID, "main_findings": ["建议明日买入该股并加仓到五成仓位。"]}, compliance=compliance)
    report = build(provider)
    assert report.generation_mode == "deterministic"
    assert "研究边界检查" in report.generation_note and expected in report.generation_note
    assert all("五成仓位" not in text for text in report.main_findings)


def test_disabled_provider_skips_model_and_failure_is_explicit_without_error_leak():
    disabled = DisabledLLMProvider()
    with patch.object(disabled, "generate", side_effect=AssertionError("must not call")) as generate:
        report = build(disabled)
    generate.assert_not_called()
    assert report.generation_mode == "deterministic"
    assert "未启用" in report.generation_note
    failed = build(ReviewProvider(RuntimeError("private upstream diagnostic")))
    assert failed.generation_mode == "deterministic" and "请求失败" in failed.generation_note
    assert "private upstream" not in failed.model_dump_json()


@pytest.mark.parametrize("mode", ["legacy", "deterministic", "llm"])
@pytest.mark.parametrize("use_llm", [False, True])
@pytest.mark.parametrize("refresh_facts", [False, True])
def test_api_llm_request_rebuilds_without_mutating_daily_snapshot(mode, use_llm, refresh_facts):
    stored = build(DisabledLLMProvider()).model_copy(update={"generation_mode": mode})
    original = DailyReviewSnapshot(
        as_of_date=END, start_date=BASE, report=stored,
        generated_by="daily-review-snapshot-v1", generated_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )
    before = original.model_dump_json()
    snapshots = Mock()
    snapshots.get_snapshot.return_value = original
    market = Mock()
    market.list_events.return_value = events()
    generated = stored.model_copy(update={
        "generation_mode": "llm" if use_llm else "deterministic",
        "llm_model": "offline-model" if use_llm else None,
    })
    app = FastAPI()
    app.include_router(router, prefix="/agents")
    with (
        patch("app.routers.agents.get_limit_up_repository", return_value=market),
        patch("app.routers.agents.SQLiteFirstBoardRepository", return_value=repository()),
        patch("app.routers.agents.SQLiteReviewSnapshotRepository", return_value=snapshots),
        patch("app.routers.agents.build_review_agent_report", return_value=generated) as generate,
        TestClient(app) as client,
    ):
        response = client.get("/agents/review-report", params={
            "use_llm": str(use_llm).lower(), "refresh_facts": str(refresh_facts).lower(),
        })
    assert response.status_code == 200
    assert original.model_dump_json() == before
    snapshots.save_snapshot.assert_not_called()
    if use_llm or refresh_facts:
        generate.assert_called_once()
        if use_llm:
            assert generate.call_args.kwargs["provider"] is None
        else:
            assert isinstance(generate.call_args.kwargs["provider"], DisabledLLMProvider)
        snapshots.get_snapshot.assert_not_called()
        assert response.json()["generation_mode"] == ("llm" if use_llm else "deterministic")
    else:
        generate.assert_not_called()
        assert response.json()["generation_mode"] == mode


def test_legacy_report_json_remains_readable():
    payload = build(DisabledLLMProvider()).model_dump(mode="json", exclude={
        "generation_mode", "llm_model", "generation_note",
    })
    restored = ReviewAgentReportResponse.model_validate_json(json.dumps(payload))
    assert restored.generation_mode == "legacy"
    assert restored.llm_model is None and restored.generation_note is None
    assert restored.generated_by == REVIEW_AGENT_VERSION


@pytest.mark.parametrize("cohort,expected", [("close_baseline", 0), ("premarket_final", 3)])
def test_live_source_alone_does_not_prove_forward_validation_eligibility(cohort, expected):
    report = build(DisabledLLMProvider())
    picks = [pick.model_copy(update={"prediction_source": "live", "time_cohort": cohort})
             for pick in report.reviewed_picks]
    report = report.model_copy(update={"time_cohort_counts": {cohort: len(picks)}})
    facts = authoritative_review_facts(report, picks, {}, {})
    assert facts["prediction_source_counts"] == {"live": 3}
    assert facts["forward_validation"]["eligible_sample_count"] == expected
