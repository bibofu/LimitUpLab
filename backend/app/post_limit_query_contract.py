"""Typed execution contract for post-limit research tools."""

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal


POST_LIMIT_QUERY_VERSION = "post-limit-query-v4"
DEFAULT_RECENT_LIMIT_DAYS = 5
PREMARKET_OBSERVATION_RECENT_LIMIT_DAYS = 7
PostLimitMode = Literal["screen", "path", "statistics"]
PostLimitShape = Literal[
    "high_drawdown",
    "volume_consolidation",
    "pullback_stabilizing",
    "strong_nonconsecutive",
    "broken_board_repair",
    "second_to_third",
]
PREMARKET_OBSERVATION_SHAPES = frozenset(
    {"high_drawdown", "volume_consolidation"}
)


def default_recent_limit_days(shapes: tuple[str, ...], mode: str = "screen") -> int:
    """Choose the shared anchor-search window from typed shapes and mode."""

    return (
        PREMARKET_OBSERVATION_RECENT_LIMIT_DAYS
        if mode != "path" and PREMARKET_OBSERVATION_SHAPES.intersection(shapes)
        else DEFAULT_RECENT_LIMIT_DAYS
    )


@dataclass(frozen=True)
class PostLimitQueryContract:
    """Validated structured arguments shared by policy and execution."""

    version: str = POST_LIMIT_QUERY_VERSION
    mode: PostLimitMode = "screen"
    shape: PostLimitShape = "high_drawdown"
    shapes: tuple[PostLimitShape, ...] = ()
    data_as_of: date | None = None
    anchor_date: date | None = None
    recent_limit_days: int = 0
    statistics_days: int = 7
    symbol: str | None = None
    query: str | None = None
    min_peak_drawdown_pct: float | None = None
    max_volume_ratio: float | None = None
    max_range_pct: float | None = None
    min_anchor_change_pct: float | None = None
    max_anchor_change_pct: float | None = None
    board_height: int | None = None
    group_by: str | None = None
    sort_by: str | None = None
    sort_order: Literal["asc", "desc"] = "desc"
    limit: int = 10
    exhaustive: bool = False

    def __post_init__(self) -> None:
        if self.recent_limit_days == 0:
            object.__setattr__(
                self,
                "recent_limit_days",
                default_recent_limit_days(self.shapes or (self.shape,), self.mode),
            )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["shapes"] = list(self.shapes)
        for field in ("data_as_of", "anchor_date"):
            value = payload[field]
            payload[field] = value.isoformat() if value else None
        return payload

    def to_tool_arguments(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("version", None)
        payload.pop("mode", None)
        return payload
