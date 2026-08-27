"""Versioned scoring-policy definitions shared by rating and optimization."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models import ScoringPolicy


DEFAULT_SCORING_POLICY_VERSION = "first-board-rule-v5-board-shape-market-cap"
LEGACY_DEFAULT_POLICY_VERSIONS = frozenset(
    {
        "first-board-rule-v2-enriched",
        "first-board-rule-v3-board-shape-small-cap",
        "first-board-rule-v4-opening-one-word-board",
    }
)
SCORING_POLICY_REGISTRY_VERSION = "scoring-policy-registry-v1"

FACTOR_NAMES: dict[str, str] = {
    "first_limit_time": "首封时间",
    "board_pattern": "上板形态",
    "seal_stability": "封板稳定性",
    "seal_pressure": "封板次数",
    "turnover": "换手率",
    "amount": "成交额",
    "industry_heat": "行业热度",
    "market_context": "市场环境",
    "pre_limit_structure": "涨停前走势结构",
    "sector_relay": "板块强度与接力",
    "market_cap_preference": "市值偏好",
    "recent_limit_up_history": "近期股性",
    "dragon_tiger": "龙虎榜资金",
    "popularity": "市场人气",
}

FACTOR_KEYS_BY_NAME = {name: key for key, name in FACTOR_NAMES.items()}

# The two explicit preference factors each occupy 10 points; all weights total 100.
DEFAULT_FACTOR_WEIGHTS: dict[str, float] = {
    "first_limit_time": 12.0,
    "board_pattern": 10.0,
    "seal_stability": 13.0,
    "seal_pressure": 6.0,
    "turnover": 7.0,
    "amount": 5.0,
    "industry_heat": 4.0,
    "market_context": 4.0,
    "pre_limit_structure": 12.0,
    "sector_relay": 7.0,
    "market_cap_preference": 10.0,
    "recent_limit_up_history": 3.0,
    "dragon_tiger": 3.0,
    "popularity": 4.0,
}


def build_default_scoring_policy() -> ScoringPolicy:
    """Return the immutable baseline policy represented as a fresh model."""

    return ScoringPolicy(
        version=DEFAULT_SCORING_POLICY_VERSION,
        parent_version="first-board-rule-v4-opening-one-word-board",
        status="champion",
        factor_weights=dict(DEFAULT_FACTOR_WEIGHTS),
        source="default",
        rationale=[
            "Makes board pattern an explicit 10-point factor and scores intraday limit-up moves above one-word boards.",
            "Makes float-market-cap preference an explicit 10-point factor and scores smaller floats above larger floats.",
            "Separates recent limit-up behavior from market-cap evidence to avoid duplicate attribution.",
        ],
        created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        activated_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )


def validate_policy_factor_keys(policy: ScoringPolicy) -> None:
    """Reject missing or unknown factors before a policy reaches the scorer."""

    expected = set(FACTOR_NAMES)
    actual = set(policy.factor_weights)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details = []
    if missing:
        details.append(f"missing={','.join(missing)}")
    if unknown:
        details.append(f"unknown={','.join(unknown)}")
    raise ValueError(f"Invalid scoring policy factors: {'; '.join(details)}")
