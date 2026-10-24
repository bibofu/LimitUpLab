"""Ordered contracts for high-score pick review."""

from app.agents.tool_contracts import AgentToolSchema


REVIEW_TOOL_SCHEMAS = [
    AgentToolSchema(
        name="review_high_score_picks",
        time_mode="historical",
        dates=("start_date", "end_date", "as_of_date"),
        description=(
            "Run the Review Agent over each day's score-ranked Top10 first-board picks. "
            "Returns later outcomes, daily first-to-second-board success rates, the same-day "
            "full-market first-board baseline and exact D+1/3/5 coverage. start_date/end_date select "
            "candidate batches, not the outcome cutoff; omit as_of_date to observe through latest local data. "
            "Only set as_of_date when the user explicitly requests an observation cutoff."
        ),
        args_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD inclusive candidate batch start date."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD inclusive candidate batch end date; does not limit later observations."},
                "as_of_date": {
                    "type": ["string", "null"],
                    "description": "Optional independent observation cutoff YYYY-MM-DD, between end_date and the latest local data date (inclusive). Omit/null for latest local data date; never copy candidate end_date here unless the user explicitly requests that cutoff. An uncovered later cutoff is rejected, not clamped.",
                },
                "min_score": {"type": "number", "minimum": 0, "maximum": 100},
                "top_per_day": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["start_date", "end_date"],
        },
        returns=(
            "Review report with daily Top-pick versus full-market promotion comparisons, "
            "fixed candidate dates, independent as_of_date, D+1/3/5 horizon_coverage, tracked picks and "
            "grouping_definitions. live is a generation source, not premarket_final time-cohort status."
        ),
    ),
]
