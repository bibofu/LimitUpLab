"""Compact comparisons must retain their own denominators and descriptive scope."""

from copy import deepcopy

import pytest

from app.agents.review_features import build_review_feature_summary
from app.models import ReviewAgentReportResponse


def profiles(positions=None, caps=None, times=None):
    size = len(positions if positions is not None else caps if caps is not None else times)
    return [dict(
        position_label=positions[index] if positions is not None else None,
        float_market_cap=caps[index] * 100_000_000 if caps is not None else None,
        first_limit_minutes=times[index] if times is not None else None,
    ) for index in range(size)]


def test_position_compares_the_same_positive_group_label_with_each_valid_denominator():
    positive = profiles(positions=["低位启动"] * 13 + ["高位震荡"] * 10 + [None])
    negative = profiles(positions=["低位启动"] * 6 + ["高位震荡"] * 20 + [None, "未知"])
    original = deepcopy((positive, negative))
    summary = build_review_feature_summary(positive, negative)
    assert (summary.positive_count, summary.negative_count) == (24, 28)
    assert [card.key for card in summary.cards] == ["position", "market_cap", "first_seal"]
    card = summary.cards[0]
    assert card.positive.text == "低位启动 · 56.5%"
    assert card.negative.text == "低位启动 · 23.1%"
    assert (card.positive.valid_count, card.negative.valid_count) == (23, 26)
    assert "13/23" in card.positive.detail
    assert "6/26" in card.negative.detail
    assert "更高" in card.observation and "不是胜率" in card.observation
    assert (positive, negative) == original


def test_position_ties_are_stable_and_an_observed_absent_label_is_a_real_zero():
    positive = profiles(positions=["B", "A", "B", "A"])
    negative = profiles(positions=["C"] * 4)
    first = build_review_feature_summary(positive, negative).cards[0]
    reversed_card = build_review_feature_summary(list(reversed(positive)), negative).cards[0]
    assert first == reversed_card
    assert first.positive.text == "A · 50.0%"
    assert first.negative.text == "A · 0.0%"
    assert first.negative.valid_count == 4


def test_market_cap_uses_median_and_interpolated_iqr_in_hundred_million_yuan():
    summary = build_review_feature_summary(profiles(caps=[1, 2, 3, 4, 100]), profiles(caps=[2, 3, 4, 5, 6]))
    card = summary.cards[1]
    assert card.positive.text == "3.0 亿元"
    assert card.negative.text == "4.0 亿元"
    assert card.positive.detail == "中间50%：2.0–4.0 亿元"
    assert card.negative.detail == "中间50%：3.0–5.0 亿元"
    assert card.observation == "正收益组中位市值偏小，中间50%区间重叠。"


def test_market_cap_marks_disjoint_iqrs_without_inventing_significance():
    card = build_review_feature_summary(profiles(caps=[8, 9, 10]), profiles(caps=[1, 2, 3])).cards[1]
    assert card.positive.detail == "中间50%：8.5–9.5 亿元"
    assert card.observation == "正收益组中位市值偏大，中间50%区间不重叠。"


def test_market_cap_similarity_uses_only_the_display_precision():
    card = build_review_feature_summary(profiles(caps=[44.001] * 3), profiles(caps=[44.004] * 3)).cards[1]
    assert card.positive.text == card.negative.text == "44.0 亿元"
    assert "相近" in card.observation


@pytest.mark.parametrize(("positive", "negative", "direction"), [
    ([570, 600, 630], [564, 594, 624], "晚约6分钟"),
    ([564, 594, 624], [570, 600, 630], "早约6分钟"),
    ([600, 600, 600], [600, 600, 601], "相近"),
])
def test_first_seal_is_a_clock_mean_and_reports_direction_without_a_quality_claim(positive, negative, direction):
    card = build_review_feature_summary(profiles(times=positive), profiles(times=negative)).cards[2]
    assert direction in card.observation
    assert "不代表" in card.observation
    if positive == [570, 600, 630]:
        assert card.positive.text == "10:00"
        assert card.negative.text == "09:54"


def test_invalid_fields_are_missing_and_each_card_has_its_own_valid_count():
    invalid = [None, True, float("nan"), float("inf"), -1, 0, "100"]
    positive = [dict(float_market_cap=value, first_limit_minutes=value, position_label=value) for value in invalid]
    positive += profiles(positions=["低位启动"] * 3, caps=[30, 40, 50], times=[570, 580, 590])
    positive.append(dict(position_label=" 未分类 ", first_limit_minutes=1440, float_market_cap=-50))
    negative = profiles(positions=["低位启动"] * 3, caps=[30, 40, 50], times=[570, 580, 590])
    summary = build_review_feature_summary(positive, negative)
    assert [card.positive.valid_count for card in summary.cards] == [4, 3, 4]
    assert all(card.positive.sample_size == len(positive) for card in summary.cards)
    assert summary.cards[0].positive.detail.startswith("3/4")


@pytest.mark.parametrize("short_group", ["positive", "negative"])
def test_one_small_group_prevents_direction_claims_for_every_card(short_group):
    full = profiles(positions=["低位启动"] * 3, caps=[10, 20, 30], times=[570, 580, 590])
    groups = (full[:2], full) if short_group == "positive" else (full, full[:2])
    summary = build_review_feature_summary(*groups)
    for card in summary.cards:
        value = getattr(card, short_group)
        assert value.text == "样本不足"
        assert value.valid_count == value.sample_size == 2
        assert "少于3个" in card.observation and "暂不比较" in card.observation
        assert not any(word in card.observation for word in ("偏大", "偏小", "更高", "更低", "早约", "晚约"))


def test_empty_groups_produce_three_missing_cards_instead_of_zero_statistics():
    summary = build_review_feature_summary([], [])
    assert summary.positive_count == summary.negative_count == 0
    for card in summary.cards:
        assert card.positive.text == card.negative.text == "样本不足"
        assert card.positive.valid_count == card.negative.valid_count == 0


def test_a_large_group_does_not_make_a_sparse_field_comparable():
    full = profiles(positions=["低位启动"] * 5, caps=[10, 20, 30, 40, 50], times=[570] * 5)
    sparse = deepcopy(full)
    for item in sparse[2:]:
        item["float_market_cap"] = None
    summary = build_review_feature_summary(sparse, full)
    assert summary.positive_count == 5
    assert summary.cards[0].positive.valid_count == 5
    assert summary.cards[1].positive.valid_count == 2
    assert summary.cards[1].positive.text == "样本不足"
    assert "2/5" in summary.cards[1].positive.detail
    assert "暂不比较" in summary.cards[1].observation


def test_legacy_reports_accept_absent_feature_summary_and_new_reports_round_trip():
    report = ReviewAgentReportResponse(
        start_date="2026-09-28", end_date="2026-09-30", sample_size=0,
        success_count=0, failed_count=0, pending_count=0, confidence=0, generated_by="fixture",
    )
    assert report.feature_summary is None and report.summary_headline is None
    report.feature_summary = build_review_feature_summary([], [])
    report.summary_headline = "本期有效样本不足，三项特征暂不比较。"
    assert ReviewAgentReportResponse.model_validate_json(report.model_dump_json()) == report
