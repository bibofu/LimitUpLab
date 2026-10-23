"""Registry and compact field definitions generated from output models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.agents.tool_output_models import (
    DragonTigerPayload,
    DragonTigerRow,
    FirstBoardFilterPayload,
    FirstBoardRatingRow,
    FirstBoardRatingsPayload,
    HotStockPayload,
    HotStockRow,
    LimitUpEventsPayload,
    MarketEventPoolPayload,
    MarketEventRow,
    MarketSummaryPayload,
    PostLimitCandidateRow,
    PostLimitPathPayload,
    PostLimitPathRow,
    PostLimitScreenPayload,
    PostLimitStatisticsPayload,
    RecentLimitUpReturnRow,
    RecentReturnsPayload,
    ScoringPolicyStatusPayload,
)
from app.models import (
    AgentEvaluationItem,
    AgentEvaluationResponse,
    DailyBoardPromotionStat,
    FinanceNewsFacts,
    FinanceNewsItem,
    FirstBoardCriticResponse,
    LimitUpEvent,
    MarketIndexTrendFacts,
    MarketIndexTrendItem,
    PredictionQualityAuditResponse,
    RatingBacktestResponse,
    ReviewAgentReportResponse,
    SectorPerformanceFacts,
    SectorRankingItem,
    SectorStockRankingFacts,
    SectorStockTrendItem,
    StockActivityFacts,
    StockKLineBar,
    StockKLineFacts,
    StockNewsFacts,
    StockNewsItem,
)
from app.agents.tool_schemas_market import MARKET_TOOL_SCHEMAS
from app.agents.tool_schemas_news import NEWS_TOOL_SCHEMAS
from app.agents.tool_schemas_research import RESEARCH_TOOL_SCHEMAS
from app.agents.tool_schemas_review import REVIEW_TOOL_SCHEMAS


@dataclass(frozen=True)
class OutputSpec:
    """The documented row and payload models for one tool."""

    row_model: type[BaseModel] | None = None
    payload_model: type[BaseModel] | None = None


TOOL_OUTPUT_SPECS: dict[str, OutputSpec] = {
    "market_summary": OutputSpec(payload_model=MarketSummaryPayload),
    "market_index_trend": OutputSpec(
        row_model=MarketIndexTrendItem, payload_model=MarketIndexTrendFacts
    ),
    "daily_board_promotion": OutputSpec(row_model=DailyBoardPromotionStat),
    "sector_performance": OutputSpec(
        row_model=SectorRankingItem, payload_model=SectorPerformanceFacts
    ),
    "sector_stock_ranking": OutputSpec(
        row_model=SectorStockTrendItem, payload_model=SectorStockRankingFacts
    ),
    "hot_stock_ranking": OutputSpec(
        row_model=HotStockRow, payload_model=HotStockPayload
    ),
    "dragon_tiger_list": OutputSpec(
        row_model=DragonTigerRow, payload_model=DragonTigerPayload
    ),
    "first_board_ratings": OutputSpec(
        row_model=FirstBoardRatingRow, payload_model=FirstBoardRatingsPayload
    ),
    "market_event_pool": OutputSpec(
        row_model=MarketEventRow, payload_model=MarketEventPoolPayload
    ),
    "limit_up_events": OutputSpec(
        row_model=LimitUpEvent, payload_model=LimitUpEventsPayload
    ),
    "first_board_filter": OutputSpec(
        row_model=FirstBoardRatingRow, payload_model=FirstBoardFilterPayload
    ),
    "recent_limit_up_returns": OutputSpec(
        row_model=RecentLimitUpReturnRow, payload_model=RecentReturnsPayload
    ),
    "stock_kline": OutputSpec(
        row_model=StockKLineBar, payload_model=StockKLineFacts
    ),
    "post_limit_screen": OutputSpec(
        row_model=PostLimitCandidateRow, payload_model=PostLimitScreenPayload
    ),
    "post_limit_path": OutputSpec(
        row_model=PostLimitPathRow, payload_model=PostLimitPathPayload
    ),
    "post_limit_statistics": OutputSpec(payload_model=PostLimitStatisticsPayload),
    "prediction_quality_audit": OutputSpec(payload_model=PredictionQualityAuditResponse),
    "rating_backtest": OutputSpec(payload_model=RatingBacktestResponse),
    "first_board_critic": OutputSpec(payload_model=FirstBoardCriticResponse),
    "rating_evaluation": OutputSpec(
        row_model=AgentEvaluationItem, payload_model=AgentEvaluationResponse
    ),
    "review_high_score_picks": OutputSpec(payload_model=ReviewAgentReportResponse),
    "scoring_policy_status": OutputSpec(payload_model=ScoringPolicyStatusPayload),
    "finance_news": OutputSpec(
        row_model=FinanceNewsItem, payload_model=FinanceNewsFacts
    ),
    "stock_news": OutputSpec(
        row_model=StockNewsItem, payload_model=StockNewsFacts
    ),
    "stock_activity": OutputSpec(payload_model=StockActivityFacts),
}


def _field_text(field: Any) -> str:
    """Render one Pydantic field as a compact Chinese definition."""

    text = (field.description or "").strip()
    extra = field.json_schema_extra
    if isinstance(extra, dict):
        unit = extra.get("unit")
        if unit and unit not in text:
            text = f"{text}（{unit}）"
        basis = extra.get("basis")
        if basis:
            text = f"{text}；基准：{basis}"
    return text[:240]


def describe_model(model: type[BaseModel], present: Any = None) -> dict[str, str]:
    """Return documented fields, optionally limited to fields present in data."""

    descriptions = {
        name: _field_text(field)
        for name, field in model.model_fields.items()
    }
    keys: set[str] = set()
    if isinstance(present, dict):
        keys = set(present)
    elif isinstance(present, list):
        for row in present:
            if isinstance(row, dict):
                keys.update(row)
    if keys:
        return {
            name: text for name, text in descriptions.items() if name in keys
        }
    return descriptions


def field_definitions_for(
    tool: str,
    *,
    rows: Any = None,
    metadata: Any = None,
) -> dict[str, dict[str, str]]:
    """Build the compact ``rows``/``metadata`` dictionary for one result."""

    spec = TOOL_OUTPUT_SPECS.get(tool)
    if spec is None:
        return {}
    result: dict[str, dict[str, str]] = {}
    if spec.row_model is not None:
        result["rows"] = describe_model(spec.row_model, rows)
    if spec.payload_model is not None:
        result["metadata"] = describe_model(spec.payload_model, metadata)
    return {
        section: definitions
        for section, definitions in result.items()
        if definitions
    }


def merge_field_definitions(
    parent_definitions: list[dict[str, dict[str, str]]],
    *,
    rows: Any = None,
) -> dict[str, dict[str, str]]:
    """Merge parent definitions for a computed result and keep row fields."""

    descriptions: dict[str, str] = {}
    for definitions in parent_definitions:
        descriptions.update(definitions.get("rows", {}))
    if not descriptions:
        return {}
    if isinstance(rows, list) and rows:
        present = set()
        for row in rows:
            if isinstance(row, dict):
                present.update(row)
        descriptions = {
            name: text for name, text in descriptions.items() if name in present
        }
    return {"rows": descriptions} if descriptions else {}


_SCHEMAS = (
    *MARKET_TOOL_SCHEMAS,
    *RESEARCH_TOOL_SCHEMAS,
    *REVIEW_TOOL_SCHEMAS,
    *NEWS_TOOL_SCHEMAS,
)
_CONTRACT_NAMES = {contract.name for contract in _SCHEMAS}
_UNKNOWN_SPECS = set(TOOL_OUTPUT_SPECS) - _CONTRACT_NAMES
if _UNKNOWN_SPECS:
    raise RuntimeError(f"Unknown output contracts: {sorted(_UNKNOWN_SPECS)}")
for _contract in _SCHEMAS:
    _spec = TOOL_OUTPUT_SPECS.get(_contract.name)
    if _spec is None:
        raise RuntimeError(f"Missing output contract: {_contract.name}")
    if _contract.collection and _spec.row_model is None:
        raise RuntimeError(f"Missing row model: {_contract.name}")
    if not _contract.collection and _spec.payload_model is None:
        raise RuntimeError(f"Missing payload model: {_contract.name}")
    for _model in (_spec.row_model, _spec.payload_model):
        if _model is None:
            continue
        _undocumented = [
            name for name, field in _model.model_fields.items()
            if not (field.description or "").strip()
        ]
        if _undocumented:
            raise RuntimeError(
                f"Undocumented fields in {_model.__name__}: {sorted(_undocumented)}"
            )
