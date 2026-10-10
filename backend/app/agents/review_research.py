"""Join descriptive candidate evidence with an independently reconstructed market cohort."""

from datetime import date
from typing import Iterable

from app.agents.review_feature_study import build_feature_study
from app.agents.review_market_leaders import build_market_leader_profiles
from app.review_research_models import (
    ReviewFeatureResearch, ReviewFeatureStudy, ReviewInsight,
)
from app.models import LimitUpEvent
from app.repositories import SQLiteFirstBoardRepository


def build_feature_research(
    *,
    candidate_study: dict,
    events: list[LimitUpEvent],
    repository: SQLiteFirstBoardRepository,
    end_date: date,
    trade_dates: Iterable[date],
) -> ReviewFeatureResearch:
    days = sorted({day for day in trade_dates if day <= end_date})
    start = days[max(0, len(days) - 20)] if days else end_date
    market = build_market_leader_profiles(
        events=events, repository=repository, start_date=start,
        end_date=end_date, trade_dates=days,
    )
    study = build_feature_study(
        market["positive_profiles"], market["negative_profiles"],
        excluded_count=market["unknown_count"],
        basis="近20个交易日内的全市场首板，按同一轮连续1→2→3板分组；未到观察日或链路缺失不计入比例。",
        positive_outcome="达到3板",
    )
    calendar_notes = []
    if not days:
        calendar_notes.append("交易日历不可用，无法确认20日观察窗；仅列截止日已发现的高标，市场比例暂不计算。")
    elif len(days) < 20:
        calendar_notes.append(f"本地可核验日历仅覆盖{len(days)}个交易日，市场观察窗不足20日。")
    candidate = ReviewFeatureStudy.model_validate(candidate_study)
    return ReviewFeatureResearch(
        candidate=candidate, market=study,
        cross_checks=compare_study_directions(candidate, study),
        market_start_date=start, market_end_date=end_date,
        market_leaders=market["leaders"],
        market_detected_count=market["detected_count"],
        market_matched_count=market["matched_count"],
        notes=[
            "候选按截至日可观察的累计涨跌分组，观察长度可能不同；市场按是否连续晋级3板分组，两种比例不能相互替代。",
            "各特征比例仅使用该特征有效且结局已知的样本；不足5条不展示比例。它们是本期观察，不是未来走强概率。",
            "位置、市值、封板、炸板与换手均回到首板当日，不能用高标时的数值替代首板特征。",
            *calendar_notes,
            *market["notes"],
        ],
    )


def compare_study_directions(candidate: ReviewFeatureStudy, market: ReviewFeatureStudy) -> list[str]:
    """Check numeric directions before asking a model to describe agreement."""
    checks = []
    candidate_features = {item.key: item for item in candidate.features}
    market_features = {item.key: item for item in market.features}
    dimensions = (
        ("float_market_cap", "流通市值中位数", ("更小", "相近", "更大")),
        ("first_limit_minutes", "平均首封时间", ("更早", "相近", "更晚")),
        ("turnover_rate", "换手率中位数", ("更低", "相近", "更高")),
    )
    for key, label, words in dimensions:
        groups = [features.get(key) for features in (candidate_features, market_features)]
        if any(group is None or group.positive_value is None or group.negative_value is None for group in groups):
            checks.append(f"{label}：至少一组有效数据不足，不能比较两类样本的方向。")
            continue
        directions = []
        for group in groups:
            # Use the displayed precision; sub-minute clock differences are not a signal.
            delta = round(group.positive_value - group.negative_value, 0 if key == "first_limit_minutes" else 1)
            directions.append(1 if delta > 0 else -1 if delta < 0 else 0)
        left, right = directions
        verdict = "差异接近显示精度，暂不归纳一致方向" if 0 in directions else "方向一致" if left == right else "方向相反"
        checks.append(f"{label}：候选较好组比其较差组{words[left + 1]}，市场3板组比未达3板组{words[right + 1]}；{verdict}。")
    return checks


def deterministic_research_insights(research: ReviewFeatureResearch) -> list[ReviewInsight]:
    """Keep useful evidence visible before the optional model interpretation arrives."""
    def observation(study: ReviewFeatureStudy) -> str:
        if study.signals:
            return study.signals[0][:180]
        return "当前有效对照样本不足，先查看两组特征与缺失数量，暂不能提炼可靠的差异。"

    return [
        ReviewInsight(scope="candidate", title="候选表现：先看组间差异", detail=observation(research.candidate)),
        ReviewInsight(scope="market", title="市场高标：回到首板验证", detail=observation(research.market)),
        ReviewInsight(
            scope="synthesis", title="两类样本：核对共性与分歧",
            detail="".join(research.cross_checks)[:180] or "当前有效数据不足，暂不能比较两类样本是否具有一致的特征差异。",
        ),
    ]
