"""Bound model commentary to the new review's precomputed observations."""

from collections import Counter
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.review_digest_models import ReviewDigest


class ReviewSectionNarrative(BaseModel):
    scope: Literal["excellent", "weak", "leaders"]
    summary: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=8, max_length=100)]
    observation_ids: list[str] = Field(max_length=5)


class ReviewNarrative(BaseModel):
    sections: list[ReviewSectionNarrative] = Field(min_length=3, max_length=3)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def require_distinct_scopes(self):
        if {section.scope for section in self.sections} != {"excellent", "weak", "leaders"}:
            raise ValueError("Each evidence group needs one section")
        return self


def apply_review_narrative(digest: ReviewDigest, narrative: ReviewNarrative) -> ReviewDigest:
    """Reject invented/cross-group IDs; the model never writes evidence or counts."""
    result = digest.model_copy(deep=True)
    for section in narrative.sections:
        group = getattr(result, section.scope)
        lookup = {observation.id: observation for observation in group.observations}
        selected = section.observation_ids
        if len(selected) != len(set(selected)) or any(key not in lookup for key in selected):
            raise ValueError("Unknown or duplicate review evidence reference")
        if len(selected) > (5 if section.scope == "leaders" else 3):
            raise ValueError("Too many selected observations")
        if not group.sample_size or not lookup:
            if selected:
                raise ValueError("Empty evidence cannot support a model observation")
            continue
        if not selected:
            raise ValueError("Narrative has no supporting observations")
        if any(item.dimension == "next_open_pct" for item in lookup.values()):
            if not any(lookup[key].dimension == "next_open_pct" for key in selected):
                raise ValueError("Review must include available next-opening evidence")
        if section.scope == "leaders" and any(item.dimension.startswith("second_") for item in lookup.values()):
            if not any(lookup[key].dimension.startswith("second_") for key in selected):
                raise ValueError("Leader review must include available second-board evidence")
        group.summary = section.summary
        group.selected_observation_ids = selected
    return result


def authoritative_review_facts(report, picks, feature_comparison, evaluation_scope):
    """One concise, authoritative evidence set, independent of planner selection."""
    digest = report.review_digest
    return {
        "review_digest": digest.model_dump(mode="json", exclude={
            "excellent": {"stocks"}, "weak": {"stocks"}, "leaders": {"stocks"},
        }) if digest else None,
        "forward_validation": {
            "eligible_sample_count": sum(item.time_cohort == "premarket_final" for item in picks),
            "cohort_counts": dict(Counter(item.time_cohort for item in picks)),
            "definition": "完整追踪报告中只有premarket_final具有盘前终选前向验证资格；新五日候选的资格数量另见review_digest.notes。",
        },
        "sampling_limits": evaluation_scope,
    }
