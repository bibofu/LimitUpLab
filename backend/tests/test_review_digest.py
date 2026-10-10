"""The new digest follows the user-selected windows and exact price thresholds."""

from datetime import date
from types import SimpleNamespace as Obj
from unittest.mock import Mock, patch

import pytest

from app.agents.review_digest import (
    build_digest_overview, build_review_digest, candidate_stock, concept_labels, performance_group,
)
from app.models import ReviewPromotionComparison
from app.review_digest_models import DigestStock


DAYS = [date(2026, 9, day) for day in (21, 22, 23, 24, 25, 28, 29)]


def pick(day=DAYS[1], symbol="600001"):
    return Obj(trade_date=day, symbol=symbol, name="测试", time_cohort="close_baseline")


def prediction():
    return Obj(facts_json={"industry": "软件", "concept": "国产软件;人工智能;人工智能",
        "first_limit_time": "09:35:00", "break_count": 0, "turnover_rate": 5.8,
        "enrichment": {"position": {"primary": {"regime": "low", "label": "低位启动首板"}},
                       "float_market_cap": 4e9, "float_market_cap_source": "derived_from_amount_and_turnover"}})


def bar(day, close, symbol="600001"):
    return Obj(symbol=symbol, trade_date=day, close=close)


def test_provider_concept_separators_count_individual_deduplicated_themes():
    assert concept_labels("固态电池+新能源;固态电池，汽车、未知|软件") == ["固态电池", "新能源", "汽车", "软件"]
    assert concept_labels(None) == []


@pytest.mark.parametrize("last,expected", [(10.98, "excellent"), (10.97999, "ordinary"), (11, "excellent"),
                                            (9.5, "ordinary"), (9.49999, "weak"), (10, "ordinary")])
def test_price_thresholds_are_inclusive_98_and_strict_minus_5_without_rounding(last, expected):
    stock = candidate_stock(pick(), prediction(), [bar(DAYS[1], 10), bar(DAYS[-1], last)], DAYS, DAYS[-1])
    assert performance_group(stock) == expected


@pytest.mark.parametrize("bars,calendar", [
    ([bar(DAYS[1], 10), bar(DAYS[-2], 12)], DAYS),
    ([bar(DAYS[-1], 12)], DAYS),
    ([bar(DAYS[1], 10), bar(date(2026, 9, 30), 12)], DAYS),
    ([bar(DAYS[1], 10), bar(DAYS[-1], 12), bar(DAYS[-1], 11)], DAYS),
    ([bar(DAYS[1], 10), bar(DAYS[-1], 12)], DAYS[:-1]),
])
def test_missing_exact_prices_calendar_or_conflicting_close_stays_unobserved(bars, calendar):
    stock = candidate_stock(pick(), prediction(), bars, calendar, DAYS[-1])
    assert stock.return_pct is None and performance_group(stock) == "missing"
    assert stock.data_missing


def test_exact_cutoff_decline_overrides_earlier_peak_and_future_recovery():
    stock = candidate_stock(pick(), prediction(), [bar(DAYS[1], 10), bar(DAYS[2], 12),
        bar(DAYS[-1], 9.4), bar(date(2026, 9, 30), 13)], DAYS, DAYS[-1])
    assert performance_group(stock) == "weak" and stock.return_pct == -6
    assert stock.observed_days == 5
    assert stock.concepts == ["国产软件", "人工智能"]
    assert stock.position_label == "低位启动首板" and stock.break_count == 0


def test_digest_excludes_cutoff_and_older_batch_deduplicates_and_keeps_calendar_holes():
    picks = [pick(DAYS[0], "old"), pick(), pick(), pick(DAYS[-2], "weak"), pick(DAYS[-1], "today")]
    predictions = {(item.trade_date, item.symbol): prediction() for item in picks}
    repo = Mock()
    repo.list_daily_bars_for_symbols.return_value = [bar(DAYS[1], 10), bar(DAYS[-1], 10.98),
        bar(DAYS[-2], 10, "weak"), bar(DAYS[-1], 9.4, "weak")]
    with patch("app.agents.review_digest.build_digest_leaders", return_value=([], [])):
        digest = build_review_digest(picks=picks, predictions=predictions, promotion_comparisons=[],
            events=[], repository=repo, end_date=DAYS[-1], trade_dates=DAYS)
    assert digest.candidate_dates == DAYS[1:-1] and digest.market_dates == DAYS[-5:]
    assert digest.overview.candidate_count == 2
    assert digest.overview.excellent_count == 1 and digest.overview.weak_count == 1
    assert digest.excellent.stocks[0].observed_days == 5 and digest.weak.stocks[0].observed_days == 1
    assert any("批次缺失" in note and DAYS[2].isoformat() in note for note in digest.notes)
    assert any("当日成交额/换手率推算" in note for note in digest.notes)
    assert digest.excellent.distributions[0].baseline_total_count == 2
    assert repo.list_daily_bars_for_symbols.call_args.kwargs["end_date"] == DAYS[-1]


def comparison(day, candidate_k, candidate_n, market_k, market_n, ready=True):
    return ReviewPromotionComparison(
        trade_date=day, next_trade_date=DAYS[-1], outcome_ready=ready,
        top_pick_sample_size=candidate_n, top_pick_promoted_count=candidate_k,
        top_pick_promotion_rate=candidate_k / candidate_n if ready else None,
        market_first_board_sample_size=market_n, market_promoted_count=market_k,
        market_promotion_rate=market_k / market_n if ready else None,
    )


def test_overall_rate_uses_pooled_counts_and_best_day_relative_to_own_market():
    comparisons = [comparison(DAYS[1], 1, 2, 8, 10), comparison(DAYS[2], 3, 10, 1, 10),
                   comparison(DAYS[3], 0, 10, 0, 20), comparison(DAYS[4], 0, 10, 0, 20, False)]
    overview = build_digest_overview(comparisons, DAYS[1:-1], 32, 5, 7, 18, 2)
    assert overview.candidate_promotion_rate == 4 / 22
    assert overview.market_promotion_rate == 9 / 40
    assert overview.comparable_days == 3 and overview.outperform_days == 1
    assert overview.best_date == DAYS[2]  # 30% beats its market, although day one has 50%.
    assert "低于" in overview.headline and "可比较的3天" in overview.headline
    assert overview.candidate_count == 32 and overview.unobserved_count == 2


def test_no_comparable_data_never_claims_zero_rate_or_a_best_day():
    overview = build_digest_overview([], [], 0, 0, 0, 0, 0)
    assert overview.candidate_promotion_rate is None and overview.best_date is None
    assert "暂无可同时核验" in overview.headline and "0.0%" not in overview.headline


def test_unknown_position_and_midnight_sentinel_are_missing():
    pred = prediction()
    pred.facts_json["first_limit_time"] = "00:00:00"
    pred.facts_json["enrichment"]["position"]["primary"] = {"regime": "unclassified", "label": "结构不明"}
    stock = candidate_stock(pick(), pred, [bar(DAYS[1], 10), bar(DAYS[-1], 11)], DAYS, DAYS[-1])
    assert stock.position_label is None and stock.first_limit_time is None
    assert performance_group(stock) == "excellent"
