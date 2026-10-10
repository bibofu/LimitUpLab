"""Ordered contracts for rating evaluation, critique and policy review."""

from app.agents.tool_contracts import AgentToolSchema


REVIEW_TOOL_SCHEMAS = [
    AgentToolSchema(
        name="rating_backtest",
        time_mode="historical",
        dates=("start_date", "end_date"),
        description="回测一段日期内首板评分 A/B/C/D 的后续表现，并输出评分自我评价。",
        args_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD inclusive start date."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD inclusive end date."},
                "failure_limit": {"type": "integer", "minimum": 0, "maximum": 30},
            },
            "required": ["start_date", "end_date"],
        },
        returns="Rating bucket performance, weak high-rated samples and self-evaluation observations.",
    ),
    AgentToolSchema(
        name="first_board_critic",
        time_mode="historical",
        dates=("trade_date",),
        description="Critique one first-board rating by checking support evidence, counter evidence, missing data and confidence adjustment.",
        args_schema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Six-digit A-share symbol."},
                "trade_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD first-board date; omit or null for latest local date.",
                },
            },
            "required": ["symbol"],
        },
        returns="Critic verdict, supporting evidence, opposing evidence, missing data and suggested confidence.",
    ),
    AgentToolSchema(
        name="rating_evaluation",
        time_mode="historical",
        dates=("start_date", "end_date"),
        collection="items",
        description="Evaluate saved first-board rating predictions against later outcomes and summarize successes, misses and false negatives.",
        args_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD inclusive start date."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD inclusive end date."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["start_date", "end_date"],
        },
        returns="Prediction evaluation labels, lessons, scoring suggestions and summary counts.",
    ),
    AgentToolSchema(
        name="review_high_score_picks",
        time_mode="historical",
        dates=("start_date", "end_date"),
        description=(
            "Run the Review Agent over each day's score-ranked Top10 first-board picks. "
            "Returns later outcomes, daily first-to-second-board success rates, the same-day "
            "full-market first-board baseline, successful/failed patterns and scoring adjustments."
        ),
        args_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD inclusive start date."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD inclusive end date."},
                "min_score": {"type": "number", "minimum": 0, "maximum": 100},
                "top_per_day": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["start_date", "end_date"],
        },
        returns=(
            "Review report with daily Top-pick versus full-market promotion comparisons, "
            "tracked picks, findings, patterns, scoring bias and adjustment suggestions."
        ),
    ),
    AgentToolSchema(
        name="scoring_policy_status",
        time_mode="current",
        notes="Current policy configuration, not a historical policy snapshot.",
        description="读取当前评分 Champion、历史 Challenger、最近一次样本外优化结果和晋级门槛，不修改线上权重。",
        args_schema={"type": "object", "properties": {}, "required": []},
        returns="Current scoring policy, factor weights, latest Challenger comparison and promotion status.",
    ),
]
