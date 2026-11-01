"""Typed distinction between a new selection, prior entities and prior conditions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReferenceIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["none", "entities", "analysis", "conditions"]
    quote: str | None = Field(default=None, min_length=1, max_length=300)
    ordinal: int | None = Field(default=None, ge=0)
    all_entities: bool = False
    date_override: Literal["previous_trade_date", "today", "explicit"] | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if self.kind == "none" and (self.quote is not None or self.ordinal is not None
                                    or self.date_override is not None):
            raise ValueError("A new selection cannot contain a prior-reference quote or ordinal")
        if self.date_override is not None and self.kind != "analysis":
            raise ValueError("Only an analysis follow-up may override its date")
        if self.all_entities and (self.kind != "entities" or self.ordinal is not None):
            raise ValueError("Only an explicit entity batch may select all entities, without an ordinal")
        if self.kind != "none" and self.quote is None:
            raise ValueError("A historical reference requires an exact current-user quote")
        if self.kind in {"conditions", "analysis"} and self.ordinal is not None:
            raise ValueError("Conditions and analysis follow-ups do not select a stock ordinal")
        return self


def validated_reference_intent(value, query: str) -> ReferenceIntent:
    intent = value if isinstance(value, ReferenceIntent) else ReferenceIntent.model_validate(value)
    if intent.quote is not None and intent.quote not in query:
        raise ValueError("Reference intent quote must be an exact excerpt of the current user message")
    return intent
