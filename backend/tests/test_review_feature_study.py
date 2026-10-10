"""Review feature statistics use observed, field-local denominators."""

from copy import deepcopy

import pytest

from app.agents.review_feature_study import build_feature_study


def study(positive, negative, *, excluded_count=0, positive_outcome="正收益"):
    return build_feature_study(
        positive, negative, excluded_count=excluded_count,
        basis="首板至观察日收盘", positive_outcome=positive_outcome,
    )


def feature(result, key):
    return next(item for item in result.features if item.key == key)


def buckets(result, key):
    return {bucket.label: bucket for bucket in feature(result, key).buckets}


def test_conditional_rate_is_not_positive_group_composition():
    positive = [{"position_label": "低位"}] * 6 + [{"position_label": "平台"}] * 4
    negative = [{"position_label": "低位"}] * 2 + [{"position_label": "平台"}] * 8
    result = study(positive, negative, excluded_count=12)
    low = buckets(result, "position_label")["低位"]
    assert result.baseline_rate == .5
    assert result.positive_count == result.negative_count == 10
    assert result.excluded_count == 12
    assert result.basis == "首板至观察日收盘"
    assert low.positive_count == 6
    assert low.negative_count == 2
    assert low.sample_size == 8
    assert low.positive_rate == .75  # 6/(6+2), not 6/10 positive-group composition.
    assert low.baseline_rate == .5
    assert low.delta_pp == 25
    assert "低位 60.0%" in feature(result, "position_label").positive_summary
    assert "6/8（75.0%）" in result.signals[0]
    assert "高25.0个百分点" in result.signals[0]


def test_every_field_uses_its_own_valid_denominator():
    positive = [
        {"position_label": "低位", "float_market_cap": 20e8},
        {"position_label": "低位", "float_market_cap": 20e8},
        {"position_label": "平台", "float_market_cap": 20e8},
        {"position_label": "平台"}, {}, {},
    ]
    negative = [{"position_label": "低位", "float_market_cap": 20e8}] * 3 + [{"float_market_cap": 40e8}] * 3
    result = study(positive, negative)
    assert result.baseline_rate == .5
    position = buckets(result, "position_label")["低位"]
    cap = buckets(result, "float_market_cap")["<30亿元"]
    assert position.baseline_rate == pytest.approx(4 / 7)
    assert position.positive_rate == .4
    assert position.delta_pp == pytest.approx((.4 - 4 / 7) * 100)
    assert cap.baseline_rate == pytest.approx(3 / 9)
    assert cap.positive_rate == .5
    assert cap.delta_pp == pytest.approx((.5 - 3 / 9) * 100)
    assert feature(result, "float_market_cap").positive_valid_count == 3
    assert "有效 3/6" in feature(result, "float_market_cap").positive_detail


@pytest.mark.parametrize(("key", "values", "labels", "expected"), [
    ("float_market_cap", [1e8, 29.9e8, 30e8, 60e8, 100e8],
     ["<30亿元", "30–60亿元", "60–100亿元", "≥100亿元"], [2, 1, 1, 1]),
    ("first_limit_minutes", [0, 565, 599.99, 600, 659.99, 660, 1439.99],
     ["10点前", "10–11点", "11点及以后"], [3, 2, 2]),
    ("break_count", [0, 0.0, 1, 2, 3], ["0次", "1次", "≥2次"], [2, 1, 2]),
    ("turnover_rate", [.1, 4.99, 5, 9.99, 10, 19.99, 20],
     ["<5%", "5–10%", "10–20%", "≥20%"], [2, 2, 2, 1]),
])
def test_fixed_bucket_boundaries_and_order(key, values, labels, expected):
    result = study([{key: value} for value in values], [])
    actual = feature(result, key).buckets
    assert [bucket.label for bucket in actual] == labels
    assert [bucket.positive_count for bucket in actual] == expected
    assert [bucket.negative_count for bucket in actual] == [0] * len(labels)
    assert sum(bucket.sample_size for bucket in actual) == len(values)
    assert feature(result, key).positive_valid_count == len(values)


@pytest.mark.parametrize(("key", "bad"), [
    ("float_market_cap", [0, -1]),
    ("first_limit_minutes", [-1, 1440]),
    ("break_count", [-1, .5, 2.1]),
    ("turnover_rate", [0, -1]),
])
def test_invalid_numeric_values_are_missing(key, bad):
    invalid = bad + [None, "5", True, False, float("nan"), float("inf"), -float("inf"), {}, 10 ** 1000]
    result = study([{key: value} for value in invalid], [])
    row = feature(result, key)
    assert row.positive_valid_count == 0
    assert row.positive_summary == "样本不足"
    assert sum(bucket.sample_size for bucket in row.buckets) == 0
    assert all(bucket.baseline_rate is None for bucket in row.buckets)


def test_missing_positions_and_stable_top_two_categories():
    profiles = [{"position_label": value} for value in [
        "低位", " 平台 ", "二波", "平台", "低位", "二波",
        "", "未知", "其他", "数据不足", "NULL", "Unknown", "--", 7, None,
    ]]
    result = study(profiles, profiles)
    row = feature(result, "position_label")
    assert row.positive_valid_count == 6
    assert row.positive_summary == "二波 33.3%；低位 33.3%"
    assert "平台 2/6（33.3%）" in row.positive_detail
    assert [bucket.label for bucket in row.buckets] == ["二波", "低位", "平台"]
    assert all(bucket.positive_rate is None for bucket in row.buckets)


def test_summary_statistics_and_explicit_first_seal_clock():
    profiles = [
        {"float_market_cap": cap * 1e8, "turnover_rate": turn, "first_limit_minutes": first}
        for cap, turn, first in zip([10, 20, 40, 100], [2, 4, 8, 20], [565, 590, 600, 605])
    ]
    result = study(profiles, profiles)
    cap = feature(result, "float_market_cap")
    assert cap.positive_summary == "30.0亿元"
    assert "中间50%：17.5–55.0亿元" in cap.positive_detail
    turn = feature(result, "turnover_rate")
    assert turn.positive_summary == "6.0%"
    assert "中间50%：3.5–11.0%" in turn.positive_detail
    first = feature(result, "first_limit_minutes")
    assert first.positive_summary == "09:50"
    assert "平均首次封板时间" in first.positive_detail


def test_valid_end_of_day_time_does_not_round_to_24_hours():
    result = study([{"first_limit_minutes": 1439.9}] * 3, [])
    assert feature(result, "first_limit_minutes").positive_summary == "23:59"


def test_numeric_summary_values_preserve_units_and_unrounded_statistics():
    positive = [
        {"float_market_cap": cap * 1e8, "first_limit_minutes": first, "turnover_rate": turn,
         "position_label": "低位", "break_count": 0}
        for cap, first, turn in zip([10.1, 20.2, 99], [565, 590, 601], [2.1, 4.25, 20])
    ]
    negative = [{"float_market_cap": 30e8, "first_limit_minutes": 610, "turnover_rate": 8}] * 3
    result = study(positive, negative)
    cap = feature(result, "float_market_cap")
    first = feature(result, "first_limit_minutes")
    turnover = feature(result, "turnover_rate")
    assert (cap.positive_value, cap.negative_value) == pytest.approx((20.2, 30))
    assert (first.positive_value, first.negative_value) == pytest.approx((1756 / 3, 610))
    assert (turnover.positive_value, turnover.negative_value) == pytest.approx((4.25, 8))
    for key in ("position_label", "break_count"):
        assert feature(result, key).positive_value is None
        assert feature(result, key).negative_value is None


def test_numeric_summary_values_require_three_valid_samples_per_field_and_group():
    positive = [{"float_market_cap": 20e8, "first_limit_minutes": 590, "turnover_rate": 4}] * 2 + [
        {"float_market_cap": 30e8, "first_limit_minutes": float("nan"), "turnover_rate": 0},
    ]
    negative = [{"first_limit_minutes": 600, "turnover_rate": 8}] * 3
    result = study(positive, negative)
    assert feature(result, "float_market_cap").positive_value == 20
    assert feature(result, "float_market_cap").negative_value is None
    assert feature(result, "first_limit_minutes").positive_value is None
    assert feature(result, "first_limit_minutes").negative_value == 600
    assert feature(result, "turnover_rate").positive_value is None
    assert feature(result, "turnover_rate").negative_value == 8


def test_small_groups_keep_counts_but_hide_summary_and_interpretation():
    positive = [{"position_label": "低位", "break_count": 0}] * 2
    negative = [{"position_label": "低位", "break_count": 0}] * 4
    result = study(positive, negative)
    row = feature(result, "position_label")
    assert row.positive_valid_count == 2
    assert row.negative_valid_count == 4
    assert row.positive_summary == "样本不足"
    assert "有效 2/2" in row.positive_detail
    assert row.negative_summary == "低位 100.0%"
    assert row.buckets[0].positive_rate == pytest.approx(2 / 6)
    assert "不足" in result.signals[0]
    assert "比例为" not in result.signals[0]


def test_rate_thresholds_depend_on_bucket_and_field_population_separately():
    result = study(
        [{"position_label": "低位"}] * 3,
        [{"position_label": "低位"}] + [{"position_label": "平台"}] * 2,
    )
    low = buckets(result, "position_label")["低位"]
    assert low.sample_size == 4
    assert low.positive_rate is None
    assert low.delta_pp is None
    assert low.baseline_rate == .5
    five = study([{"position_label": "低位"}] * 3, [{"position_label": "低位"}] * 2)
    assert five.baseline_rate == .6
    assert buckets(five, "position_label")["低位"].positive_rate == .6
    four = study([{"position_label": "低位"}] * 2, [{"position_label": "低位"}] * 2)
    assert four.baseline_rate is None
    assert buckets(four, "position_label")["低位"].baseline_rate is None


def test_no_comparison_group_and_empty_study_are_explicit():
    result = study([{"position_label": "低位"}] * 5, [])
    assert result.baseline_rate == 1
    assert feature(result, "position_label").negative_valid_count == 0
    assert "不足" in result.signals[0]
    empty = study([], [], excluded_count=8)
    assert empty.baseline_rate is None
    assert len(empty.features) == 5
    assert all(row.positive_summary == row.negative_summary == "样本不足" for row in empty.features)
    assert empty.excluded_count == 8


def test_market_outcome_semantics_and_signals_are_transparent():
    positive = [{"position_label": "低位"}] * 4 + [{"position_label": "平台"}] * 2
    negative = [{"position_label": "低位"}] + [{"position_label": "平台"}] * 5
    result = study(positive, negative, positive_outcome="达到3板")
    assert len(result.signals) == 1
    assert "样本达到3板比例为4/5（80.0%）" in result.signals[0]
    assert "高30.0个百分点" in result.signals[0]
    assert all(word not in result.model_dump_json() for word in ("下跌", "负收益", "胜率", "预测", "显著"))


def test_signal_can_report_supported_bucket_below_its_field_baseline():
    result = study(
        [{"position_label": "分散A"}, {"position_label": "分散B"}, {"position_label": "低位"}],
        [{"position_label": "低位"}] * 5,
    )
    assert "1/6（16.7%）" in result.signals[0]
    assert "低20.8个百分点" in result.signals[0]


def test_signal_selects_highest_supported_delta_within_each_feature():
    result = study(
        [{"position_label": "A"}] * 3 + [{"position_label": "B"}] * 3 + [{"position_label": "C"}],
        [{"position_label": "A"}] * 2 + [{"position_label": "B"}] * 6,
    )
    assert len(result.signals) == 1
    assert "首板位置：A" in result.signals[0]
    assert "3/5（60.0%）" in result.signals[0]
    assert "高13.3个百分点" in result.signals[0]
    assert buckets(result, "position_label")["C"].positive_rate is None


def test_neutral_signals_do_not_claim_a_direction():
    result = study([{"break_count": 0}] * 3, [{"break_count": 0}] * 3)
    assert "3/6（50.0%）" in result.signals[0]
    assert "基准相近" in result.signals[0]


def test_signals_are_limited_to_three_stable_features_without_mutation():
    positive = [{
        "position_label": "低位", "float_market_cap": 20e8,
        "first_limit_minutes": 590, "break_count": 0, "turnover_rate": 4,
    }] * 4 + [{
        "position_label": "平台", "float_market_cap": 40e8,
        "first_limit_minutes": 620, "break_count": 1, "turnover_rate": 8,
    }] * 2
    negative = positive[:1] + positive[4:] * 3
    before = deepcopy((positive, negative))
    result = study(positive, negative)
    reversed_result = study(list(reversed(positive)), list(reversed(negative)))
    assert result.model_dump() == reversed_result.model_dump()
    assert (positive, negative) == before
    assert len(result.signals) == 3
    assert "首板位置" in result.signals[0]
    assert "流通市值" in result.signals[1]
    assert "首次封板时间" in result.signals[2]
    assert [row.key for row in result.features] == [
        "position_label", "float_market_cap", "first_limit_minutes", "break_count", "turnover_rate",
    ]
