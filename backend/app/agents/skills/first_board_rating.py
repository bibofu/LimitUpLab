"""Skill contract for explainable first-board ratings and candidate questions."""

from app.agents.skills.contract import AgentSkill, AgentSkillTool


FIRST_BOARD_RATING_SKILL = AgentSkill(
    name="first_board_rating",
    description=(
        "Rank and explain first-board candidates using structured rating facts, "
        "score breakdowns, position assessment, reasons, risks and missing data."
    ),
    examples=(
        "哪些首板候选评分靠前",
        "为什么这只股票评分高",
        "首板评级前10名",
        "这只票的主要风险是什么",
    ),
    required_tools=(AgentSkillTool(name="first_board_ratings"),),
    answer_rules=(
        "When the user gives no date, use the latest local completed trading day and state its ISO date.",
        "For rankings, default to at most 10 candidates and include rank, name, symbol, rating, score and concise high-score reasons.",
        "For one-stock explanations, distinguish company facts, factor scores, supporting evidence, risks and missing inputs.",
        "Use the recorded first-board position classification and seal facts exactly; do not invent fundamentals, news or price behavior.",
        "Describe the output as a research rating, not a prediction guarantee or direct trading recommendation.",
    ),
)
