"""Semantic task interpretation and deterministic presentation validation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.react_runtime.display_fields import DISPLAY_FIELD_CATALOG


class FieldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: str = Field(min_length=1, max_length=120,
        description="One user-requested display field, preserving its original meaning and order.")
    field: str | None = Field(default=None,
        description="Exact catalog field, or null when unknown or ambiguous. Never omit the requested item.")


class FieldResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["unrestricted", "resolved", "unresolved"] = "unrestricted"
    proposed_fields: list[str | None] = Field(default_factory=list, max_length=12)
    reason: str = "no_explicit_fields"


class OutputContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["freeform", "table_only"] = "freeform"
    field_requests: list[FieldRequest] = Field(default_factory=list, max_length=12)
    fields: list[str] = Field(default_factory=list, max_length=12,
        description="Backend-derived hard fields; also accepts legacy checkpoints without field_requests.")
    field_resolution: FieldResolution = Field(default_factory=FieldResolution)
    table_required: bool = Field(default=False,
        description="A table is a deliverable for THIS turn, not merely a preference for future answers.")

    @model_validator(mode="after")
    def resolve_fields(self):
        # Only actual requests or legacy fields can establish a hard constraint.
        # A model-supplied diagnostic is never authority to resolve fields.
        active = bool(self.field_requests or self.fields)
        proposed = ([item.field for item in self.field_requests] if self.field_requests
                    else list(self.fields) if self.fields else list(self.field_resolution.proposed_fields))
        if not proposed:
            reason, status = "no_explicit_fields", "unrestricted"
        elif any(field is None for field in proposed):
            reason, status = "unbound_requested_field", "unresolved"
        elif any(field not in DISPLAY_FIELD_CATALOG for field in proposed):
            reason, status = "unknown_catalog_field", "unresolved"
        elif len(set(proposed)) != len(proposed):
            reason, status = "duplicate_catalog_field", "unresolved"
        elif not active:
            reason, status = "saved_proposal_is_not_a_binding", "unresolved"
        else:
            reason, status = "all_requested_fields_bound", "resolved"
        self.fields = list(proposed) if status == "resolved" else []
        self.field_resolution = FieldResolution(status=status, proposed_fields=proposed, reason=reason)
        return self


KnownDisplayField = Literal.__getitem__(tuple(DISPLAY_FIELD_CATALOG))


class ReviewFieldRequest(FieldRequest):
    field: KnownDisplayField | None = Field(default=None,
        description="Choose an exact field from display_field_catalog, or null. Chinese labels are requested text, never raw fields.")


class ReviewOutputContract(BaseModel):
    """LLM wire schema has one mapping list and no backend diagnostics/fields."""
    model_config = ConfigDict(extra="forbid")

    mode: Literal["freeform", "table_only"] = "freeform"
    field_requests: list[ReviewFieldRequest] = Field(default_factory=list, max_length=12,
        description="Every explicitly requested display field, in order. Include unknown fields with field=null; never omit an item or list only a known subset.")
    table_required: bool = False


class TaskInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_task: str = Field(default="", max_length=2400,
        description="Self-contained current request, resolving only relevant inherited intent and current overrides; no historical market facts.")
    pending_slots: list[str] = Field(default_factory=list, max_length=8,
        description="Required USER input that remains unknown. Not a data lookup or unsupported tool. Never guess a missing comparison date or stock from page defaults.")
    output_contract: OutputContract = Field(default_factory=OutputContract)


def validate_delivery(final, interpretation):
    """Reject a draft; never silently remove model text or change its columns."""
    contract = interpretation.output_contract
    if interpretation.pending_slots and final.status not in {"clarify", "refuse"}:
        raise ValueError("Required user input is unresolved; clarify: " + ", ".join(interpretation.pending_slots))
    if final.table is not None and contract.fields:
        actual = [column.field for column in final.table.columns]
        if actual != contract.fields:
            raise ValueError(f"Table fields must match the requested order exactly: {contract.fields}; got {actual}")
    if contract.table_required and final.status == "complete" and final.table is None:
        raise ValueError("The current task requires an evidence table; a prose summary cannot complete it")
    if contract.mode == "table_only" and final.status == "complete" and final.table is not None:
        if final.answer.strip() != "{{evidence_table}}":
            raise ValueError("A complete table-only answer must contain only {{evidence_table}}, without heading or prose")
    # Partial/empty/clarify/refuse may explain real missing data or safety limits.
    # Their semantic truth remains subject to evidence/status and compliance gates.
