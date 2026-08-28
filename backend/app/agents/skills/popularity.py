"""Skill contract for current stock-popularity and hot-stock questions."""

from app.agents.skills.contract import AgentSkill, AgentSkillTool


POPULARITY_SKILL = AgentSkill(
    name="popularity",
    description=(
        "Answer current hot-stock, popularity, attention and ranking questions from "
        "the newest available provider snapshot."
    ),
    examples=(
        "有哪些票比较热门",
        "热股榜前20名",
        "同花顺人气榜有哪些股票",
        "哪些个股关注度高",
    ),
    required_tools=(
        AgentSkillTool(
            name="hot_stock_ranking",
            arguments={"period": "day", "limit": 20, "source": "auto"},
        ),
    ),
    answer_rules=(
        "Always use the newest fetched ranking snapshot and state its source and Beijing capture time.",
        "List stocks in factual rank order with rank, name and six-digit symbol; honor an explicit Top-N count.",
        "If the provider returned fewer rows than requested, disclose the actual returned count instead of filling gaps.",
        "Treat popularity as attention and crowding only; never present it as a forecast, score or buy signal.",
        "Never mix provider identities or describe Eastmoney rows as Tonghuashun rows.",
    ),
)
