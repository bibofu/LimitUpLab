"""The market window and independent cohort denominators survive report assembly."""

from datetime import date, timedelta
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from app.agents.review_agent import _review_feature_profile
from app.agents.review_feature_study import build_feature_study
from app.agents.review_research import build_feature_research, deterministic_research_insights
from app.repositories import SQLiteFirstBoardRepository
from app.services.sample_data import SAMPLE_EVENTS


def event(day, symbol, height):
    return SAMPLE_EVENTS[2].model_copy(update={
        "trade_date": day, "symbol": symbol, "board_height": height, "closed_limit": True,
    })


def candidate():
    return build_feature_study(
        [{"position_label": "低位启动"}] * 3, [{"position_label": "高位整理"}] * 4,
        excluded_count=2, basis="独立候选窗口", positive_outcome="正收益",
    ).model_dump(mode="json")


def repository():
    repo = Mock(spec=SQLiteFirstBoardRepository)
    repo.list_enrichment_for_date.return_value = []
    repo.list_daily_bars_for_symbols.return_value = []
    return repo


def test_recent_twenty_sessions_use_all_first_boards_and_keep_outside_leaders_separate():
    days = [date(2026, 8, 1) + timedelta(days=index) for index in range(25)]
    observations = [event(days[index], "OLD", index + 1) for index in range(7)]
    observations += [event(days[5 + index], "WIN", index + 1) for index in range(3)]
    observations += [event(days[5], "MISS", 1), event(days[24], "PENDING", 1)]
    # A later result cannot decide an immature first board at the selected cutoff.
    observations += [event(days[-1] + timedelta(days=1), "PENDING", 3)]
    research = build_feature_research(
        candidate_study=candidate(), events=observations, repository=repository(),
        end_date=days[-1], trade_dates=days,
    )
    assert research.market_start_date == days[5]
    assert (research.market.positive_count, research.market.negative_count, research.market.excluded_count) == (1, 1, 1)
    assert (research.candidate.positive_count, research.candidate.negative_count, research.candidate.excluded_count) == (3, 4, 2)
    assert research.market_matched_count == 1
    old = next(item for item in research.market_leaders if item.symbol == "OLD")
    assert old.status == "outside_window" and old.first_board_date == days[0]
    assert not any(item.symbol == "PENDING" for item in research.market_leaders)
    assert [item.scope for item in deterministic_research_insights(research)] == ["candidate", "market", "synthesis"]


def test_missing_calendar_is_disclosed_without_substituting_event_dates_or_zero_rate():
    end = date(2026, 9, 30)
    research = build_feature_research(
        candidate_study=candidate(), events=[event(end, "FIRST", 1), event(end, "LEADER", 3)],
        repository=repository(), end_date=end, trade_dates=(),
    )
    assert research.market.baseline_rate is None
    assert research.market.excluded_count == 1
    assert any("交易日历不可用" in line for line in research.notes)
    assert research.market_leaders[0].first_board_date is None
    assert research.candidate.positive_count == 3


@pytest.mark.parametrize("primary", [
    {"regime": "unclassified", "label": "任意旧标签"}, {"label": "结构不明"},
])
def test_candidate_collector_sentinels_do_not_become_early_seal_or_position_evidence(primary):
    prediction = SimpleNamespace(
        facts_json={"first_limit_time": "00:00:00", "enrichment": {"position": {"primary": primary}}},
        score=80, confidence=.8,
    )
    evaluation = SimpleNamespace(next_open_to_close_pct=1, max_drawdown_from_next_open_3d=-1)
    profile = _review_feature_profile(
        evaluation, prediction, group_label="success", tracked_return=1, promotion=None,
    )
    study = build_feature_study([profile] * 3, [profile] * 3, excluded_count=0, basis="测试", positive_outcome="正收益")
    for key in ("position_label", "first_limit_minutes"):
        feature = next(item for item in study.features if item.key == key)
        assert feature.positive_valid_count == feature.negative_valid_count == 0
        assert feature.positive_summary == "样本不足"
        assert all(bucket.positive_rate is None for bucket in feature.buckets)
