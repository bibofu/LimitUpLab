"""Versioned scoring-policy definitions shared by rating and optimization."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models import ScoringPolicy


DEFAULT_SCORING_POLICY_VERSION = "first-board-rule-v4-opening-one-word-board"
LEGACY_DEFAULT_POLICY_VERSIONS = frozenset(
    {
        "first-board-rule-v2-enriched",
        "first-board-rule-v3-board-shape-small-cap",
    }
)
SCORING_POLICY_REGISTRY_VERSION = "scoring-policy-registry-v1"

FACTOR_NAMES: dict[str, str] = {
    "first_limit_time": "首封时间",
    "seal_stability": "封板稳定性",
    "seal_pressure": "封板次数",
    "turnover": "换手率",
    "amount": "成交额",
    "industry_heat": "行业热度",
    "market_context": "市场环境",
    "pre_limit_structure": "涨停前走势结构",
    "sector_relay": "板块强度与接力",
    "profile_history": "流通盘与近期股性",
    "dragon_tiger": "龙虎榜资金",
    "popularity": "市场人气",
}

FACTOR_KEYS_BY_NAME = {name: key for key, name in FACTOR_NAMES.items()}

# These weights exactly reproduce the previous hard-coded 65/35 scoring split.
DEFAULT_FACTOR_WEIGHTS: dict[str, float] = {
    "first_limit_time": 16.25,
    "seal_stability": 16.25,
    "seal_pressure": 7.8,
    "turnover": 7.8,
    "amount": 6.5,
    "industry_heat": 5.2,
    "market_context": 5.2,
    "pre_limit_structure": 15.0,
    "sector_relay": 8.0,
    "profile_history": 5.0,
    "dragon_tiger": 3.0,
    "popularity": 4.0,
}


def build_default_scoring_policy() -> ScoringPolicy:
    """Return the immutable baseline policy represented as a fresh model."""

    return ScoringPolicy(
        version=DEFAULT_SCORING_POLICY_VERSION,
        parent_version="first-board-rule-v3-board-shape-small-cap",
        status="champion",
        factor_weights=dict(DEFAULT_FACTOR_WEIGHTS),
        source="default",
        rationale=[
            "Penalizes unbroken one-word boards versus intraday limit-up moves.",
            "Treats data-source-normalized 09:30 opening seals as one-word boards.",
            "Scores smaller float market caps above larger float market caps.",
        ],
        created_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
        activated_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
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
