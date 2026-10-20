"""Excluded securities must not change another stock's relay evidence or score."""

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

from app.agents.first_board import build_first_board_ratings
from app.models import LimitUpEvent
from app.services.analysis import summarize_market
from app.services.first_board_enrichment import (
    RELAY_CONTEXT_VERSION_SUFFIX,
    adapt_enrichment_relay_context,
    build_enrichment_snapshot,
    _market_context,
)
from app.services.first_board_features import build_first_board_features
from app.services.scoring_policy import build_default_scoring_policy


DAY = date(2026, 10, 20)
PREVIOUS = DAY - timedelta(days=1)
CAPTURED = datetime(2026, 10, 20, 8, tzinfo=timezone.utc)


def event(symbol, name="普通样本", *, day=DAY, height=1, closed=True):
    return LimitUpEvent(
        symbol=symbol, name=name, trade_date=day,
        first_limit_time=time(9, 40), last_limit_time=time(9, 40),
        seal_count=1, break_count=0 if closed else 1, closed_limit=closed,
        board_height=height, amount=800_000_000, turnover_rate=6,
        industry="机器人", concept="机器人", next_open_pct=0, next_high_pct=0,
        next_close_pct=0, three_day_return_pct=0, five_day_return_pct=0,
        continued_next_day=False,
    )


def cohort():
    target = event("600001")
    normal = [
        event("600002", day=PREVIOUS), event("600003", day=PREVIOUS),
        target, event("600002", height=2), event("600004", closed=False),
    ]
    excluded = [
        event("000639", "ST样本", height=15),
        event("600302", "*st样本"),
        event("000666", "退市样本", closed=False),
        event("920001", "北交高板", height=12),
        event("430001", "北交首板"),
        event("830001", "北交炸板", closed=False),
        event("000639", "ST样本", day=PREVIOUS),
        event("920001", "北交样本", day=PREVIOUS),
    ]
    return target, normal, excluded


def enrichment(target, events):
    return build_enrichment_snapshot(
        event=target, events=events, bars=[], listing_date=date(2020, 1, 1),
        dragon_tiger=None, popularity=None, popularity_source_ready=False,
    ).model_copy(update={"created_at": CAPTURED})


def rating(target, events):
    snapshot = enrichment(target, events)
    repository = SimpleNamespace(list_enrichment_for_date=lambda _day: [snapshot])
    return build_first_board_ratings(
        events, trade_date=DAY, first_board_repository=repository,
        scoring_policy=build_default_scoring_policy(),
    )


def test_excluded_events_do_not_change_candidate_facts_scores_or_enrichment():
    target, normal, excluded = cohort()
    before, after = rating(target, normal), rating(target, normal + excluded)

    assert before.candidates == after.candidates
    assert after.candidates[0].facts.market_max_board_height == 2
    assert after.candidates[0].facts.same_industry_limit_up_count == 2
    assert after.candidates[0].facts.market_failed_limit_up_rate == 0.3333
    assert after.universe_count == 9
    assert {"000639", "600302", "920001", "430001"} <= {
        item.symbol for item in after.filtered_out
    }
    assert enrichment(target, normal) == enrichment(target, normal + excluded)
    context = _market_context(normal + excluded, target)
    assert context["previous_first_board_promotion_rate"] == 0.5
    assert context["industry_max_board_height"] == 2
    assert context["industry_first_board_count"] == 1
    assert context["industry_failed_count"] == 1
    assert context["market_first_board_seal_rate"] == 0.5

    # The public market overview still describes the original raw universe.
    assert summarize_market(normal + excluded).max_board_height == 15


def test_feature_context_and_rows_exclude_st_and_beijing():
    _target, normal, excluded = cohort()
    before = build_first_board_features(normal, DAY)
    after = build_first_board_features(normal + excluded, DAY)
    assert [row.model_dump(exclude={"created_at"}) for row in before] == [
        row.model_dump(exclude={"created_at"}) for row in after
    ]
    assert [row.symbol for row in after] == ["600001"]


def test_entirely_excluded_day_has_no_candidates_or_features():
    events = [event("000639", "ST样本"), event("920001", "北交样本")]
    repository = SimpleNamespace(list_enrichment_for_date=lambda _day: [])
    result = build_first_board_ratings(
        events, trade_date=DAY, first_board_repository=repository,
        scoring_policy=build_default_scoring_policy(),
    )
    assert result.candidates == []
    assert len(result.filtered_out) == result.universe_count == 2
    assert build_first_board_features(events, DAY) == []


def test_context_keeps_other_preexisting_market_segments():
    target, normal, _excluded = cohort()
    result = rating(target, normal + [
        event("300001", "创业板样本", height=4),
        event("688001", "科创板样本", height=3),
    ])
    facts = result.candidates[0].facts
    assert facts.market_max_board_height == 4
    assert facts.same_industry_limit_up_count == 4


def test_excluded_only_previous_day_does_not_reuse_an_older_promotion_cohort():
    target = event("600001")
    events = [
        event("600002", day=PREVIOUS - timedelta(days=1)),
        event("000639", "ST样本", day=PREVIOUS),
        target, event("600002", height=2),
    ]
    context = _market_context(events, target)
    assert context["previous_first_board_promotion_rate"] is None


def test_legacy_enrichment_recalculates_only_context_for_new_ratings():
    target, normal, excluded = cohort()
    baseline = enrichment(target, normal).model_copy(update={
        "return_20d_pct": 12.3, "kline_bar_count": 30,
        "dragon_tiger_on_list": True, "dragon_tiger_source": "recorded-dt",
        "dragon_tiger_net_buy_amount": 20_000_000,
        "popularity_rank": 18, "popularity_source": "recorded-popularity",
        "popularity_snapshot_at": CAPTURED,
    })
    legacy = baseline.model_copy(update={
        "feature_version": "first-board-enrichment-v4-hithink-market",
        "industry_first_board_count": 8, "industry_continued_board_count": 7,
        "industry_failed_count": 9, "industry_max_board_height": 15,
        "industry_first_limit_rank": 5,
        "previous_first_board_promotion_rate": 0.99,
        "market_first_board_seal_rate": 0.1,
    })
    original = legacy.model_dump()

    def calculate(snapshot):
        repository = SimpleNamespace(list_enrichment_for_date=lambda _day: [snapshot])
        return build_first_board_ratings(
            normal + excluded, trade_date=DAY, first_board_repository=repository,
            scoring_policy=build_default_scoring_policy(),
        ).candidates[0]

    expected, actual = calculate(baseline), calculate(legacy)
    assert actual.score == expected.score
    assert actual.score_breakdown == expected.score_breakdown
    projected = actual.facts.enrichment
    assert projected is not None
    assert projected.model_dump(exclude={"feature_version"}) == baseline.model_dump(
        exclude={"feature_version"},
    )
    assert projected.feature_version == legacy.feature_version + RELAY_CONTEXT_VERSION_SUFFIX
    assert legacy.model_dump() == original
    assert projected.created_at == legacy.created_at == CAPTURED


def test_current_enrichment_context_is_not_relabelled_or_rebuilt():
    target, normal, _excluded = cohort()
    current = enrichment(target, normal)
    assert adapt_enrichment_relay_context(
        current, event=target, events=normal,
    ) is current
