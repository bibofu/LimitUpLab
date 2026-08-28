"""Skill contract for limit-up, first-board and continued-board pool queries."""

from app.agents.skills.contract import AgentSkill, AgentSkillTool


LIMIT_UP_POOL_SKILL = AgentSkill(
    name="limit_up_pool",
    description=(
        "Query and summarize A-share limit-up event pools, including first-board, "
        "continued-board, failed-board, market-segment, topic and board-height filters."
    ),
    examples=(
        "首板票有哪些",
        "今天二连板有哪些",
        "创业板涨停股票",
        "列出炸板未回封股票",
    ),
    required_tools=(AgentSkillTool(name="limit_up_events"),),
    answer_rules=(
        "When the user gives no date, use the latest local completed trading day and state its ISO date.",
        "Preserve every explicit date, market, board-height, event-status, topic, sort and result-count filter.",
        "State the matched count before listing stocks; list name and six-digit symbol in the requested order.",
        "Never add a stock that is absent from the filtered event facts or confuse failed boards with intraday breaks that later resealed.",
        "For exhaustive requests, include every returned match exactly once; otherwise keep the list compact.",
    ),
)
