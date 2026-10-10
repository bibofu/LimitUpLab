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
    return ReviewFeatureResearch(
        candidate=ReviewFeatureStudy.model_validate(candidate_study), market=study,
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
            scope="synthesis", title="观察重点：寻找重复出现的线索",
            detail="优先核对候选较好组与市场3板组是否出现相同特征；方向不一致时保留分歧。单一特征的本期比例不能作为未来预测，样本少或缺失多的项目需继续观察。",
        ),
    ]
