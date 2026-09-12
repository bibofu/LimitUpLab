"""Central policy engine for grounding Agent answers with required tools."""

from __future__ import annotations

import re
from app.agents.tool_schema import validate_schema_value as _validate_schema_value
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, TypedDict

from app.agents.limit_up_execution import execute_limit_up_query
from app.agents.query_contract import (
    build_market_event_query_contract,
    build_limit_up_query_contract,
    extract_board_filters as contract_board_filters,
    extract_market_event_type,
    extract_result_limit,
    extract_market_segment as contract_market_segment,
    extract_trade_date as contract_trade_date,
    looks_like_market_event_query,
)
from app.post_limit_query_contract import (
    build_post_limit_query_contract,
    looks_like_post_limit_path_question,
    looks_like_post_limit_question,
    looks_like_post_limit_statistics_question,
)
from app.agents.tools import (
    AgentToolRegistry,
    ToolResult,
    compact_first_board_position_groups,
    compact_prediction_quality_audit,
)
from app.models import (
    AgentChatRequest,
    AgentToolTrace,
    FirstBoardRating,
    FirstBoardRatingsResponse,
)






RepairAction = Callable[[AgentChatRequest, QuestionSignals, ToolExecution, str | None], None]




















































































# Parse an optional date argument before applying it to a query.
# A None result represents the unavailable or inapplicable branch; callers must check it before


# Construct a calendar date while handling invalid year/month/day combinations.
# A None result represents the unavailable or inapplicable branch; callers must check it before
