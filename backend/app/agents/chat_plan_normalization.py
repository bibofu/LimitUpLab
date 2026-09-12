"""Validation and normalization for LLM-produced chat plans."""

import json
import re
from typing import Any

from app.agents.query_contract import (
    extract_market_event_type,
    looks_like_market_event_query,
)
from app.agents.tool_policy import (
    extract_kline_days as _extract_kline_days,
    extract_promotion_days as _extract_promotion_days,
    extract_stock_news_days as _extract_stock_news_days,
    extract_trade_date as _extract_trade_date,
    looks_like_broad_sector_ranking_question as _looks_like_broad_sector_ranking_question,
    looks_like_daily_board_promotion_question as _looks_like_daily_board_promotion_question,
    looks_like_first_board_position_question as _looks_like_first_board_position_question,
    looks_like_stock_kline_question as _looks_like_stock_kline_question,
    looks_like_stock_news_question as _looks_like_stock_news_question,
)
from app.models import AgentChatRequest
