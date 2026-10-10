"""Group summaries describe supported marginal distributions with explicit coverage."""

from copy import deepcopy

import pytest

from app.agents.review_digest_profiles import build_digest_group
from app.review_digest_models import DigestStock


def stock(**fields):
    return DigestStock(symbol="600001", name="样本", **fields)


def distribution(group, key):
    return next(item for item in group.distributions if item.key == key)


def counts(group, key):
    return {item.label: item.count for item in distribution(group, key).buckets}


def test_candidate_shares_are_group_composition_against_all_candidates():
    selected = [stock(position_label="低位")] * 6 + [stock(position_label="平台")] * 2
    baseline = selected + [stock(position_label="平台")] * 12
    group = build_digest_group("excellent", selected, baseline)
    item = distribution(group, "position_label")
    low = next(bucket for bucket in item.buckets if bucket.label == "低位")
    assert (low.count, low.share, low.baseline_count, low.baseline_share) == (6, .75, 6, .3)
    assert group.observations[0].id == "excellent:position_label:低位"
    assert "6/8条（75.0%）" in group.observations[0].text
    assert "6/20条（30.0%）" in group.observations[0].text
    assert "高45.0个百分点" in group.observations[0].text
    assert group.summary == "下面几项特征与全部候选的分布存在差异。"
    assert "成功" not in group.summary


def test_field_specific_denominators_and_missing_coverage():
    selected = [stock(position_label="低位", industry="行业A")] * 3 + [stock(position_label="低位")]
    baseline = selected + [stock(position_label="平台", industry="行业B")] * 6 + [stock()]
    group = build_digest_group("weak", selected, baseline)
    position = distribution(group, "position_label")
    industry = distribution(group, "industry")
    assert (position.valid_count, position.baseline_valid_count) == (4, 10)
    assert (industry.valid_count, industry.baseline_valid_count) == (3, 9)
    assert "本组有效3/4条，缺失1条" in industry.note
    assert "全部候选有效9/11条，缺失2条" in industry.note
    assert group.observations[1].sample_size == 3


def test_multilabel_concepts_are_deduplicated_per_record():
    selected = [stock(concepts=["AI", "AI", " AI ", "机器人", "未知", ""])] * 3
    group = build_digest_group("leaders", selected)
    item = distribution(group, "concepts")
    assert item.valid_count == 3
    assert counts(group, "concepts") == {"AI": 3, "机器人": 3}
    assert sum(bucket.share for bucket in item.buckets) == 2
    assert "超过100%" in item.note


@pytest.mark.parametrize(("key", "values", "expected"), [
    ("float_market_cap", [1e8, 30e8, 60e8, 100e8],
     {"<30亿元": 1, "30–60亿元": 1, "60–100亿元": 1, "≥100亿元": 1}),
    ("break_count", [0, 1, 2, 10], {"0次": 1, "1次": 1, "≥2次": 2}),
    ("turnover_rate", [.01, 5, 10, 20], {"<5%": 1, "5–10%": 1, "10–20%": 1, "≥20%": 1}),
    ("next_open_pct", [-.1, 0, 2.99, 3, 6.99, 7], {"低开": 1, "平开": 1, "高开0–3%": 1, "高开3–7%": 2, "高开≥7%": 1}),
    ("second_break_count", [0, 1, 2], {"0次": 1, "1次": 1, "≥2次": 1}),
    ("second_turnover_rate", [4.99, 5, 10, 20], {"<5%": 1, "5–10%": 1, "10–20%": 1, "≥20%": 1}),
])
def test_numeric_bucket_boundaries(key, values, expected):
    group = build_digest_group("leaders", [stock(**{key: value}) for value in values])
    assert counts(group, key) == expected


@pytest.mark.parametrize("key", ["first_limit_time", "second_limit_time"])
def test_seal_time_buckets_exclude_nontrading_and_invalid_times(key):
    valid = ["09:25", "09:25:59", "09:30", "09:59:59", "10:00", "11:30", "13:00", "15:00"]
    invalid = ["00:00", "09:24", "09:26", "11:30:01", "12:00", "15:00:01", "25:00", "oops", "09:30+08:00", None]
    group = build_digest_group("leaders", [stock(**{key: value}) for value in valid + invalid])
    assert counts(group, key) == {"09:25竞价": 2, "09:30–10:00": 2, "10:00–11:30": 2, "午后": 2}
    assert distribution(group, key).valid_count == 8


def test_invalid_numeric_and_category_values_are_not_zero_or_real_labels():
    rows = [stock(float_market_cap=value, turnover_rate=value, position_label="结构不明")
            for value in [None, 0, -1, float("nan"), float("inf")]]
    rows.append(DigestStock.model_construct(symbol="x", name="x", break_count=.5, float_market_cap=True))
    group = build_digest_group("leaders", rows)
    for key in ["float_market_cap", "turnover_rate", "position_label", "break_count"]:
        assert distribution(group, key).valid_count == 0
    assert group.observations == []


def test_exact_ten_percentage_points_is_eligible_and_smaller_differences_are_not():
    selected = [stock(industry="A")] * 3 + [stock(industry="B")] * 2
    baseline = selected + [stock(industry="A")] + [stock(industry="B")] * 4
    group = build_digest_group("excellent", selected, baseline)
    assert len(group.observations) == 1  # 60% vs 40%, B has insufficient within-group support.
    exactly = build_digest_group("excellent", selected, selected + [stock(industry="A")] * 2 + [stock(industry="B")] * 3)
    assert "高10.0个百分点" in exactly.observations[0].text
    close = build_digest_group("excellent", selected, selected * 3 + [stock(industry="B")])
    assert close.observations == []
    assert "特征差异不突出" in close.summary


def test_underrepresentation_requires_observed_support_and_reports_lower_share():
    selected = [stock(industry="A")] * 3 + [stock(industry="B")] * 3
    baseline = selected + [stock(industry="A")] * 6
    group = build_digest_group("weak", selected, baseline)
    assert group.observations[0].id == "weak:industry:A"
    assert "低25.0个百分点" in group.observations[0].text
    assert group.observations[0].text.startswith("较差组")


def test_small_empty_groups_and_missing_baselines_do_not_force_findings():
    assert build_digest_group("excellent", []).distributions == []
    assert build_digest_group("leaders", []).observations == []
    small = build_digest_group("leaders", [stock(industry="A")] * 2)
    assert small.observations == [] and "不足" in small.summary
    missing = build_digest_group("excellent", [stock(industry="A")] * 3)
    assert missing.observations == [] and "缺少全部候选对照" in missing.summary
    same = build_digest_group("weak", [stock(industry="A")] * 3, [stock(industry="A")] * 9)
    assert same.observations == [] and "特征差异不突出" in same.summary


def test_leaders_ignore_baseline_and_have_only_descriptive_observations():
    group = build_digest_group("leaders", [stock(industry="A")] * 3, [stock(industry="B")] * 8)
    item = distribution(group, "industry")
    assert item.baseline_valid_count is None and item.baseline_total_count is None
    assert all(bucket.baseline_count is None and bucket.baseline_share is None for bucket in item.buckets)
    assert "3/3条（100.0%）" in group.observations[0].text
    assert group.summary == "先看首板时的画像，再看二板当天如何封板。"
    assert all(word not in group.summary for word in ["成功", "优势", "高于", "低于", "全部候选"])


def complete_stock(**overrides):
    return stock(**{
        "position_label": "低位", "industry": "行业A", "concepts": ["题材A"],
        "float_market_cap": 20e8, "first_limit_time": "09:25", "break_count": 0, "turnover_rate": 4,
        "second_open_pct": 5, "second_limit_time": "10:30", "second_break_count": 1,
        "second_turnover_rate": 8, "second_board_shape": "换手二板", **overrides,
    })


def test_leader_selection_has_three_first_board_and_two_second_board_dimensions():
    group = build_digest_group("leaders", [complete_stock()] * 4)
    assert len(group.observations) == 11
    assert len(set(item.id for item in group.observations)) == 11
    selected = [item for item in group.observations if item.id in group.selected_observation_ids]
    assert [item.dimension for item in selected] == ["position_label", "industry", "concepts", "second_board_shape", "second_limit_time"]
    assert all(item.id in {obs.id for obs in group.observations} for item in selected)
    assert "各特征分别统计" in group.notes[0]


def test_available_second_board_shape_is_selected_without_other_second_board_facts():
    group = build_digest_group("leaders", [stock(industry="A", second_board_shape="一字二板", second_break_count=0)] * 3)
    assert any(":second_board_shape:" in item for item in group.selected_observation_ids)
    assert all(":second_break_count:" not in item for item in group.selected_observation_ids)
    assert any("二板形态" in item.text for item in group.observations)


def test_candidate_default_selection_is_cross_dimension_priority_and_input_stable():
    rows = [complete_stock()] * 3 + [stock(position_label="平台", industry="行业B")]
    baseline = rows + [stock(position_label="平台", industry="行业B", concepts=["题材B"], float_market_cap=80e8,
                             first_limit_time="13:30", break_count=3, turnover_rate=25)] * 8
    before = deepcopy((rows, baseline))
    group = build_digest_group("excellent", rows, baseline)
    reverse = build_digest_group("excellent", list(reversed(rows)), list(reversed(baseline)))
    assert group.observations == reverse.observations
    assert group.distributions == reverse.distributions
    assert group.selected_observation_ids == reverse.selected_observation_ids
    assert (rows, baseline) == before
    assert [item.dimension for item in group.observations[:3]] == ["position_label", "industry", "concepts"]
    assert len(group.selected_observation_ids) == 3
    assert all(not item.key.startswith("second_") for item in group.distributions)


def test_next_opening_comparison_keeps_zero_and_uses_its_own_valid_denominator():
    rows = [stock(next_open_pct=7)] * 3 + [stock(next_open_pct=0), stock()]
    baseline = rows + [stock(next_open_pct=-2)] * 5
    group = build_digest_group("excellent", rows, baseline)
    item = distribution(group, "next_open_pct")
    assert (item.valid_count, item.total_count, item.baseline_valid_count) == (4, 5, 9)
    assert counts(group, "next_open_pct")["平开"] == 1
    observed = next(obs for obs in group.observations if obs.dimension == "next_open_pct")
    assert "3/4条（75.0%）" in observed.text and "3/9条（33.3%）" in observed.text
    assert observed.id in group.selected_observation_ids
    assert any("不能当作首板时已知" in note for note in group.notes)


def test_leader_selection_preserves_next_opening_and_second_board_without_truncating_ids():
    group = build_digest_group("leaders", [complete_stock(next_open_pct=7)] * 4)
    lookup = {item.id: item.dimension for item in group.observations}
    selected = [lookup[key] for key in group.selected_observation_ids]
    assert len(selected) == 5
    assert "next_open_pct" in selected and "second_board_shape" in selected
    assert "second_limit_time" in selected
    assert all(item.key != "second_open_pct" for item in group.distributions)
